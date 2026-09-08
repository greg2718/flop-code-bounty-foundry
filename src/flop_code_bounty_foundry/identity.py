from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flop_work_exchange.canonical import atomic_write_text, load_json_object
from flop_work_exchange.identity import (
    create_ephemeral_party,
    generate_key,
    is_valid_ed25519_did,
    load_private_key,
    public_did,
    require_did,
    sign_canonical,
    verify_canonical,
)

from flop_code_bounty_foundry.constants import (
    DEFAULT_PRODUCTION_STATE,
    FOUNDRY_DID,
    FOUNDRY_OPERATOR_GROUP,
    KNOWN_FAMILY_AGENTS,
    TOURNAMENT_DID,
    assert_isolated_state_dir,
    assert_not_production_auto_init,
)
from flop_code_bounty_foundry.exceptions import SafetyError, ValidationError

IDENTITY_CONFIRMATION = "CREATE-FLOP-CODE-BOUNTY-FOUNDRY-IDENTITY"
TEST_IDENTITY_PEM = "identity-test-only.pem"
TEST_IDENTITY_JSON = "identity-test-only.json"
PRODUCTION_IDENTITY_PEM = "identity.pem"
PRODUCTION_IDENTITY_JSON = "identity.json"


def _write_pem(
    path: Path, key: Ed25519PrivateKey, *, encrypted: bool, passphrase: str | None
) -> None:
    if encrypted:
        if not passphrase:
            raise SafetyError("encrypted identity requires a passphrase")
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(passphrase.encode("utf-8")),
        )
    else:
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    atomic_write_text(path, pem.decode("ascii"), mode=0o600)


def _load_ed25519_key(pem_path: Path, passphrase: str | None = None) -> Ed25519PrivateKey:
    try:
        return load_private_key(pem_path, passphrase=passphrase)
    except TypeError as exc:
        raise ValidationError("encrypted identity PEM requires a passphrase to unlock") from exc
    except ValueError as exc:
        raise ValidationError("failed to decrypt identity PEM") from exc


def _metadata(
    did: str, *, purpose: str, persistent: bool, private_key_path: Path
) -> dict[str, Any]:
    return {
        "schema_version": "flop-code-bounty-foundry.identity.v0.1",
        "created_at": datetime.now(UTC).isoformat(),
        "did": did,
        "key_type": "Ed25519",
        "purpose": purpose,
        "persistent": persistent,
        "private_key_path": str(private_key_path),
        "operator_group": {
            "common_control_disclosure": True,
            "operator_group_id": FOUNDRY_OPERATOR_GROUP,
            "related_agents": [agent["name"] for agent in KNOWN_FAMILY_AGENTS]
            + [
                "FLOP Work Exchange",
                "FLOP Code Bounty Foundry",
                "FLOP Capability Tournament",
            ],
            "note": (
                "Scout, Bench, Router, Sentinel, Work Exchange, Foundry, and Capability "
                "Tournament are related agents under common operator control. They must "
                "not count as independent peers. Example currently minted production DIDs "
                f"(not exclusive; any valid Ed25519 did:key in identity.json is accepted): "
                f"Foundry {FOUNDRY_DID}; Tournament {TOURNAMENT_DID}."
            ),
        },
    }


def create_test_identity(state_dir: Path) -> dict[str, Any]:
    resolved = assert_isolated_state_dir(state_dir)
    assert_not_production_auto_init(resolved)
    resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
    pem_path = resolved / TEST_IDENTITY_PEM
    json_path = resolved / TEST_IDENTITY_JSON
    if pem_path.exists() or json_path.exists():
        raise SafetyError("test identity already exists; refusing to overwrite")
    private_key = generate_key()
    did = public_did(private_key)
    _write_pem(pem_path, private_key, encrypted=False, passphrase=None)
    meta = _metadata(did, purpose="test-only", persistent=False, private_key_path=pem_path)
    atomic_write_text(json_path, json.dumps(meta, indent=2, sort_keys=True) + "\n", mode=0o600)
    return meta


def load_identity_meta(state_dir: Path) -> dict[str, Any]:
    resolved = assert_isolated_state_dir(state_dir)
    production = resolved / PRODUCTION_IDENTITY_JSON
    test = resolved / TEST_IDENTITY_JSON
    if production.exists():
        return load_json_object(production)
    if test.exists():
        return load_json_object(test)
    raise ValidationError(
        "no foundry identity found; run identity init, identity init-production, or demo"
    )


def load_foundry_key(
    state_dir: Path, passphrase: str | None = None
) -> tuple[Ed25519PrivateKey, str]:
    resolved = assert_isolated_state_dir(state_dir)
    prod_pem = resolved / PRODUCTION_IDENTITY_PEM
    test_pem = resolved / TEST_IDENTITY_PEM
    if prod_pem.exists():
        key = _load_ed25519_key(prod_pem, passphrase=passphrase)
        meta = load_json_object(resolved / PRODUCTION_IDENTITY_JSON)
        return key, str(meta["did"])
    if test_pem.exists():
        key = _load_ed25519_key(test_pem)
        meta = load_json_object(resolved / TEST_IDENTITY_JSON)
        return key, str(meta["did"])
    raise ValidationError(
        "no foundry identity found; run identity init, identity init-production, or demo"
    )


def ensure_test_identity(state_dir: Path) -> dict[str, Any]:
    resolved = assert_isolated_state_dir(state_dir)
    json_path = resolved / TEST_IDENTITY_JSON
    if json_path.exists():
        return load_json_object(json_path)
    return create_test_identity(resolved)


def create_production_identity(
    *,
    state_dir: Path,
    confirm: str,
    passphrase: str,
    passphrase_confirmation: str,
) -> dict[str, Any]:
    if confirm != IDENTITY_CONFIRMATION:
        raise SafetyError("explicit identity creation confirmation value is required")
    resolved = assert_isolated_state_dir(state_dir)
    expected = DEFAULT_PRODUCTION_STATE.expanduser().resolve(strict=False)
    if resolved != expected:
        raise SafetyError(
            "production identity state directory must resolve exactly to "
            "FLOP Code Bounty Foundry production state"
        )
    if passphrase != passphrase_confirmation:
        raise SafetyError("passphrase confirmation does not match")
    if len(passphrase) < 16:
        raise SafetyError("passphrase must be at least 16 characters")
    resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
    pem_path = resolved / PRODUCTION_IDENTITY_PEM
    json_path = resolved / PRODUCTION_IDENTITY_JSON
    if pem_path.exists() or json_path.exists():
        raise SafetyError("production identity already exists; refusing to overwrite")
    key = generate_key()
    did = public_did(key)
    _write_pem(pem_path, key, encrypted=True, passphrase=passphrase)
    meta = _metadata(
        did,
        purpose="flop-code-bounty-foundry-production",
        persistent=True,
        private_key_path=pem_path,
    )
    atomic_write_text(json_path, json.dumps(meta, indent=2, sort_keys=True) + "\n", mode=0o600)
    return meta


__all__ = [
    "IDENTITY_CONFIRMATION",
    "PRODUCTION_IDENTITY_JSON",
    "PRODUCTION_IDENTITY_PEM",
    "TEST_IDENTITY_JSON",
    "TEST_IDENTITY_PEM",
    "create_ephemeral_party",
    "create_production_identity",
    "create_test_identity",
    "ensure_test_identity",
    "generate_key",
    "is_valid_ed25519_did",
    "load_foundry_key",
    "load_identity_meta",
    "public_did",
    "require_did",
    "sign_canonical",
    "verify_canonical",
]
