"""Integration and security tests for authentication and session token verification."""

from datetime import datetime, timezone
import pytest
from httpx import ASGITransport, AsyncClient
import jwt
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.application.authorization.context import ResourceTarget
from app.application.identity.token_service import JwtTokenService
from app.config import Settings, get_settings
from app.dependencies import get_async_session, get_authorization_service
from app.domain.authorization.permissions import ProjectAction
from app.domain.enums import ExternalIdentityStatus, ProjectVisibility, UserStatus, WorkspaceRole
from app.infrastructure.database.models.external_identity import ExternalIdentityModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.workspace import WorkspaceModel
from app.main import create_app


@pytest.mark.asyncio
async def test_dev_token_issuance_and_auth_me_success(db_session: AsyncSession):
    """Verify happy path: development token issuance and authenticated profile retrieval."""
    ws_id = uuid7()
    user_id = uuid7()
    ext_id = uuid7()

    workspace = WorkspaceModel(id=ws_id, name="Auth WS", slug=f"auth-ws-{ws_id.hex[:6]}")
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="alex@example.com",
        display_name="Alex Vance",
        handle=f"alex_{user_id.hex[:6]}",
        workspace_role=WorkspaceRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    ext_identity = ExternalIdentityModel(
        id=ext_id,
        workspace_id=ws_id,
        user_id=user_id,
        provider="google",
        provider_subject_id="google-sub-alex-123",
        provider_email="alex@example.com",
        provider_display_name="Alex (Google)",
        status=ExternalIdentityStatus.ACTIVE,
        is_primary=True,
    )
    db_session.add_all([workspace, user, ext_identity])
    await db_session.flush()

    settings = Settings(
        app_name="Test App",
        environment="testing",
        jwt_secret_key="dev-secret-key-change-in-production-must-be-32-chars-long",
    )
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_async_session] = lambda: db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Obtain dev session token
        resp = await ac.post(
            "/api/v1/auth/dev-token",
            json={"workspace_id": str(ws_id), "user_id": str(user_id)},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] == 3600
        token = data["access_token"]

        # 2. Access /api/v1/auth/me with Bearer token
        me_resp = await ac.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 200, me_resp.text
        me_data = me_resp.json()
        assert me_data["id"] == str(user_id)
        assert me_data["workspace_id"] == str(ws_id)
        assert me_data["email"] == "alex@example.com"
        assert me_data["workspace_role"] == "member"
        assert me_data["status"] == "active"
        assert len(me_data["external_identities"]) == 1
        assert me_data["external_identities"][0]["provider"] == "google"
        assert me_data["external_identities"][0]["provider_subject_id"] == "google-sub-alex-123"
        assert me_data["external_identities"][0]["is_primary"] is True


