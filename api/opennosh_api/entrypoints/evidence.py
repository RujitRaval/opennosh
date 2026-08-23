from opennosh_api.capacity import ProcessRole
from opennosh_api.entrypoints._worker import run_reserved_worker


def main() -> int:
    return run_reserved_worker(ProcessRole.EVIDENCE)
