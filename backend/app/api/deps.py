"""Request-scoped dependencies: database sessions and the acting operator.

The ROE console ships without authentication — there are no accounts, no
passwords, and no login step. Every request is therefore attributed to a single
well-known console operator so that the audit trail (Requirement 16) still
records *who* changed *what* and *when* with a stable identity.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

#: Stable identity recorded as ``audit_log.acting_user`` for console actions.
#: Fixed (rather than random) so audit history remains queryable across restarts.
CONSOLE_OPERATOR_ID = uuid.UUID("00000000-0000-0000-0000-00000000d15b")

#: Human-readable label for the same identity, surfaced in the audit log UI.
CONSOLE_OPERATOR_LABEL = "dispatcher console"

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_db(session: SessionDep) -> AsyncIterator[AsyncSession]:  # pragma: no cover
    yield session


async def get_actor() -> uuid.UUID:
    """Identity credited with the current operation in the audit trail."""
    return CONSOLE_OPERATOR_ID


#: Every endpoint is open; this only supplies the audit attribution.
ActorDep = Annotated[uuid.UUID, Depends(get_actor)]
