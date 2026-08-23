from __future__ import annotations

from opennosh_api.capacity import ProcessRole, load_capacity_manifest
from opennosh_api.runtime import ROLE_COMPOSITIONS
from opennosh_api.settings import get_settings


def run_reserved_worker(role: ProcessRole) -> int:
    settings = get_settings()
    manifest = load_capacity_manifest(settings.database_capacity_manifest_path)
    manifest.active_role_budget(role)
    composition = ROLE_COMPOSITIONS[role]
    raise RuntimeError(
        f"{role.value} is capacity-enabled but has no installed queue driver; "
        f"refusing to claim lanes {sorted(composition.lanes)}"
    )