@pytest.mark.asyncio
async def test_token_rejected_when_forged_or_invalid_signature(db_session: AsyncSession):
    """Verify forged tokens signed with an unauthorized secret or tampered payload fail closed."""
    ws_id = uuid7()
    user_id = uuid7()

    workspace = WorkspaceModel(id=ws_id, name="Forged WS", slug=f"forged-ws-{ws_id.hex[:6]}")
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="forged@example.com",
        display_name="Forged User",
        handle=f"forged_{user_id.hex[:6]}",
        workspace_role=WorkspaceRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    db_session.add_all([workspace, user])
    await db_session.flush()

    settings = Settings(
        app_name="Test App",
        environment="testing",
        jwt_secret_key="legit-server-secret-key-32-chars-minimum!!",
    )
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_async_session] = lambda: db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Attacker signs token with a different secret
        attacker_service = JwtTokenService(
            secret_key="attacker-malicious-secret-key-32-chars-long",
            issuer="company-brain-auth",
            audience="company-brain-api",
        )
        forged_token = attacker_service.create_session_token(workspace_id=ws_id, user_id=user_id)

        resp = await ac.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {forged_token}"},
        )
        assert resp.status_code == 401
        assert "invalid_token" in resp.headers.get("www-authenticate", "")

        # 2. Tampered token payload
        tampered_token = forged_token[:-5] + "XXXXX"
        resp2 = await ac.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {tampered_token}"},
        )
        assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_token_rejected_when_expired(db_session: AsyncSession):
    """Verify expired tokens are rejected with HTTP 401."""
    ws_id = uuid7()
    user_id = uuid7()

    workspace = WorkspaceModel(id=ws_id, name="Exp WS", slug=f"exp-ws-{ws_id.hex[:6]}")
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="exp@example.com",
        display_name="Expired User",
        handle=f"exp_{user_id.hex[:6]}",
        workspace_role=WorkspaceRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    db_session.add_all([workspace, user])
    await db_session.flush()

    secret = "test-secret-key-32-characters-minimum!!"
    settings = Settings(
        app_name="Test App",
        environment="testing",
        jwt_secret_key=secret,
    )
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_async_session] = lambda: db_session

    token_service = JwtTokenService(secret_key=secret)
    # Token expired 10 seconds ago
    expired_token = token_service.create_session_token(workspace_id=ws_id, user_id=user_id, expire_seconds=-10)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401
        assert "expired" in resp.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_token_rejected_when_invalid_issuer_or_audience(db_session: AsyncSession):
    """Verify tokens with unexpected issuer or audience are rejected."""
    ws_id = uuid7()
    user_id = uuid7()

    workspace = WorkspaceModel(id=ws_id, name="Aud WS", slug=f"aud-ws-{ws_id.hex[:6]}")
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="aud@example.com",
        display_name="Audience User",
        handle=f"aud_{user_id.hex[:6]}",
        workspace_role=WorkspaceRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    db_session.add_all([workspace, user])
    await db_session.flush()

    secret = "test-secret-key-32-characters-minimum!!"
    settings = Settings(
        app_name="Test App",
        environment="testing",
        jwt_secret_key=secret,
        jwt_issuer="company-brain-auth",
        jwt_audience="company-brain-api",
    )
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_async_session] = lambda: db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Rogue issuer
        bad_issuer_svc = JwtTokenService(secret_key=secret, issuer="rogue-issuer", audience="company-brain-api")
        bad_iss_token = bad_issuer_svc.create_session_token(workspace_id=ws_id, user_id=user_id)
        r1 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {bad_iss_token}"})
        assert r1.status_code == 401

        # Rogue audience
        bad_aud_svc = JwtTokenService(secret_key=secret, issuer="company-brain-auth", audience="rogue-audience")
        bad_aud_token = bad_aud_svc.create_session_token(workspace_id=ws_id, user_id=user_id)
        r2 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {bad_aud_token}"})
        assert r2.status_code == 401


