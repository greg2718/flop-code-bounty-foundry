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
    FOUNDRY_OPERATOR_GROUP,
    KNOWN_FAMILY_AGENTS,
    assert_isolated_state_dir,
    assert_not_production_auto_init,
)
from flop_code_bounty_foundry.exceptions import SafetyError, ValidationError

TEST_IDENTITY_PEM = "identity-test-only.pem"
TEST_IDENTITY_JSON = "identity-test-only.json"


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
            + ["FLOP Work Exchange", "FLOP Code Bounty Foundry"],
            "note": (
                "Scout, Bench, Router, Sentinel, Work Exchange, and Foundry are related "
                "agents under common operator control. They must not count as independent peers."
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
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    atomic_write_text(pem_path, pem.decode("ascii"), mode=0o600)
    meta = _metadata(did, purpose="test-only", persistent=False, private_key_path=pem_path)
    atomic_write_text(json_path, json.dumps(meta, indent=2, sort_keys=True) + "\n", mode=0o600)
    return meta


def load_identity_meta(state_dir: Path) -> dict[str, Any]:
    resolved = assert_isolated_state_dir(state_dir)
    path = resolved / TEST_IDENTITY_JSON
    if not path.exists():
        raise ValidationError("no foundry identity found; run identity init or demo")
    return load_json_object(path)


def load_foundry_key(state_dir: Path) -> tuple[Ed25519PrivateKey, str]:
    resolved = assert_isolated_state_dir(state_dir)
    pem_path = resolved / TEST_IDENTITY_PEM
    if not pem_path.exists():
        raise ValidationError("no foundry identity found; run identity init or demo")
    key = load_private_key(pem_path)
    meta = load_json_object(resolved / TEST_IDENTITY_JSON)
    return key, str(meta["did"])


def ensure_test_identity(state_dir: Path) -> dict[str, Any]:
    resolved = assert_isolated_state_dir(state_dir)
    json_path = resolved / TEST_IDENTITY_JSON
    if json_path.exists():
        return load_json_object(json_path)
    return create_test_identity(resolved)


__all__ = [
    "TEST_IDENTITY_JSON",
    "TEST_IDENTITY_PEM",
    "create_ephemeral_party",
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
