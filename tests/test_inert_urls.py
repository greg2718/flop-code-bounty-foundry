from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from flop_code_bounty_foundry.adapters.bench import InertUrlError, looks_like_url, reject_url_fetch
from flop_code_bounty_foundry.exceptions import SafetyError
from tests.helpers import docs_spec, fresh_dids, make_foundry


def test_repo_url_is_recorded_not_fetched(tmp_path: Path) -> None:
    foundry = make_foundry(tmp_path)
    sponsor = fresh_dids(1)[0]
    foundry.credit_paper(sponsor, "20")
    spec = docs_spec()
    spec.repo_url = "https://github.com/greg2718/flop-work-exchange"
    with patch("urllib.request.urlopen") as urlopen:
        bounty = foundry.create_bounty(sponsor_did=sponsor, spec=spec)
        urlopen.assert_not_called()
    assert bounty.spec.repo_url == spec.repo_url
    assert bounty.sentinel_status in {"ALLOW", "REVIEW"}


def test_bench_procedure_rejects_url_path() -> None:
    with pytest.raises(InertUrlError):
        reject_url_fetch({"adapter": "file_exists", "path": "https://evil.example/README.md"})
    assert looks_like_url("https://github.com/greg2718/flop-bench") is True


def test_local_command_not_self_authorized(tmp_path: Path) -> None:
    from flop_code_bounty_foundry.adapters.bench import run_passive_step

    with pytest.raises(SafetyError, match="local command"):
        run_passive_step(
            {"adapter": "local_command", "argv": ["echo", "hi"], "cwd": str(tmp_path)},
            tmp_path,
        )
