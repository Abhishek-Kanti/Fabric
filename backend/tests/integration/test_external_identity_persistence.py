"""Integration tests for ExternalIdentity persistence, constraints, and migration.

Verifies:
- Multiple external identities per canonical User
- Provider subject uniqueness within a Workspace
- Cross-workspace identity reuse (tenant isolation)
- Primary identity constraint (at most one active primary per user)
- Soft-unlink lifecycle and re-link reuse
- Composite foreign key workspace consistency
- Cascade deletion when canonical user is deleted
- Legacy data migration and downgrade rollback semantics
"""

import os
import subprocess
from datetime import datetime, timezone
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.domain.enums import ExternalIdentityStatus, UserStatus, WorkspaceRole
from app.infrastructure.database.models import ExternalIdentityModel, UserModel, WorkspaceModel
from app.shared.utils.uuid import generate_uuid7


@pytest.mark.asyncio
async def test_multiple_identities_per_user(db_session: AsyncSession):
    """Verify that a canonical user can have multiple linked identities across different providers."""
    ws_id = generate_uuid7()
    workspace = WorkspaceModel(id=ws_id, name="Multi Identity Workspace", slug="multi-id-ws")
    db_session.add(workspace)
    await db_session.flush()

    user_id = generate_uuid7()
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="developer@example.com",
        display_name="Dev Example",
        handle="devexample",
    )
    db_session.add(user)
    await db_session.flush()

    # Link Google identity as primary
    google_id = generate_uuid7()
    google_identity = ExternalIdentityModel(
        id=google_id,
        workspace_id=ws_id,
        user_id=user_id,
        provider="google",
        provider_subject_id="google-sub-101",
        provider_email="developer@example.com",
        provider_username="dev.google",
        provider_display_name="Dev Example (Google)",
        provider_metadata={"hd": "example.com"},
        is_primary=True,
        status=ExternalIdentityStatus.ACTIVE,
    )
    db_session.add(google_identity)

    # Link GitHub identity as non-primary
    github_id = generate_uuid7()
    github_identity = ExternalIdentityModel(
        id=github_id,
        workspace_id=ws_id,
        user_id=user_id,
        provider="github",
        provider_subject_id="github-sub-202",
        provider_email="devexample@users.noreply.github.com",
        provider_username="devexample",
        provider_display_name="Dev Example (GitHub)",
        provider_metadata={"github_id": 123456},
        is_primary=False,
        status=ExternalIdentityStatus.ACTIVE,
    )
    db_session.add(github_identity)

    # Link Microsoft identity as non-primary
    ms_id = generate_uuid7()
    ms_identity = ExternalIdentityModel(
        id=ms_id,
        workspace_id=ws_id,
        user_id=user_id,
        provider="microsoft",
        provider_subject_id="ms-sub-303",
        provider_tenant_id="ms-tenant-404",
        provider_email="developer@enterprise.onmicrosoft.com",
        provider_username="developer@enterprise.onmicrosoft.com",
        provider_display_name="Dev Example (MS)",
        is_primary=False,
        status=ExternalIdentityStatus.ACTIVE,
    )
    db_session.add(ms_identity)
    await db_session.flush()

    # Query back and verify user relationship
    result = await db_session.execute(
        select(UserModel)
        .where(UserModel.id == user_id)
        .options(selectinload(UserModel.external_identities))
    )
    loaded_user = result.scalar_one()
    identities = loaded_user.external_identities
    assert len(identities) == 3

    providers = {i.provider: i for i in identities}
    assert "google" in providers
    assert "github" in providers
    assert "microsoft" in providers

    assert providers["google"].is_primary is True
    assert providers["google"].provider_subject_id == "google-sub-101"
    assert providers["github"].is_primary is False
    assert providers["microsoft"].provider_tenant_id == "ms-tenant-404"


