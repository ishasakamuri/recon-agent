"""Deterministic matching engine.

Matching is rule-based on purpose: every match has to be explainable and
reproducible for an auditor, so the LLM never decides what matches. It only
investigates what this module could *not* match.

Passes, in order (each pass only sees records the earlier passes left over):
  1. reference + exact amount           -> matched
  2. exact amount, date within tolerance -> matched
  3. near-misses                         -> paired exceptions
       a. same reference, different amount           AMOUNT_MISMATCH
       b. same amount, date outside tolerance        POSSIBLE_MATCH
       c. similar description, close date, amount
          within a small percentage                  AMOUNT_MISMATCH
  4. leftovers                           -> DUPLICATE_ENTRY, MISSING_FROM_STATEMENT,
                                            MISSING_FROM_LEDGER
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher

from .loader import Record

EXCEPTION_TYPES = {
    "AMOUNT_MISMATCH": "Same transaction, but the amounts disagree",
    "POSSIBLE_MATCH": "Same amount, but dates are further apart than the tolerance",
    "DUPLICATE_ENTRY": "Appears to be a repeat of a record that already matched",
    "MISSING_FROM_STATEMENT": "In the ledger but not on the bank statement",
    "MISSING_FROM_LEDGER": "On the bank statement but not in the ledger",
}


@dataclass
class Config:
    date_tolerance_days: int = 3
    possible_match_window_days: int = 10
    amount_tolerance_pct: Decimal = Decimal("0.05")
    description_similarity: float = 0.5
    high_severity_amount: Decimal = Decimal("1000")
    medium_severity_amount: Decimal = Decimal("100")


@dataclass
class Match:
    ledger_id: str
    statement_id: str
    rule: str
    date_diff_days: int
    description_similarity: float


@dataclass
class ReconException:
    id: str
    type: str
    severity: str
    ledger_ids: list[str]
    statement_ids: list[str]
    amount: str  # absolute amount at stake
    difference: str  # ledger minus statement, for paired exceptions
    detail: str
    ai_proposal: dict | None = None
    human_decision: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Result:
    matches: list[Match] = field(default_factory=list)
    exceptions: list[ReconException] = field(default_factory=list)


_REF_PREFIX = re.compile(r"^(CHECK|CHK|CHQ|REF|NO|NUM)+")
_STOPWORDS = {"ACH", "DEBIT", "CREDIT", "DEPOSIT", "PAYMENT", "CUSTOMER", "VENDOR",
              "INC", "LTD", "LLC", "CO", "THE", "AND", "INCOMING", "OUTGOING", "WIRE"}


def norm_ref(ref: str) -> str:
    s = re.sub(r"[^A-Z0-9]", "", ref.upper())
    s = _REF_PREFIX.sub("", s)
    return s.lstrip("0")


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[A-Z0-9]+", text.upper()) if t not in _STOPWORDS and len(t) > 1}


def description_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    jaccard = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    seq = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return round(max(jaccard, seq), 3)


def _days(a: Record, b: Record) -> int:
    return abs((a.date - b.date).days)


def _severity(kind: str, amount: Decimal, cfg: Config) -> str:
    if kind in ("AMOUNT_MISMATCH", "DUPLICATE_ENTRY") or amount >= cfg.high_severity_amount:
        return "high"
    if amount >= cfg.medium_severity_amount:
        return "medium"
    return "low"


def _greedy_assign(pairs):
    """pairs: list of (sort_key, ledger_rec, statement_rec, extra). Lowest key wins."""
    used_l, used_s, chosen = set(), set(), []
    for _, l, s, extra in sorted(pairs, key=lambda p: p[0]):
        if l.id in used_l or s.id in used_s:
            continue
        used_l.add(l.id)
        used_s.add(s.id)
        chosen.append((l, s, extra))
    return chosen


def reconcile(ledger: list[Record], statement: list[Record], cfg: Config | None = None) -> Result:
    cfg = cfg or Config()
    open_l = {r.id: r for r in ledger}
    open_s = {r.id: r for r in statement}
    result = Result()
    paired: list[tuple[str, Record, Record, str]] = []

    def take(l: Record, s: Record):
        open_l.pop(l.id)
        open_s.pop(s.id)

    # Pass 1: reference + exact amount.
    pairs = []
    for l in open_l.values():
        if not norm_ref(l.reference):
            continue
        for s in open_s.values():
            if norm_ref(s.reference) == norm_ref(l.reference) and s.amount == l.amount:
                pairs.append(((_days(l, s),), l, s, None))
    for l, s, _ in _greedy_assign(pairs):
        take(l, s)
        result.matches.append(Match(l.id, s.id, "reference+amount", _days(l, s),
                                    description_similarity(l.description, s.description)))

    # Pass 2: exact amount within the date tolerance. Ties broken by closer
    # date, then by more similar description.
    pairs = []
    for l in open_l.values():
        for s in open_s.values():
            if s.amount == l.amount and _days(l, s) <= cfg.date_tolerance_days:
                sim = description_similarity(l.description, s.description)
                pairs.append(((_days(l, s), -sim), l, s, sim))
    for l, s, sim in _greedy_assign(pairs):
        take(l, s)
        result.matches.append(Match(l.id, s.id, "amount+date", _days(l, s), sim))

    # Pass 3a: same reference, amount differs.
    pairs = []
    for l in open_l.values():
        if not norm_ref(l.reference):
            continue
        for s in open_s.values():
            if norm_ref(s.reference) == norm_ref(l.reference):
                pairs.append(((_days(l, s),), l, s, "AMOUNT_MISMATCH"))
    for l, s, kind in _greedy_assign(pairs):
        take(l, s)
        paired.append((kind, l, s, f"Reference {l.reference} matches but amounts differ"))

    # Pass 3b: same amount, date outside tolerance but inside the wider window.
    pairs = []
    for l in open_l.values():
        for s in open_s.values():
            d = _days(l, s)
            if s.amount == l.amount and cfg.date_tolerance_days < d <= cfg.possible_match_window_days:
                sim = description_similarity(l.description, s.description)
                pairs.append(((d, -sim), l, s, (d, sim)))
    for l, s, (d, sim) in _greedy_assign(pairs):
        take(l, s)
        paired.append(("POSSIBLE_MATCH", l, s,
                       f"Amounts equal, dates {d} days apart (tolerance {cfg.date_tolerance_days}); "
                       f"description similarity {sim}"))

    # Pass 3c: similar description, close date, amount off by a small percentage.
    pairs = []
    for l in open_l.values():
        for s in open_s.values():
            if l.amount == 0 or (l.amount > 0) != (s.amount > 0):
                continue
            pct = abs(l.amount - s.amount) / abs(l.amount)
            sim = description_similarity(l.description, s.description)
            if (_days(l, s) <= cfg.date_tolerance_days and pct <= cfg.amount_tolerance_pct
                    and sim >= cfg.description_similarity):
                pairs.append(((pct, -sim), l, s, (pct, sim)))
    for l, s, (pct, sim) in _greedy_assign(pairs):
        take(l, s)
        paired.append(("AMOUNT_MISMATCH", l, s,
                       f"Similar description ({sim}) and date, amounts differ by {pct:.1%}"))

    # Pass 4: classify leftovers. A leftover that is a twin of an already
    # matched record from the same file is a likely duplicate posting.
    by_id = {r.id: r for r in ledger + statement}
    matched_ids = {m.ledger_id for m in result.matches} | {m.statement_id for m in result.matches}

    def twin_of(rec: Record) -> Record | None:
        pool = ledger if rec.source == "ledger" else statement
        for other in pool:
            if (other.id != rec.id and other.id in matched_ids and other.amount == rec.amount
                    and _days(other, rec) <= 1
                    and description_similarity(other.description, rec.description) >= 0.9):
                return other
        return None

    exceptions: list[tuple] = []
    for kind, l, s, detail in paired:
        exceptions.append((kind, [l.id], [s.id], max(abs(l.amount), abs(s.amount)), l.amount - s.amount, detail))
    for rec in list(open_l.values()) + list(open_s.values()):
        twin = twin_of(rec)
        if twin:
            exceptions.append(("DUPLICATE_ENTRY",
                               [rec.id] if rec.source == "ledger" else [],
                               [rec.id] if rec.source == "statement" else [],
                               abs(rec.amount), rec.amount if rec.source == "ledger" else -rec.amount,
                               f"Same amount, date and description as {twin.id}, which already matched"))
        elif rec.source == "ledger":
            exceptions.append(("MISSING_FROM_STATEMENT", [rec.id], [], abs(rec.amount), rec.amount,
                               "No statement line with this amount near this date"))
        else:
            exceptions.append(("MISSING_FROM_LEDGER", [], [rec.id], abs(rec.amount), -rec.amount,
                               "No ledger entry with this amount near this date"))

    def first_date(e):
        ids = e[1] + e[2]
        return min(by_id[i].date for i in ids)

    for n, (kind, lids, sids, amt, diff, detail) in enumerate(sorted(exceptions, key=first_date), start=1):
        result.exceptions.append(ReconException(
            id=f"EX-{n:03d}", type=kind, severity=_severity(kind, amt, cfg),
            ledger_ids=lids, statement_ids=sids, amount=f"{amt:.2f}", difference=f"{diff:.2f}",
            detail=detail,
        ))
    return result
