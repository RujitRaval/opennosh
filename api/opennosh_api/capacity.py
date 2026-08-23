from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


class ProcessRole(StrEnum):
    WEB = "web"
    PUBLICATION = "publication"
    EVIDENCE = "evidence"
    PROJECTION = "projection"
    RECONCILER = "reconciler"
    SCHEDULER = "scheduler"


class JobRole(StrEnum):
    MIGRATION = "migration"
    ADMINISTRATION = "administration"


class ConnectionBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pool_size: PositiveInt
    max_overflow: Literal[0]
    acquisition_timeout_ms: PositiveInt
    statement_timeout_ms: PositiveInt
    max_in_flight_database_sections: PositiveInt

    @model_validator(mode="after")
    def validate_in_flight_limit(self) -> Self:
        if self.max_in_flight_database_sections > self.pool_size:
            raise ValueError("Database sections cannot exceed the role pool size")
        return self


class RoleBudget(ConnectionBudget):
    replicas: Annotated[int, Field(ge=0)]
    worker_concurrency: PositiveInt

    @model_validator(mode="after")
    def validate_worker_concurrency(self) -> Self:
        if self.max_in_flight_database_sections > self.worker_concurrency:
            raise ValueError("Database sections cannot exceed worker concurrency")
        return self

    @property
    def allocated_connections(self) -> int:
        return self.replicas * self.pool_size


class ReservedHeadroom(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    migrations: PositiveInt
    administration: PositiveInt
    monitoring: PositiveInt
    recovery: PositiveInt
    failover: PositiveInt

    @property
    def total(self) -> int:
        return sum(
            (
                self.migrations,
                self.administration,
                self.monitoring,
                self.recovery,
                self.failover,
            )
        )


class CapacityManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"]
    manifest_version: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}(?:\.\d+)?$")
    deployment_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    postgresql_connection_ceiling: PositiveInt
    reserved_headroom: ReservedHeadroom
    roles: dict[ProcessRole, RoleBudget]
    jobs: dict[JobRole, ConnectionBudget]

    @model_validator(mode="after")
    def validate_global_capacity(self) -> Self:
        expected_roles = set(ProcessRole)
        if set(self.roles) != expected_roles:
            missing = sorted(role.value for role in expected_roles - set(self.roles))
            extra = sorted(str(role) for role in set(self.roles) - expected_roles)
            raise ValueError(f"Role budgets must be complete; missing={missing}, extra={extra}")
        if set(self.jobs) != set(JobRole):
            raise ValueError("Migration and administration job budgets are required")
        if self.jobs[JobRole.MIGRATION].pool_size > self.reserved_headroom.migrations:
            raise ValueError("Migration pool exceeds reserved migration headroom")
        if self.jobs[JobRole.ADMINISTRATION].pool_size > self.reserved_headroom.administration:
            raise ValueError("Administration pool exceeds reserved administration headroom")
        if self.total_committed_connections > self.postgresql_connection_ceiling:
            raise ValueError(
                "Configured role pools plus reserved headroom exceed the PostgreSQL ceiling"
            )
        return self

    @property
    def application_connections(self) -> int:
        return sum(budget.allocated_connections for budget in self.roles.values())

    @property
    def total_committed_connections(self) -> int:
        return self.application_connections + self.reserved_headroom.total

    @property
    def uncommitted_connections(self) -> int:
        return self.postgresql_connection_ceiling - self.total_committed_connections

    def active_role_budget(self, role: ProcessRole) -> RoleBudget:
        budget = self.roles[role]
        if budget.replicas < 1:
            raise ValueError(
                f"Process role {role.value!r} has no replica allocation in this manifest"
            )
        return budget


def default_manifest_path() -> Path:
    packaged = Path(str(resources.files("opennosh_api").joinpath("database-capacity.v1.json")))
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[2] / "config/database-capacity.v1.json"


def load_capacity_manifest(path: str | Path | None = None) -> CapacityManifest:
    resolved = Path(path) if path is not None else default_manifest_path()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    return CapacityManifest.model_validate(payload)


