from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flop_work_exchange.amounts import micro_to_flop_string, parse_flop_to_micro
from flop_work_exchange.models import BenchVerdict, Job, Receipt, now_iso
from flop_work_exchange.policy import (
    apply_deal_policy,
    classify_operator_relationship,
    detect_wash_risk,
    reject_payment_proof_claims,
)
from flop_work_exchange.receipts import sign_receipt, verify_receipt

from flop_code_bounty_foundry.adapters.factory import AdapterBundle, resolve_adapters
from flop_code_bounty_foundry.config import FoundryConfig, write_resolved_config
from flop_code_bounty_foundry.constants import (
    ADAPTER_PUBLISH_ACCOUNT,
    BENCH_FEE_ACCOUNT,
    FOUNDRY_FEE_ACCOUNT,
)
from flop_code_bounty_foundry.exceptions import (
    AdapterError,
    BountyStateError,
    NotLiveError,
    PolicyError,
    SafetyError,
    ValidationError,
)
from flop_code_bounty_foundry.exchange_client import (
    InProcessWorkExchangeClient,
    WorkExchangeClient,
)
from flop_code_bounty_foundry.identity import (
    ensure_test_identity,
    load_foundry_key,
    load_identity_meta,
    require_did,
)
from flop_code_bounty_foundry.models import (
    Bounty,
    BountyReceiptBundle,
    BountyReceiptLeg,
    BountySpec,
    BountyStatus,
    ReviewAssignment,
)
from flop_code_bounty_foundry.store import FoundryStore, new_id, require_bounty_status

ALLOWED = {
    "assign": {BountyStatus.POSTED.value},
    "submit_work": {BountyStatus.ASSIGNED.value},
    "request_review": {BountyStatus.WORK_SUBMITTED.value},
    "submit_review": {BountyStatus.REVIEW_REQUESTED.value},
    "verify": {
        BountyStatus.WORK_SUBMITTED.value,
        BountyStatus.REVIEW_SUBMITTED.value,
    },
    "settle": {BountyStatus.VERIFIED.value},
}

REVIEW_VERDICTS = {"APPROVE", "REQUEST_CHANGES", "REJECT"}


def _job_outcome(spec: BountySpec) -> str:
    items = "; ".join(spec.acceptance.items) if spec.acceptance.items else spec.acceptance.summary
    return (
        f"{spec.title}: {spec.summary} Acceptance: {items}. "
        f"Bench claim {spec.bench.claim_id or spec.title}."
    )


