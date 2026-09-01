from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from types import SimpleNamespace

import opennosh_api.cli as cli
import opennosh_api.importers.wger as standalone_wger
import pytest


def _claims_readiness_arguments() -> argparse.Namespace:
    return cli.build_parser().parse_args(["commons", "production-claims-readiness", "--json"])


def test_production_claims_readiness_command_parses() -> None:
    arguments = _claims_readiness_arguments()

    assert arguments.commons_command == "production-claims-readiness"
    assert arguments.json is True


def test_production_claims_readiness_reports_ready_digest(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def collect(_settings: object) -> dict[str, object]:
        return {"schema_version": "1.0", "status": "ready", "readiness_sha256": "a" * 64}

    monkeypatch.setattr(cli, "get_settings", lambda: object())
    monkeypatch.setattr(cli, "collect_production_claims_readiness", collect)

    assert cli.run_commons_command(_claims_readiness_arguments()) == 0
    output = capsys.readouterr()
    assert '"readiness_sha256":' in output.out
    assert output.err == ""


def test_production_claims_readiness_blocks_without_leaking_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def reject(_settings: object) -> dict[str, object]:
        raise ValueError("sensitive database configuration")

    monkeypatch.setattr(cli, "get_settings", lambda: object())
    monkeypatch.setattr(cli, "collect_production_claims_readiness", reject)

    assert cli.run_commons_command(_claims_readiness_arguments()) == 5
    output = capsys.readouterr()
    assert "configuration or database probe failed" in output.err
    assert "sensitive" not in output.err
    assert output.out == ""


def _natural_proof_arguments() -> argparse.Namespace:
    return cli.build_parser().parse_args(
        ["commons", "natural-publication-proof", "--request-file", "proof.json", "--json"]
    )


def test_natural_publication_proof_command_parses() -> None:
    arguments = _natural_proof_arguments()

    assert arguments.commons_command == "natural-publication-proof"
    assert arguments.request_file == Path("proof.json")
    assert arguments.json is True


def test_natural_publication_proof_reports_verified_digest(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def collect(_settings: object, request: object) -> dict[str, object]:
        assert request is sentinel
        return {"schema_version": "1.0", "status": "verified", "proof_sha256": "a" * 64}

    sentinel = object()
    monkeypatch.setattr(cli, "load_natural_publication_proof_request", lambda _path: sentinel)
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    monkeypatch.setattr(cli, "collect_natural_publication_proof", collect)

    assert cli.run_commons_command(_natural_proof_arguments()) == 0
    output = capsys.readouterr()
    assert '"proof_sha256":' in output.out
    assert output.err == ""


def test_natural_publication_proof_rejects_request_without_leaking_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def reject(_path: object) -> object:
        raise cli.NaturalPublicationProofRequestError("private request content")

    monkeypatch.setattr(cli, "load_natural_publication_proof_request", reject)

    assert cli.run_commons_command(_natural_proof_arguments()) == 4
    output = capsys.readouterr()
    assert "request file is invalid" in output.err
    assert "private" not in output.err
    assert output.out == ""


def test_natural_publication_proof_reports_blocked_and_probe_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sentinel = object()
    monkeypatch.setattr(cli, "load_natural_publication_proof_request", lambda _path: sentinel)
    monkeypatch.setattr(cli, "get_settings", lambda: object())

    async def blocked(_settings: object, _request: object) -> dict[str, object]:
        return {"status": "blocked", "failures": ["safe_code"]}

    monkeypatch.setattr(cli, "collect_natural_publication_proof", blocked)
    assert cli.run_commons_command(_natural_proof_arguments()) == 2
    assert '"status": "blocked"' in capsys.readouterr().out

    async def failed(_settings: object, _request: object) -> dict[str, object]:
        raise ValueError("private failure")

    monkeypatch.setattr(cli, "collect_natural_publication_proof", failed)
    assert cli.run_commons_command(_natural_proof_arguments()) == 5
    output = capsys.readouterr()
    assert "read-only probe failed" in output.err
    assert "private failure" not in output.err


def test_natural_publication_readiness_command_and_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = cli.build_parser().parse_args(
        [
            "commons",
            "natural-publication-readiness",
            "--blueprint",
            "render.yaml",
            "--json",
        ]
    )

    async def collect(
        _settings: object,
        *,
        blueprint_path: Path,
    ) -> dict[str, object]:
        assert blueprint_path == Path("render.yaml")
        return {"schema_version": "1.0", "status": "ready", "readiness_sha256": "b" * 64}

    monkeypatch.setattr(cli, "get_settings", lambda: object())
    monkeypatch.setattr(cli, "collect_natural_publication_readiness", collect)

    assert cli.run_commons_command(arguments) == 0
    output = capsys.readouterr()
    assert '"readiness_sha256":' in output.out
    assert output.err == ""


def test_natural_publication_readiness_reports_blocked_and_probe_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = cli.build_parser().parse_args(
        ["commons", "natural-publication-readiness", "--json"]
    )
    monkeypatch.setattr(cli, "get_settings", lambda: object())

    async def blocked(_settings: object, *, blueprint_path: Path) -> dict[str, object]:
        assert blueprint_path == Path("render.yaml")
        return {"status": "blocked", "failures": ["safe_code"]}

    monkeypatch.setattr(cli, "collect_natural_publication_readiness", blocked)
    assert cli.run_commons_command(arguments) == 2
    assert '"status": "blocked"' in capsys.readouterr().out

    async def failed(_settings: object, *, blueprint_path: Path) -> dict[str, object]:
        raise OSError("private path")

    monkeypatch.setattr(cli, "collect_natural_publication_readiness", failed)
    assert cli.run_commons_command(arguments) == 5
    output = capsys.readouterr()
    assert "read-only probe failed" in output.err
    assert "private path" not in output.err


def test_exercise_import_command_parses_offline_paths() -> None:
    arguments = cli.build_parser().parse_args(
        ["exercises", "import-wger", "one.json", "two.json", "--batch-size", "25", "--json"]
    )

    assert arguments.command == "exercises"
    assert arguments.exercise_command == "import-wger"
    assert arguments.paths == [Path("one.json"), Path("two.json")]
    assert arguments.batch_size == 25
    assert arguments.json is True


def test_commons_build_command_requires_explicit_release_inputs() -> None:
    arguments = cli.build_parser().parse_args(
        [
            "commons",
            "build-starter-release",
            "--packs-root",
            "packs",
            "--output",
            "/secure/release",
            "--release-version",
            "0.56.0.0",
            "--published-at",
            "2026-08-27T02:00:00+00:00",
            "--source-commit",
            "a" * 40,
            "--manifest-key-id",
            "manifest-production",
            "--manifest-private-key",
            "/secure/manifest.key",
            "--receipt-key-id",
            "receipt-production",
            "--receipt-private-key",
            "/secure/receipt.key",
            "--decision-reference",
            "https://github.com/RujitRaval/opennosh/issues/97",
            "--approving-actor",
            "github:RujitRaval",
            "--json",
        ]
    )

    assert arguments.command == "commons"
    assert arguments.commons_command == "build-starter-release"
    assert arguments.release_version == "0.56.0.0"
    assert arguments.packs_root == Path("packs")
    assert arguments.json is True


def test_commons_publish_command_requires_the_reviewed_wrangler_path() -> None:
    arguments = cli.build_parser().parse_args(
        [
            "commons",
            "publish-starter-release",
            "/secure/release",
            "--inventory-sha256",
            "a" * 64,
            "--bucket",
            "opennosh-public-commons",
            "--origin-url",
            "https://commons-artifacts.opennosh.org",
            "--wrangler",
            "/approved/wrangler",
        ]
    )

    assert arguments.commons_command == "publish-starter-release"
    assert arguments.wrangler == Path("/approved/wrangler")


def test_commons_warm_command_bounds_operator_concurrency() -> None:
    arguments = cli.build_parser().parse_args(
        [
            "commons",
            "warm-live-release",
            "/secure/release",
            "--inventory-sha256",
            "a" * 64,
            "--api-origin",
            "https://opennosh.org",
            "--concurrency",
            "4",
        ]
    )

    assert arguments.commons_command == "warm-live-release"
    assert arguments.concurrency == 4


def test_first_contribution_commands_require_explicit_inputs() -> None:
    prepare = cli.build_parser().parse_args(
        [
            "commons",
            "prepare-usda-first-contribution",
            "--source-json",
            "usda.json",
            "--output",
            "review-package.json",
            "--json",
        ]
    )
    commit = cli.build_parser().parse_args(
        [
            "commons",
            "commit-usda-first-contribution",
            "--package",
            "review-package.json",
            "--steward-actor-id",
            "11111111-1111-4111-8111-111111111111",
            "--expected-base-commit",
            "a" * 40,
            "--reason",
            "Reviewed pinned USDA record.",
            "--bootstrap-steward",
            "--json",
        ]
    )

    assert prepare.source_json == Path("usda.json")
    assert prepare.output == Path("review-package.json")
    assert commit.steward_actor_id == __import__("uuid").UUID(
        "11111111-1111-4111-8111-111111111111"
    )
    assert commit.bootstrap_steward is True


def _publication_resubmission_arguments() -> argparse.Namespace:
    return cli.build_parser().parse_args(
        [
            "commons",
            "resubmit-publication",
            "--prior-publication-intent-id",
            "11111111-1111-4111-8111-111111111111",
            "--steward-actor-id",
            "22222222-2222-4222-8222-222222222222",
            "--expected-base-commit",
            "b" * 40,
            "--reason",
            "Retry unchanged reviewed material from fresh main.",
            "--json",
        ]
    )


def test_publication_resubmission_requires_exact_terminal_intent_and_fresh_base() -> None:
    arguments = _publication_resubmission_arguments()

    assert str(arguments.prior_publication_intent_id) == ("11111111-1111-4111-8111-111111111111")
    assert str(arguments.steward_actor_id) == "22222222-2222-4222-8222-222222222222"
    assert arguments.expected_base_commit == "b" * 40


def test_publication_resubmission_wires_database_queue_and_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Engine:
        disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def begin(self) -> Session:
            return self

    engine = Engine()
    session = Session()
    settings = SimpleNamespace(
        database_capacity_manifest_path="capacity.json",
        process_database_url=lambda role: f"postgresql://operator/{role.value}",
    )
    decided_at = __import__("datetime").datetime(2026, 8, 29, tzinfo=__import__("datetime").UTC)

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "build_administration_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(cli, "async_sessionmaker", lambda *_args, **_kwargs: lambda: session)
    monkeypatch.setattr(cli, "PgQueuerJobQueue", lambda: "queue")

    async def resubmitted(
        received_session: object,
        queue: object,
        command: cli.ResubmitPublication,
        **_options: object,
    ) -> tuple[SimpleNamespace, SimpleNamespace]:
        assert received_session is session
        assert queue == "queue"
        assert command.expected_base_commit == "b" * 40
        return (
            SimpleNamespace(
                id="decision-2",
                expected_base_commit=command.expected_base_commit,
                decided_at=decided_at,
            ),
            SimpleNamespace(id="publication-2", state="pending"),
        )

    monkeypatch.setattr(cli, "resubmit_publication", resubmitted)

    result = asyncio.run(cli._resubmit_publication(_publication_resubmission_arguments()))

    assert result["publication_intent_id"] == "publication-2"
    assert result["governance_decision_id"] == "decision-2"
    assert result["state"] == "pending"
    assert engine.disposed


@pytest.mark.parametrize("as_json", [False, True])
def test_publication_resubmission_dispatches_and_reports_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    as_json: bool,
) -> None:
    async def resubmitted(_arguments: argparse.Namespace) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "prior_publication_intent_id": "publication-1",
            "publication_intent_id": "publication-2",
        }

    arguments = _publication_resubmission_arguments()
    arguments.json = as_json
    monkeypatch.setattr(cli, "_resubmit_publication", resubmitted)

    assert cli.run_commons_command(arguments) == 0
    output = capsys.readouterr()
    assert "publication-2" in output.out
    assert ('"schema_version": "1.0"' in output.out) is as_json
    assert output.err == ""


