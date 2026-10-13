"""Load ledger / bank-statement CSVs into a common record shape.

Real exports never agree on column names, date formats, or sign conventions,
so this module maps common header aliases onto one schema and normalises
amounts to a signed Decimal (positive = money in, negative = money out).
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

ALIASES = {
    "date": ["date", "txn date", "transaction date", "posting date", "post date", "posted", "value date"],
    "amount": ["amount", "amt", "net amount", "value"],
    "debit": ["debit", "withdrawal", "withdrawals", "dr", "money out"],
    "credit": ["credit", "deposit", "deposits", "cr", "money in"],
    "description": ["description", "desc", "memo", "details", "narrative", "payee"],
    "reference": ["reference", "ref", "ref no", "reference no", "check no", "check number", "doc no"],
}

DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y", "%Y/%m/%d"]


class LoadError(ValueError):
    pass


@dataclass
class Record:
    id: str
    source: str  # "ledger" or "statement"
    row: int  # 1-based data row in the source file (header excluded)
    date: date
    amount: Decimal
    description: str
    reference: str
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "row": self.row,
            "date": self.date.isoformat(),
            "amount": str(self.amount),
            "description": self.description,
            "reference": self.reference,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Record":
        return cls(
            id=d["id"],
            source=d["source"],
            row=d["row"],
            date=date.fromisoformat(d["date"]),
            amount=Decimal(d["amount"]),
            description=d["description"],
            reference=d["reference"],
        )


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", h.lower()).strip()


def _map_columns(headers: list[str]) -> dict[str, str]:
    normed = {_norm_header(h): h for h in headers}
    mapping = {}
    for canonical, aliases in ALIASES.items():
        for alias in aliases:
            if alias in normed:
                mapping[canonical] = normed[alias]
                break
    return mapping


def parse_amount(text: str) -> Decimal | None:
    s = (text or "").strip()
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[,$£€\s()]", "", s)
    if s.endswith("-"):  # some banks print trailing minus
        negative, s = True, s[:-1]
    try:
        value = Decimal(s)
    except InvalidOperation as exc:
        raise LoadError(f"cannot parse amount {text!r}") from exc
    return -value if negative else value


def parse_date(text: str) -> date:
    s = (text or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise LoadError(f"cannot parse date {text!r}")


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_records(path: str | Path, source: str) -> list[Record]:
    prefix = "L" if source == "ledger" else "S"
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise LoadError(f"{path}: no header row")
        cols = _map_columns(reader.fieldnames)
        if "date" not in cols:
            raise LoadError(f"{path}: no date column found in {reader.fieldnames}")
        if "amount" not in cols and not ({"debit", "credit"} & cols.keys()):
            raise LoadError(f"{path}: need an amount column or debit/credit columns")

        records = []
        for i, row in enumerate(reader, start=1):
            if not any((v or "").strip() for v in row.values()):
                continue  # blank line
            try:
                if "amount" in cols:
                    amount = parse_amount(row[cols["amount"]]) or Decimal("0")
                else:
                    debit = parse_amount(row.get(cols.get("debit", ""), "")) or Decimal("0")
                    credit = parse_amount(row.get(cols.get("credit", ""), "")) or Decimal("0")
                    amount = credit - debit
                records.append(
                    Record(
                        id=f"{prefix}-{i:03d}",
                        source=source,
                        row=i,
                        date=parse_date(row[cols["date"]]),
                        amount=amount,
                        description=(row.get(cols.get("description", ""), "") or "").strip(),
                        reference=(row.get(cols.get("reference", ""), "") or "").strip(),
                        raw=dict(row),
                    )
                )
            except LoadError as exc:
                raise LoadError(f"{path} row {i}: {exc}") from exc
    return records
