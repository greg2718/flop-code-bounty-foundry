from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from flop_work_exchange.receipts import verify_receipt

from flop_code_bounty_foundry import __version__
from flop_code_bounty_foundry.config import load_config
from flop_code_bounty_foundry.constants import DEFAULT_PRODUCTION_STATE
from flop_code_bounty_foundry.demo import run_demo
from flop_code_bounty_foundry.exceptions import WorkExchangeError
from flop_code_bounty_foundry.foundry import CodeBountyFoundry
from flop_code_bounty_foundry.identity import create_test_identity, load_identity_meta
from flop_code_bounty_foundry.models import load_spec_file


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _require_state_dir(args: argparse.Namespace) -> Path:
    if not getattr(args, "state_dir", None):
        raise SystemExit("--state-dir is required (demos/tests should pass a temp dir)")
    return Path(args.state_dir)


def _open(args: argparse.Namespace) -> CodeBountyFoundry:
    state_dir = _require_state_dir(args)
    config_path = Path(args.config) if getattr(args, "config", None) else None
    return CodeBountyFoundry(load_config(state_dir, config_path))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flop-code-bounty-foundry",
        description=(
            "Turn software issues into paid paper-FLOP jobs. "
            "Settlement execution is DISABLED. URLs in specs are inert metadata."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"flop-code-bounty-foundry {__version__}"
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        help=(
            "Local state directory. Required for write commands. Production path is "
            f"{DEFAULT_PRODUCTION_STATE}; demos and tests must pass a temp dir."
        ),
    )
    parser.add_argument("--config", type=Path, help="Fee/policy config (JSON, YAML, or TOML)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    identity = sub.add_parser("identity", help="Foundry Ed25519 / did:key identity")
    identity_sub = identity.add_subparsers(dest="identity_cmd", required=True)
    identity_sub.add_parser("init", help="Create a test-only identity in --state-dir")
    identity_sub.add_parser("show", help="Show public identity metadata")

    create = sub.add_parser("create-bounty", help="Sponsor posts a bounty spec")
    create.add_argument("--sponsor-did", required=True)
    create.add_argument("--spec", type=Path, required=True, help="Bounty spec JSON or YAML")
    create.add_argument("--author-did", default=None)

    sub.add_parser("list-bounties", help="List local bounties")

    assign = sub.add_parser("assign", help="Router-shaped implementer assignment")
    assign.add_argument("--bounty-id", required=True)
    assign.add_argument("--implementer-did", required=True)
    assign.add_argument("--price-flop", required=True)
    assign.add_argument(
        "--operator-relationship",
        choices=["independent", "same_operator", "related", "unknown"],
        default=None,
    )
    assign.add_argument("--notes", default="")

    submit = sub.add_parser("submit-work", help="Implementer submits artifact hash + evidence dir")
    submit.add_argument("--bounty-id", required=True)
    submit.add_argument("--artifact-hash", required=True, help="sha256:<hex>")
    submit.add_argument(
        "--evidence-dir",
        type=Path,
        required=True,
        help="Local evidence bundle directory (never a URL)",
    )

    review = sub.add_parser(
        "request-review",
        help="Assign an independent reviewer (optional submit)",
    )
    review.add_argument("--bounty-id", required=True)
    review.add_argument("--reviewer-did", required=True)
    review.add_argument("--price-flop", default=None)
    review.add_argument(
        "--operator-relationship",
        choices=["independent", "same_operator", "related", "unknown"],
        default=None,
    )
    review.add_argument(
        "--verdict",
        choices=["APPROVE", "REQUEST_CHANGES", "REJECT"],
        default=None,
        help="If set, also submit the review result",
    )
    review.add_argument("--review-text", default="")

    verify = sub.add_parser("verify", help="Bench-adapter verify against the bounty spec")
    verify.add_argument("--bounty-id", required=True)

    settle = sub.add_parser("settle", help="Paper-settle paid legs and sign receipt bundle")
    settle.add_argument("--bounty-id", required=True)

    show = sub.add_parser("show", help="Show bounty and verified receipt bundle")
    show.add_argument("--bounty-id", required=True)

    verify_file = sub.add_parser("verify-receipt", help="Verify a receipt JSON file")
    verify_file.add_argument("receipt", type=Path)

    credit = sub.add_parser("paper-credit", help="Seed a paper ledger account (simulation only)")
    credit.add_argument("--account", required=True)
    credit.add_argument("--amount-flop", required=True)
    credit.add_argument("--reason", default="paper-seed")

    sub.add_parser("balances", help="Show paper ledger balances")

    demo = sub.add_parser("demo", help="Run one full paper bounty end-to-end")
    demo.add_argument(
        "--state-dir",
        dest="demo_state_dir",
        type=Path,
        default=None,
        help="Optional temp/state dir; created if omitted",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return _dispatch(args)
    except WorkExchangeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    if args.cmd == "demo":
        state_dir = args.demo_state_dir or args.state_dir
        if state_dir is None:
            state_dir = Path(tempfile.mkdtemp(prefix="flop-code-bounty-foundry-demo-"))
        result = run_demo(Path(state_dir))
        _print_json(result)
        return 0 if result.get("ok") else 2

    if args.cmd == "identity":
        state_dir = _require_state_dir(args)
        if args.identity_cmd == "init":
            _print_json(create_test_identity(state_dir))
            return 0
        if args.identity_cmd == "show":
            _print_json(load_identity_meta(state_dir))
            return 0

    if args.cmd == "verify-receipt":
        payload = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
        _print_json(verify_receipt(payload))
        return 0

    foundry = _open(args)
    if args.cmd == "create-bounty":
        spec = load_spec_file(Path(args.spec))
        if args.author_did:
            spec.author_did = args.author_did
        bounty = foundry.create_bounty(sponsor_did=args.sponsor_did, spec=spec)
        _print_json(bounty.to_dict())
        return 0
    if args.cmd == "list-bounties":
        _print_json([bounty.to_dict() for bounty in foundry.list_bounties()])
        return 0
    if args.cmd == "assign":
        bounty = foundry.assign(
            args.bounty_id,
            implementer_did=args.implementer_did,
            price_flop=args.price_flop,
            relationship=args.operator_relationship,
            notes=args.notes,
        )
        _print_json(bounty.to_dict())
        return 0
    if args.cmd == "submit-work":
        bounty = foundry.submit_work(
            args.bounty_id,
            artifact_hash=args.artifact_hash,
            evidence_bundle_path=args.evidence_dir,
        )
        _print_json(bounty.to_dict())
        return 0
    if args.cmd == "request-review":
        bounty = foundry.request_review(
            args.bounty_id,
            reviewer_did=args.reviewer_did,
            price_flop=args.price_flop,
            relationship=args.operator_relationship,
            verdict=args.verdict,
            review_text=args.review_text,
        )
        _print_json(bounty.to_dict())
        return 0
    if args.cmd == "verify":
        _print_json(foundry.verify(args.bounty_id).to_dict())
        return 0
    if args.cmd == "settle":
        bundle, path = foundry.settle(args.bounty_id)
        payload = bundle.to_dict()
        payload["bundle_path"] = str(path)
        _print_json(payload)
        return 0
    if args.cmd == "show":
        _print_json(foundry.show(args.bounty_id))
        return 0
    if args.cmd == "paper-credit":
        foundry.credit_paper(args.account, args.amount_flop, args.reason)
        _print_json({"ok": True, "account": args.account, "balances": foundry.balances()})
        return 0
    if args.cmd == "balances":
        _print_json(foundry.balances())
        return 0
    raise SystemExit(f"unknown command: {args.cmd}")
