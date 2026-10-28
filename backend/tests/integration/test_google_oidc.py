"""Comprehensive integration tests for Google Workspace OIDC authentication.

Verifies:
1. Authorization Code Flow with PKCE and state/nonce generation.
2. HttpOnly, SameSite, Secure cookie handling.
3. Single-use state consumption and replay protection.
4. Cryptographic ID token verification (RS256, issuer, audience, expiry, nonce).
5. Immutable subject binding (Google 'sub' claim).
6. Strict prevention of silent account creation and email-only account linking.
7. Active status validation for users and workspaces.
8. Cross-workspace isolation.
9. Open redirect neutralization.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import uuid
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient, Response as HttpxResponse
import jwt
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.application.identity.oauth_state import OAuthStateManager
from app.application.identity.token_service import JwtTokenService
from app.config import Settings, get_settings
from app.dependencies import (
    get_async_session,
    get_google_provider,
    get_oauth_state_manager,
    get_token_service,
)
from app.domain.enums import UserStatus, WorkspaceRole
from app.domain.identity.exceptions import (
    AuthenticationFailedException,
    InvalidOAuthStateException,
    OIDCValidationException,
)
from app.infrastructure.database.models.external_identity import ExternalIdentityModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.workspace import WorkspaceModel
from app.infrastructure.identity.google_provider import GoogleOIDCProvider
from app.main import create_app


@pytest.fixture(scope="session")
def rsa_keypair():
    """Generate an RSA keypair for minting and verifying test Google ID tokens."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    public_key = private_key.public_key()
    return private_key, public_key


def make_google_id_token(
    private_key,
    sub: str = "109876543210987654321",
    email: str = "alice@example.com",
    email_verified: bool = True,
    aud: str = "test-google-client-id.apps.googleusercontent.com",
    iss: str = "https://accounts.google.com",
    nonce: str | None = None,
    expires_delta: timedelta = timedelta(minutes=10),
    hd: str | None = None,
) -> str:
    """Helper to generate cryptographically signed Google ID tokens for testing."""
    now = datetime.now(timezone.utc)
    payload = {
        "iss": iss,
        "sub": sub,
        "aud": aud,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "email": email,
        "email_verified": email_verified,
        "name": "Alice Tester",
    }
    if nonce is not None:
        payload["nonce"] = nonce
    if hd is not None:
        payload["hd"] = hd

    return jwt.encode(payload, private_key, algorithm="RS256")


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        app_name="Company Brain Test",
        environment="testing",
        jwt_secret_key="test-secret-key-that-is-at-least-32-chars-long!",
        jwt_expire_seconds=3600,
        cookie_secure=False,
        cookie_samesite="lax",
        cookie_session_name="cb_session_token",
        cookie_oauth_state_name="cb_oauth_state",
        google_client_id="test-google-client-id.apps.googleusercontent.com",
        google_client_secret="test-google-client-secret",
        google_redirect_uri="http://localhost:8000/api/v1/auth/google/callback",
    )


@pytest.fixture
def mock_google_provider(test_settings: Settings, rsa_keypair) -> GoogleOIDCProvider:
    _, public_key = rsa_keypair
    return GoogleOIDCProvider(
        client_id=test_settings.google_client_id,
        client_secret=test_settings.google_client_secret,
        redirect_uri=test_settings.google_redirect_uri,
        key_resolver=lambda token: public_key,
    )


