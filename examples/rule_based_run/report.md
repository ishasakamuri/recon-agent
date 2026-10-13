# Bank reconciliation report

- **Status:** FINAL, approved by Demo Reviewer at 2026-10-13T20:32:26+00:00
- **Prepared by:** rule-based baseline (draft); reviewed by Demo Reviewer
- **Inputs:** ledger `sample_data/ledger.csv` (sha256 5274e78fbcd6...); statement `sample_data/statement.csv` (sha256 f868d14e0fcc...)
- **Matched:** 15/20 ledger records (75%)
- **Exceptions:** 9 (2 still open)
- **Net unreconciled difference (ledger minus bank):** 3899.88
- **Tie-out:** ledger -15637.91 - bank -19537.79 = 3899.88, equal to the sum of exception differences
- **Reviewer agreed with AI recommendation:** 7/9 (78%)

## Summary

## Reconciliation summary (rule-based draft)

15 pairs matched; 9 exceptions out of 20 ledger and 21 statement records (ledger net -15637.91, statement net -19537.79).

### Exceptions
- **EX-001** AMOUNT_MISMATCH (high), 1254.00: correct_ledger_amount
- **EX-003** DUPLICATE_ENTRY (high), 2340.00: reverse_duplicate_entry
- **EX-004** MISSING_FROM_LEDGER (high), 2875.00: record_bank_item_in_ledger
- **EX-006** MISSING_FROM_STATEMENT (high), 2100.00: timing_difference_no_action
- **EX-007** MISSING_FROM_STATEMENT (high), 5400.00: timing_difference_no_action
- **EX-005** POSSIBLE_MATCH (medium), 318.25: confirm_match
- **EX-002** MISSING_FROM_LEDGER (low), 25.00: record_bank_item_in_ledger
- **EX-008** MISSING_FROM_LEDGER (low), 35.00: record_bank_item_in_ledger
- **EX-009** MISSING_FROM_LEDGER (low), 4.12: record_bank_item_in_ledger

## Exception decisions

| ID | Type | Severity | Amount | AI recommended | Final decision | Reviewer note |
|---|---|---|---:|---|---|---|
| EX-001 | AMOUNT_MISMATCH | high | 1254.00 | correct_ledger_amount | correct_ledger_amount |  |
| EX-002 | MISSING_FROM_LEDGER | low | 25.00 | record_bank_item_in_ledger | record_bank_item_in_ledger |  |
| EX-003 | DUPLICATE_ENTRY | high | 2340.00 | reverse_duplicate_entry | reverse_duplicate_entry |  |
| EX-004 | MISSING_FROM_LEDGER | high | 2875.00 | record_bank_item_in_ledger | **investigate_further** (overridden) | NSF on Contoso deposit L-005; confirm with AR before booking |
| EX-005 | POSSIBLE_MATCH | medium | 318.25 | confirm_match | confirm_match |  |
| EX-006 | MISSING_FROM_STATEMENT | high | 2100.00 | timing_difference_no_action | timing_difference_no_action |  |
| EX-007 | MISSING_FROM_STATEMENT | high | 5400.00 | timing_difference_no_action | **escalated** | AR to confirm deposit slip |
| EX-008 | MISSING_FROM_LEDGER | low | 35.00 | record_bank_item_in_ledger | record_bank_item_in_ledger |  |
| EX-009 | MISSING_FROM_LEDGER | low | 4.12 | record_bank_item_in_ledger | record_bank_item_in_ledger |  |

## Open items carried forward

- EX-004 MISSING_FROM_LEDGER 2875.00: investigate_further (NSF on Contoso deposit L-005; confirm with AR before booking)
- EX-007 MISSING_FROM_STATEMENT 5400.00: escalated (AR to confirm deposit slip)

## Automated checks the reviewer acknowledged

- You overrode or escalated 2 AI recommendation(s); check the narrative still matches your decisions.
- Reviewer note: Override reflected in decision table

_Generated 2026-10-13T20:32:26+00:00. Full trail in `audit_log.jsonl`._
