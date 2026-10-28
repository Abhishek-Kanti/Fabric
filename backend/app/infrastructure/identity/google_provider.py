"""Google Workspace OIDC identity provider implementing authorization code flow with PKCE."""

from typing import Any, Callable
import urllib.parse
import uuid
import httpx
import jwt

from app.application.identity.ports import IdentityProvider
from app.domain.identity.exceptions import AuthenticationFailedException, OIDCValidationException
from app.domain.identity.models import ProviderIdentityProfile


class GoogleOIDCProvider(IdentityProvider):
    """Google Workspace / Google Identity Services OpenID Connect provider.
    
    Adheres strictly to the architectural invariant that Google's immutable 'sub'
    claim is the external subject identifier. Never matches or links by email alone.
    """

    GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
    GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
    ALLOWED_ISSUERS = ["https://accounts.google.com", "accounts.google.com"]

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        key_resolver: Callable[[str], Any] | None = None,
        token_endpoint: str | None = None,
    ) -> None:
        if not client_id:
            raise ValueError("Google client_id must be provided")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._key_resolver = key_resolver
        self._token_endpoint = token_endpoint or self.GOOGLE_TOKEN_URL
        self._jwks_client: jwt.PyJWKClient | None = None

    @property
    def provider_name(self) -> str:
        return "google"

    async def get_authorization_url(
        self,
        workspace_id: uuid.UUID,
        state: str,
        code_challenge: str,
        nonce: str | None = None,
        hosted_domain: str | None = None,
    ) -> str:
        """Construct the Google OIDC authorization redirect URL with PKCE, nonce, and optional hosted domain."""
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "select_account",
        }
        if nonce:
            params["nonce"] = nonce
        if hosted_domain:
            params["hd"] = hosted_domain

        return f"{self.GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"

    async def authenticate_code(
        self,
        code: str,
        code_verifier: str,
        expected_nonce: str | None = None,
    ) -> ProviderIdentityProfile:
        """Exchange authorization code with Google for tokens and verify ID token."""
        data = {
            "code": code,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "redirect_uri": self._redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self._token_endpoint,
                data=data,
                headers={"Accept": "application/json"},
                timeout=10.0,
            )

        if resp.status_code != 200:
            raise AuthenticationFailedException(
                f"Failed to exchange code with Google: {resp.text}"
            )

        token_payload = resp.json()
        id_token = token_payload.get("id_token")
        if not id_token:
            raise AuthenticationFailedException("Google token response did not contain an id_token")

        return self.verify_id_token(id_token, expected_nonce=expected_nonce)

    def verify_id_token(
        self,
        id_token: str,
        expected_nonce: str | None = None,
    ) -> ProviderIdentityProfile:
        """Cryptographically verify Google ID token claims, signature, issuer, and nonce.
        
        Raises:
            OIDCValidationException: If token is expired, signature is invalid, or claims mismatch.
        """
        # Resolve signing key (either via custom resolver for tests or Google JWKS)
        signing_key: Any = None
        if self._key_resolver:
            signing_key = self._key_resolver(id_token)
        else:
            if self._jwks_client is None:
                self._jwks_client = jwt.PyJWKClient(self.GOOGLE_JWKS_URL, cache_jwk_set=True, lifespan=3600)
            try:
                signing_key = self._jwks_client.get_signing_key_from_jwt(id_token).key
            except Exception as e:
                raise OIDCValidationException(f"Failed to resolve Google signing key: {e}") from e

        try:
            payload = jwt.decode(
                id_token,
                signing_key,
                algorithms=["RS256", "ES256"],
                audience=self._client_id,
                options={
                    "verify_exp": True,
                    "verify_aud": True,
                    "require": ["sub", "iss", "aud", "exp", "iat"],
                },
            )
        except jwt.ExpiredSignatureError as e:
            raise OIDCValidationException("Google ID token has expired") from e
        except jwt.InvalidTokenError as e:
            raise OIDCValidationException(f"Invalid Google ID token: {e}") from e

        # Validate issuer
        iss = payload.get("iss")
        if iss not in self.ALLOWED_ISSUERS:
            raise OIDCValidationException(
                f"Invalid ID token issuer '{iss}'. Expected one of {self.ALLOWED_ISSUERS}"
            )

        # Validate nonce if expected
        if expected_nonce is not None:
            token_nonce = payload.get("nonce")
            if token_nonce != expected_nonce:
                raise OIDCValidationException(
                    f"OIDC nonce mismatch: expected '{expected_nonce}', received '{token_nonce}'"
                )

        # Validate email_verified
        email_verified = payload.get("email_verified", False)
        if not email_verified:
            raise OIDCValidationException("Google account email is unverified")

        sub = payload.get("sub")
        if not sub:
            raise OIDCValidationException("Google ID token missing 'sub' claim")

        return ProviderIdentityProfile(
            provider="google",
            provider_subject_id=str(sub),
            email=payload.get("email"),
            email_verified=bool(email_verified),
            display_name=payload.get("name"),
            tenant_id=payload.get("hd"),
            metadata={
                "hd": payload.get("hd"),
                "picture": payload.get("picture"),
            },
        )
