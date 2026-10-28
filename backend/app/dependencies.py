"""Application dependency injection providers for Company Brain."""

from typing import Annotated
import uuid
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.authorization.context import IdentityContext
from app.application.authorization.service import AuthorizationService
from app.application.identity.oauth_state import OAuthStateManager
from app.application.identity.ports import TokenService
from app.application.identity.token_service import JwtTokenService
from app.config import Settings, get_settings
from app.domain.enums import UserStatus, WorkspaceRole
from app.domain.identity.exceptions import AuthenticationFailedException
from app.infrastructure.authorization.postgres_repository import PostgresAuthorizationRepository
from app.infrastructure.database.session import get_async_session
from app.infrastructure.identity.google_provider import GoogleOIDCProvider

SettingsDep = Annotated[Settings, Depends(get_settings)]
AsyncSessionDep = Annotated[AsyncSession, Depends(get_async_session)]

http_bearer_scheme = HTTPBearer(auto_error=False)

_oauth_state_manager = OAuthStateManager(default_ttl_seconds=600)


def get_oauth_state_manager() -> OAuthStateManager:
    """Dependency provider for singleton ephemeral OAuthStateManager."""
    return _oauth_state_manager


OAuthStateManagerDep = Annotated[OAuthStateManager, Depends(get_oauth_state_manager)]


def get_token_service(settings: SettingsDep) -> TokenService:
    """Dependency provider for native session TokenService."""
    return JwtTokenService(
        secret_key=settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        default_expire_seconds=settings.jwt_expire_seconds,
    )


TokenServiceDep = Annotated[TokenService, Depends(get_token_service)]


def get_google_provider(settings: SettingsDep) -> GoogleOIDCProvider:
    """Dependency provider for Google Workspace OIDC provider."""
    return GoogleOIDCProvider(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        redirect_uri=settings.google_redirect_uri,
    )


GoogleProviderDep = Annotated[GoogleOIDCProvider, Depends(get_google_provider)]


def get_authorization_service(session: AsyncSessionDep) -> AuthorizationService:
    """Dependency provider for AuthorizationService backed by PostgreSQL ReBAC."""
    repo = PostgresAuthorizationRepository(session)
    return AuthorizationService(repo)


AuthorizationServiceDep = Annotated[AuthorizationService, Depends(get_authorization_service)]


async def get_current_identity(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(http_bearer_scheme)],
    token_service: TokenServiceDep,
    session: AsyncSessionDep,
    request: Request = None,  # type: ignore[assignment]
) -> IdentityContext:
    """FastAPI authentication dependency resolving an authenticated IdentityContext from a session token.
    
    Security Invariants:
    1. Supports Bearer header authorization or safe HttpOnly session cookies.
    2. Cryptographically validates token signature, algorithm, issuer, audience, and expiry.
    3. Queries live PostgreSQL state on EVERY request to ensure active status and current workspace role.
    4. Prevents stale JWT role bypass; role and status mutations take effect immediately.
    5. Validates that the workspace exists and is active.
    6. Fails closed (HTTP 401) on missing credentials, forged tokens, expired tokens, or inactive accounts.
    """
    settings = get_settings()
    raw_token: str | None = None
    if credentials is not None and credentials.credentials:
        raw_token = credentials.credentials
    elif request is not None and settings.cookie_session_name in request.cookies:
        raw_token = request.cookies[settings.cookie_session_name]

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = token_service.verify_session_token(raw_token)
    except AuthenticationFailedException as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from e

    # Load current account and workspace state from PostgreSQL
    query = text(
        """
        SELECT 
            u.id AS user_id,
            u.workspace_id,
            u.workspace_role,
            u.status AS user_status,
            u.deleted_at AS user_deleted_at,
            w.is_active AS ws_is_active,
            w.deleted_at AS ws_deleted_at
        FROM users u
        JOIN workspaces w ON w.id = u.workspace_id
        WHERE u.id = :user_id AND u.workspace_id = :workspace_id;
        """
    )
    result = await session.execute(
        query,
        {"user_id": claims.user_id, "workspace_id": claims.workspace_id},
    )
    row = result.fetchone()

    if row is None or row._mapping["user_deleted_at"] is not None or row._mapping["ws_deleted_at"] is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User or workspace not found or deleted",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )

    m = row._mapping
    if not m["ws_is_active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Workspace is inactive",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )

    user_status_val = m["user_status"]
    if user_status_val != UserStatus.ACTIVE.value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"User account is {user_status_val}",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )

    identity = IdentityContext.create_user(
        workspace_id=claims.workspace_id,
        user_id=claims.user_id,
        workspace_role=WorkspaceRole(m["workspace_role"]),
        user_status=UserStatus(user_status_val),
    )
    identity.validate_active()
    return identity


CurrentIdentityDep = Annotated[IdentityContext, Depends(get_current_identity)]
