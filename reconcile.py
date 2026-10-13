"""Reconciliation agent CLI.

  python reconcile.py run    --ledger L.csv --statement S.csv --out runs/sep [--no-llm]
  python reconcile.py status --run runs/sep
  python reconcile.py review --run runs/sep --reviewer "Your Name"
  python reconcile.py finalize --run runs/sep --reviewer "Your Name"
"""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal

from recon.agent import MODEL, AgentError, rule_based_draft, run_agent
from recon.checks import proposal_warnings, summary_warnings
from recon.loader import LoadError, file_sha256, load_records
from recon.matcher import Config, reconcile
from recon.report import NotReviewed, finalize
from recon.review import review
from recon.state import RunState


def cmd_run(args) -> int:
    try:
        ledger = load_records(args.ledger, "ledger")
        statement = load_records(args.statement, "statement")
    except LoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    cfg = Config(date_tolerance_days=args.date_tolerance,
                 possible_match_window_days=args.window,
                 amount_tolerance_pct=Decimal(str(args.amount_tolerance)))
    result = reconcile(ledger, statement, cfg)
    state = RunState.create(
        args.out, ledger=ledger, statement=statement,
        matches=result.matches, exceptions=result.exceptions,
        inputs={"ledger": {"path": args.ledger, "sha256": file_sha256(args.ledger)},
                "statement": {"path": args.statement, "sha256": file_sha256(args.statement)}},
        config={"date_tolerance_days": cfg.date_tolerance_days,
                "possible_match_window_days": cfg.possible_match_window_days,
                "amount_tolerance_pct": str(cfg.amount_tolerance_pct)},
    )
    print(f"Loaded {len(ledger)} ledger and {len(statement)} statement records.")
    print(f"Matched {len(result.matches)} pairs; {len(result.exceptions)} exceptions.\n")
    for e in result.exceptions:
        print(f"  {e.id}  {e.type:<23} {e.severity:<6} {e.amount:>10}  {' '.join(e.ledger_ids + e.statement_ids)}")

    use_llm = not args.no_llm
    if use_llm and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("\nNo ANTHROPIC_API_KEY set; the SDK will try other credential sources (e.g. `ant auth login`).")
    print("\nDrafting exception resolutions " + ("with Claude..." if use_llm else "with the rule-based baseline..."))
    try:
        meta = run_agent(state, model=args.model, effort=args.effort) if use_llm else rule_based_draft(state)
    except AgentError as exc:
        print(f"agent stopped: {exc}", file=sys.stderr)
        return 1
    if meta.get("usage"):
        print(f"  {meta['tool_calls']} tool calls, {meta['usage']['input_tokens']} in / "
              f"{meta['usage']['output_tokens']} out tokens")

    print(f"\nDraft ready in {args.out}. Nothing is final until a person reviews it.")
    print(f"Next: python reconcile.py review --run {args.out} --reviewer \"Your Name\"")
    return 0


def cmd_status(args) -> int:
    state = RunState.load(args.run)
    print(f"Status: {state.status}")
    agent = state.data.get("agent", {})
    print(f"Drafted by: {agent.get('model') or agent.get('mode', '-')}")
    print(f"Matches: {len(state.data['matches'])}   Exceptions: {len(state.exceptions)}\n")
    for e in state.exceptions:
        p, h = e.get("ai_proposal") or {}, e.get("human_decision") or {}
        flags = len(proposal_warnings(state, e))
        print(f"  {e['id']}  {e['type']:<23} {e['amount']:>10}  AI: {p.get('action', '-'):<28} "
              f"human: {h.get('decision', 'PENDING'):<10}{'  (' + str(flags) + ' checks)' if flags else ''}")
    s = state.data["summary"]
    print(f"\nSummary: {'approved by ' + s['approved']['by'] if s.get('approved') else 'pending review'}"
          f"; {len(summary_warnings(state))} automated check(s) on the draft")
    return 0


def cmd_review(args) -> int:
    state = RunState.load(args.run)
    return 0 if review(state, args.reviewer) else 1


def cmd_finalize(args) -> int:
    state = RunState.load(args.run)
    try:
        for p in finalize(state, args.reviewer):
            print(p)
    except NotReviewed as exc:
        print(f"refusing to finalize: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Two-file reconciliation with an AI exception agent and human sign-off.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="match records, flag exceptions, and draft resolutions")
    r.add_argument("--ledger", required=True)
    r.add_argument("--statement", required=True)
    r.add_argument("--out", required=True, help="new directory for this run")
    r.add_argument("--no-llm", action="store_true", help="use the rule-based baseline instead of Claude")
    r.add_argument("--model", default=MODEL)
    r.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    r.add_argument("--date-tolerance", type=int, default=3, help="days (default 3)")
    r.add_argument("--window", type=int, default=10, help="possible-match window in days (default 10)")
    r.add_argument("--amount-tolerance", type=float, default=0.05, help="fraction for near-miss amounts (default 0.05)")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("status", help="show where a run stands")
    s.add_argument("--run", required=True)
    s.set_defaults(func=cmd_status)

    v = sub.add_parser("review", help="human review: decide each exception, approve the summary, finalize")
    v.add_argument("--run", required=True)
    v.add_argument("--reviewer", required=True)
    v.set_defaults(func=cmd_review)

    f = sub.add_parser("finalize", help="finalize a fully reviewed run (refuses otherwise)")
    f.add_argument("--run", required=True)
    f.add_argument("--reviewer", required=True)
    f.set_defaults(func=cmd_finalize)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
