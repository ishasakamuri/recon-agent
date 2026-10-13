"""Deterministic checks on AI output, shown to the reviewer as warnings.

The reviewer is the final control, but they shouldn't have to re-derive every
number by hand. These checks point them at the places where the draft is most
likely to be wrong: figures that don't trace to the data, IDs that don't
exist, high-risk items the summary skipped, and actions that don't fit the
exception type.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .actions import COMPATIBLE
from .state import RunState

_MONEY = re.compile(r"\(?-?\$?-?\d[\d,]*\.\d{2}\)?")
_IDS = re.compile(r"\b(?:EX|L|S)-\d{3}\b")


def _known_ids(state: RunState) -> set[str]:
    return ({e["id"] for e in state.exceptions}
            | {r["id"] for r in state.data["ledger"]}
            | {r["id"] for r in state.data["statement"]})


def known_amounts(state: RunState) -> set[Decimal]:
    """Every figure a correct summary could legitimately quote."""
    amounts: set[Decimal] = set()
    ledger = [Decimal(r["amount"]) for r in state.data["ledger"]]
    stmt = [Decimal(r["amount"]) for r in state.data["statement"]]
    amounts |= {abs(a) for a in ledger + stmt}
    amounts |= {abs(sum(ledger)), abs(sum(stmt)), abs(sum(ledger) - sum(stmt))}
    exs = state.exceptions
    amounts |= {Decimal(e["amount"]) for e in exs} | {abs(Decimal(e["difference"])) for e in exs}
    amounts.add(sum((Decimal(e["amount"]) for e in exs), Decimal("0")))
    amounts.add(abs(sum((Decimal(e["difference"]) for e in exs), Decimal("0"))))
    for kind in {e["type"] for e in exs}:
        amounts.add(sum((Decimal(e["amount"]) for e in exs if e["type"] == kind), Decimal("0")))
    for sev in ("high", "medium", "low"):
        amounts.add(sum((Decimal(e["amount"]) for e in exs if e["severity"] == sev), Decimal("0")))
    return {a.quantize(Decimal("0.01")) for a in amounts}


def _parse_money(token: str) -> Decimal | None:
    try:
        return abs(Decimal(re.sub(r"[^\d.]", "", token))).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def proposal_warnings(state: RunState, ex: dict) -> list[str]:
    p = ex.get("ai_proposal")
    if not p:
        return ["No AI proposal: the agent skipped this exception."]
    warns = []
    if p["action"] not in COMPATIBLE[ex["type"]]:
        warns.append(f"Action '{p['action']}' is unusual for {ex['type']}.")
    if p["confidence"] == "low":
        warns.append("The agent marked this low confidence.")
    cited = set(_IDS.findall(p["rationale"]))
    bogus = cited - _known_ids(state)
    if bogus:
        warns.append(f"Rationale cites IDs that don't exist: {', '.join(sorted(bogus))}.")
    if not cited:
        warns.append("Rationale doesn't cite any record IDs.")
    known = known_amounts(state)
    for tok in _MONEY.findall(p["rationale"] + " " + p.get("suggested_entry", "")):
        val = _parse_money(tok)
        if val is not None and val not in known:
            warns.append(f"Figure {tok.strip()} doesn't trace to the data.")
    return warns


def summary_warnings(state: RunState, text: str | None = None) -> list[str]:
    text = text if text is not None else (state.data["summary"].get("draft") or "")
    if not text:
        return ["No summary draft."]
    warns = []
    known = known_amounts(state)
    for tok in sorted(set(_MONEY.findall(text))):
        val = _parse_money(tok)
        if val is not None and val not in known:
            warns.append(f"Figure {tok.strip()} doesn't trace to any record, exception, or total.")
    bogus = set(_IDS.findall(text)) - _known_ids(state)
    if bogus:
        warns.append(f"Mentions IDs that don't exist: {', '.join(sorted(bogus))}.")
    for ex in state.exceptions:
        if ex["severity"] != "high":
            continue
        amt = Decimal(ex["amount"])
        mentioned = ex["id"] in text or any(_parse_money(t) == amt for t in _MONEY.findall(text))
        if not mentioned:
            warns.append(f"High-severity {ex['id']} ({ex['type']}, {ex['amount']}) isn't mentioned.")
    return warns