@pytest.mark.asyncio
async def test_cross_workspace_token_tampering_rejected(db_session: AsyncSession):
    """Verify that a valid user token tampered with another workspace ID is rejected."""
    ws_a = uuid7()
    ws_b = uuid7()
    user_a = uuid7()

    w_a = WorkspaceModel(id=ws_a, name="Workspace A", slug=f"ws-a-{ws_a.hex[:6]}")
    w_b = WorkspaceModel(id=ws_b, name="Workspace B", slug=f"ws-b-{ws_b.hex[:6]}")
    u_a = UserModel(
        id=user_a,
        workspace_id=ws_a,
        email="usera@example.com",
        display_name="User A",
        handle=f"usera_{user_a.hex[:6]}",
        workspace_role=WorkspaceRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    db_session.add_all([w_a, w_b, u_a])
    await db_session.flush()

    secret = "test-secret-key-32-characters-minimum!!"
    settings = Settings(
        app_name="Test App",
        environment="testing",
        jwt_secret_key=secret,
    )
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_async_session] = lambda: db_session

    # Token signed correctly, but claims user_a belongs to ws_b
    token_service = JwtTokenService(secret_key=secret)
    tampered_ws_token = token_service.create_session_token(workspace_id=ws_b, user_id=user_a)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {tampered_ws_token}"},
        )
        assert resp.status_code == 401
        assert "not found" in resp.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_token_rejected_when_user_inactive_or_suspended(db_session: AsyncSession):
    """Verify that changing a user to suspended/deactivated fails closed on the next request."""
    ws_id = uuid7()
    user_id = uuid7()

    workspace = WorkspaceModel(id=ws_id, name="Suspend WS", slug=f"suspend-ws-{ws_id.hex[:6]}")
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="suspend@example.com",
        display_name="Suspended User",
        handle=f"suspend_{user_id.hex[:6]}",
        workspace_role=WorkspaceRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    db_session.add_all([workspace, user])
    await db_session.flush()

    secret = "test-secret-key-32-characters-minimum!!"
    settings = Settings(
        app_name="Test App",
        environment="testing",
        jwt_secret_key=secret,
    )
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_async_session] = lambda: db_session

    token_service = JwtTokenService(secret_key=secret)
    token = token_service.create_session_token(workspace_id=ws_id, user_id=user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Initial request succeeds
        r1 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r1.status_code == 200

        # Suspend user in DB
        user.status = UserStatus.SUSPENDED
        await db_session.flush()

        # Immediate fail closed on next request
        r2 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r2.status_code == 401
        assert "suspended" in r2.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_token_rejected_when_user_or_workspace_soft_deleted(db_session: AsyncSession):
    """Verify that soft-deleting a user or workspace immediately invalidates existing session tokens."""
    ws_id = uuid7()
    user_id = uuid7()

    workspace = WorkspaceModel(id=ws_id, name="Delete WS", slug=f"delete-ws-{ws_id.hex[:6]}")
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="del@example.com",
        display_name="Deleted User",
        handle=f"del_{user_id.hex[:6]}",
        workspace_role=WorkspaceRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    db_session.add_all([workspace, user])
    await db_session.flush()

    secret = "test-secret-key-32-characters-minimum!!"
    settings = Settings(
        app_name="Test App",
        environment="testing",
        jwt_secret_key=secret,
    )
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_async_session] = lambda: db_session

    token_service = JwtTokenService(secret_key=secret)
    token = token_service.create_session_token(workspace_id=ws_id, user_id=user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Soft-delete user
        user.deleted_at = datetime.now(timezone.utc)
        await db_session.flush()

        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert "deleted" in resp.json().get("detail", "").lower()

        # Restore user, soft-delete workspace
        user.deleted_at = None
        workspace.deleted_at = datetime.now(timezone.utc)
        await db_session.flush()

        resp2 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp2.status_code == 401
        assert "deleted" in resp2.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_role_change_takes_immediate_effect_without_reissuing_token(db_session: AsyncSession):
    """Verify that role mutations in PostgreSQL immediately update IdentityContext without stale JWT bypass."""
    ws_id = uuid7()
    user_id = uuid7()

    workspace = WorkspaceModel(id=ws_id, name="Role Mutate WS", slug=f"role-ws-{ws_id.hex[:6]}")
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="rolechange@example.com",
        display_name="Role Change User",
        handle=f"role_{user_id.hex[:6]}",
        workspace_role=WorkspaceRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    db_session.add_all([workspace, user])
    await db_session.flush()

    secret = "test-secret-key-32-characters-minimum!!"
    settings = Settings(
        app_name="Test App",
        environment="testing",
        jwt_secret_key=secret,
    )
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_async_session] = lambda: db_session

    token_service = JwtTokenService(secret_key=secret)
    token = token_service.create_session_token(workspace_id=ws_id, user_id=user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # First request: Member role
        r1 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r1.status_code == 200
        assert r1.json()["workspace_role"] == "member"

        # Mutate role in database to Admin
        user.workspace_role = WorkspaceRole.ADMIN
        await db_session.flush()

        # Second request using the EXACT SAME token: Immediately reflects Admin role
        r2 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r2.status_code == 200
        assert r2.json()["workspace_role"] == "admin"


@pytest.mark.asyncio
async def test_dev_token_endpoint_strictly_rejected_in_production_environment(db_session: AsyncSession):
    """Verify development authentication endpoint fails closed (HTTP 403) in production environment."""
    ws_id = uuid7()
    user_id = uuid7()

    workspace = WorkspaceModel(id=ws_id, name="Prod WS", slug=f"prod-ws-{ws_id.hex[:6]}")
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="produser@example.com",
        display_name="Prod User",
        handle=f"prod_{user_id.hex[:6]}",
        workspace_role=WorkspaceRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    db_session.add_all([workspace, user])
    await db_session.flush()

    prod_settings = Settings(
        app_name="Test App",
        environment="production",
        jwt_secret_key="production-secure-secret-key-that-is-at-least-32-chars-long!",
        google_client_id="prod-client-id.apps.googleusercontent.com",
        google_client_secret="prod-client-secret-secure",
    )
    app = create_app(settings=prod_settings)
    app.dependency_overrides[get_settings] = lambda: prod_settings
    app.dependency_overrides[get_async_session] = lambda: db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/auth/dev-token",
            json={"workspace_id": str(ws_id), "user_id": str(user_id)},
        )
        assert resp.status_code == 403
        assert "disabled" in resp.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_authorization_service_integration_with_authenticated_identity(db_session: AsyncSession):
    """Verify that authenticated IdentityContext integrates seamlessly with AuthorizationService."""
    ws_id = uuid7()
    member_user = uuid7()
    admin_user = uuid7()

    workspace = WorkspaceModel(id=ws_id, name="Authz WS", slug=f"authz-ws-{ws_id.hex[:6]}")
    u_member = UserModel(
        id=member_user,
        workspace_id=ws_id,
        email="member@example.com",
        display_name="Member",
        handle=f"member_{member_user.hex[:6]}",
        workspace_role=WorkspaceRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    u_admin = UserModel(
        id=admin_user,
        workspace_id=ws_id,
        email="admin@example.com",
        display_name="Admin",
        handle=f"admin_{admin_user.hex[:6]}",
        workspace_role=WorkspaceRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    db_session.add_all([workspace, u_member, u_admin])
    await db_session.flush()

    project = ProjectModel(
        id=uuid7(),
        workspace_id=ws_id,
        name="Private Proj",
        slug="priv-proj",
        visibility=ProjectVisibility.PRIVATE,
        created_by_user_id=admin_user,
    )
    db_session.add(project)
    await db_session.flush()

    authz_service = get_authorization_service(db_session)
    secret = "test-secret-key-32-characters-minimum!!"
    token_service = JwtTokenService(secret_key=secret)

    member_token = token_service.create_session_token(workspace_id=ws_id, user_id=member_user)
    admin_token = token_service.create_session_token(workspace_id=ws_id, user_id=admin_user)

    settings = Settings(
        app_name="Test App",
        environment="testing",
        jwt_secret_key=secret,
    )
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_async_session] = lambda: db_session

    from fastapi.security import HTTPAuthorizationCredentials
    from app.dependencies import get_current_identity

    # 1. Member identity: cannot read private project
    member_creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=member_token)
    member_ctx = await get_current_identity(member_creds, token_service, db_session)
    target = ResourceTarget.for_project(ws_id, project.id, visibility=ProjectVisibility.PRIVATE)

    assert await authz_service.can(member_ctx, ProjectAction.READ, target) is False

    # 2. Admin identity: can read private project
    admin_creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=admin_token)
    admin_ctx = await get_current_identity(admin_creds, token_service, db_session)

    assert await authz_service.can(admin_ctx, ProjectAction.READ, target) is True


