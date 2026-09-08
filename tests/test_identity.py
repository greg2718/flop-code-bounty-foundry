from __future__ import annotations

import json
from pathlib import Path

import pytest

from flop_code_bounty_foundry.cli import main
from flop_code_bounty_foundry.constants import DEFAULT_PRODUCTION_STATE, FOUNDRY_DID, TOURNAMENT_DID
from flop_code_bounty_foundry.exceptions import SafetyError, ValidationError
from flop_code_bounty_foundry.identity import (
    IDENTITY_CONFIRMATION,
    PRODUCTION_IDENTITY_JSON,
    PRODUCTION_IDENTITY_PEM,
    TEST_IDENTITY_JSON,
    TEST_IDENTITY_PEM,
    create_production_identity,
    create_test_identity,
    load_foundry_key,
    load_identity_meta,
)

TEST_PASSPHRASE = "foundry-test-pass!"  # noqa: S105
WRONG_PASSPHRASE = "wrong-passphrase!!"  # noqa: S105
MISMATCH_PASSPHRASE = "different-pass-ok!"  # noqa: S105
SHORT_PASSPHRASE = "short"  # noqa: S105


def _mint_production(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setattr(
        "flop_code_bounty_foundry.identity.DEFAULT_PRODUCTION_STATE",
        tmp_path,
    )
    return create_production_identity(
        state_dir=tmp_path,
        confirm=IDENTITY_CONFIRMATION,
        passphrase=TEST_PASSPHRASE,
        passphrase_confirmation=TEST_PASSPHRASE,
    )


def test_create_and_load_encrypted_production_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meta = _mint_production(tmp_path, monkeypatch)
    pem_path = tmp_path / PRODUCTION_IDENTITY_PEM
    json_path = tmp_path / PRODUCTION_IDENTITY_JSON
    assert pem_path.exists()
    assert json_path.exists()
    pem_text = pem_path.read_text(encoding="ascii")
    assert "BEGIN ENCRYPTED PRIVATE KEY" in pem_text
    assert meta["persistent"] is True
    assert meta["purpose"] == "flop-code-bounty-foundry-production"
    assert meta["did"].startswith("did:key:z")
    related = meta["operator_group"]["related_agents"]
    assert "FLOP Capability Tournament" in related
    assert "FLOP Code Bounty Foundry" in related
    assert FOUNDRY_DID in meta["operator_group"]["note"]
    assert TOURNAMENT_DID in meta["operator_group"]["note"]

    loaded = load_identity_meta(tmp_path)
    assert loaded["did"] == meta["did"]
    assert loaded["persistent"] is True

    key, did = load_foundry_key(tmp_path, passphrase=TEST_PASSPHRASE)
    assert did == meta["did"]
    assert key.public_key() is not None

    with pytest.raises(ValidationError, match="passphrase"):
        load_foundry_key(tmp_path)

    with pytest.raises(ValidationError, match="decrypt"):
        load_foundry_key(tmp_path, passphrase=WRONG_PASSPHRASE)


def test_load_identity_meta_prefers_production_over_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prod = _mint_production(tmp_path, monkeypatch)
    (tmp_path / TEST_IDENTITY_JSON).write_text(
        json.dumps({"did": "did:key:z-test-only-placeholder", "persistent": False}) + "\n",
        encoding="utf-8",
    )
    shown = load_identity_meta(tmp_path)
    assert shown["did"] == prod["did"]
    assert shown["persistent"] is True


def test_create_production_identity_gates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SafetyError, match="confirmation"):
        create_production_identity(
            state_dir=tmp_path,
            confirm="nope",
            passphrase=TEST_PASSPHRASE,
            passphrase_confirmation=TEST_PASSPHRASE,
        )
    with pytest.raises(SafetyError, match="must resolve exactly"):
        create_production_identity(
            state_dir=tmp_path,
            confirm=IDENTITY_CONFIRMATION,
            passphrase=TEST_PASSPHRASE,
            passphrase_confirmation=TEST_PASSPHRASE,
        )
    monkeypatch.setattr(
        "flop_code_bounty_foundry.identity.DEFAULT_PRODUCTION_STATE",
        tmp_path,
    )
    with pytest.raises(SafetyError, match="does not match"):
        create_production_identity(
            state_dir=tmp_path,
            confirm=IDENTITY_CONFIRMATION,
            passphrase=TEST_PASSPHRASE,
            passphrase_confirmation=MISMATCH_PASSPHRASE,
        )
    with pytest.raises(SafetyError, match="at least 16"):
        create_production_identity(
            state_dir=tmp_path,
            confirm=IDENTITY_CONFIRMATION,
            passphrase=SHORT_PASSPHRASE,
            passphrase_confirmation=SHORT_PASSPHRASE,
        )
    create_production_identity(
        state_dir=tmp_path,
        confirm=IDENTITY_CONFIRMATION,
        passphrase=TEST_PASSPHRASE,
        passphrase_confirmation=TEST_PASSPHRASE,
    )
    with pytest.raises(SafetyError, match="refusing to overwrite"):
        create_production_identity(
            state_dir=tmp_path,
            confirm=IDENTITY_CONFIRMATION,
            passphrase=TEST_PASSPHRASE,
            passphrase_confirmation=TEST_PASSPHRASE,
        )


def test_test_identity_init_refuses_production_path() -> None:
    with pytest.raises(SafetyError, match="refusing to auto-create identity"):
        create_test_identity(DEFAULT_PRODUCTION_STATE)
    assert not (DEFAULT_PRODUCTION_STATE / TEST_IDENTITY_PEM).exists()


def test_test_identity_load_still_unencrypted(tmp_path: Path) -> None:
    meta = create_test_identity(tmp_path)
    pem_text = (tmp_path / TEST_IDENTITY_PEM).read_text(encoding="ascii")
    assert "BEGIN PRIVATE KEY" in pem_text
    assert "ENCRYPTED" not in pem_text
    key, did = load_foundry_key(tmp_path)
    assert did == meta["did"]
    assert key.public_key() is not None
    shown = load_identity_meta(tmp_path)
    assert shown["persistent"] is False


def test_cli_identity_show_reads_encrypted_production_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    meta = _mint_production(tmp_path, monkeypatch)
    assert main(["--state-dir", str(tmp_path), "identity", "show"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["did"] == meta["did"]
    assert shown["persistent"] is True
    assert shown["purpose"] == "flop-code-bounty-foundry-production"


def test_cli_init_production_uses_getpass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "flop_code_bounty_foundry.identity.DEFAULT_PRODUCTION_STATE",
        tmp_path,
    )
    monkeypatch.setattr("getpass.getpass", lambda prompt="": TEST_PASSPHRASE)
    code = main(
        [
            "--state-dir",
            str(tmp_path),
            "identity",
            "init-production",
            "--confirm",
            IDENTITY_CONFIRMATION,
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["persistent"] is True
    assert (tmp_path / PRODUCTION_IDENTITY_PEM).exists()
    key, did = load_foundry_key(tmp_path, passphrase=TEST_PASSPHRASE)
    assert did == payload["did"]
    assert key.public_key() is not None
