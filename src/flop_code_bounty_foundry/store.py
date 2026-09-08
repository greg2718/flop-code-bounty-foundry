from __future__ import annotations

from pathlib import Path
from typing import Any

from flop_work_exchange.canonical import atomic_write_json, load_json_object
from flop_work_exchange.models import EvidenceProfile
from flop_work_exchange.store import new_id

from flop_code_bounty_foundry.constants import assert_isolated_state_dir
from flop_code_bounty_foundry.exceptions import BountyStateError
from flop_code_bounty_foundry.models import (
    Bounty,
    BountyReceiptBundle,
    did_slug,
)


class FoundryStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = assert_isolated_state_dir(state_dir)
        self.bounties_dir = self.state_dir / "bounties"
        self.bundles_dir = self.state_dir / "receipt_bundles"
        self.receipts_dir = self.state_dir / "receipts"
        self.profiles_dir = self.state_dir / "evidence_profiles"

    def initialize(self) -> None:
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        for path in (
            self.bounties_dir,
            self.bundles_dir,
            self.receipts_dir,
            self.profiles_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def save_bounty(self, bounty: Bounty) -> None:
        atomic_write_json(self.bounties_dir / f"{bounty.bounty_id}.json", bounty.to_dict())

    def load_bounty(self, bounty_id: str) -> Bounty:
        path = self.bounties_dir / f"{bounty_id}.json"
        if not path.exists():
            raise BountyStateError(f"unknown bounty: {bounty_id}")
        return Bounty.from_dict(load_json_object(path))

    def list_bounties(self) -> list[Bounty]:
        return [
            Bounty.from_dict(load_json_object(path))
            for path in sorted(self.bounties_dir.glob("FLOP-BOUNTY-*.json"))
        ]

    def save_bundle(self, bundle: BountyReceiptBundle) -> Path:
        path = self.bundles_dir / f"{bundle.bounty_id}.json"
        atomic_write_json(path, bundle.to_dict())
        return path

    def load_bundle(self, bounty_id: str) -> dict[str, Any]:
        path = self.bundles_dir / f"{bounty_id}.json"
        if not path.exists():
            raise BountyStateError(f"no receipt bundle for bounty: {bounty_id}")
        return load_json_object(path)

    def save_receipt_leg(self, bounty_id: str, role: str, payload: dict[str, Any]) -> Path:
        path = self.receipts_dir / f"{bounty_id}.{role}.json"
        atomic_write_json(path, payload)
        return path

    def load_profile(self, did: str) -> EvidenceProfile:
        path = self.profiles_dir / f"{did_slug(did)}.json"
        if not path.exists():
            return EvidenceProfile(did=did)
        return EvidenceProfile.from_dict(load_json_object(path))

    def save_profile(self, profile: EvidenceProfile) -> None:
        atomic_write_json(self.profiles_dir / f"{did_slug(profile.did)}.json", profile.to_dict())


def require_bounty_status(bounty: Bounty, allowed: set[str], action: str) -> None:
    if bounty.status not in allowed:
        raise BountyStateError(
            f"cannot {action} bounty {bounty.bounty_id} in status {bounty.status}; "
            f"allowed: {', '.join(sorted(allowed))}"
        )


__all__ = ["FoundryStore", "new_id", "require_bounty_status"]
