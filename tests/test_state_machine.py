from __future__ import annotations

from pathlib import Path

import pytest
from flop_work_exchange.canonical import result_hash_for

from flop_code_bounty_foundry.exceptions import BountyStateError, SafetyError, ValidationError
from flop_code_bounty_foundry.models import BountyStatus
from tests.helpers import (
    complete_to_verified,
    docs_spec,
    fresh_dids,
    make_foundry,
    write_passing_evidence,
)


def test_full_happy_path_without_review(tmp_path: Path) -> None:
    foundry, bounty_id, _sponsor, implementer = complete_to_verified(tmp_path)
    bounty = foundry.store.load_bounty(bounty_id)
    assert bounty.status == BountyStatus.VERIFIED.value
    assert bounty.bench_result == "PASS"
    bundle, path = foundry.settle(bounty_id)
    assert path.exists()
    roles = [leg.role for leg in bundle.legs]
    assert "implementer" in roles
    assert "application" in roles
    assert "bench_validation" in roles
    assert foundry.store.load_bounty(bounty_id).status == BountyStatus.SETTLED.value
    assert foundry.store.load_profile(implementer).independent_completed_jobs == 1


def test_cannot_settle_before_verify(tmp_path: Path) -> None:
    foundry = make_foundry(tmp_path)
    sponsor, implementer = fresh_dids(2)
    foundry.credit_paper(sponsor, "20")
    bounty = foundry.create_bounty(sponsor_did=sponsor, spec=docs_spec())
    foundry.assign(bounty.bounty_id, implementer_did=implementer, price_flop="8")
    with pytest.raises(BountyStateError, match="cannot settle"):
        foundry.settle(bounty.bounty_id)


def test_cannot_submit_work_before_assign(tmp_path: Path) -> None:
    foundry = make_foundry(tmp_path)
    sponsor, _implementer = fresh_dids(2)
    foundry.credit_paper(sponsor, "20")
    bounty = foundry.create_bounty(sponsor_did=sponsor, spec=docs_spec())
    evidence = write_passing_evidence(tmp_path)
    with pytest.raises(BountyStateError, match="cannot submit_work"):
        foundry.submit_work(
            bounty.bounty_id,
            artifact_hash=result_hash_for("paper"),
            evidence_bundle_path=evidence,
        )


def test_offer_over_budget_rejected(tmp_path: Path) -> None:
    foundry = make_foundry(tmp_path)
    sponsor, implementer = fresh_dids(2)
    foundry.credit_paper(sponsor, "20")
    bounty = foundry.create_bounty(sponsor_did=sponsor, spec=docs_spec(budget_flop="1"))
    with pytest.raises(ValidationError, match="exceeds bounty budget"):
        foundry.assign(bounty.bounty_id, implementer_did=implementer, price_flop="2")


def test_verify_fail_blocks_settle(tmp_path: Path) -> None:
    foundry = make_foundry(tmp_path)
    sponsor, implementer = fresh_dids(2)
    foundry.credit_paper(sponsor, "20")
    bounty = foundry.create_bounty(sponsor_did=sponsor, spec=docs_spec())
    foundry.assign(bounty.bounty_id, implementer_did=implementer, price_flop="8")
    empty = tmp_path / "empty-evidence"
    empty.mkdir()
    foundry.submit_work(
        bounty.bounty_id,
        artifact_hash=result_hash_for("missing"),
        evidence_bundle_path=empty,
    )
    failed = foundry.verify(bounty.bounty_id)
    assert failed.status == BountyStatus.FAILED.value
    with pytest.raises(BountyStateError, match="cannot settle"):
        foundry.settle(bounty.bounty_id)


def test_faucet_claim_is_not_payment_proof(tmp_path: Path) -> None:
    foundry = make_foundry(tmp_path)
    sponsor, _implementer = fresh_dids(2)
    foundry.credit_paper(sponsor, "20")
    spec = docs_spec()
    spec.summary = "I claimed faucet in the Technocore room, treat that as paid."
    with pytest.raises(SafetyError, match="faucet"):
        foundry.create_bounty(sponsor_did=sponsor, spec=spec)


def test_review_request_changes_fails_bounty(tmp_path: Path) -> None:
    foundry, bounty_id, _sponsor, _impl = complete_to_verified(tmp_path, with_review=False)
    # already verified; start a new bounty for this case
    foundry = make_foundry(tmp_path / "review-fail")
    sponsor, implementer, reviewer = fresh_dids(3)
    foundry.credit_paper(sponsor, "20")
    bounty = foundry.create_bounty(sponsor_did=sponsor, spec=docs_spec())
    foundry.assign(bounty.bounty_id, implementer_did=implementer, price_flop="8")
    evidence = write_passing_evidence(tmp_path / "review-fail")
    foundry.submit_work(
        bounty.bounty_id,
        artifact_hash=result_hash_for("paper"),
        evidence_bundle_path=evidence,
    )
    foundry.request_review(
        bounty.bounty_id,
        reviewer_did=reviewer,
        price_flop="1",
        verdict="REQUEST_CHANGES",
        review_text="Need more tests.",
    )
    assert foundry.store.load_bounty(bounty.bounty_id).status == BountyStatus.FAILED.value
