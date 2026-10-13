"""Final outputs. `finalize` is the only code path that sets status FINAL, and
it refuses unless every exception has a human decision and the summary has
been approved by a person."""

from __future__ import annotations

import csv
from decimal import Decimal

from .state import FINAL, RunState, now

OPEN_ACTIONS = {"escalated", "investigate_further", "contact_bank"}


class NotReviewed(RuntimeError):
    pass


def _check_ready(state: RunState) -> None:
    undecided = [e["id"] for e in state.exceptions if not e.get("human_decision")]
    if undecided:
        raise NotReviewed(f"exceptions without a human decision: {', '.join(undecided)}")
    if not state.data["summary"].get("approved"):
        raise NotReviewed("summary has not been approved by a reviewer")


def finalize(state: RunState, reviewer: str) -> list[str]:
    if state.status == FINAL:
        raise NotReviewed("run is already final")
    _check_ready(state)

    out = state.dir / "final"
    out.mkdir(exist_ok=True)
    exs = state.exceptions
    matches = state.data["matches"]
    approved = state.data["summary"]["approved"]
    agent = state.data.get("agent", {})

    with_ai = [e for e in exs if e.get("ai_proposal")]
    accepted = sum(1 for e in with_ai if e["human_decision"]["decision"] == "accepted")
    agreement = f"{accepted}/{len(with_ai)} ({accepted / len(with_ai):.0%})" if with_ai else "n/a"
    n_ledger = len(state.data["ledger"])
    match_rate = f"{len(matches)}/{n_ledger} ledger records ({len(matches) / n_ledger:.0%})" if n_ledger else "n/a"
    open_items = [e for e in exs if e["human_decision"]["action"] in OPEN_ACTIONS]
    unreconciled = sum((Decimal(e["difference"]) for e in exs), Decimal("0"))
    ledger_total = sum((Decimal(r["amount"]) for r in state.data["ledger"]), Decimal("0"))
    stmt_total = sum((Decimal(r["amount"]) for r in state.data["statement"]), Decimal("0"))
    ties_out = unreconciled == ledger_total - stmt_total
    proof = (f"ledger {ledger_total:.2f} - bank {stmt_total:.2f} = {ledger_total - stmt_total:.2f}, "
             f"equal to the sum of exception differences" if ties_out else
             f"DOES NOT TIE: ledger - bank = {ledger_total - stmt_total:.2f} but exceptions sum to {unreconciled:.2f}")

    lines = [
        "# Bank reconciliation report",
        "",
        f"- **Status:** FINAL, approved by {approved['by']} at {approved['at']}",
        f"- **Prepared by:** {agent.get('model') or 'rule-based baseline'} (draft); "
        f"reviewed by {reviewer}",
        f"- **Inputs:** " + "; ".join(f"{k} `{v['path']}` (sha256 {v['sha256'][:12]}...)"
                                     for k, v in state.data["inputs"].items()),
        f"- **Matched:** {match_rate}",
        f"- **Exceptions:** {len(exs)} ({len(open_items)} still open)",
        f"- **Net unreconciled difference (ledger minus bank):** {unreconciled:.2f}",
        f"- **Tie-out:** {proof}",
        f"- **Reviewer agreed with AI recommendation:** {agreement}",
        "",
        "## Summary",
        "",
        approved["text"],
        "",
        "## Exception decisions",
        "",
        "| ID | Type | Severity | Amount | AI recommended | Final decision | Reviewer note |",
        "|---|---|---|---:|---|---|---|",
    ]
    for e in exs:
        h, p = e["human_decision"], e.get("ai_proposal") or {}
        if h["decision"] == "accepted":
            final = h["action"]
        elif h["decision"] == "escalated":
            final = "**escalated**"
        else:
            final = f"**{h['action']}** (overridden)"
        note = (h.get("note") or "").replace("|", "/")
        lines.append(f"| {e['id']} | {e['type']} | {e['severity']} | {e['amount']} | "
                     f"{p.get('action', '-')} | {final} | {note} |")
    if open_items:
        lines += ["", "## Open items carried forward", ""]
        lines += [f"- {e['id']} {e['type']} {e['amount']}: {e['human_decision']['action']}"
                  f" ({e['human_decision'].get('note') or 'no note'})" for e in open_items]
    if approved.get("warnings_acknowledged"):
        lines += ["", "## Automated checks the reviewer acknowledged", ""]
        lines += [f"- {w}" for w in approved["warnings_acknowledged"]]
        lines += [f"- Reviewer note: {approved['ack_note']}"]
    lines += ["", f"_Generated {now()}. Full trail in `audit_log.jsonl`._", ""]

    report = out / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")

    exc_csv = out / "exceptions.csv"
    with open(exc_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "type", "severity", "amount", "difference", "ledger_ids", "statement_ids",
                    "ai_action", "ai_confidence", "ai_rationale", "decision", "final_action",
                    "reviewer", "note", "decided_at"])
        for e in exs:
            p, h = e.get("ai_proposal") or {}, e["human_decision"]
            w.writerow([e["id"], e["type"], e["severity"], e["amount"], e["difference"],
                        " ".join(e["ledger_ids"]), " ".join(e["statement_ids"]),
                        p.get("action", ""), p.get("confidence", ""), p.get("rationale", ""),
                        h["decision"], h["action"], h["reviewer"], h.get("note", ""), h["at"]])

    match_csv = out / "matches.csv"
    with open(match_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ledger_id", "statement_id", "rule", "date_diff_days", "description_similarity"])
        for m in matches:
            w.writerow([m["ledger_id"], m["statement_id"], m["rule"], m["date_diff_days"],
                        m["description_similarity"]])

    state.data["status"] = FINAL
    state.data["finalized"] = {"by": reviewer, "at": now()}
    state.save()
    state.log("finalized", reviewer=reviewer, agreement=agreement, open_items=len(open_items))
    return [str(report), str(exc_csv), str(match_csv), str(state.dir / "audit_log.jsonl")]