@pytest.mark.parametrize(
    ("error", "exit_code", "message"),
    [
        (cli.GovernanceDecisionError("steward_role_not_active"), 3, "authority failed"),
        (cli.GovernanceDecisionError("publication_intervened"), 4, "conflict"),
        (
            cli.ValidationError.from_exception_data(
                "operator settings",
                [{"type": "missing", "loc": ("value",), "input": {}}],
            ),
            5,
            "configuration is invalid",
        ),
        (ValueError("secret database detail"), 5, "operator operation failed"),
    ],
)
def test_publication_resubmission_maps_redacted_exit_contracts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    exit_code: int,
    message: str,
) -> None:
    async def reject(_arguments: argparse.Namespace) -> dict[str, object]:
        raise error

    monkeypatch.setattr(cli, "_resubmit_publication", reject)

    assert cli._run_resubmit_publication(_publication_resubmission_arguments()) == exit_code
    captured = capsys.readouterr()
    assert message in captured.err
    assert "secret database detail" not in captured.err
    assert captured.out == ""


def _first_contribution_commit_arguments() -> argparse.Namespace:
    return cli.build_parser().parse_args(
        [
            "commons",
            "commit-usda-first-contribution",
            "--package",
            "review-package.json",
            "--steward-actor-id",
            "11111111-1111-4111-8111-111111111111",
            "--expected-base-commit",
            "a" * 40,
            "--reason",
            "Reviewed pinned USDA record.",
            "--bootstrap-steward",
            "--json",
        ]
    )


