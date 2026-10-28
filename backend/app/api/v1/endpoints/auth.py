"""Authentication endpoints for Company Brain."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.application.authorization.context import IdentityContext
from app.application.identity.ports import TokenService
from app.config import Settings
from app.dependencies import (
    AsyncSessionDep,
    CurrentIdentityDep,
    GoogleProviderDep,
    OAuthStateManagerDep,
    SettingsDep,
    TokenServiceDep,
)
from app.domain.enums import UserStatus
from app.domain.identity.exceptions import (
    AuthenticationFailedException,
    InvalidOAuthStateException,
    OIDCValidationException,
)
from app.infrastructure.database.models.external_identity import ExternalIdentityModel
from app.infrastructure.database.models.user import UserModel
from app.schemas.auth import (
    AuthenticatedUserResponse,
    DevTokenRequest,
    ExternalIdentityDTO,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/dev-token", response_model=TokenResponse)
async def issue_development_token(
    payload: DevTokenRequest,
    settings: SettingsDep,
    token_service: TokenServiceDep,
    session: AsyncSessionDep,
) -> TokenResponse:
    """Issue a development session token for testing and local development.
    
    Security Guardrail:
    Strictly disabled outside 'development' and 'testing' environments.
    Validates that the target user and workspace exist, are active, and belong together.
    """
    if not settings.enable_dev_auth or settings.environment not in ("development", "testing"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Development authentication is disabled outside development/testing environments",
        )

    # Validate that target user and workspace exist and are active
    query = text(
        """
        SELECT u.id, u.status, u.deleted_at, w.is_active AS ws_is_active, w.deleted_at AS ws_deleted_at
        FROM users u
        JOIN workspaces w ON w.id = u.workspace_id
        WHERE u.id = :user_id AND u.workspace_id = :workspace_id;
        """
    )
    result = await session.execute(
        query,
        {"user_id": payload.user_id, "workspace_id": payload.workspace_id},
    )
    row = result.fetchone()

    if row is None or row._mapping["deleted_at"] is not None or row._mapping["ws_deleted_at"] is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User or workspace not found or deleted",
        )

    m = row._mapping
    if not m["ws_is_active"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot issue token for inactive workspace",
        )

    if m["status"] != UserStatus.ACTIVE.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot issue token for user in '{m['status']}' status",
        )

    token = token_service.create_session_token(
        workspace_id=payload.workspace_id,
        user_id=payload.user_id,
        expire_seconds=settings.jwt_expire_seconds,
    )
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.jwt_expire_seconds,
    )


@router.get("/google/login")
async def google_login(
    workspace_slug: str | None = None,
    workspace_id: str | None = None,
    redirect_url: str | None = None,
    format: str | None = None,
    settings: SettingsDep = ...,
    state_manager: OAuthStateManagerDep = ...,
    google_provider: GoogleProviderDep = ...,
    session: AsyncSessionDep = ...,
) -> Response:
    """Initiate Google Workspace OIDC authorization code flow with PKCE.
    
    1. Validates intended workspace existence and active status.
    2. Generates PKCE code challenge and ephemeral OIDC nonce.
    3. Binds state to the specific workspace.
    4. Sets HttpOnly CSRF state cookie.
    5. Redirects user to Google or returns JSON authorization URL.
    """
    if workspace_id is not None:
        query = text("SELECT id, settings, is_active, deleted_at FROM workspaces WHERE id = :wid AND deleted_at IS NULL;")
        result = await session.execute(query, {"wid": workspace_id})
    elif workspace_slug is not None:
        query = text("SELECT id, settings, is_active, deleted_at FROM workspaces WHERE slug = :slug AND deleted_at IS NULL;")
        result = await session.execute(query, {"slug": workspace_slug})
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either workspace_slug or workspace_id must be provided",
        )

    ws_row = result.fetchone()
    if ws_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found",
        )

    if not ws_row._mapping["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace is inactive",
        )

    target_ws_id = ws_row._mapping["id"]
    ws_settings = ws_row._mapping.get("settings") or {}
    hosted_domain = ws_settings.get("google_workspace_domain") or (
        settings.google_allowed_domains[0] if settings.google_allowed_domains else None
    )

    oauth_state, code_challenge = state_manager.generate_state(
        workspace_id=target_ws_id,
        redirect_url=redirect_url,
    )

    auth_url = await google_provider.get_authorization_url(
        workspace_id=target_ws_id,
        state=oauth_state.state_id,
        code_challenge=code_challenge,
        nonce=oauth_state.nonce,
        hosted_domain=hosted_domain,
    )

    if format == "json":
        resp: Response = JSONResponse(
            content={
                "authorization_url": auth_url,
                "state": oauth_state.state_id,
            }
        )
    else:
        resp = RedirectResponse(
            url=auth_url,
            status_code=status.HTTP_302_FOUND,
        )

    resp.set_cookie(
        key=settings.cookie_oauth_state_name,
        value=oauth_state.state_id,
        max_age=600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/api/v1/auth",
    )
    return resp


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str,
    state: str,
    format: str | None = None,
    settings: SettingsDep = ...,
    state_manager: OAuthStateManagerDep = ...,
    google_provider: GoogleProviderDep = ...,
    token_service: TokenServiceDep = ...,
    session: AsyncSessionDep = ...,
) -> Response:
    """Handle Google OIDC authorization callback.
    
    Security Guarantees:
    1. CSRF Verification: Validates that state query param matches the HttpOnly state cookie.
    2. Replay Prevention: Atomically consumes state; replay attempts fail immediately.
    3. Nonce & ID Token Validation: Cryptographically validates signature, issuer, audience, expiry, and nonce.
    4. Immutable Subject Binding: Resolves user strictly by Google's immutable 'sub' in external_identities.
    5. Zero Silent Account Creation: If not provisioned in the workspace, fails closed with HTTP 401.
    6. Browser Safe Transport: Sets HttpOnly session cookie; never passes session token in URL parameters.
    """
    # 1. CSRF State Cookie Check
    state_cookie = request.cookies.get(settings.cookie_oauth_state_name)
    if not state_cookie or state_cookie != state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth state cookie missing or mismatched (CSRF protection failed)",
        )

    # 2. Atomic Single-Use State Consumption (Replay Guard)
    try:
        oauth_state = state_manager.consume_state(state)
    except InvalidOAuthStateException as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    # 3. Exchange Code and Cryptographically Validate ID Token Claims
    try:
        profile = await google_provider.authenticate_code(
            code=code,
            code_verifier=oauth_state.code_verifier,
            expected_nonce=oauth_state.nonce,
        )
    except (AuthenticationFailedException, OIDCValidationException) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Google authentication failed: {e}",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from e

    # 4. Canonical External Identity Lookup (Strictly by Google sub, NEVER by email alone)
    query = text(
        """
        SELECT 
            ei.user_id,
            ei.workspace_id,
            u.status AS user_status,
            u.deleted_at AS user_deleted_at,
            w.settings AS ws_settings,
            w.is_active AS ws_is_active,
            w.deleted_at AS ws_deleted_at
        FROM external_identities ei
        JOIN users u ON u.id = ei.user_id
        JOIN workspaces w ON w.id = u.workspace_id
        WHERE ei.workspace_id = :workspace_id
          AND ei.provider = 'google'
          AND ei.provider_subject_id = :provider_subject_id
          AND ei.deleted_at IS NULL;
        """
    )
    result = await session.execute(
        query,
        {
            "workspace_id": oauth_state.workspace_id,
            "provider_subject_id": profile.provider_subject_id,
        },
    )
    row = result.fetchone()

    # 5. Fail closed if not provisioned: NO SILENT CREATION, NO EMAIL LINKING
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google identity is not linked to any user in this workspace. Please contact your workspace administrator to provision your identity.",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )

    m = row._mapping
    if m["user_deleted_at"] is not None or m["ws_deleted_at"] is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account or workspace is deleted",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )

    if not m["ws_is_active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Workspace is inactive",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )

    if m["user_status"] != UserStatus.ACTIVE.value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"User account is {m['user_status']}",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )

    # Enforce Google Workspace hosted domain (hd) restrictions if configured
    ws_settings = m["ws_settings"] or {}
    allowed_domains: list[str] = []
    if ws_settings.get("allowed_google_domains"):
        allowed_domains.extend(ws_settings["allowed_google_domains"])
    elif ws_settings.get("google_workspace_domain"):
        allowed_domains.append(ws_settings["google_workspace_domain"])
    elif settings.google_allowed_domains:
        allowed_domains.extend(settings.google_allowed_domains)

    if allowed_domains:
        token_hd = profile.tenant_id
        if not token_hd or token_hd.lower() not in [d.lower() for d in allowed_domains]:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Google account hosted domain '{token_hd or 'unmanaged'}' is not authorized for this workspace. Must belong to {allowed_domains}",
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )

    # 6. Issue Native Session Token (NO roles or permissions in token)
    token = token_service.create_session_token(
        workspace_id=oauth_state.workspace_id,
        user_id=m["user_id"],
        expire_seconds=settings.jwt_expire_seconds,
    )

    # 7. Safe Browser Delivery: Tokens kept out of URL query strings
    if format == "json":
        response: Response = JSONResponse(
            content={
                "access_token": token,
                "token_type": "bearer",
                "expires_in": settings.jwt_expire_seconds,
            }
        )
    else:
        response = RedirectResponse(
            url=oauth_state.redirect_url,
            status_code=status.HTTP_302_FOUND,
        )

    # Clear one-time OAuth state cookie
    response.delete_cookie(
        key=settings.cookie_oauth_state_name,
        path="/api/v1/auth",
    )

    # Set secure HttpOnly session cookie
    response.set_cookie(
        key=settings.cookie_session_name,
        value=token,
        max_age=settings.jwt_expire_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )

    return response


@router.get("/me", response_model=AuthenticatedUserResponse)
async def get_current_user_profile(
    identity: CurrentIdentityDep,
    session: AsyncSessionDep,
) -> AuthenticatedUserResponse:
    """Retrieve profile and active workspace role for the current authenticated identity.
    
    Demonstrates zero-bypass integration: IdentityContext is cryptographically verified
    and backed by live database verification.
    """
    stmt = (
        select(UserModel)
        .options(selectinload(UserModel.external_identities))
        .where(
            UserModel.id == identity.user_id,
            UserModel.workspace_id == identity.workspace_id,
            UserModel.deleted_at.is_(None),
        )
    )
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User record not found",
        )

    ext_identities = [
        ExternalIdentityDTO(
            provider=ext.provider,
            provider_subject_id=ext.provider_subject_id,
            provider_email=ext.provider_email,
            provider_username=ext.provider_username,
            provider_display_name=ext.provider_display_name,
            is_primary=ext.is_primary,
        )
        for ext in user.external_identities
        if ext.deleted_at is None
    ]

    return AuthenticatedUserResponse(
        id=user.id,
        workspace_id=user.workspace_id,
        email=user.email,
        display_name=user.display_name,
        handle=user.handle,
        workspace_role=user.workspace_role,
        status=user.status,
        external_identities=ext_identities,
    )
