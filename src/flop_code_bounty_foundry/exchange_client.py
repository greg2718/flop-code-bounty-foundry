"""Work Exchange client protocol and in-process adapter.

Foundry depends on flop-work-exchange for Job, Offer, Deal, Receipt,
PaperSettlement, and operator_relationship. This protocol is the seam used if
the git dependency is later swapped for a remote client.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Protocol

from flop_work_exchange.adapters.bench import StubBenchAdapter
from flop_work_exchange.adapters.router import StubRouterAdapter
from flop_work_exchange.adapters.scout import StubScoutAdapter
from flop_work_exchange.adapters.sentinel import StubSentinelAdapter
from flop_work_exchange.adapters.settlement import PaperSettlement, TestnetSettlement
from flop_work_exchange.adapters.tclk import StubTclkAdapter
from flop_work_exchange.config import ExchangeConfig, FeeSchedule, PolicyConfig
from flop_work_exchange.constants import KNOWN_FAMILY_DIDS
from flop_work_exchange.exceptions import NotLiveError
from flop_work_exchange.exchange import WorkExchange
from flop_work_exchange.identity import ensure_test_identity as ensure_exchange_identity
from flop_work_exchange.models import (
    Deal,
    ExecutionPlan,
    Job,
    Offer,
    SentinelVerdict,
    WorkerCandidate,
)


class WorkExchangeClient(Protocol):
    """Mirrors the Work Exchange public orchestration surface Foundry needs."""

    def exchange_did(self) -> str: ...

    def credit_paper(self, account: str, amount_flop: str, reason: str = "paper-seed") -> None: ...

    def post_job(
        self,
        *,
        buyer_did: str,
        outcome: str,
        service: str,
        budget_flop: str | None = None,
        budget_micro: int | None = None,
    ) -> Job: ...

    def submit_offer(
        self,
        *,
        job_id: str,
        seller_did: str,
        price_flop: str | None = None,
        price_micro: int | None = None,
        notes: str = "",
    ) -> Offer: ...

    def route_job(self, job_id: str) -> ExecutionPlan: ...

    def accept_offer(self, offer_id: str, *, relationship: str | None = None) -> Deal: ...

    def submit_result(
        self, job_id: str, result_text: str, result_hash: str | None = None
    ) -> Job: ...

    def verify(self, job_id: str) -> Job: ...

    def screen(self, artifact_type: str, artifact: dict[str, Any]) -> SentinelVerdict: ...

    def find_candidates(self, job_id: str) -> list[WorkerCandidate]: ...

    def transfer(
        self,
        *,
        debit_account: str,
        credit_account: str,
        amount_micro: int,
        reason: str,
        job_id: str | None = None,
    ) -> None: ...

    def paper_balance(self, account: str) -> int: ...

    def balances(self) -> dict[str, int]: ...

    def load_job(self, job_id: str) -> Job: ...

    def load_deal(self, deal_id: str) -> Deal: ...

    def deal_counterparty_pairs(self) -> list[tuple[str, str]]: ...


class InProcessWorkExchangeClient:
    """In-process adapter over flop_work_exchange.WorkExchange.

    Nested under Foundry ``state_dir/work-exchange/`` so production Exchange
    identity is never auto-created. Work Exchange posting/orchestration fees are
    zeroed; Foundry owns bounty fee policy and multi-leg paper settlement.
    """

    def __init__(
        self,
        state_dir: Path,
        *,
        bench: StubBenchAdapter | None = None,
        scout: StubScoutAdapter | None = None,
        router: StubRouterAdapter | None = None,
        sentinel: StubSentinelAdapter | None = None,
        settlement_backend: str = "paper",
        policy: PolicyConfig | None = None,
        known_family_dids: frozenset[str] | None = None,
    ) -> None:
        wx_dir = state_dir / "work-exchange"
        backend: Literal["paper", "testnet"] = (
            "testnet" if settlement_backend == "testnet" else "paper"
        )
        config = ExchangeConfig(
            state_dir=wx_dir,
            payment_mode="paper",
            settlement_backend=backend,
            fees=FeeSchedule(
                job_posting_micro=0,
                orchestration_micro=0,
                completion_micro=0,
                bench_validation_micro=0,
            ),
            policy=policy or PolicyConfig(),
            known_family_dids=known_family_dids or KNOWN_FAMILY_DIDS,
        )
        self._wx = WorkExchange(
            config,
            scout=scout or StubScoutAdapter(),
            router=router or StubRouterAdapter(),
            sentinel=sentinel or StubSentinelAdapter(),
            bench=bench or StubBenchAdapter(),
            tclk=StubTclkAdapter(),
        )
        ensure_exchange_identity(wx_dir)
        self.exchange = self._wx

    def exchange_did(self) -> str:
        return self._wx.exchange_did()

    def credit_paper(self, account: str, amount_flop: str, reason: str = "paper-seed") -> None:
        self._wx.credit_paper(account, amount_flop, reason)

    def post_job(
        self,
        *,
        buyer_did: str,
        outcome: str,
        service: str,
        budget_flop: str | None = None,
        budget_micro: int | None = None,
    ) -> Job:
        return self._wx.post_job(
            buyer_did=buyer_did,
            outcome=outcome,
            service=service,
            budget_flop=budget_flop,
            budget_micro=budget_micro,
        )

    def submit_offer(
        self,
        *,
        job_id: str,
        seller_did: str,
        price_flop: str | None = None,
        price_micro: int | None = None,
        notes: str = "",
    ) -> Offer:
        return self._wx.submit_offer(
            job_id=job_id,
            seller_did=seller_did,
            price_flop=price_flop,
            price_micro=price_micro,
            notes=notes,
        )

    def route_job(self, job_id: str) -> ExecutionPlan:
        return self._wx.route_job(job_id)

    def accept_offer(self, offer_id: str, *, relationship: str | None = None) -> Deal:
        return self._wx.accept_offer(offer_id, relationship=relationship)

    def submit_result(self, job_id: str, result_text: str, result_hash: str | None = None) -> Job:
        return self._wx.submit_result(job_id, result_text, result_hash)

    def verify(self, job_id: str) -> Job:
        return self._wx.verify(job_id)

    def screen(self, artifact_type: str, artifact: dict[str, Any]) -> SentinelVerdict:
        return self._wx.sentinel.screen(artifact_type, artifact)

    def find_candidates(self, job_id: str) -> list[WorkerCandidate]:
        return self._wx.find_candidates(job_id)

    def transfer(
        self,
        *,
        debit_account: str,
        credit_account: str,
        amount_micro: int,
        reason: str,
        job_id: str | None = None,
    ) -> None:
        self._wx.settlement.transfer(
            debit_account=debit_account,
            credit_account=credit_account,
            amount_micro=amount_micro,
            reason=reason,
            job_id=job_id,
        )

    def paper_balance(self, account: str) -> int:
        return self._wx.store.paper_balance(account)

    def balances(self) -> dict[str, int]:
        return self._wx.balances()

    def load_job(self, job_id: str) -> Job:
        return self._wx.store.load_job(job_id)

    def load_deal(self, deal_id: str) -> Deal:
        return self._wx.store.load_deal(deal_id)

    def deal_counterparty_pairs(self) -> list[tuple[str, str]]:
        return self._wx.store.deal_counterparty_pairs()

    def require_paper_settlement(self) -> PaperSettlement:
        if isinstance(self._wx.settlement, TestnetSettlement):
            raise NotLiveError("TestnetSettlement is not live; settlement_execution is DISABLED")
        if not isinstance(self._wx.settlement, PaperSettlement):
            raise NotLiveError("only PaperSettlement is available")
        return self._wx.settlement