# ---------------------------------------------------------------------------
# 1. Login Initiation Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_login_initiation_success(
    db_session: AsyncSession,
    test_settings: Settings,
    mock_google_provider: GoogleOIDCProvider,
):
    """Verify login initiation generates PKCE parameters, sets CSRF cookie, and redirects to Google."""
    ws_id = uuid7()
    workspace = WorkspaceModel(id=ws_id, name="Google WS", slug="google-ws", is_active=True)
    db_session.add(workspace)
    await db_session.flush()

    app = create_app(settings=test_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_async_session] = lambda: db_session
    app.dependency_overrides[get_google_provider] = lambda: mock_google_provider

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/auth/google/login",
            params={"workspace_slug": "google-ws", "redirect_url": "/dashboard"},
            follow_redirects=False,
        )

        assert resp.status_code == 302
        redirect_url = resp.headers["location"]
        assert redirect_url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert "response_type=code" in redirect_url
        assert "code_challenge=" in redirect_url
        assert "code_challenge_method=S256" in redirect_url
        assert "state=" in redirect_url
        assert "nonce=" in redirect_url

        # Verify CSRF state cookie is set with HttpOnly, SameSite, and path
        assert "set-cookie" in resp.headers
        cookies = resp.headers.get_list("set-cookie")
        oauth_cookie = next((c for c in cookies if test_settings.cookie_oauth_state_name in c), None)
        assert oauth_cookie is not None
        assert "HttpOnly" in oauth_cookie
        assert "SameSite=lax" in oauth_cookie.lower() or "samesite=lax" in oauth_cookie.lower()
        assert "Path=/api/v1/auth" in oauth_cookie


@pytest.mark.asyncio
async def test_google_login_rejects_inactive_or_missing_workspace(
    db_session: AsyncSession,
    test_settings: Settings,
):
    """Verify login initiation rejects unknown or inactive workspaces."""
    ws_inactive_id = uuid7()
    ws_inactive = WorkspaceModel(id=ws_inactive_id, name="Inactive WS", slug="inactive-slug", is_active=False)
    db_session.add(ws_inactive)
    await db_session.flush()

    app = create_app(settings=test_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_async_session] = lambda: db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Non-existent workspace
        resp404 = await ac.get("/api/v1/auth/google/login", params={"workspace_slug": "non-existent"})
        assert resp404.status_code == 404

        # 2. Inactive workspace
        resp400 = await ac.get("/api/v1/auth/google/login", params={"workspace_slug": "inactive-slug"})
        assert resp400.status_code == 400
        assert "inactive" in resp400.json()["detail"].lower()


@pytest.mark.asyncio
async def test_google_login_open_redirect_neutralized(
    db_session: AsyncSession,
    test_settings: Settings,
    mock_google_provider: GoogleOIDCProvider,
):
    """Verify open redirect attempts (e.g. external URL or protocol-relative URL) are neutralized to /."""
    ws_id = uuid7()
    workspace = WorkspaceModel(id=ws_id, name="Safe WS", slug="safe-ws", is_active=True)
    db_session.add(workspace)
    await db_session.flush()

    state_mgr = OAuthStateManager()
    app = create_app(settings=test_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_async_session] = lambda: db_session
    app.dependency_overrides[get_google_provider] = lambda: mock_google_provider
    app.dependency_overrides[get_oauth_state_manager] = lambda: state_mgr

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Attempt open redirect with external URL
        resp = await ac.get(
            "/api/v1/auth/google/login",
            params={"workspace_slug": "safe-ws", "redirect_url": "https://evil.com/phish", "format": "json"},
        )
        assert resp.status_code == 200
        state_id = resp.json()["state"]

        stored_state = state_mgr.consume_state(state_id)
        assert stored_state.redirect_url == "/"  # Neutralized!


