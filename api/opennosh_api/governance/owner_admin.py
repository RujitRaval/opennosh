"""Grant/revoke one pack owner's review authority using the migration role.

The normal web role cannot write grants. This command never creates users and
requires an existing active steward assignment for the exact account and pack.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from opennosh_api.governance.models import GovernanceOwnerAuthorization, GovernanceRoleAssignment


async def change_owner_authorization(
    database_url: str,
    *,
    actor_id: UUID,
    pack_id: str,
    authorized_by: UUID,
    reason: str,
    revoke: bool = False,
) -> dict[str, str]:
    if not pack_id or len(pack_id) > 160 or not reason.strip() or len(reason) > 1000:
        raise ValueError("A bounded pack and audit reason are required")
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                {"scope": f"opennosh.governance-pack:{pack_id}"},
            )
            now = datetime.now(UTC)
            existing = await session.scalar(
                select(GovernanceOwnerAuthorization).where(
                    GovernanceOwnerAuthorization.pack_id == pack_id,
                    GovernanceOwnerAuthorization.actor_id == actor_id,
                )
            )
            if revoke:
                if existing is None:
                    raise ValueError("Owner authorization does not exist")
                if existing.revoked_at is None:
                    existing.revoked_at = now
                    existing.revoked_by_actor_id = authorized_by
                    existing.revocation_reason = reason.strip()
            elif existing is not None:
                if existing.revoked_at is not None:
                    raise ValueError("Revoked owner authorization cannot be rewritten")
            else:
                role = await session.scalar(
                    select(GovernanceRoleAssignment.id).where(
                        GovernanceRoleAssignment.pack_id == pack_id,
                        GovernanceRoleAssignment.actor_id == actor_id,
                        GovernanceRoleAssignment.role == "steward",
                        GovernanceRoleAssignment.granted_at <= now,
                        (
                            GovernanceRoleAssignment.revoked_at.is_(None)
                            | (GovernanceRoleAssignment.revoked_at > now)
                        ),
                    )
                )
                if role is None:
                    raise ValueError("An existing active pack steward assignment is required")
                existing = GovernanceOwnerAuthorization(
                    pack_id=pack_id,
                    actor_id=actor_id,
                    role="owner",
                    granted_by_actor_id=authorized_by,
                    grant_reason=reason.strip(),
                    granted_at=now,
                )
                session.add(existing)
            await session.flush()
            return {
                "authorization_id": str(existing.id),
                "actor_id": str(actor_id),
                "pack_id": pack_id,
                "state": "revoked" if existing.revoked_at else "active",
            }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor-id", required=True, type=UUID)
    parser.add_argument("--pack-id", required=True)
    parser.add_argument("--authorized-by", required=True, type=UUID)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--revoke", action="store_true")
    args = parser.parse_args()
    database_url = os.environ.get("MIGRATION_DATABASE_URL")
    if not database_url:
        raise SystemExit("MIGRATION_DATABASE_URL is required; do not use the web role")
    result = asyncio.run(
        change_owner_authorization(
            database_url,
            actor_id=args.actor_id,
            pack_id=args.pack_id,
            authorized_by=args.authorized_by,
            reason=args.reason,
            revoke=args.revoke,
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