@pytest.mark.asyncio
async def test_token_rejected_when_workspace_inactive(db_session: AsyncSession):
    """Verify that an inactive workspace (is_active=False) immediately invalidates session tokens."""
    ws_id = uuid7()
    user_id = uuid7()

    workspace = WorkspaceModel(
        id=ws_id,
        name="Inactive WS",
        slug=f"inact-ws-{ws_id.hex[:6]}",
        is_active=False,
    )
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="inactivews@example.com",
        display_name="Inactive WS User",
        handle=f"inact_{user_id.hex[:6]}",
        workspace_role=WorkspaceRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    db_session.add_all([workspace, user])
    await db_session.flush()

    secret = "test-secret-key-32-characters-minimum!!"
    settings = Settings(
        app_name="Test App",
        environment="testing",
        jwt_secret_key=secret,
    )
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_async_session] = lambda: db_session

    token_service = JwtTokenService(secret_key=secret)
    token = token_service.create_session_token(workspace_id=ws_id, user_id=user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert "workspace is inactive" in resp.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_dev_token_rejected_when_workspace_inactive(db_session: AsyncSession):
    """Verify that dev-token cannot be issued for an inactive workspace."""
    ws_id = uuid7()
    user_id = uuid7()

    workspace = WorkspaceModel(
        id=ws_id,
        name="Inactive WS 2",
        slug=f"inact-ws2-{ws_id.hex[:6]}",
        is_active=False,
    )
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="inact2@example.com",
        display_name="Inact 2",
        handle=f"inact2_{user_id.hex[:6]}",
        workspace_role=WorkspaceRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    db_session.add_all([workspace, user])
    await db_session.flush()

    settings = Settings(app_name="Test App", environment="testing")
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_async_session] = lambda: db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/auth/dev-token",
            json={"workspace_id": str(ws_id), "user_id": str(user_id)},
        )
        assert resp.status_code == 400
        assert "inactive workspace" in resp.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_dev_token_cannot_impersonate_cross_workspace_users(db_session: AsyncSession):
    """Verify dev-token rejects pairing user from Workspace A with Workspace B."""
    ws_a = uuid7()
    ws_b = uuid7()
    user_a = uuid7()

    w_a = WorkspaceModel(id=ws_a, name="WS A", slug=f"wsa-{ws_a.hex[:6]}")
    w_b = WorkspaceModel(id=ws_b, name="WS B", slug=f"wsb-{ws_b.hex[:6]}")
    u_a = UserModel(
        id=user_a,
        workspace_id=ws_a,
        email="user_a@example.com",
        display_name="User A",
        handle=f"usera_{user_a.hex[:6]}",
        workspace_role=WorkspaceRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    db_session.add_all([w_a, w_b, u_a])
    await db_session.flush()

    settings = Settings(app_name="Test App", environment="testing")
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_async_session] = lambda: db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Request token for user_a under ws_b (invalid tenancy)
        resp = await ac.post(
            "/api/v1/auth/dev-token",
            json={"workspace_id": str(ws_b), "user_id": str(user_a)},
        )
        assert resp.status_code == 404
        assert "not found" in resp.json().get("detail", "").lower()