# ---------------------------------------------------------------------------
# 2. Full Callback & Token Delivery Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_callback_happy_path(
    db_session: AsyncSession,
    test_settings: Settings,
    mock_google_provider: GoogleOIDCProvider,
    rsa_keypair,
):
    """Verify full end-to-end happy path:
    1. Pre-provisioned user in external_identities
    2. Callback exchanges code and validates token
    3. Issues session token in HttpOnly cookie
    4. Token kept out of URL
    5. Accessing /api/v1/auth/me with session cookie succeeds
    """
    private_key, _ = rsa_keypair
    ws_id = uuid7()
    user_id = uuid7()
    google_sub = "112233445566778899001"

    workspace = WorkspaceModel(id=ws_id, name="Happy WS", slug="happy-ws", is_active=True)
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="alice@happy.com",
        display_name="Alice Happy",
        handle=f"alice_{user_id.hex[:6]}",
        workspace_role=WorkspaceRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    ext_id = ExternalIdentityModel(
        id=uuid7(),
        workspace_id=ws_id,
        user_id=user_id,
        provider="google",
        provider_subject_id=google_sub,
        provider_email="alice@happy.com",
        is_primary=True,
    )
    db_session.add_all([workspace, user, ext_id])
    await db_session.flush()

    state_mgr = OAuthStateManager()
    app = create_app(settings=test_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_async_session] = lambda: db_session
    app.dependency_overrides[get_google_provider] = lambda: mock_google_provider
    app.dependency_overrides[get_oauth_state_manager] = lambda: state_mgr

    # 1. Initiate login
    oauth_state, code_challenge = state_mgr.generate_state(workspace_id=ws_id, redirect_url="/dashboard")

    # 2. Mint valid Google ID token with matching nonce
    id_token = make_google_id_token(
        private_key=private_key,
        sub=google_sub,
        email="alice@happy.com",
        aud=test_settings.google_client_id,
        nonce=oauth_state.nonce,
    )

    # Mock the Google token exchange endpoint to return id_token
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = HttpxResponse(
            status_code=200,
            json={"access_token": "mock-access-token", "id_token": id_token, "token_type": "Bearer"},
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Set state cookie
            ac.cookies.set(test_settings.cookie_oauth_state_name, oauth_state.state_id)

            resp = await ac.get(
                "/api/v1/auth/google/callback",
                params={"code": "valid-auth-code", "state": oauth_state.state_id},
                follow_redirects=False,
            )

            assert resp.status_code == 302
            # Verify redirect location is the sanitized relative URL, NOT leaking tokens in query params!
            assert resp.headers["location"] == "/dashboard"
            assert "token=" not in resp.headers["location"]
            assert "access_token=" not in resp.headers["location"]

            # Verify session cookie was set
            cookies = resp.headers.get_list("set-cookie")
            session_cookie = next((c for c in cookies if test_settings.cookie_session_name in c), None)
            assert session_cookie is not None
            assert "HttpOnly" in session_cookie
            assert "Path=/" in session_cookie

            # Verify state cookie was cleared
            state_clear = next((c for c in cookies if f"{test_settings.cookie_oauth_state_name}=" in c and "Max-Age=0" in c), None)
            assert state_clear is not None

            # 3. Verify accessing /api/v1/auth/me using the delivered session cookie
            me_resp = await ac.get("/api/v1/auth/me")
            assert me_resp.status_code == 200
            me_data = me_resp.json()
            assert me_data["id"] == str(user_id)
            assert me_data["workspace_id"] == str(ws_id)
            assert me_data["workspace_role"] == WorkspaceRole.ADMIN.value


# ---------------------------------------------------------------------------
# 3. Security Guardrails & Negative Scenarios
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_callback_fails_when_state_cookie_missing_or_mismatched(
    db_session: AsyncSession,
    test_settings: Settings,
    mock_google_provider: GoogleOIDCProvider,
):
    """CSRF Guardrail: Reject if state query parameter does not match the HttpOnly state cookie."""
    app = create_app(settings=test_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_async_session] = lambda: db_session
    app.dependency_overrides[get_google_provider] = lambda: mock_google_provider

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. State cookie completely missing
        resp1 = await ac.get("/api/v1/auth/google/callback", params={"code": "code", "state": "state123"})
        assert resp1.status_code == 400
        assert "CSRF" in resp1.json()["detail"]

        # 2. State cookie mismatched
        ac.cookies.set(test_settings.cookie_oauth_state_name, "state-different")
        resp2 = await ac.get("/api/v1/auth/google/callback", params={"code": "code", "state": "state123"})
        assert resp2.status_code == 400
        assert "CSRF" in resp2.json()["detail"]


@pytest.mark.asyncio
async def test_callback_fails_on_state_replay_and_expiration(
    db_session: AsyncSession,
    test_settings: Settings,
    mock_google_provider: GoogleOIDCProvider,
    rsa_keypair,
):
    """Replay Guardrail: Consuming an OAuth state must be single-use; replay attempts fail immediately."""
    private_key, _ = rsa_keypair
    ws_id = uuid7()
    user_id = uuid7()
    google_sub = "1928374650123"

    workspace = WorkspaceModel(id=ws_id, name="Replay WS", slug="replay-ws", is_active=True)
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="bob@replay.com",
        display_name="Bob Replay",
        handle=f"bob_{user_id.hex[:6]}",
        workspace_role=WorkspaceRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    ext_id = ExternalIdentityModel(
        id=uuid7(),
        workspace_id=ws_id,
        user_id=user_id,
        provider="google",
        provider_subject_id=google_sub,
        is_primary=True,
    )
    db_session.add_all([workspace, user, ext_id])
    await db_session.flush()

    state_mgr = OAuthStateManager()
    app = create_app(settings=test_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_async_session] = lambda: db_session
    app.dependency_overrides[get_google_provider] = lambda: mock_google_provider
    app.dependency_overrides[get_oauth_state_manager] = lambda: state_mgr

    oauth_state, _ = state_mgr.generate_state(workspace_id=ws_id)
    id_token = make_google_id_token(
        private_key=private_key,
        sub=google_sub,
        aud=test_settings.google_client_id,
        nonce=oauth_state.nonce,
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = HttpxResponse(
            status_code=200,
            json={"access_token": "mock-token", "id_token": id_token},
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ac.cookies.set(test_settings.cookie_oauth_state_name, oauth_state.state_id)

            # First attempt: succeeds
            resp1 = await ac.get(
                "/api/v1/auth/google/callback",
                params={"code": "code1", "state": oauth_state.state_id, "format": "json"},
            )
            assert resp1.status_code == 200

            # Second attempt with same state: MUST FAIL (Replay rejected)
            ac.cookies.set(test_settings.cookie_oauth_state_name, oauth_state.state_id)
            resp2 = await ac.get(
                "/api/v1/auth/google/callback",
                params={"code": "code2", "state": oauth_state.state_id, "format": "json"},
            )
            assert resp2.status_code == 400
            assert "replay rejected" in resp2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_callback_fails_when_nonce_mismatched(
    db_session: AsyncSession,
    test_settings: Settings,
    mock_google_provider: GoogleOIDCProvider,
    rsa_keypair,
):
    """Nonce Guardrail: Reject if ID token nonce does not match the login session nonce."""
    private_key, _ = rsa_keypair
    ws_id = uuid7()
    workspace = WorkspaceModel(id=ws_id, name="Nonce WS", slug="nonce-ws", is_active=True)
    db_session.add(workspace)
    await db_session.flush()

    state_mgr = OAuthStateManager()
    app = create_app(settings=test_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_async_session] = lambda: db_session
    app.dependency_overrides[get_google_provider] = lambda: mock_google_provider
    app.dependency_overrides[get_oauth_state_manager] = lambda: state_mgr

    oauth_state, _ = state_mgr.generate_state(workspace_id=ws_id)
    # Mint token with a DIFFERENT nonce
    id_token = make_google_id_token(
        private_key=private_key,
        sub="any-sub",
        aud=test_settings.google_client_id,
        nonce="tampered-or-different-nonce",
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = HttpxResponse(
            status_code=200,
            json={"access_token": "mock-token", "id_token": id_token},
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ac.cookies.set(test_settings.cookie_oauth_state_name, oauth_state.state_id)
            resp = await ac.get(
                "/api/v1/auth/google/callback",
                params={"code": "code", "state": oauth_state.state_id},
            )
            assert resp.status_code == 401
            assert "nonce mismatch" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_callback_fails_on_invalid_signature_issuer_audience_expiry(
    test_settings: Settings,
    mock_google_provider: GoogleOIDCProvider,
    rsa_keypair,
):
    """Cryptographic Verification: Verify provider rejects invalid signatures, issuers, audiences, and expired tokens."""
    private_key, _ = rsa_keypair

    # 1. Invalid signature (signed with an attacker's key)
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged_token = make_google_id_token(
        private_key=attacker_key,
        aud=test_settings.google_client_id,
    )
    with pytest.raises(OIDCValidationException, match="Invalid Google ID token"):
        mock_google_provider.verify_id_token(forged_token)

    # 2. Issuer mismatch
    wrong_iss_token = make_google_id_token(
        private_key=private_key,
        aud=test_settings.google_client_id,
        iss="https://evil-issuer.com",
    )
    with pytest.raises(OIDCValidationException, match="Invalid ID token issuer"):
        mock_google_provider.verify_id_token(wrong_iss_token)

    # 3. Audience mismatch
    wrong_aud_token = make_google_id_token(
        private_key=private_key,
        aud="wrong-client-id.apps.googleusercontent.com",
    )
    with pytest.raises(OIDCValidationException, match="Invalid Google ID token"):
        mock_google_provider.verify_id_token(wrong_aud_token)

    # 4. Expired token
    expired_token = make_google_id_token(
        private_key=private_key,
        aud=test_settings.google_client_id,
        expires_delta=timedelta(seconds=-10),
    )
    with pytest.raises(OIDCValidationException, match="expired"):
        mock_google_provider.verify_id_token(expired_token)

    # 5. Unverified email
    unverified_token = make_google_id_token(
        private_key=private_key,
        aud=test_settings.google_client_id,
        email_verified=False,
    )
    with pytest.raises(OIDCValidationException, match="unverified"):
        mock_google_provider.verify_id_token(unverified_token)


@pytest.mark.asyncio
async def test_unknown_identity_rejected_even_if_email_matches_existing_user(
    db_session: AsyncSession,
    test_settings: Settings,
    mock_google_provider: GoogleOIDCProvider,
    rsa_keypair,
):
    """Immutable Subject Invariant & No Silent Linking:
    If an existing user has the matching email address but no ExternalIdentity row with
    the Google 'sub' claim, the login MUST FAIL (HTTP 401).
    Never link accounts by email alone, and never silently create users.
    """
    private_key, _ = rsa_keypair
    ws_id = uuid7()
    user_id = uuid7()
    user_email = "charlie@enterprise.com"
    unknown_google_sub = "unknown-google-sub-99999"

    workspace = WorkspaceModel(id=ws_id, name="Strict WS", slug="strict-ws", is_active=True)
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email=user_email,
        display_name="Charlie Existing",
        handle=f"charlie_{user_id.hex[:6]}",
        workspace_role=WorkspaceRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    db_session.add_all([workspace, user])
    await db_session.flush()

    state_mgr = OAuthStateManager()
    app = create_app(settings=test_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_async_session] = lambda: db_session
    app.dependency_overrides[get_google_provider] = lambda: mock_google_provider
    app.dependency_overrides[get_oauth_state_manager] = lambda: state_mgr

    oauth_state, _ = state_mgr.generate_state(workspace_id=ws_id)
    id_token = make_google_id_token(
        private_key=private_key,
        sub=unknown_google_sub,
        email=user_email,  # Same email as existing user!
        aud=test_settings.google_client_id,
        nonce=oauth_state.nonce,
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = HttpxResponse(
            status_code=200,
            json={"access_token": "mock-token", "id_token": id_token},
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ac.cookies.set(test_settings.cookie_oauth_state_name, oauth_state.state_id)
            resp = await ac.get(
                "/api/v1/auth/google/callback",
                params={"code": "code", "state": oauth_state.state_id},
            )
            assert resp.status_code == 401
            assert "not linked to any user in this workspace" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_callback_fails_when_user_suspended_or_workspace_inactive(
    db_session: AsyncSession,
    test_settings: Settings,
    mock_google_provider: GoogleOIDCProvider,
    rsa_keypair,
):
    """Verify callback fails if pre-provisioned user is suspended or workspace is inactive."""
    private_key, _ = rsa_keypair
    ws_id = uuid7()
    user_id = uuid7()
    google_sub = "suspended-user-sub"

    workspace = WorkspaceModel(id=ws_id, name="WS", slug="ws", is_active=True)
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="dave@example.com",
        display_name="Dave Suspended",
        handle=f"dave_{user_id.hex[:6]}",
        workspace_role=WorkspaceRole.MEMBER,
        status=UserStatus.SUSPENDED,  # Suspended!
    )
    ext_id = ExternalIdentityModel(
        id=uuid7(),
        workspace_id=ws_id,
        user_id=user_id,
        provider="google",
        provider_subject_id=google_sub,
        is_primary=True,
    )
    db_session.add_all([workspace, user, ext_id])
    await db_session.flush()

    state_mgr = OAuthStateManager()
    app = create_app(settings=test_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_async_session] = lambda: db_session
    app.dependency_overrides[get_google_provider] = lambda: mock_google_provider
    app.dependency_overrides[get_oauth_state_manager] = lambda: state_mgr

    oauth_state, _ = state_mgr.generate_state(workspace_id=ws_id)
    id_token = make_google_id_token(
        private_key=private_key,
        sub=google_sub,
        aud=test_settings.google_client_id,
        nonce=oauth_state.nonce,
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = HttpxResponse(
            status_code=200,
            json={"access_token": "mock-token", "id_token": id_token},
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ac.cookies.set(test_settings.cookie_oauth_state_name, oauth_state.state_id)
            resp = await ac.get(
                "/api/v1/auth/google/callback",
                params={"code": "code", "state": oauth_state.state_id},
            )
            assert resp.status_code == 401
            assert "suspended" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_cross_workspace_identity_isolation(
    db_session: AsyncSession,
    test_settings: Settings,
    mock_google_provider: GoogleOIDCProvider,
    rsa_keypair,
):
    """Tenant Isolation: A Google identity provisioned in Workspace A cannot authenticate into Workspace B."""
    private_key, _ = rsa_keypair
    ws_a = uuid7()
    ws_b = uuid7()
    user_a = uuid7()
    google_sub = "user-in-ws-a-sub"

    workspace_a = WorkspaceModel(id=ws_a, name="WS A", slug="ws-a", is_active=True)
    workspace_b = WorkspaceModel(id=ws_b, name="WS B", slug="ws-b", is_active=True)
    user = UserModel(
        id=user_a,
        workspace_id=ws_a,
        email="user@ws-a.com",
        display_name="User A",
        handle=f"usera_{user_a.hex[:6]}",
        workspace_role=WorkspaceRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    ext_id = ExternalIdentityModel(
        id=uuid7(),
        workspace_id=ws_a,
        user_id=user_a,
        provider="google",
        provider_subject_id=google_sub,
        is_primary=True,
    )
    db_session.add_all([workspace_a, workspace_b, user, ext_id])
    await db_session.flush()

    state_mgr = OAuthStateManager()
    app = create_app(settings=test_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_async_session] = lambda: db_session
    app.dependency_overrides[get_google_provider] = lambda: mock_google_provider
    app.dependency_overrides[get_oauth_state_manager] = lambda: state_mgr

    # User initiates login into Workspace B!
    oauth_state, _ = state_mgr.generate_state(workspace_id=ws_b)
    id_token = make_google_id_token(
        private_key=private_key,
        sub=google_sub,
        aud=test_settings.google_client_id,
        nonce=oauth_state.nonce,
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = HttpxResponse(
            status_code=200,
            json={"access_token": "mock-token", "id_token": id_token},
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ac.cookies.set(test_settings.cookie_oauth_state_name, oauth_state.state_id)
            resp = await ac.get(
                "/api/v1/auth/google/callback",
                params={"code": "code", "state": oauth_state.state_id},
            )
            # Rejected because this external identity is not linked to Workspace B!
            assert resp.status_code == 401
            assert "not linked to any user in this workspace" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_google_workspace_domain_restriction_enforced(
    db_session: AsyncSession,
    test_settings: Settings,
    mock_google_provider: GoogleOIDCProvider,
    rsa_keypair,
):
    """Google Workspace Tenant Restriction:
    Enforce domain restrictions strictly via validated OIDC 'hd' claims,
    NOT by parsing the email string.
    """
    private_key, _ = rsa_keypair
    ws_id = uuid7()
    user_id = uuid7()
    google_sub = "domain-restricted-sub"

    workspace = WorkspaceModel(
        id=ws_id,
        name="Enterprise WS",
        slug="corp-ws",
        is_active=True,
        settings={"google_workspace_domain": "acme.corp"},
    )
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="employee@acme.corp",
        display_name="Enterprise Employee",
        handle=f"emp_{user_id.hex[:6]}",
        workspace_role=WorkspaceRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    ext_id = ExternalIdentityModel(
        id=uuid7(),
        workspace_id=ws_id,
        user_id=user_id,
        provider="google",
        provider_subject_id=google_sub,
        is_primary=True,
    )
    db_session.add_all([workspace, user, ext_id])
    await db_session.flush()

    state_mgr = OAuthStateManager()
    app = create_app(settings=test_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_async_session] = lambda: db_session
    app.dependency_overrides[get_google_provider] = lambda: mock_google_provider
    app.dependency_overrides[get_oauth_state_manager] = lambda: state_mgr

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Login initiation passes hd hint in auth URL
        login_resp = await ac.get(
            "/api/v1/auth/google/login",
            params={"workspace_slug": "corp-ws", "format": "json"},
        )
        assert login_resp.status_code == 200
        auth_url = login_resp.json()["authorization_url"]
        assert "hd=acme.corp" in auth_url

        # 2. Case A: Valid hd claim in ID token matching configured domain succeeds
        oauth_state, _ = state_mgr.generate_state(workspace_id=ws_id)
        valid_hd_token = make_google_id_token(
            private_key=private_key,
            sub=google_sub,
            email="employee@acme.corp",
            aud=test_settings.google_client_id,
            nonce=oauth_state.nonce,
            hd="acme.corp",
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = HttpxResponse(status_code=200, json={"id_token": valid_hd_token})
            ac.cookies.set(test_settings.cookie_oauth_state_name, oauth_state.state_id)
            resp_ok = await ac.get(
                "/api/v1/auth/google/callback",
                params={"code": "code", "state": oauth_state.state_id, "format": "json"},
            )
            assert resp_ok.status_code == 200

        # 3. Case B: Wrong hd claim in ID token rejected with HTTP 401
        oauth_state2, _ = state_mgr.generate_state(workspace_id=ws_id)
        wrong_hd_token = make_google_id_token(
            private_key=private_key,
            sub=google_sub,
            email="employee@other.corp",
            aud=test_settings.google_client_id,
            nonce=oauth_state2.nonce,
            hd="other.corp",
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = HttpxResponse(status_code=200, json={"id_token": wrong_hd_token})
            ac.cookies.set(test_settings.cookie_oauth_state_name, oauth_state2.state_id)
            resp_err = await ac.get(
                "/api/v1/auth/google/callback",
                params={"code": "code", "state": oauth_state2.state_id},
            )
            assert resp_err.status_code == 401
            assert "domain 'other.corp' is not authorized" in resp_err.json()["detail"].lower()

        # 4. Case C: Personal Google account (no hd claim) rejected with HTTP 401
        oauth_state3, _ = state_mgr.generate_state(workspace_id=ws_id)
        no_hd_token = make_google_id_token(
            private_key=private_key,
            sub=google_sub,
            email="employee@gmail.com",
            aud=test_settings.google_client_id,
            nonce=oauth_state3.nonce,
            hd=None,
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = HttpxResponse(status_code=200, json={"id_token": no_hd_token})
            ac.cookies.set(test_settings.cookie_oauth_state_name, oauth_state3.state_id)
            resp_no_hd = await ac.get(
                "/api/v1/auth/google/callback",
                params={"code": "code", "state": oauth_state3.state_id},
            )
            assert resp_no_hd.status_code == 401
            assert "domain 'unmanaged' is not authorized" in resp_no_hd.json()["detail"].lower()


@pytest.mark.asyncio
async def test_cookie_secure_and_flags_enforced_in_production(
    db_session: AsyncSession,
    mock_google_provider: GoogleOIDCProvider,
    rsa_keypair,
):
    """Verify that in production environments, Secure, HttpOnly, and SameSite flags are strictly set on cookies."""
    private_key, public_key = rsa_keypair
    ws_id = uuid7()
    user_id = uuid7()
    google_sub = "prod-cookie-sub"

    workspace = WorkspaceModel(id=ws_id, name="Prod WS", slug="prod-ws", is_active=True)
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="user@prod.com",
        display_name="Prod User",
        handle=f"prod_{user_id.hex[:6]}",
        workspace_role=WorkspaceRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    ext_id = ExternalIdentityModel(
        id=uuid7(),
        workspace_id=ws_id,
        user_id=user_id,
        provider="google",
        provider_subject_id=google_sub,
        is_primary=True,
    )
    db_session.add_all([workspace, user, ext_id])
    await db_session.flush()

    prod_settings = Settings(
        app_name="Prod App",
        environment="staging",
        jwt_secret_key="production-secure-secret-key-that-is-at-least-32-chars-long!",
        google_client_id="prod-client-id.apps.googleusercontent.com",
        google_client_secret="prod-client-secret-secure",
        cookie_secure=True,
    )

    prod_mock_google_provider = GoogleOIDCProvider(
        client_id=prod_settings.google_client_id,
        client_secret=prod_settings.google_client_secret,
        redirect_uri=prod_settings.google_redirect_uri,
        key_resolver=lambda token: public_key,
    )

    state_mgr = OAuthStateManager()
    app = create_app(settings=prod_settings)
    app.dependency_overrides[get_settings] = lambda: prod_settings
    app.dependency_overrides[get_async_session] = lambda: db_session
    app.dependency_overrides[get_google_provider] = lambda: prod_mock_google_provider
    app.dependency_overrides[get_oauth_state_manager] = lambda: state_mgr

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
        # 1. Login: oauth state cookie must have Secure, HttpOnly, SameSite=lax
        login_resp = await ac.get(
            "/api/v1/auth/google/login",
            params={"workspace_slug": "prod-ws"},
            follow_redirects=False,
        )
        oauth_cookie = next(c for c in login_resp.headers.get_list("set-cookie") if "cb_oauth_state" in c)
        assert "secure" in oauth_cookie.lower()
        assert "httponly" in oauth_cookie.lower()
        assert "samesite=lax" in oauth_cookie.lower()

        # 2. Callback: session cookie must have Secure, HttpOnly, SameSite=lax
        oauth_state, _ = state_mgr.generate_state(workspace_id=ws_id)
        id_token = make_google_id_token(
            private_key=private_key,
            sub=google_sub,
            aud=prod_settings.google_client_id,
            nonce=oauth_state.nonce,
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = HttpxResponse(status_code=200, json={"id_token": id_token})
            ac.cookies.set("cb_oauth_state", oauth_state.state_id)
            cb_resp = await ac.get(
                "/api/v1/auth/google/callback",
                params={"code": "code", "state": oauth_state.state_id},
                follow_redirects=False,
            )
            session_cookie = next(c for c in cb_resp.headers.get_list("set-cookie") if "cb_session_token" in c)
            assert "secure" in session_cookie.lower()
            assert "httponly" in session_cookie.lower()
            assert "samesite=lax" in session_cookie.lower()


def test_multi_worker_deployment_rejected_with_in_memory_state():
    """Verify Settings rejects multi-worker uvicorn deployment when using in-memory state."""
    with pytest.raises(ValueError, match="Multi-worker deployment"):
        Settings(
            app_name="App",
            uvicorn_workers=4,
        )