@pytest.mark.asyncio
async def test_single_primary_identity_constraint(db_session: AsyncSession):
    """Verify that partial unique index enforces at most one primary identity per user."""
    ws_id = generate_uuid7()
    workspace = WorkspaceModel(id=ws_id, name="Primary Constraint WS", slug="primary-ws")
    db_session.add(workspace)

    user_id = generate_uuid7()
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="alice@primary.ws",
        display_name="Alice",
        handle="alice",
    )
    db_session.add(user)
    await db_session.flush()

    # First primary identity
    id_1 = ExternalIdentityModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        user_id=user_id,
        provider="google",
        provider_subject_id="sub-primary-1",
        is_primary=True,
    )
    db_session.add(id_1)
    await db_session.flush()

    # Attempting to add a second primary identity for the same user must fail
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            id_2 = ExternalIdentityModel(
                id=generate_uuid7(),
                workspace_id=ws_id,
                user_id=user_id,
                provider="github",
                provider_subject_id="sub-primary-2",
                is_primary=True,
            )
            db_session.add(id_2)
            await db_session.flush()

    # Soft-deleting the first primary identity allows another primary identity
    id_1.deleted_at = datetime.now(timezone.utc)
    await db_session.flush()

    id_3 = ExternalIdentityModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        user_id=user_id,
        provider="github",
        provider_subject_id="sub-primary-3",
        is_primary=True,
    )
    db_session.add(id_3)
    await db_session.flush()
    assert id_3.is_primary is True


@pytest.mark.asyncio
async def test_provider_subject_uniqueness_within_workspace(db_session: AsyncSession):
    """Verify that the same external provider subject cannot be bound to two users in the same workspace."""
    ws_id = generate_uuid7()
    workspace = WorkspaceModel(id=ws_id, name="Subject Uniqueness WS", slug="subject-ws")
    db_session.add(workspace)

    user_1_id = generate_uuid7()
    user_1 = UserModel(
        id=user_1_id,
        workspace_id=ws_id,
        email="user1@subject.ws",
        display_name="User 1",
        handle="user1",
    )
    db_session.add(user_1)

    user_2_id = generate_uuid7()
    user_2 = UserModel(
        id=user_2_id,
        workspace_id=ws_id,
        email="user2@subject.ws",
        display_name="User 2",
        handle="user2",
    )
    db_session.add(user_2)
    await db_session.flush()

    # Bind google sub-shared to user 1
    id_1 = ExternalIdentityModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        user_id=user_1_id,
        provider="google",
        provider_subject_id="sub-shared-123",
        is_primary=True,
    )
    db_session.add(id_1)
    await db_session.flush()

    # Attempting to bind same provider & subject to user 2 must fail
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            id_duplicate = ExternalIdentityModel(
                id=generate_uuid7(),
                workspace_id=ws_id,
                user_id=user_2_id,
                provider="google",
                provider_subject_id="sub-shared-123",
                is_primary=True,
            )
            db_session.add(id_duplicate)
            await db_session.flush()

    # Different provider with same subject_id works fine
    id_diff_provider = ExternalIdentityModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        user_id=user_2_id,
        provider="github",
        provider_subject_id="sub-shared-123",
        is_primary=True,
    )
    db_session.add(id_diff_provider)
    await db_session.flush()


@pytest.mark.asyncio
async def test_cross_workspace_identity_reuse(db_session: AsyncSession):
    """Verify that tenant isolation allows identical external provider subjects across different workspaces."""
    # Workspace A
    ws_a_id = generate_uuid7()
    ws_a = WorkspaceModel(id=ws_a_id, name="Tenant A", slug="tenant-a")
    db_session.add(ws_a)

    user_a_id = generate_uuid7()
    user_a = UserModel(
        id=user_a_id,
        workspace_id=ws_a_id,
        email="dev@tenanta.com",
        display_name="Dev A",
        handle="deva",
    )
    db_session.add(user_a)

    # Workspace B
    ws_b_id = generate_uuid7()
    ws_b = WorkspaceModel(id=ws_b_id, name="Tenant B", slug="tenant-b")
    db_session.add(ws_b)

    user_b_id = generate_uuid7()
    user_b = UserModel(
        id=user_b_id,
        workspace_id=ws_b_id,
        email="dev@tenantb.com",
        display_name="Dev B",
        handle="devb",
    )
    db_session.add(user_b)
    await db_session.flush()

    # Both users bind the same external GitHub account in their respective workspaces
    id_a = ExternalIdentityModel(
        id=generate_uuid7(),
        workspace_id=ws_a_id,
        user_id=user_a_id,
        provider="github",
        provider_subject_id="gh-shared-developer",
        is_primary=True,
    )
    db_session.add(id_a)

    id_b = ExternalIdentityModel(
        id=generate_uuid7(),
        workspace_id=ws_b_id,
        user_id=user_b_id,
        provider="github",
        provider_subject_id="gh-shared-developer",
        is_primary=True,
    )
    db_session.add(id_b)
    await db_session.flush()

    # Query back and verify both persisted cleanly
    res_a = await db_session.execute(select(ExternalIdentityModel).where(ExternalIdentityModel.id == id_a.id))
    assert res_a.scalar_one().workspace_id == ws_a_id

    res_b = await db_session.execute(select(ExternalIdentityModel).where(ExternalIdentityModel.id == id_b.id))
    assert res_b.scalar_one().workspace_id == ws_b_id