def test_token_service_rejects_missing_mandatory_claims():
    """Verify TokenService rejects tokens missing any required RFC/security claim."""
    secret = "test-secret-key-32-characters-minimum!!"
    token_svc = JwtTokenService(secret_key=secret)
    now = int(datetime.now(timezone.utc).timestamp())

    required_claims = ["sub", "workspace_id", "jti", "iat", "exp", "iss", "aud"]
    base_payload = {
        "sub": str(uuid7()),
        "workspace_id": str(uuid7()),
        "jti": str(uuid7()),
        "iat": now,
        "exp": now + 3600,
        "iss": "company-brain-auth",
        "aud": "company-brain-api",
    }

    from app.domain.identity.exceptions import InvalidTokenException

    for claim in required_claims:
        tampered_payload = {k: v for k, v in base_payload.items() if k != claim}
        token = jwt.encode(tampered_payload, secret, algorithm="HS256")
        with pytest.raises(InvalidTokenException):
            token_svc.verify_session_token(token)


def test_token_service_algorithm_pinning_and_rejection():
    """Verify TokenService rejects unsupported algorithms and algorithm 'none'."""
    secret = "test-secret-key-32-characters-minimum!!"

    # 1. Insecure algorithm rejected at service creation
    with pytest.raises(ValueError, match="Insecure or unsupported"):
        JwtTokenService(secret_key=secret, algorithm="none")

    with pytest.raises(ValueError, match="Insecure or unsupported"):
        JwtTokenService(secret_key=secret, algorithm="INVALID_ALG")

    # 2. Token signed with wrong algorithm rejected at verification
    token_svc = JwtTokenService(secret_key=secret, algorithm="HS256")
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "sub": str(uuid7()),
        "workspace_id": str(uuid7()),
        "jti": str(uuid7()),
        "iat": now,
        "exp": now + 3600,
        "iss": "company-brain-auth",
        "aud": "company-brain-api",
    }
    token_hs512 = jwt.encode(payload, secret + secret, algorithm="HS512")

    from app.domain.identity.exceptions import InvalidTokenException
    with pytest.raises(InvalidTokenException):
        token_svc.verify_session_token(token_hs512)


