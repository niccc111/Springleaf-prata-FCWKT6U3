"""Remove authentication: drop the users table and the 'user' audit entity type.

The dispatcher console ships without accounts or a login step, so the ``users``
table and the ``user`` audit entity type have no remaining writers. Console
actions are attributed to a single fixed console-operator identity
(``app.api.deps.CONSOLE_OPERATOR_ID``), which keeps the audit trail intact.

The only ``user`` audit rows ever written were access denials, which no longer
have any meaning; they are removed so the tightened check constraint can be
applied. ``audit_log`` blocks DELETE through a rewrite rule, so the rule is
dropped for that one statement and recreated immediately afterwards.
"""

from __future__ import annotations

from alembic import op

revision = "0003_remove_authentication"
down_revision = "0002_audit_immutability"
branch_labels = None
depends_on = None

OLD_CHECK = "entity_type IN ('order', 'vehicle', 'route', 'alert', 'user')"
NEW_CHECK = "entity_type IN ('order', 'vehicle', 'route', 'alert')"
CONSTRAINT = "ck_audit_log_audit_entity_type"


def upgrade() -> None:
    # Access-denial rows are the only 'user' rows ever written; with no roles
    # left to deny they carry no meaning and would violate the new constraint.
    op.execute("DROP RULE IF EXISTS audit_no_delete ON audit_log")
    op.execute("DELETE FROM audit_log WHERE entity_type = 'user'")
    op.execute("CREATE RULE audit_no_delete AS ON DELETE TO audit_log DO INSTEAD NOTHING")

    op.execute(f"ALTER TABLE audit_log DROP CONSTRAINT {CONSTRAINT}")
    op.execute(f"ALTER TABLE audit_log ADD CONSTRAINT {CONSTRAINT} CHECK ({NEW_CHECK})")

    op.drop_table("users")


def downgrade() -> None:
    op.execute(f"ALTER TABLE audit_log DROP CONSTRAINT {CONSTRAINT}")
    op.execute(f"ALTER TABLE audit_log ADD CONSTRAINT {CONSTRAINT} CHECK ({OLD_CHECK})")

    op.execute(
        """
        CREATE TABLE users (
            user_id UUID NOT NULL,
            email TEXT NOT NULL,
            full_name TEXT,
            password_hash TEXT,
            role TEXT NOT NULL,
            active BOOLEAN NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_users PRIMARY KEY (user_id),
            CONSTRAINT uq_users_email UNIQUE (email),
            CONSTRAINT ck_users_users_role CHECK (role IN ('dispatcher', 'administrator'))
        )
        """
    )