def manifest_digest(manifest: CapacityManifest) -> str:
    canonical = json.dumps(
        manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def preflight_report(manifest: CapacityManifest) -> dict[str, object]:
    return {
        "status": "valid",
        "schema_version": manifest.schema_version,
        "manifest_version": manifest.manifest_version,
        "manifest_sha256": manifest_digest(manifest),
        "deployment_id": manifest.deployment_id,
        "postgresql_connection_ceiling": manifest.postgresql_connection_ceiling,
        "application_connections": manifest.application_connections,
        "reserved_headroom": manifest.reserved_headroom.total,
        "total_committed_connections": manifest.total_committed_connections,
        "uncommitted_connections": manifest.uncommitted_connections,
        "roles": {
            role.value: {
                "replicas": budget.replicas,
                "pool_size": budget.pool_size,
                "allocated_connections": budget.allocated_connections,
                "max_overflow": budget.max_overflow,
                "acquisition_timeout_ms": budget.acquisition_timeout_ms,
                "statement_timeout_ms": budget.statement_timeout_ms,
                "worker_concurrency": budget.worker_concurrency,
                "max_in_flight_database_sections": (budget.max_in_flight_database_sections),
            }
            for role, budget in manifest.roles.items()
        },
    }


def parse_deployed_role_counts(values: list[str]) -> dict[ProcessRole, int]:
    deployed: dict[ProcessRole, int] = {}
    for value in values:
        role_name, separator, count_text = value.partition("=")
        if not separator:
            raise ValueError(f"Deployed role {value!r} must use the ROLE=REPLICAS format")
        role = ProcessRole(role_name)
        if role in deployed:
            raise ValueError(f"Deployed role {role.value!r} is duplicated")
        try:
            count = int(count_text)
        except ValueError as error:
            raise ValueError(
                f"Deployed role {role.value!r} replica count must be an integer"
            ) from error
        if count < 0:
            raise ValueError(f"Deployed role {role.value!r} replica count cannot be negative")
        deployed[role] = count
    return deployed


def validate_deployed_role_counts(
    manifest: CapacityManifest, deployed: dict[ProcessRole, int]
) -> None:
    if set(deployed) != set(ProcessRole):
        missing = sorted(role.value for role in set(ProcessRole) - set(deployed))
        raise ValueError(f"Deployment topology must declare every process role; missing={missing}")
    mismatches = {
        role.value: {"deployed": deployed[role], "declared": budget.replicas}
        for role, budget in manifest.roles.items()
        if deployed[role] != budget.replicas
    }
    if mismatches:
        raise ValueError(
            "Deployment replica counts do not match the capacity manifest: "
            f"{json.dumps(mismatches, sort_keys=True)}"
        )


async def live_postgresql_connection_ceiling(database_url: str) -> int:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(text("SHOW max_connections"))
    finally:
        await engine.dispose()
    if value is None:
        raise ValueError("PostgreSQL did not report max_connections")
    return int(value)


def validate_live_connection_ceiling(manifest: CapacityManifest, live_ceiling: int) -> None:
    if live_ceiling != manifest.postgresql_connection_ceiling:
        raise ValueError(
            "Live PostgreSQL max_connections does not match the capacity manifest: "
            f"live={live_ceiling}, declared={manifest.postgresql_connection_ceiling}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the deployment-level PostgreSQL connection budget."
    )
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument(
        "--deployed-role",
        action="append",
        default=[],
        metavar="ROLE=REPLICAS",
        help="repeat for every process role to verify deployment topology",
    )
    parser.add_argument(
        "--require-deployment-topology",
        action="store_true",
        help="require complete --deployed-role values",
    )
    parser.add_argument(
        "--require-live-database",
        action="store_true",
        help="require DATABASE_CAPACITY_URL and verify live max_connections",
    )
    arguments = parser.parse_args(argv)
    try:
        manifest = load_capacity_manifest(arguments.manifest)
        if arguments.require_deployment_topology and not arguments.deployed_role:
            raise ValueError("Deployment topology is required")
        if arguments.deployed_role:
            validate_deployed_role_counts(
                manifest, parse_deployed_role_counts(arguments.deployed_role)
            )
        database_url = os.getenv("DATABASE_CAPACITY_URL")
        if arguments.require_live_database and not database_url:
            raise ValueError("DATABASE_CAPACITY_URL is required for live capacity validation")
        if database_url:
            live_ceiling = asyncio.run(live_postgresql_connection_ceiling(database_url))
            validate_live_connection_ceiling(manifest, live_ceiling)
    except (OSError, json.JSONDecodeError, SQLAlchemyError, ValueError) as error:
        print(json.dumps({"status": "invalid", "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps(preflight_report(manifest), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