@pytest.mark.asyncio
async def test_soft_unlink_and_relink(db_session: AsyncSession):
    """Verify that soft-deleting an identity allows re-linking the same provider subject ID."""
    ws_id = generate_uuid7()
    workspace = WorkspaceModel(id=ws_id, name="Unlink WS", slug="unlink-ws")
    db_session.add(workspace)

    user_id = generate_uuid7()
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="bob@unlink.ws",
        display_name="Bob",
        handle="bob",
    )
    db_session.add(user)
    await db_session.flush()

    # Link initial identity
    orig_id = ExternalIdentityModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        user_id=user_id,
        provider="google",
        provider_subject_id="google-relink-sub",
        is_primary=True,
    )
    db_session.add(orig_id)
    await db_session.flush()

    # Soft-delete the identity (soft unlink)
    orig_id.deleted_at = datetime.now(timezone.utc)
    orig_id.is_primary = False
    await db_session.flush()

    # Re-link same provider subject ID
    new_id = ExternalIdentityModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        user_id=user_id,
        provider="google",
        provider_subject_id="google-relink-sub",
        is_primary=True,
    )
    db_session.add(new_id)
    await db_session.flush()

    # Verify both records exist (one soft-deleted, one active)
    result = await db_session.execute(
        select(ExternalIdentityModel)
        .where(
            ExternalIdentityModel.workspace_id == ws_id,
            ExternalIdentityModel.provider == "google",
            ExternalIdentityModel.provider_subject_id == "google-relink-sub",
        )
    )
    records = result.scalars().all()
    assert len(records) == 2
    active_records = [r for r in records if r.deleted_at is None]
    assert len(active_records) == 1
    assert active_records[0].id == new_id.id


@pytest.mark.asyncio
async def test_workspace_isolation_composite_fk_rejection(db_session: AsyncSession):
    """Verify that composite foreign key blocks attaching an identity to a user in a mismatched workspace."""
    ws_a_id = generate_uuid7()
    ws_a = WorkspaceModel(id=ws_a_id, name="WS Isolation A", slug="ws-iso-a")
    db_session.add(ws_a)

    ws_b_id = generate_uuid7()
    ws_b = WorkspaceModel(id=ws_b_id, name="WS Isolation B", slug="ws-iso-b")
    db_session.add(ws_b)

    user_a_id = generate_uuid7()
    user_a = UserModel(
        id=user_a_id,
        workspace_id=ws_a_id,
        email="usera@iso.com",
        display_name="User A",
        handle="usera-iso",
    )
    db_session.add(user_a)
    await db_session.flush()

    # Attempt to insert external identity with workspace_id = WS B, but user_id = User A (from WS A)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            mismatched_identity = ExternalIdentityModel(
                id=generate_uuid7(),
                workspace_id=ws_b_id,  # Mismatched!
                user_id=user_a_id,      # Belongs to WS A!
                provider="google",
                provider_subject_id="google-mismatch-1",
            )
            db_session.add(mismatched_identity)
            await db_session.flush()


@pytest.mark.asyncio
async def test_cascade_deletion_on_user_delete(db_session: AsyncSession):
    """Verify that deleting a canonical user cascades and physically deletes all associated external identities."""
    ws_id = generate_uuid7()
    workspace = WorkspaceModel(id=ws_id, name="Cascade WS", slug="cascade-ws")
    db_session.add(workspace)

    user_id = generate_uuid7()
    user = UserModel(
        id=user_id,
        workspace_id=ws_id,
        email="carol@cascade.ws",
        display_name="Carol",
        handle="carol",
    )
    db_session.add(user)
    await db_session.flush()

    id_1 = ExternalIdentityModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        user_id=user_id,
        provider="google",
        provider_subject_id="sub-cascade-1",
        is_primary=True,
    )
    id_2 = ExternalIdentityModel(
        id=generate_uuid7(),
        workspace_id=ws_id,
        user_id=user_id,
        provider="github",
        provider_subject_id="sub-cascade-2",
        is_primary=False,
    )
    db_session.add_all([id_1, id_2])
    await db_session.flush()

    # Physically delete user
    await db_session.delete(user)
    await db_session.flush()

    # Query external identities for deleted user
    result = await db_session.execute(
        select(ExternalIdentityModel).where(ExternalIdentityModel.user_id == user_id)
    )
    remaining_identities = result.scalars().all()
    assert len(remaining_identities) == 0


