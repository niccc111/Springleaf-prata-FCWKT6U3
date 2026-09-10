"""Audit log immutability rules and least-privilege application role.

``audit_no_update`` / ``audit_no_delete`` rewrite rules make UPDATE and DELETE
against ``audit_log`` no-ops (defence in depth), and the ``roe_app`` role is
granted INSERT + SELECT only.

Task 2.2 — Requirements: 16.4 (Property 33)
"""

from __future__ import annotations

from alembic import op

revision = "0002_audit_immutability"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

APP_ROLE = "roe_app"


def upgrade() -> None:
    op.execute("CREATE RULE audit_no_update AS ON UPDATE TO audit_log DO INSTEAD NOTHING")
    op.execute("CREATE RULE audit_no_delete AS ON DELETE TO audit_log DO INSTEAD NOTHING")

    # A non-superuser application role with INSERT/SELECT-only access to the
    # audit trail. Services connect as this role in staging/production.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN;
            END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
    )
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON audit_log TO {APP_ROLE}")
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP RULE IF EXISTS audit_no_update ON audit_log")
    op.execute("DROP RULE IF EXISTS audit_no_delete ON audit_log")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}")