def test_token_service_malformed_and_non_uuid_claims():
    """Verify TokenService rejects non-UUID claims and completely malformed strings."""
    secret = "test-secret-key-32-characters-minimum!!"
    token_svc = JwtTokenService(secret_key=secret)
    now = int(datetime.now(timezone.utc).timestamp())

    from app.domain.identity.exceptions import InvalidTokenException

    # 1. Non-UUID sub
    token_bad_sub = jwt.encode(
        {
            "sub": "not-a-valid-uuid",
            "workspace_id": str(uuid7()),
            "jti": str(uuid7()),
            "iat": now,
            "exp": now + 3600,
            "iss": "company-brain-auth",
            "aud": "company-brain-api",
        },
        secret,
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenException, match="Malformed UUID"):
        token_svc.verify_session_token(token_bad_sub)

    # 2. Non-UUID workspace_id
    token_bad_ws = jwt.encode(
        {
            "sub": str(uuid7()),
            "workspace_id": "not-a-valid-uuid",
            "jti": str(uuid7()),
            "iat": now,
            "exp": now + 3600,
            "iss": "company-brain-auth",
            "aud": "company-brain-api",
        },
        secret,
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenException, match="Malformed UUID"):
        token_svc.verify_session_token(token_bad_ws)

    # 3. Completely malformed token string
    with pytest.raises(InvalidTokenException):
        token_svc.verify_session_token("not-even-a-jwt")


def test_production_configuration_rejects_weak_secrets():
    """Verify Settings rejects default dev secret and weak keys in production/staging environments."""
    # 1. Default dev secret in production -> ValueError
    with pytest.raises(ValueError, match="Insecure jwt_secret_key in staging/production"):
        Settings(
            environment="production",
            jwt_secret_key="dev-secret-key-change-in-production-must-be-32-chars-long",
        )

    # 2. Short secret (<32 chars) in staging -> ValueError
    with pytest.raises(ValueError, match="Insecure jwt_secret_key in staging/production"):
        Settings(
            environment="staging",
            jwt_secret_key="too-short-secret",
        )

    # 3. Valid 32+ char secret in production -> Succeeds
    valid_settings = Settings(
        environment="production",
        jwt_secret_key="a-very-secure-production-secret-key-at-least-32-chars!",
        google_client_id="prod-client-id.apps.googleusercontent.com",
        google_client_secret="prod-client-secret-secure",
    )
    assert valid_settings.environment == "production"