@pytest.mark.asyncio
async def test_legacy_data_migration_and_rollback():
    """Verify Alembic migration backfills legacy user columns into external_identities and restores them on downgrade."""
    settings = get_settings()
    test_db_url = settings.async_test_database_url
    env = os.environ.copy()
    env["DATABASE_URL"] = test_db_url

    # 1. Downgrade to 9f60cf46a58a
    res = subprocess.run([".venv\\Scripts\\alembic", "downgrade", "9f60cf46a58a"], env=env, capture_output=True, text=True)
    assert res.returncode == 0, f"Downgrade to 9f60cf46a58a failed: {res.stderr}"

    engine = create_async_engine(test_db_url)
    legacy_ws_id = generate_uuid7()
    legacy_user_id = generate_uuid7()

    try:
        # 2. Insert legacy user in pre-migration schema
        async with engine.begin() as conn:
            # Confirm legacy columns exist in users table
            res = await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'users' AND column_name IN ('auth_provider', 'auth_provider_id');"
            ))
            cols = {r[0] for r in res.fetchall()}
            assert cols == {"auth_provider", "auth_provider_id"}

            await conn.execute(text(f"""
                INSERT INTO workspaces (id, name, slug)
                VALUES ('{legacy_ws_id}', 'Legacy Test WS', 'legacy-test-ws')
                ON CONFLICT (id) DO NOTHING;
            """))
            await conn.execute(text(f"""
                INSERT INTO users (id, workspace_id, email, display_name, handle, auth_provider, auth_provider_id)
                VALUES (
                    '{legacy_user_id}',
                    '{legacy_ws_id}',
                    'legacy.migrated@test.com',
                    'Legacy Migrated User',
                    'legacymigrated',
                    'google',
                    'google-legacy-sub-777'
                );
            """))

        # 3. Upgrade to head (31519ed25161)
        res = subprocess.run([".venv\\Scripts\\alembic", "upgrade", "head"], env=env, capture_output=True, text=True)
        assert res.returncode == 0, f"Upgrade to head failed: {res.stderr}"

        # 4. Verify backfilled record in external_identities
        async with engine.begin() as conn:
            res = await conn.execute(text(f"""
                SELECT id, workspace_id, user_id, provider, provider_subject_id, provider_email, is_primary, status
                FROM external_identities
                WHERE user_id = '{legacy_user_id}';
            """))
            migrated_row = res.fetchone()
            assert migrated_row is not None, "Legacy auth data was not backfilled into external_identities!"
            mapping = migrated_row._mapping
            assert str(mapping["workspace_id"]) == str(legacy_ws_id)
            assert mapping["provider"] == "google"
            assert mapping["provider_subject_id"] == "google-legacy-sub-777"
            assert mapping["provider_email"] == "legacy.migrated@test.com"
            assert mapping["is_primary"] is True
            assert mapping["status"] == "active"

            # Check that legacy columns have been removed from users table
            res = await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'users' AND column_name IN ('auth_provider', 'auth_provider_id');"
            ))
            remaining_cols = res.fetchall()
            assert len(remaining_cols) == 0, f"Legacy columns still exist in users table: {remaining_cols}"

        # 5. Downgrade back to 9f60cf46a58a to verify reverse backfill
        res = subprocess.run([".venv\\Scripts\\alembic", "downgrade", "9f60cf46a58a"], env=env, capture_output=True, text=True)
        assert res.returncode == 0, f"Downgrade rollback failed: {res.stderr}"

        async with engine.begin() as conn:
            res = await conn.execute(text(f"""
                SELECT auth_provider, auth_provider_id
                FROM users
                WHERE id = '{legacy_user_id}';
            """))
            restored_user = res.fetchone()
            assert restored_user is not None
            assert restored_user._mapping["auth_provider"] == "google"
            assert restored_user._mapping["auth_provider_id"] == "google-legacy-sub-777"

            # Cleanup test data in downgraded state
            await conn.execute(text(f"DELETE FROM users WHERE id = '{legacy_user_id}';"))
            await conn.execute(text(f"DELETE FROM workspaces WHERE id = '{legacy_ws_id}';"))

    finally:
        # 6. Ensure test database is returned to head
        res = subprocess.run([".venv\\Scripts\\alembic", "upgrade", "head"], env=env, capture_output=True, text=True)
        assert res.returncode == 0, f"Restoring head failed: {res.stderr}"
        await engine.dispose()
