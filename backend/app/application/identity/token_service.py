"""JWT implementation of TokenService for Company Brain native session tokens."""

from datetime import datetime, timedelta, timezone
import uuid
import jwt
from uuid6 import uuid7

from app.application.identity.ports import TokenService
from app.domain.identity.exceptions import InvalidTokenException, TokenExpiredException
from app.domain.identity.models import SessionTokenClaims


class JwtTokenService(TokenService):
    """Cryptographic JWT token service implementing native session tokens."""

    def __init__(
        self,
        secret_key: str,
        algorithm: str = "HS256",
        issuer: str = "company-brain-auth",
        audience: str = "company-brain-api",
        default_expire_seconds: int = 3600,
    ) -> None:
        if not secret_key:
            raise ValueError("JWT secret key must not be empty")

        allowed_algorithms = {"HS256", "HS384", "HS512", "RS256", "ES256"}
        if not algorithm or algorithm.lower() == "none" or algorithm not in allowed_algorithms:
            raise ValueError(f"Insecure or unsupported JWT algorithm: '{algorithm}'")

        self._secret_key = secret_key
        self._algorithm = algorithm
        self._issuer = issuer
        self._audience = audience
        self._default_expire_seconds = default_expire_seconds

    def create_session_token(
        self,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        expire_seconds: int | None = None,
    ) -> str:
        """Issue a signed JWT session token with RFC-compliant claims."""
        now = datetime.now(timezone.utc)
        exp_delta = timedelta(seconds=expire_seconds if expire_seconds is not None else self._default_expire_seconds)
        exp = now + exp_delta

        payload = {
            "sub": str(user_id),
            "workspace_id": str(workspace_id),
            "jti": str(uuid7()),
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
            "iss": self._issuer,
            "aud": self._audience,
        }
        return jwt.encode(payload, self._secret_key, algorithm=self._algorithm)

    def verify_session_token(self, token: str) -> SessionTokenClaims:
        """Decode and cryptographically verify session token claims."""
        try:
            payload = jwt.decode(
                token,
                self._secret_key,
                algorithms=[self._algorithm],
                issuer=self._issuer,
                audience=self._audience,
                options={
                    "require": ["sub", "workspace_id", "jti", "iat", "exp", "iss", "aud"],
                    "verify_exp": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except jwt.ExpiredSignatureError as e:
            raise TokenExpiredException("Session token has expired") from e
        except jwt.InvalidTokenError as e:
            raise InvalidTokenException(f"Invalid session token: {e}") from e

        try:
            user_id = uuid.UUID(payload["sub"])
            workspace_id = uuid.UUID(payload["workspace_id"])
            jti = uuid.UUID(payload["jti"])
        except (ValueError, KeyError, TypeError) as e:
            raise InvalidTokenException("Malformed UUID claims in session token") from e

        return SessionTokenClaims(
            user_id=user_id,
            workspace_id=workspace_id,
            jti=jti,
            issued_at=datetime.fromtimestamp(payload["iat"], tz=timezone.utc),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
            issuer=payload["iss"],
            audience=payload["aud"],
        )