def test_first_contribution_package_and_usage_fail_with_exit_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def reject(_arguments: argparse.Namespace) -> dict[str, object]:
        raise cli.FirstContributionPreparationError("invalid package")

    monkeypatch.setattr(cli, "_commit_first_contribution", reject)

    assert cli._run_commit_first_contribution(_first_contribution_commit_arguments()) == 2
    assert "invalid package" in capsys.readouterr().err


def test_first_contribution_provider_failure_never_prints_secret_material(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def reject(_arguments: argparse.Namespace) -> dict[str, object]:
        raise cli.R2PublicationError("secret-access-key-value")

    monkeypatch.setattr(cli, "_commit_first_contribution", reject)

    assert cli._run_commit_first_contribution(_first_contribution_commit_arguments()) == 5
    captured = capsys.readouterr()
    assert "provider operation failed" in captured.err
    assert "secret-access-key-value" not in captured.err
    assert captured.out == ""


def test_first_contribution_rejects_unreviewed_package_before_external_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _first_contribution_commit_arguments()
    monkeypatch.setattr(
        cli,
        "load_first_contribution_package",
        lambda _path: SimpleNamespace(package_digest="d" * 64),
    )
    monkeypatch.setattr(
        cli,
        "FirstContributionOperatorSettings",
        lambda: SimpleNamespace(
            reviewed_base_commit="a" * 40,
            reviewed_package_digest="c" * 64,
        ),
    )

    with pytest.raises(cli.FirstContributionConflictError, match="Package digest"):
        asyncio.run(cli._commit_first_contribution(arguments))


def test_first_contribution_rejects_unreviewed_base_before_external_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _first_contribution_commit_arguments()
    monkeypatch.setattr(
        cli,
        "load_first_contribution_package",
        lambda _path: SimpleNamespace(package_digest="d" * 64),
    )
    monkeypatch.setattr(
        cli,
        "FirstContributionOperatorSettings",
        lambda: SimpleNamespace(
            reviewed_base_commit="b" * 40,
            reviewed_package_digest="d" * 64,
        ),
    )

    with pytest.raises(cli.FirstContributionConflictError, match="fresh main"):
        asyncio.run(cli._commit_first_contribution(arguments))


def _prepared_package() -> SimpleNamespace:
    return SimpleNamespace(
        schema_version="1.0",
        fdc_id="1105314",
        draft_fields={"pack_id": "common-fruits"},
        record_id="bananas-ripe-and-slightly-ripe-raw",
        source_record_digest="a" * 64,
        evidence_id="11111111-1111-4111-8111-111111111111",
        approved_changes={"digest": "b" * 64},
        package_digest="d" * 64,
    )


@pytest.mark.parametrize("as_json", [False, True])
def test_first_contribution_prepare_reports_text_and_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    as_json: bool,
) -> None:
    package = _prepared_package()
    monkeypatch.setattr(cli, "prepare_usda_first_contribution", lambda *_args: package)
    arguments = cli.build_parser().parse_args(
        [
            "commons",
            "prepare-usda-first-contribution",
            "--source-json",
            "usda.json",
            "--output",
            "package.json",
            *(["--json"] if as_json else []),
        ]
    )

    assert cli.run_commons_command(arguments) == 0
    output = capsys.readouterr().out
    assert package.package_digest in output
    assert ('"schema_version": "1.0"' in output) is as_json


