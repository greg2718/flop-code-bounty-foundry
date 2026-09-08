from __future__ import annotations

import json
from pathlib import Path

import pytest
from flop_work_exchange.receipts import verify_receipt

from flop_code_bounty_foundry.cli import main


def test_module_demo_writes_verifiable_receipts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["demo", "--state-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["bench_result"] == "PASS"
    assert payload["payment_mode"] == "paper"
    assert payload["settlement_status"] == "simulated"
    assert payload["urls_are_inert"] is True
    roles = payload["receipt_roles"]
    assert "implementer" in roles
    assert "application" in roles
    assert "bench_validation" in roles
    assert "reviewer" in roles
    assert "author" in roles
    for item in payload["receipt_verifications"]:
        assert item["verification"]["ok"] is True
    bundle_path = Path(payload["bundle_path"])
    assert bundle_path.exists()
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    for leg in bundle["legs"]:
        assert verify_receipt(leg["receipt"])["ok"] is True


def test_cli_list_bounties_after_demo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["demo", "--state-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["--state-dir", str(tmp_path), "list-bounties"]) == 0
    bounties = json.loads(capsys.readouterr().out)
    assert bounties[0]["status"] == "SETTLED"
    bounty_id = bounties[0]["bounty_id"]
    assert main(["--state-dir", str(tmp_path), "show", "--bounty-id", bounty_id]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["receipt_bundle"]["payment_mode"] == "paper"


def test_help_lists_required_commands() -> None:
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    text = buffer.getvalue()
    for command in (
        "create-bounty",
        "list-bounties",
        "assign",
        "submit-work",
        "request-review",
        "verify",
        "settle",
        "show",
        "demo",
    ):
        assert command in text


def test_example_specs_load() -> None:
    from flop_code_bounty_foundry.models import load_spec_file

    root = Path(__file__).resolve().parents[1] / "examples"
    for name in (
        "docs-improvement.json",
        "passive-test-addition.json",
        "protocol-conformance.json",
    ):
        spec = load_spec_file(root / name)
        assert spec.budget_micro > 0
        assert spec.bench.mode == "passive"