class CodeBountyFoundry:
    def __init__(
        self,
        config: FoundryConfig,
        *,
        exchange: WorkExchangeClient | None = None,
        adapters: AdapterBundle | None = None,
    ) -> None:
        self.config = config
        self.store = FoundryStore(config.resolved_state_dir())
        self.store.initialize()
        write_resolved_config(self.store.state_dir, config)
        try:
            load_identity_meta(self.store.state_dir)
        except ValidationError:
            ensure_test_identity(self.store.state_dir)

        def _lookup(job_id: str) -> Bounty | None:
            for bounty in self.store.list_bounties():
                if bounty.implementer_job_id == job_id:
                    return bounty
                if bounty.review and bounty.review.job_id == job_id:
                    return bounty
            return None

        self.adapters = adapters or resolve_adapters(
            config.adapters, bounty_lookup=_lookup
        )
        if getattr(self.adapters.bench, "lookup", None) is None:
            self.adapters.bench.lookup = _lookup
        self.bench = self.adapters.bench
        if config.settlement_backend == "testnet" and exchange is None:
            self.exchange: WorkExchangeClient = InProcessWorkExchangeClient(
                self.store.state_dir,
                settlement_backend="testnet",
                policy=config.policy,
                known_family_dids=config.known_family_dids,
                scout=self.adapters.scout,
                router=self.adapters.router,
                sentinel=self.adapters.sentinel,
                bench=self.adapters.exchange_bench,
            )
        elif exchange is None:
            self.exchange = InProcessWorkExchangeClient(
                self.store.state_dir,
                policy=config.policy,
                known_family_dids=config.known_family_dids,
                scout=self.adapters.scout,
                router=self.adapters.router,
                sentinel=self.adapters.sentinel,
                bench=self.adapters.exchange_bench,
            )
        else:
            self.exchange = exchange

    @classmethod
    def open(cls, state_dir: Path, config_path: Path | None = None) -> CodeBountyFoundry:
        from flop_code_bounty_foundry.config import load_config

        return cls(load_config(state_dir, config_path))

    def foundry_did(self) -> str:
        return str(load_identity_meta(self.store.state_dir)["did"])

    def credit_paper(self, account: str, amount_flop: str, reason: str = "paper-seed") -> None:
        self.exchange.credit_paper(account, amount_flop, reason)

    def create_bounty(self, *, sponsor_did: str, spec: BountySpec) -> Bounty:
        require_did(sponsor_did)
        reject_payment_proof_claims(spec.title)
        reject_payment_proof_claims(spec.summary)
        reject_payment_proof_claims(spec.notes)
        reject_payment_proof_claims(spec.acceptance.summary)
        for item in spec.acceptance.items:
            reject_payment_proof_claims(item)
        if spec.repo_url:
            # Recorded only. Never used as a fetch target.
            reject_payment_proof_claims(spec.repo_url)
        verdict = self.exchange.screen(
            "bounty",
            {
                "title": spec.title,
                "summary": spec.summary,
                "bounty_type": spec.bounty_type,
                "repo_url": spec.repo_url,
                "local_path": spec.local_path,
                "acceptance": spec.acceptance.to_dict(),
                "sponsor_did": sponsor_did,
            },
        )
        if verdict.action == "REJECT":
            raise AdapterError(f"Sentinel REJECT on bounty: {', '.join(verdict.reasons)}")
        job = self.exchange.post_job(
            buyer_did=sponsor_did,
            outcome=_job_outcome(spec),
            service=f"bounty.{spec.bounty_type}",
            budget_micro=spec.budget_micro,
        )
        bounty = Bounty(
            bounty_id=new_id("FLOP-BOUNTY"),
            sponsor_did=sponsor_did,
            spec=spec,
            payment_mode=self.config.payment_mode,
            implementer_job_id=job.job_id,
            sentinel_status=verdict.action,
            notes=list(verdict.reasons),
        )
        if verdict.action == "REVIEW":
            bounty.notes.append(
                "repo_url/local_path are inert metadata; Foundry does not fetch or clone them"
            )
        self.store.save_bounty(bounty)
        return bounty

    def list_bounties(self) -> list[Bounty]:
        return self.store.list_bounties()

    def assign(
        self,
        bounty_id: str,
        *,
        implementer_did: str,
        price_flop: str | None = None,
        price_micro: int | None = None,
        relationship: str | None = None,
        notes: str = "",
    ) -> Bounty:
        require_did(implementer_did)
        bounty = self.store.load_bounty(bounty_id)
        require_bounty_status(bounty, ALLOWED["assign"], "assign")
        if not bounty.implementer_job_id:
            raise BountyStateError("bounty has no Work Exchange job")
        if price_micro is None:
            if price_flop is None:
                raise ValidationError("price_flop or price_micro is required")
            price_micro = parse_flop_to_micro(price_flop)
        if price_micro <= 0:
            raise ValidationError("implementer price must be positive")
        if price_micro > bounty.spec.budget_micro:
            raise ValidationError("implementer price exceeds bounty budget")
        offer = self.exchange.submit_offer(
            job_id=bounty.implementer_job_id,
            seller_did=implementer_did,
            price_micro=price_micro,
            notes=notes or "foundry implementer offer",
        )
        plan = self.exchange.route_job(bounty.implementer_job_id)
        if plan.qualification != "QUALIFIED_PLAN" or plan.selected_offer_id != offer.offer_id:
            raise AdapterError("Router did not select this implementer: " + "; ".join(plan.reasons))
        deal = self.exchange.accept_offer(offer.offer_id, relationship=relationship)
        bounty.implementer_did = implementer_did
        bounty.implementer_offer_id = offer.offer_id
        bounty.implementer_deal_id = deal.deal_id
        bounty.implementer_price_micro = deal.price_micro
        bounty.implementer_relationship = deal.operator_relationship
        bounty.implementer_independent_reputation_eligible = deal.independent_reputation_eligible
        bounty.implementer_independent_fee_volume_eligible = deal.independent_fee_volume_eligible
        bounty.implementer_wash_risk = deal.wash_risk
        bounty.implementer_tclk_deal_id = deal.tclk_deal_id
        bounty.status = BountyStatus.ASSIGNED.value
        bounty.notes.append(f"router:{plan.qualification}")
        self.store.save_bounty(bounty)
        return bounty

    def submit_work(
        self,
        bounty_id: str,
        *,
        artifact_hash: str,
        evidence_bundle_path: str | Path,
        result_text: str | None = None,
    ) -> Bounty:
        bounty = self.store.load_bounty(bounty_id)
        require_bounty_status(bounty, ALLOWED["submit_work"], "submit_work")
        if not bounty.implementer_job_id:
            raise BountyStateError("bounty has no Work Exchange job")
        evidence = Path(evidence_bundle_path).expanduser()
        if not evidence.is_dir():
            raise ValidationError("evidence_bundle_path must be a local directory")
        if not artifact_hash.startswith("sha256:"):
            raise ValidationError("artifact_hash must be sha256:<hex>")
        payload = result_text or json.dumps(
            {
                "artifact_hash": artifact_hash,
                "evidence_bundle_path": str(evidence.resolve()),
                "bounty_id": bounty.bounty_id,
            },
            sort_keys=True,
        )
        reject_payment_proof_claims(payload)
        job = self.exchange.submit_result(bounty.implementer_job_id, payload)
        bounty.work_artifact_hash = artifact_hash
        bounty.work_result_text = payload
        bounty.evidence_bundle_path = str(evidence.resolve())
        bounty.status = BountyStatus.WORK_SUBMITTED.value
        bounty.notes.append(f"work_result_hash:{job.result_hash}")
        self.store.save_bounty(bounty)
        return bounty

    def request_review(
        self,
        bounty_id: str,
        *,
        reviewer_did: str,
        price_flop: str | None = None,
        price_micro: int | None = None,
        relationship: str | None = None,
        verdict: str | None = None,
        review_text: str = "",
        evidence_bundle_path: str | Path | None = None,
    ) -> Bounty:
        require_did(reviewer_did)
        bounty = self.store.load_bounty(bounty_id)
        require_bounty_status(bounty, ALLOWED["request_review"], "request_review")
        if not bounty.implementer_did:
            raise BountyStateError("assign an implementer before requesting review")
        if reviewer_did == bounty.implementer_did:
            raise PolicyError("implementer cannot review their own bounty deliverable")
        if reviewer_did == bounty.sponsor_did and not self.config.policy.allow_self_deals:
            raise PolicyError("self-deals are disabled by policy")
        if price_micro is None:
            if price_flop is None:
                price_micro = self.config.fees.reviewer_micro
            else:
                price_micro = parse_flop_to_micro(price_flop)
        if price_micro <= 0:
            raise ValidationError("reviewer price must be positive")
        classified = classify_operator_relationship(
            bounty.implementer_did,
            reviewer_did,
            known_family_dids=self.config.known_family_dids,
        )
        if relationship is not None:
            if relationship not in {"independent", "same_operator", "related", "unknown"}:
                raise ValidationError("invalid operator_relationship")
            if classified == "same_operator" and relationship == "independent":
                raise SafetyError(
                    "cannot present same-operator family DIDs as independent counterparties"
                )
            classified = relationship  # type: ignore[assignment]
        prior_pairs = self.exchange.deal_counterparty_pairs()
        wash_risk = detect_wash_risk(
            buyer_did=bounty.sponsor_did,
            seller_did=reviewer_did,
            prior_pairs=prior_pairs,
        )
        flags = apply_deal_policy(
            buyer_did=bounty.sponsor_did,
            seller_did=reviewer_did,
            relationship=classified,
            policy=self.config.policy,
            wash_risk=wash_risk,
        )
        review_job = self.exchange.post_job(
            buyer_did=bounty.sponsor_did,
            outcome=(
                f"Independent review of bounty {bounty.bounty_id} "
                f"({bounty.spec.title}). Verdict must be APPROVE, REQUEST_CHANGES, or REJECT."
            ),
            service="bounty.review_pull_request",
            budget_micro=price_micro,
        )
        offer = self.exchange.submit_offer(
            job_id=review_job.job_id,
            seller_did=reviewer_did,
            price_micro=price_micro,
            notes="foundry reviewer offer",
        )
        plan = self.exchange.route_job(review_job.job_id)
        if plan.qualification != "QUALIFIED_PLAN" or plan.selected_offer_id != offer.offer_id:
            raise AdapterError("Router did not select this reviewer: " + "; ".join(plan.reasons))
        deal = self.exchange.accept_offer(offer.offer_id, relationship=classified)
        assignment = ReviewAssignment(
            assignment_id=new_id("FLOP-REVIEW"),
            bounty_id=bounty.bounty_id,
            reviewer_did=reviewer_did,
            price_micro=price_micro,
            status="ASSIGNED",
            offer_id=offer.offer_id,
            job_id=review_job.job_id,
            deal_id=deal.deal_id,
            operator_relationship=deal.operator_relationship,
            independent_reputation_eligible=flags["independent_reputation_eligible"],
            independent_fee_volume_eligible=flags["independent_fee_volume_eligible"],
            wash_risk=flags["wash_risk"] or deal.wash_risk,
            tclk_deal_id=deal.tclk_deal_id,
        )
        bounty.review = assignment
        bounty.status = BountyStatus.REVIEW_REQUESTED.value
        self.store.save_bounty(bounty)
        if verdict is not None:
            bounty = self.submit_review(
                bounty.bounty_id,
                verdict=verdict,
                review_text=review_text,
                evidence_bundle_path=evidence_bundle_path,
            )
        return bounty

    def submit_review(
        self,
        bounty_id: str,
        *,
        verdict: str,
        review_text: str = "",
        evidence_bundle_path: str | Path | None = None,
    ) -> Bounty:
        bounty = self.store.load_bounty(bounty_id)
        require_bounty_status(bounty, ALLOWED["submit_review"], "submit_review")
        if not bounty.review or not bounty.review.job_id:
            raise BountyStateError("no review assignment")
        if verdict not in REVIEW_VERDICTS:
            raise ValidationError(f"verdict must be one of {sorted(REVIEW_VERDICTS)}")
        reject_payment_proof_claims(review_text)
        payload = json.dumps(
            {
                "verdict": verdict,
                "summary": review_text or f"Reviewer {verdict}",
                "bounty_id": bounty.bounty_id,
            },
            sort_keys=True,
        )
        job = self.exchange.submit_result(bounty.review.job_id, payload)
        bounty.review.result_text = payload
        bounty.review.result_hash = job.result_hash
        bounty.review.verdict = verdict
        if evidence_bundle_path is not None:
            path = Path(evidence_bundle_path).expanduser().resolve()
            bounty.review.evidence_bundle_path = str(path)
        bounty.review.status = "SUBMITTED"
        bounty.status = BountyStatus.REVIEW_SUBMITTED.value
        if verdict != "APPROVE":
            bounty.status = BountyStatus.FAILED.value
            bounty.notes.append(f"reviewer:{verdict}")
        self.store.save_bounty(bounty)
        return bounty

    def verify(self, bounty_id: str) -> Bounty:
        bounty = self.store.load_bounty(bounty_id)
        require_bounty_status(bounty, ALLOWED["verify"], "verify")
        if bounty.review and bounty.review.verdict and bounty.review.verdict != "APPROVE":
            bounty.status = BountyStatus.FAILED.value
            bounty.bench_result = "FAIL"
            bounty.bench_notes = f"reviewer verdict {bounty.review.verdict}"
            self.store.save_bounty(bounty)
            return bounty
        verdict = self.bench.verify_bounty(bounty)
        bounty.bench_result = verdict.result
        bounty.bench_notes = verdict.notes
        if bounty.implementer_job_id:
            wx_job: Job = self.exchange.verify(bounty.implementer_job_id)
            if wx_job.bench_result != "PASS":
                verdict = BenchVerdict(
                    result="FAIL",
                    evidence_id=verdict.evidence_id,
                    notes=f"{verdict.notes}; Work Exchange Bench adapter: {wx_job.bench_result}",
                    local_exec=False,
                )
                bounty.bench_result = "FAIL"
                bounty.bench_notes = verdict.notes
        if bounty.review and bounty.review.job_id and bounty.status != BountyStatus.FAILED.value:
            # Review job is not the software deliverable; hash-lock only via Exchange stub
            # is skipped because Foundry's Bench adapter looks up the same bounty spec.
            pass
        if bounty.bench_result == "PASS":
            bounty.status = BountyStatus.VERIFIED.value
        else:
            bounty.status = BountyStatus.FAILED.value
        bounty.notes.append(verdict.notes)
        self.store.save_bounty(bounty)
        return bounty

    def settle(self, bounty_id: str) -> tuple[BountyReceiptBundle, Path]:
        if self.config.settlement_backend != "paper":
            raise NotLiveError("settlement_execution is DISABLED for non-paper backends")
        bounty = self.store.load_bounty(bounty_id)
        require_bounty_status(bounty, ALLOWED["settle"], "settle")
        if bounty.bench_result != "PASS" or not bounty.work_artifact_hash:
            raise ValidationError("bounty is not Bench-PASS with an artifact hash")
        if not bounty.implementer_did or bounty.implementer_price_micro is None:
            raise ValidationError("bounty has no implementer deal")
        fees = self.config.fees
        include_author = bool(bounty.spec.author_did)
        include_reviewer = bool(
            bounty.review
            and bounty.review.verdict == "APPROVE"
            and bounty.review.reviewer_did
        )
        include_adapter = bounty.spec.bounty_type == "build_adapter"
        needed = (
            bounty.implementer_price_micro
            + (fees.author_micro if include_author else 0)
            + (bounty.review.price_micro if include_reviewer and bounty.review else 0)
            + fees.bench_validation_micro
            + fees.application_micro
            + fees.management_micro
            + (fees.adapter_publish_micro if include_adapter else 0)
        )
        available = self.exchange.paper_balance(bounty.sponsor_did)
        if available < needed:
            raise ValidationError(
                f"sponsor paper balance {available} micro cannot cover settle total {needed}"
            )
        result_hash = bounty.work_artifact_hash
        completed_at = now_iso()
        key, foundry_did = load_foundry_key(self.store.state_dir)
        legs: list[BountyReceiptLeg] = []

        def _pay(
            *,
            role: str,
            credit_account: str,
            receipt_seller_did: str,
            amount_micro: int,
            service: str,
            reason: str,
            relationship: str,
            independent_rep: bool,
            independent_fee: bool,
            tclk_deal_id: str,
            update_profile: bool,
        ) -> None:
            if amount_micro <= 0:
                return
            self.exchange.transfer(
                debit_account=bounty.sponsor_did,
                credit_account=credit_account,
                amount_micro=amount_micro,
                reason=reason,
                job_id=bounty.implementer_job_id,
            )
            receipt = Receipt(
                job_id=bounty.implementer_job_id or bounty.bounty_id,
                buyer_did=bounty.sponsor_did,
                seller_did=receipt_seller_did,
                operator_relationship=relationship,  # type: ignore[arg-type]
                service=service,
                price_flop=micro_to_flop_string(amount_micro),
                payment_mode="paper",
                tclk_deal_id=tclk_deal_id,
                result_hash=result_hash,
                bench_result=bounty.bench_result or "PASS",
                completed_at=completed_at,
                settlement_status="simulated",
                schema="flop-code-bounty-foundry.receipt.v0.1",
                price_micro=amount_micro,
                independent_reputation_eligible=independent_rep,
                independent_fee_volume_eligible=independent_fee,
            )
            sign_receipt(receipt, key, foundry_did)
            payload = receipt.to_dict()
            verification = verify_receipt(payload)
            self.store.save_receipt_leg(bounty.bounty_id, role, payload)
            legs.append(
                BountyReceiptLeg(
                    role=role,
                    receipt=payload,
                    verification=verification,
                    ledger_reason=reason,
                    amount_micro=amount_micro,
                )
            )
            if not update_profile:
                return
            profile = self.store.load_profile(receipt_seller_did)
            profile.record_completion(
                relationship=relationship,  # type: ignore[arg-type]
                fee_volume_micro=amount_micro,
                receipt_id=receipt.receipt_id,
                job_id=bounty.bounty_id,
                result_hash=result_hash,
                bench_result=bounty.bench_result or "PASS",
                independent_reputation_eligible=independent_rep,
                independent_fee_volume_eligible=independent_fee,
                wash_risk=bounty.implementer_wash_risk,
            )
            self.store.save_profile(profile)

        if include_author and bounty.spec.author_did:
            author_rel = classify_operator_relationship(
                bounty.sponsor_did,
                bounty.spec.author_did,
                known_family_dids=self.config.known_family_dids,
            )
            author_flags = apply_deal_policy(
                buyer_did=bounty.sponsor_did,
                seller_did=bounty.spec.author_did,
                relationship=author_rel,
                policy=self.config.policy,
                wash_risk=False,
            )
            _pay(
                role="author",
                credit_account=bounty.spec.author_did,
                receipt_seller_did=bounty.spec.author_did,
                amount_micro=fees.author_micro,
                service="bounty.author",
                reason="author-fee",
                relationship=author_rel,
                independent_rep=author_flags["independent_reputation_eligible"],
                independent_fee=author_flags["independent_fee_volume_eligible"],
                tclk_deal_id=bounty.implementer_tclk_deal_id or "tclk-paper-foundry-author",
                update_profile=True,
            )
        _pay(
            role="implementer",
            credit_account=bounty.implementer_did,
            receipt_seller_did=bounty.implementer_did,
            amount_micro=bounty.implementer_price_micro,
            service="bounty.implementer",
            reason="implementer-payout",
            relationship=bounty.implementer_relationship or "unknown",
            independent_rep=bounty.implementer_independent_reputation_eligible,
            independent_fee=bounty.implementer_independent_fee_volume_eligible,
            tclk_deal_id=bounty.implementer_tclk_deal_id or "tclk-paper-foundry-implementer",
            update_profile=True,
        )
        if include_reviewer and bounty.review:
            _pay(
                role="reviewer",
                credit_account=bounty.review.reviewer_did,
                receipt_seller_did=bounty.review.reviewer_did,
                amount_micro=bounty.review.price_micro,
                service="bounty.reviewer",
                reason="reviewer-payout",
                relationship=bounty.review.operator_relationship or "unknown",
                independent_rep=bounty.review.independent_reputation_eligible,
                independent_fee=bounty.review.independent_fee_volume_eligible,
                tclk_deal_id=bounty.review.tclk_deal_id or "tclk-paper-foundry-reviewer",
                update_profile=True,
            )
        _pay(
            role="bench_validation",
            credit_account=BENCH_FEE_ACCOUNT,
            receipt_seller_did=foundry_did,
            amount_micro=fees.bench_validation_micro,
            service="bounty.bench_validation",
            reason="bench-validation-fee",
            relationship="same_operator",
            independent_rep=False,
            independent_fee=False,
            tclk_deal_id="tclk-paper-foundry-bench",
            update_profile=False,
        )
        application_total = fees.application_micro + fees.management_micro
        _pay(
            role="application",
            credit_account=FOUNDRY_FEE_ACCOUNT,
            receipt_seller_did=foundry_did,
            amount_micro=application_total,
            service="bounty.application",
            reason="foundry-application-and-management-fee",
            relationship="related",
            independent_rep=False,
            independent_fee=False,
            tclk_deal_id="tclk-paper-foundry-application",
            update_profile=False,
        )
        if include_adapter:
            _pay(
                role="adapter_publish",
                credit_account=ADAPTER_PUBLISH_ACCOUNT,
                receipt_seller_did=foundry_did,
                amount_micro=fees.adapter_publish_micro,
                service="bounty.adapter_publish",
                reason="adapter-publish-fee",
                relationship="related",
                independent_rep=False,
                independent_fee=False,
                tclk_deal_id="tclk-paper-foundry-adapter",
                update_profile=False,
            )

        if not any(leg.role == "implementer" for leg in legs):
            raise ValidationError("settle produced no implementer receipt")
        bundle = BountyReceiptBundle(
            bundle_id=new_id("FLOP-BUNDLE"),
            bounty_id=bounty.bounty_id,
            foundry_did=foundry_did,
            legs=legs,
            created_at=completed_at,
        )
        path = self.store.save_bundle(bundle)
        bounty.status = BountyStatus.SETTLED.value
        bounty.receipt_bundle_id = bundle.bundle_id
        self.store.save_bounty(bounty)
        return bundle, path

    def show(self, bounty_id: str) -> dict[str, Any]:
        bounty = self.store.load_bounty(bounty_id)
        payload: dict[str, Any] = {"bounty": bounty.to_dict()}
        if bounty.receipt_bundle_id:
            bundle = self.store.load_bundle(bounty_id)
            payload["receipt_bundle"] = bundle
            payload["receipt_verifications"] = [
                {"role": leg.get("role"), "ok": (leg.get("verification") or {}).get("ok")}
                for leg in bundle.get("legs") or []
            ]
        return payload

    def balances(self) -> dict[str, int]:
        return self.exchange.balances()