def test_first_contribution_prepare_failure_is_exit_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def reject(*_args: object) -> None:
        raise cli.FirstContributionPreparationError("invalid source")

    monkeypatch.setattr(cli, "prepare_usda_first_contribution", reject)
    arguments = cli.build_parser().parse_args(
        [
            "commons",
            "prepare-usda-first-contribution",
            "--source-json",
            "usda.json",
            "--output",
            "package.json",
        ]
    )

    assert cli.run_commons_command(arguments) == 2
    assert "invalid source" in capsys.readouterr().err


@pytest.mark.parametrize("as_json", [False, True])
def test_first_contribution_commit_reports_text_and_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    as_json: bool,
) -> None:
    async def committed(_arguments: argparse.Namespace) -> dict[str, object]:
        return {"publication_intent_id": "publication-1", "schema_version": "1.0"}

    arguments = _first_contribution_commit_arguments()
    arguments.json = as_json
    monkeypatch.setattr(cli, "_commit_first_contribution", committed)

    assert cli.run_commons_command(arguments) == 0
    output = capsys.readouterr().out
    assert "publication-1" in output
    assert ('"schema_version": "1.0"' in output) is as_json


@pytest.mark.parametrize(
    ("error", "exit_code", "message"),
    [
        (cli.FirstContributionAuthorityError("not steward"), 3, "authority failed"),
        (cli.FirstContributionConflictError("changed"), 4, "conflict"),
        (cli.FirstContributionEvidenceConflictError("changed"), 4, "conflict"),
        (
            cli.ValidationError.from_exception_data(
                "operator settings",
                [{"type": "missing", "loc": ("value",), "input": {}}],
            ),
            5,
            "configuration is invalid",
        ),
        (ValueError("provider details"), 5, "provider operation failed"),
    ],
)
def test_first_contribution_commit_maps_failures_to_stable_exit_contracts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    exit_code: int,
    message: str,
) -> None:
    async def reject(_arguments: argparse.Namespace) -> dict[str, object]:
        raise error

    monkeypatch.setattr(cli, "_commit_first_contribution", reject)

    assert cli._run_commit_first_contribution(_first_contribution_commit_arguments()) == exit_code
    captured = capsys.readouterr()
    assert message in captured.err
    assert "provider details" not in captured.err


