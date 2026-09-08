from __future__ import annotations

from pathlib import Path

import pytest
from flop_work_exchange.models import Receipt
from flop_work_exchange.receipts import sign_receipt, verify_receipt

from flop_code_bounty_foundry.cli import main
from flop_code_bounty_foundry.exceptions import ValidationError
from flop_code_bounty_foundry.identity import create_ephemeral_party, generate_key, public_did
from tests.helpers import complete_to_verified

REQUIRED = (
    "job_id",
    "buyer_did",
    "seller_did",
    "operator_relationship",
    "service",
    "price_flop",
    "payment_mode",
    "tclk_deal_id",
    "result_hash",
    "bench_result",
    "completed_at",
    "settlement_status",
)


def test_sign_and_verify_receipt_roundtrip() -> None:
    key = generate_key()
    did = public_did(key)
    _, buyer = create_ephemeral_party("b")
    _, seller = create_ephemeral_party("s")
    receipt = Receipt(
        job_id="FLOP-JOB-demo",
        buyer_did=buyer,
        seller_did=seller,
        operator_relationship="independent",
        service="bounty.implementer",
        price_flop="8",
        payment_mode="paper",
        tclk_deal_id="tclk-paper-abc",
        result_hash="sha256:" + ("ab" * 32),
        bench_result="PASS",
        completed_at="2026-09-08T00:00:00Z",
        settlement_status="simulated",
        price_micro=8_000_000,
    )
    sign_receipt(receipt, key, did)
    payload = receipt.to_dict()
    assert payload["signature"]
    assert verify_receipt(payload)["ok"] is True


def test_tampered_receipt_fails_verify() -> None:
    key = generate_key()
    did = public_did(key)
    _, buyer = create_ephemeral_party("b")
    _, seller = create_ephemeral_party("s")
    receipt = Receipt(
        job_id="FLOP-JOB-demo",
        buyer_did=buyer,
        seller_did=seller,
        operator_relationship="independent",
        service="bounty.implementer",
        price_flop="8",
        payment_mode="paper",
        tclk_deal_id="tclk-paper-abc",
        result_hash="sha256:" + ("ab" * 32),
        bench_result="PASS",
        completed_at="2026-09-08T00:00:00Z",
        settlement_status="simulated",
    )
    sign_receipt(receipt, key, did)
    payload = receipt.to_dict()
    payload["price_flop"] = "999"
    with pytest.raises(ValidationError, match="signature verification failed"):
        verify_receipt(payload)


def test_live_payment_mode_rejected_on_receipt() -> None:
    key = generate_key()
    did = public_did(key)
    _, buyer = create_ephemeral_party("b")
    _, seller = create_ephemeral_party("s")
    receipt = Receipt(
        job_id="FLOP-JOB-demo",
        buyer_did=buyer,
        seller_did=seller,
        operator_relationship="independent",
        service="bounty.implementer",
        price_flop="8",
        payment_mode="live",
        tclk_deal_id="tclk-paper-abc",
        result_hash="sha256:" + ("ab" * 32),
        bench_result="PASS",
        completed_at="2026-09-08T00:00:00Z",
        settlement_status="simulated",
    )
    with pytest.raises(ValidationError, match="payment_mode"):
        sign_receipt(receipt, key, did)


def test_settled_bundle_receipts_verify(tmp_path: Path) -> None:
    foundry, bounty_id, _sponsor, _impl = complete_to_verified(
        tmp_path, with_review=True, with_author=True
    )
    bundle, path = foundry.settle(bounty_id)
    assert path.exists()
    for leg in bundle.legs:
        for field in REQUIRED:
            assert field in leg.receipt
        assert leg.receipt["payment_mode"] == "paper"
        assert leg.receipt["settlement_status"] == "simulated"
        assert verify_receipt(leg.receipt)["ok"] is True
        assert leg.verification["ok"] is True
    roles = {leg.role for leg in bundle.legs}
    assert {"author", "implementer", "reviewer", "bench_validation", "application"} <= roles
    impl_receipt = tmp_path / "receipts" / f"{bounty_id}.implementer.json"
    assert impl_receipt.exists()
    assert main(["verify-receipt", str(impl_receipt)]) == 0
