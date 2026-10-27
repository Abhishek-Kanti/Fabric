"""create_external_identities

Revision ID: 31519ed25161
Revises: 9f60cf46a58a
Create Date: 2026-10-27 01:34:30.876593

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '31519ed25161'
down_revision: Union[str, Sequence[str], None] = '9f60cf46a58a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: Create external_identities, backfill legacy data, and deprecate flat user auth columns."""
    # 1. Create external_identities table
    op.create_table(
        'external_identities',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('workspace_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('provider', sa.String(length=50), nullable=False),
        sa.Column('provider_subject_id', sa.String(length=255), nullable=False),
        sa.Column('provider_email', sa.String(length=320), nullable=True),
        sa.Column('provider_username', sa.String(length=255), nullable=True),
        sa.Column('provider_display_name', sa.String(length=255), nullable=True),
        sa.Column('provider_tenant_id', sa.String(length=255), nullable=True),
        sa.Column('provider_metadata', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('is_primary', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('status', sa.Enum('active', 'suspended', name='external_identity_status_enum'), server_default='active', nullable=False),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_authenticated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_external_identities_workspace_id_workspaces'), ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['user_id', 'workspace_id'], ['users.id', 'users.workspace_id'], name='fk_external_identities_user', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_external_identities')),
    )

    # 3. Create indexes and unique constraints
    op.create_index(
        'uq_external_identities_provider_subject',
        'external_identities',
        ['workspace_id', 'provider', 'provider_subject_id'],
        unique=True,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )
    op.create_index(
        'uq_external_identities_primary_user',
        'external_identities',
        ['workspace_id', 'user_id'],
        unique=True,
        postgresql_where=sa.text('is_primary = TRUE AND deleted_at IS NULL'),
    )
    op.create_index(
        'idx_external_identities_user_lookup',
        'external_identities',
        ['workspace_id', 'user_id'],
        unique=False,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )
    op.create_index(
        'idx_external_identities_email_lookup',
        'external_identities',
        ['workspace_id', 'provider_email'],
        unique=False,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )

    # 4. Migrate legacy data from users to external_identities (where auth_provider_id is set)
    op.execute(
        """
        INSERT INTO external_identities (
            id,
            workspace_id,
            user_id,
            provider,
            provider_subject_id,
            provider_email,
            provider_display_name,
            provider_metadata,
            is_primary,
            status,
            verified_at,
            created_at,
            updated_at
        )
        SELECT 
            gen_random_uuid(),
            workspace_id,
            id,
            auth_provider,
            auth_provider_id,
            email,
            display_name,
            '{}'::jsonb,
            TRUE,
            'active',
            created_at,
            created_at,
            updated_at
        FROM users
        WHERE auth_provider_id IS NOT NULL;
        """
    )

    # 5. Drop legacy index and columns from users table
    op.drop_index('idx_users_auth_lookup', table_name='users', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_column('users', 'auth_provider_id')
    op.drop_column('users', 'auth_provider')


def downgrade() -> None:
    """Downgrade schema: Restore legacy user auth columns and reverse backfill primary identities."""
    # 1. Re-add legacy columns to users table
    op.add_column('users', sa.Column('auth_provider', sa.String(length=50), server_default='local', nullable=False))
    op.add_column('users', sa.Column('auth_provider_id', sa.String(length=255), nullable=True))

    # 2. Reverse backfill from active primary external identities
    op.execute(
        """
        UPDATE users u
        SET auth_provider = ei.provider,
            auth_provider_id = ei.provider_subject_id
        FROM external_identities ei
        WHERE ei.user_id = u.id AND ei.is_primary = TRUE AND ei.deleted_at IS NULL;
        """
    )

    # 3. Re-create legacy index on users table
    op.create_index(
        'idx_users_auth_lookup',
        'users',
        ['workspace_id', 'auth_provider', 'auth_provider_id'],
        unique=False,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )

    # 4. Drop external_identities indexes and table
    op.drop_index('idx_external_identities_email_lookup', table_name='external_identities', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('idx_external_identities_user_lookup', table_name='external_identities', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('uq_external_identities_primary_user', table_name='external_identities', postgresql_where=sa.text('is_primary = TRUE AND deleted_at IS NULL'))
    op.drop_index('uq_external_identities_provider_subject', table_name='external_identities', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_table('external_identities')

    # 5. Drop enum type
    sa.Enum(name='external_identity_status_enum').drop(op.get_bind(), checkfirst=True)