def test_first_contribution_commit_wires_clients_and_closes_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = SimpleNamespace(package_digest="d" * 64)
    secret = SimpleNamespace(get_secret_value=lambda: "secret")

    class Engine:
        disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    class Writer:
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    engine = Engine()
    writer = Writer()
    settings = SimpleNamespace(
        reviewed_base_commit="a" * 40,
        reviewed_package_digest="d" * 64,
        administration_database_url="postgresql+asyncpg://operator@db/opennosh",
        database_capacity_manifest_path=None,
        r2_account_id="account",
        r2_access_key_id=secret,
        r2_secret_access_key=secret,
        r2_bucket="opennosh-public-commons",
    )
    monkeypatch.setattr(cli, "load_first_contribution_package", lambda _path: package)
    monkeypatch.setattr(cli, "FirstContributionOperatorSettings", lambda: settings)
    monkeypatch.setattr(cli, "build_administration_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(cli, "async_sessionmaker", lambda *_args, **_kwargs: "factory")
    monkeypatch.setattr(cli, "S3R2ObjectWriter", lambda **_kwargs: writer)
    monkeypatch.setattr(cli, "PgQueuerJobQueue", lambda: "queue")
    monkeypatch.setattr(cli, "R2FirstContributionEvidenceStore", lambda **_kwargs: "store")

    async def committed(
        factory: object,
        queue: object,
        store: object,
        received_package: object,
        **options: object,
    ) -> SimpleNamespace:
        assert (factory, queue, store, received_package) == (
            "factory",
            "queue",
            "store",
            package,
        )
        assert options["expected_base_commit"] == "a" * 40
        return SimpleNamespace(
            model_dump=lambda **_kwargs: {"publication_intent_id": "publication-1"}
        )

    monkeypatch.setattr(cli, "commit_usda_first_contribution", committed)

    result = asyncio.run(cli._commit_first_contribution(_first_contribution_commit_arguments()))

    assert result == {"publication_intent_id": "publication-1"}
    assert writer.closed
    assert engine.disposed


def _commons_inventory() -> SimpleNamespace:
    return SimpleNamespace(
        release_version="0.56.0.0",
        food_count=165,
        pack_count=4,
        objects=(object(), object()),
    )


def test_commons_build_command_executes_and_reports_the_inventory_anchor(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = _commons_inventory()
    inventory.model_dump = lambda **_options: {
        "release_version": inventory.release_version,
        "food_count": inventory.food_count,
        "pack_count": inventory.pack_count,
    }

    async def verified(_directory: Path, received: SimpleNamespace) -> None:
        assert received is inventory

    monkeypatch.setattr(cli, "build_starter_release", lambda **_options: inventory)
    monkeypatch.setattr(cli, "verify_starter_release", verified)
    monkeypatch.setattr(cli, "inventory_sha256", lambda _path: "b" * 64)
    arguments = cli.build_parser().parse_args(
        [
            "commons",
            "build-starter-release",
            "--packs-root",
            "packs",
            "--output",
            "/secure/release",
            "--release-version",
            "0.56.0.0",
            "--published-at",
            "2026-08-27T02:00:00+00:00",
            "--source-commit",
            "a" * 40,
            "--manifest-key-id",
            "manifest-production",
            "--manifest-private-key",
            "/secure/manifest.key",
            "--receipt-key-id",
            "receipt-production",
            "--receipt-private-key",
            "/secure/receipt.key",
            "--decision-reference",
            "https://github.com/RujitRaval/opennosh/issues/97",
            "--approving-actor",
            "github:RujitRaval",
            "--json",
        ]
    )

    assert cli.run_commons_command(arguments) == 0
    output = capsys.readouterr()
    assert f'"inventory_sha256": "{"b" * 64}"' in output.out
    assert output.err == ""


def test_commons_verify_command_executes_and_reports_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = _commons_inventory()

    async def verified(_directory: Path, received: SimpleNamespace) -> None:
        assert received is inventory

    monkeypatch.setattr(cli, "load_verified_inventory", lambda *_args, **_kwargs: inventory)
    monkeypatch.setattr(cli, "verify_starter_release", verified)
    arguments = cli.build_parser().parse_args(
        [
            "commons",
            "verify-starter-release",
            "/secure/release",
            "--inventory-sha256",
            "a" * 64,
            "--json",
        ]
    )

    assert cli.run_commons_command(arguments) == 0
    output = capsys.readouterr()
    assert '"verified": true' in output.out
    assert '"object_count": 2' in output.out
    assert output.err == ""


def test_commons_publish_command_executes_the_verified_release(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = _commons_inventory()
    writer = object()

    async def verified(_directory: Path, _inventory: SimpleNamespace) -> None:
        return None

    async def published(**options: object) -> SimpleNamespace:
        assert options["inventory"] is inventory
        assert options["writer"] is writer
        return SimpleNamespace(
            release_version="0.56.0.0",
            uploaded_immutable=172,
            reused_immutable=0,
            pointer_replaced=True,
        )

    monkeypatch.setattr(cli, "load_verified_inventory", lambda *_args, **_kwargs: inventory)
    monkeypatch.setattr(cli, "verify_starter_release", verified)
    monkeypatch.setattr(cli, "WranglerR2ObjectWriter", lambda _path: writer)
    monkeypatch.setattr(cli, "publish_starter_release_to_r2", published)
    arguments = cli.build_parser().parse_args(
        [
            "commons",
            "publish-starter-release",
            "/secure/release",
            "--inventory-sha256",
            "a" * 64,
            "--bucket",
            "opennosh-public-commons",
            "--origin-url",
            "https://commons-artifacts.opennosh.org",
            "--wrangler",
            "/approved/wrangler",
            "--json",
        ]
    )

    assert cli.run_commons_command(arguments) == 0
    output = capsys.readouterr()
    assert '"uploaded_immutable": 172' in output.out
    assert '"pointer_replaced": true' in output.out
    assert output.err == ""


def test_commons_warm_command_executes_and_errors_are_controlled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = _commons_inventory()

    async def verified(_directory: Path, _inventory: SimpleNamespace) -> None:
        return None

    async def warmed(**options: object) -> SimpleNamespace:
        assert options["concurrency"] == 4
        return SimpleNamespace(
            release_version="0.56.0.0",
            foods_warmed=165,
            provenance_warmed=165,
            packs_warmed=4,
            latest_checkpoint_advanced=True,
        )

    monkeypatch.setattr(cli, "load_verified_inventory", lambda *_args, **_kwargs: inventory)
    monkeypatch.setattr(cli, "verify_starter_release", verified)
    monkeypatch.setattr(cli, "warm_and_verify_public_api", warmed)
    argv = [
        "commons",
        "warm-live-release",
        "/secure/release",
        "--inventory-sha256",
        "a" * 64,
        "--api-origin",
        "https://opennosh.org",
        "--concurrency",
        "4",
    ]

    assert cli.main(argv) == 0
    output = capsys.readouterr()
    assert "0.56.0.0, 165 foods, 4 packs" in output.out
    assert output.err == ""

    monkeypatch.setattr(
        cli,
        "load_verified_inventory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("inventory changed")),
    )
    assert cli.main(argv) == 2
    assert "Commons release failed: inventory changed" in capsys.readouterr().err


def test_exercise_import_command_reports_rejections(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_import(arguments: argparse.Namespace) -> dict[str, object]:
        del arguments
        return {
            "rows_seen": 2,
            "rows_written": 1,
            "rows_inserted": 1,
            "rows_updated": 0,
            "rows_skipped_stale": 0,
            "rows_rejected": 1,
            "issues_omitted": 0,
            "issues": [
                {
                    "source_path": "fixture.json",
                    "row_number": 2,
                    "source_id": "202",
                    "message": "license is not allowlisted",
                }
            ],
        }

    monkeypatch.setattr(cli, "_run_wger_import", fake_import)
    arguments = cli.build_parser().parse_args(["exercises", "import-wger", "fixture.json"])

    assert cli.run_exercise_command(arguments) == 2
    captured = capsys.readouterr()
    assert "2 read, 1 written, 0 stale, 1 rejected" in captured.out
    assert "fixture.json:2: license is not allowlisted" in captured.err


@pytest.mark.parametrize("json_output", [False, True])
def test_exercise_import_command_reports_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    json_output: bool,
) -> None:
    async def fake_import(arguments: argparse.Namespace) -> dict[str, object]:
        del arguments
        return {
            "rows_seen": 2,
            "rows_written": 2,
            "rows_inserted": 2,
            "rows_updated": 0,
            "rows_skipped_stale": 0,
            "rows_rejected": 0,
            "issues_omitted": 0,
            "issues": [],
        }

    monkeypatch.setattr(cli, "_run_wger_import", fake_import)
    argv = ["exercises", "import-wger", "fixture.json"]
    if json_output:
        argv.append("--json")

    assert cli.main(argv) == 0
    captured = capsys.readouterr()
    if json_output:
        assert '"rows_written": 2' in captured.out
    else:
        assert "2 read, 2 written, 0 stale, 0 rejected" in captured.out
    assert captured.err == ""


def test_exercise_import_command_and_standalone_entrypoint_report_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def failed_import(arguments: argparse.Namespace) -> dict[str, object]:
        del arguments
        raise ValueError("bad export")

    monkeypatch.setattr(cli, "_run_wger_import", failed_import)
    assert cli.main(["exercises", "import-wger", "fixture.json"]) == 2
    assert "wger import failed: bad export" in capsys.readouterr().err

    async def failed_standalone(arguments: argparse.Namespace) -> int:
        del arguments
        raise standalone_wger.WgerFormatError("partial export")

    monkeypatch.setattr(standalone_wger, "_run_cli", failed_standalone)
    assert standalone_wger.main(["fixture.json"]) == 2
    assert "wger import failed: partial export" in capsys.readouterr().err


def test_standalone_wger_entrypoint_returns_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def successful_standalone(arguments: argparse.Namespace) -> int:
        assert arguments.paths == [Path("fixture.json")]
        return 0

    monkeypatch.setattr(standalone_wger, "_run_cli", successful_standalone)
    assert standalone_wger.main(["fixture.json"]) == 0
