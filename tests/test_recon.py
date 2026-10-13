import builtins
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from pathlib import Path

from recon.agent import rule_based_draft, tool_propose_resolution
from recon.checks import proposal_warnings, summary_warnings
from recon.loader import load_records, parse_amount
from recon.matcher import reconcile
from recon.report import NotReviewed, finalize
from recon.review import review
from recon.state import FINAL, RunState

DATA = Path(__file__).resolve().parent.parent / "sample_data"


def make_run(tmp) -> RunState:
    ledger = load_records(DATA / "ledger.csv", "ledger")
    stmt = load_records(DATA / "statement.csv", "statement")
    res = reconcile(ledger, stmt)
    return RunState.create(Path(tmp) / "run", ledger=ledger, statement=stmt, matches=res.matches,
                           exceptions=res.exceptions,
                           inputs={"ledger": {"path": "l", "sha256": "0" * 64},
                                   "statement": {"path": "s", "sha256": "0" * 64}},
                           config={})


class LoaderTests(unittest.TestCase):
    def test_amount_formats(self):
        self.assertEqual(parse_amount("$1,254.00"), Decimal("1254.00"))
        self.assertEqual(parse_amount("(35.00)"), Decimal("-35.00"))
        self.assertEqual(parse_amount("12.50-"), Decimal("-12.50"))
        self.assertIsNone(parse_amount(""))

    def test_debit_credit_columns_become_signed(self):
        stmt = load_records(DATA / "statement.csv", "statement")
        self.assertEqual(stmt[0].amount, Decimal("4250.00"))   # credit
        self.assertEqual(stmt[1].amount, Decimal("-12480.55"))  # debit


class MatcherTests(unittest.TestCase):
    def setUp(self):
        self.ledger = load_records(DATA / "ledger.csv", "ledger")
        self.stmt = load_records(DATA / "statement.csv", "statement")
        self.res = reconcile(self.ledger, self.stmt)

    def test_finds_every_planted_problem(self):
        got = sorted((e.type, tuple(e.ledger_ids + e.statement_ids)) for e in self.res.exceptions)
        expected = sorted([
            ("AMOUNT_MISMATCH", ("L-006", "S-007")),         # check 1043 transposed 1245 vs 1254
            ("DUPLICATE_ENTRY", ("L-012",)),                 # Acme posted twice
            ("POSSIBLE_MATCH", ("L-014", "S-018")),          # FastShip cleared 8 days later
            ("MISSING_FROM_STATEMENT", ("L-019",)),          # outstanding check 1048
            ("MISSING_FROM_STATEMENT", ("L-020",)),          # deposit in transit
            ("MISSING_FROM_LEDGER", ("S-009",)),             # wire fee
            ("MISSING_FROM_LEDGER", ("S-013",)),             # NSF returned item
            ("MISSING_FROM_LEDGER", ("S-020",)),             # service fee
            ("MISSING_FROM_LEDGER", ("S-021",)),             # interest
        ])
        self.assertEqual(got, expected)
        self.assertEqual(len(self.res.matches), 15)

    def test_payroll_twins_match_by_date(self):
        pairs = {(m.ledger_id, m.statement_id) for m in self.res.matches}
        self.assertIn(("L-003", "S-002"), pairs)
        self.assertIn(("L-018", "S-017"), pairs)

    def test_exceptions_tie_out_to_totals(self):
        diff = sum(Decimal(e.difference) for e in self.res.exceptions)
        self.assertEqual(diff, sum(r.amount for r in self.ledger) - sum(r.amount for r in self.stmt))


class ChecksTests(unittest.TestCase):
    def test_flags_made_up_figures_and_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = make_run(tmp)
            warns = summary_warnings(state, "Net difference is $9,999.99; see EX-042.")
            self.assertTrue(any("9,999.99" in w for w in warns))
            self.assertTrue(any("EX-042" in w for w in warns))
            self.assertTrue(any("isn't mentioned" in w for w in warns))

    def test_flags_incompatible_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = make_run(tmp)
            ex = next(e for e in state.exceptions if e["type"] == "MISSING_FROM_STATEMENT")
            tool_propose_resolution(state, ex["id"], "reverse_duplicate_entry", "high",
                                    f"See {ex['ledger_ids'][0]}.", "", author="test")
            self.assertTrue(any("unusual" in w for w in proposal_warnings(state, ex)))


class HumanGateTests(unittest.TestCase):
    def test_cannot_finalize_without_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = make_run(tmp)
            rule_based_draft(state)
            with self.assertRaises(NotReviewed):
                finalize(state, "nobody")
            self.assertNotEqual(state.status, FINAL)

    def test_full_review_finalizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = make_run(tmp)
            rule_based_draft(state)
            n = len(state.exceptions)
            answers = iter(["a", ""] * n + ["a", "y"])
            orig = builtins.input
            builtins.input = lambda _="": next(answers)
            try:
                with redirect_stdout(io.StringIO()):
                    self.assertTrue(review(state, "Tester"))
            finally:
                builtins.input = orig
            self.assertEqual(state.status, FINAL)
            report = (state.dir / "final" / "report.md").read_text(encoding="utf-8")
            self.assertIn("equal to the sum of exception differences", report)
            self.assertIn(f"{n}/{n} (100%)", report)


if __name__ == "__main__":
    unittest.main()
