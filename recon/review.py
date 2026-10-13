"""Human-review gate.

Nothing becomes final until a named reviewer has made a decision on every
exception and approved (optionally after editing) the summary. Review can be
stopped and resumed; decisions are saved as they are made.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from .actions import ACTIONS
from .checks import proposal_warnings, summary_warnings
from .report import finalize
from .state import FINAL, IN_REVIEW, RunState, now


class Quit(Exception):
    pass


def _ask(prompt: str, choices: str | None = None) -> str:
    while True:
        try:
            ans = input(prompt).strip()
        except EOFError:
            raise Quit
        if choices is None:
            return ans
        if ans.lower() in list(choices):
            return ans.lower()
        print(f"  please enter one of: {', '.join(choices)}")


def _ask_note(prompt: str) -> str:
    while True:
        note = _ask(prompt)
        if note:
            return note
        print("  a note is required here (it goes in the audit log)")


def _show_exception(state: RunState, ex: dict, n: int, total: int) -> None:
    print("\n" + "=" * 72)
    print(f"[{n}/{total}] {ex['id']}  {ex['type']}  severity={ex['severity']}  amount={ex['amount']}")
    print(f"  {ex['detail']}")
    for rid in ex["ledger_ids"] + ex["statement_ids"]:
        r = state.record(rid)
        print(f"  {r.id}  {r.date}  {str(r.amount):>11}  {r.reference:<9} {r.description}")
    p = ex.get("ai_proposal")
    if p:
        print(f"\n  AI recommends: {p['action']}  (confidence: {p['confidence']}, by {p['author']})")
        for line in textwrap.wrap(p["rationale"], 66):
            print(f"    {line}")
        if p.get("suggested_entry"):
            print(f"    Suggested entry: {p['suggested_entry']}")
    for w in proposal_warnings(state, ex):
        print(f"  ! CHECK: {w}")


def _decide(state: RunState, ex: dict, reviewer: str) -> bool:
    """Returns False if the reviewer skipped this one."""
    p = ex.get("ai_proposal")
    options = "[a]ccept AI  [o]verride  [e]scalate  [s]kip  [q]uit" if p else "[o]verride  [e]scalate  [s]kip  [q]uit"
    choice = _ask(f"\n  {options}: ", "aoesq" if p else "oesq")
    if choice == "q":
        raise Quit
    if choice == "s":
        return False
    if choice == "a":
        decision, action = "accepted", p["action"]
        note = _ask("  optional note: ")
    elif choice == "o":
        names = list(ACTIONS)
        for i, a in enumerate(names, 1):
            print(f"    {i}. {a}: {ACTIONS[a]}")
        idx = _ask("  action number: ", [str(i) for i in range(1, len(names) + 1)])
        decision, action = "overridden", names[int(idx) - 1]
        note = _ask_note("  why are you overriding? ")
    else:
        decision, action = "escalated", "escalated"
        note = _ask_note("  escalate to whom / why? ")

    ex["human_decision"] = {"decision": decision, "action": action, "note": note,
                            "reviewer": reviewer, "at": now(),
                            "ai_action": p["action"] if p else None}
    if p:
        p["status"] = decision.upper()
    state.save()
    state.log("human_decision", exception_id=ex["id"], reviewer=reviewer, decision=decision,
              action=action, ai_action=p["action"] if p else None, note=note)
    return True


def _review_summary(state: RunState, reviewer: str) -> bool:
    summary = state.data["summary"]
    text = summary.get("draft") or ""
    edited = False
    overrides = sum(1 for e in state.exceptions if e["human_decision"]["decision"] != "accepted")
    draft_path = state.dir / "summary_draft.md"

    while True:
        print("\n" + "=" * 72 + "\nSUMMARY DRAFT" + (" (edited)" if edited else f" by {summary.get('author')}"))
        print("-" * 72 + f"\n{text}\n" + "-" * 72)
        warns = summary_warnings(state, text)
        if overrides:
            warns.append(f"You overrode or escalated {overrides} AI recommendation(s); "
                         "check the narrative still matches your decisions.")
        for w in warns:
            print(f"  ! CHECK: {w}")

        choice = _ask("\n  [a]pprove  [e]dit  [r]eject  [q]uit: ", "aerq")
        if choice == "q":
            raise Quit
        if choice == "e":
            draft_path.write_text(text, encoding="utf-8")
            print(f"  Edit {draft_path} in any editor, save, then press Enter here.")
            _ask("  ")
            text = draft_path.read_text(encoding="utf-8").strip()
            edited = True
            continue
        if choice == "r":
            note = _ask_note("  reason for rejecting: ")
            state.log("summary_rejected", reviewer=reviewer, note=note)
            print("  Summary rejected. The run stays in review; re-run the agent or edit the summary to continue.")
            return False

        ack = ""
        if warns:
            ack = _ask_note(f"  {len(warns)} check(s) outstanding. Note why it's OK to approve anyway: ")
        summary["approved"] = {"by": reviewer, "at": now(), "text": text, "edited": edited,
                               "warnings_acknowledged": warns, "ack_note": ack}
        state.save()
        state.log("summary_approved", reviewer=reviewer, edited=edited, warnings=len(warns), ack_note=ack)
        return True


def review(state: RunState, reviewer: str) -> bool:
    """Interactive review. Returns True once the run is final."""
    if state.status == FINAL:
        print("This run is already final.")
        return True
    if state.data["summary"].get("draft") is None:
        print("The agent hasn't produced a draft yet; run `reconcile.py run` first.")
        return False
    if state.status != IN_REVIEW:
        state.data["status"] = IN_REVIEW
        state.save()
    state.log("review_session_started", reviewer=reviewer)

    try:
        pending = [e for e in state.exceptions if not e.get("human_decision")]
        total = len(state.exceptions)
        print(f"\n{total - len(pending)}/{total} exceptions already decided. Reviewing as {reviewer}.")
        for ex in pending:
            n = state.exceptions.index(ex) + 1
            _show_exception(state, ex, n, total)
            _decide(state, ex, reviewer)

        left = [e["id"] for e in state.exceptions if not e.get("human_decision")]
        if left:
            print(f"\n{len(left)} exception(s) still need a decision: {', '.join(left)}. Run review again to continue.")
            return False

        if not (state.data["summary"].get("approved")):
            if not _review_summary(state, reviewer):
                return False

        if _ask("\n  Finalize this reconciliation? Nothing can change after this. [y/n]: ", "yn") != "y":
            print("  Not finalized. Decisions are saved; run review again to finalize.")
            return False
    except Quit:
        print("\n  Stopped. Decisions so far are saved; run review again to pick up where you left off.")
        state.log("review_session_paused", reviewer=reviewer)
        return False

    paths = finalize(state, reviewer)
    print("\nFinal outputs:")
    for p in paths:
        print(f"  {Path(p)}")
    return True
