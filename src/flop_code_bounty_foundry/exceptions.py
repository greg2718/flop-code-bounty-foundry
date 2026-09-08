from __future__ import annotations

from flop_work_exchange.exceptions import (
    AdapterError,
    InsufficientFundsError,
    IsolationError,
    NotLiveError,
    PolicyError,
    SafetyError,
    StateError,
    ValidationError,
    WorkExchangeError,
)


class FoundryError(WorkExchangeError):
    """Base error for FLOP Code Bounty Foundry."""


class BountyStateError(StateError, FoundryError):
    """Illegal bounty lifecycle transition or missing local state."""


class InertUrlError(SafetyError, FoundryError):
    """Attempted to fetch or clone a URL recorded as inert bounty metadata."""


__all__ = [
    "AdapterError",
    "BountyStateError",
    "FoundryError",
    "InertUrlError",
    "InsufficientFundsError",
    "IsolationError",
    "NotLiveError",
    "PolicyError",
    "SafetyError",
    "StateError",
    "ValidationError",
    "WorkExchangeError",
]
