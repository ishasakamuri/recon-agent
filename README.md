# Reconciliation Agent

An agent-based workflow that reconciles two CSV files (a cash ledger and a bank
statement), flags exceptions, has Claude investigate them and draft
resolutions and a summary, and **won't produce a final result until a named
person has reviewed every item**.

```
 ledger.csv ─┐                         ┌─> AI proposals (PENDING_REVIEW)
             ├─> deterministic ──> exceptions ──> Claude agent ─┤
statement.csv┘   matching engine                (tools)         └─> summary draft
                                                                         │
                         automated checks on AI output  <────────────────┤
                                                                         v
                        human review: accept / override / escalate each exception,
                        approve or edit the summary  ──> finalize ──> report.md
                                                                       exceptions.csv
                        every step ──> audit_log.jsonl                 matches.csv
```

## Design decisions

| Decision | Why |
|---|---|
| **Matching is rule-based, not LLM-based.** | Every match has to be explainable and reproducible for an auditor. The engine is deterministic: reference+amount, then amount+date tolerance, then near-miss pairing. The LLM only works on what the rules couldn't resolve. |
| **The agent can only draft.** | Claude's tools are read-only, plus two that write *drafts* (`propose_resolution`, `submit_summary`). It can't change matches or records, and no tool can finalize. Actions come from a fixed list enforced by a strict tool schema. |
| **AI output is checked by code before a person sees it.** | `checks.py` flags dollar figures in the summary that don't trace to any record or total, IDs that don't exist, high-severity items the summary left out, and actions that don't fit the exception type. The reviewer has to write a note to approve past an open warning. |
| **Only one code path can finalize.** | `finalize()` refuses unless every exception has a human decision and the summary is approved. The CLI `finalize` command goes through the same guard. |
| **The report has to tie out.** | The final report proves that ledger total minus bank total equals the sum of exception differences. If it doesn't, the report says so. |
| **Everything is logged.** | `audit_log.jsonl` records each AI proposal, each human decision (with the AI's action next to it), approvals, and finalization. The report shows how often the reviewer agreed with the AI. |
| **Inputs are treated as untrusted.** | Descriptions come from external files. The system prompt tells the model to treat them as data, and the architecture means a malicious description can't do anything beyond a draft that a person reviews. |
| **There's a no-LLM baseline.** | `--no-llm` produces the same artifacts with simple rules. It runs without an API key and gives you something to compare the agent against. |

## Quick start

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...        # or omit and use --no-llm

python reconcile.py run --ledger sample_data/ledger.csv --statement sample_data/statement.csv --out runs/sep
python reconcile.py status --run runs/sep
python reconcile.py finalize --run runs/sep --reviewer "Your Name"   # refuses: not reviewed
python reconcile.py review --run runs/sep --reviewer "Your Name"     # the human gate
python -m unittest discover -s tests -t .
```

Review is resumable. Quit at any point, and decisions made so far are saved.

The loader accepts common header variants (`Posting Date`, `Debit`/`Credit`,
`Memo`, `Check No`, ...), several date formats, and `$1,254.00` / `(35.00)` /
`12.50-` style amounts.

## Sample data: what's planted and what the engine finds

20 ledger rows and 21 statement rows for September 2026. There are 15 clean
matches, including one that clears 3 days late and two identical payroll runs
that are told apart by date. There are 9 exceptions:

| Exception | Records | What's going on |
|---|---|---|
| AMOUNT_MISMATCH | L-006 / S-007 | Check 1043 entered as 1,245.00; bank cleared 1,254.00 (transposition) |
| DUPLICATE_ENTRY | L-012 | Acme payment posted twice in the ledger |
| POSSIBLE_MATCH | L-014 / S-018 | FastShip ACH cleared 8 days after booking |
| MISSING_FROM_STATEMENT | L-019 | Outstanding check 1048 |
| MISSING_FROM_STATEMENT | L-020 | Deposit in transit on 9/30 |
| MISSING_FROM_LEDGER | S-013 | Returned NSF item for Contoso's 2,875.00 deposit (L-005) |
| MISSING_FROM_LEDGER | S-009, S-020, S-021 | Wire fee, service fee, interest not booked |

`examples/rule_based_run/` holds a complete finalized run (baseline drafter, a
reviewer who overrode one item and escalated another) so you can see the
outputs without running anything.

## Layout

```
reconcile.py        CLI: run / status / review / finalize
recon/loader.py     CSV -> normalized records (column aliases, dates, signed amounts)
recon/matcher.py    deterministic matching + exception classification
recon/agent.py      Claude tool-use loop + rule-based baseline
recon/actions.py    allowed resolutions and which fit which exception type
recon/checks.py     automated checks on AI output
recon/review.py     interactive human-review gate
recon/report.py     finalize (guarded) -> report.md, exceptions.csv, matches.csv
recon/state.py      run state on disk + append-only audit log
tests/              matcher, checks, human gate, and agent loop (fake client)
```

## Possible next steps

- Run an evaluation: label the right resolution for each sample exception, then compare the agent and the baseline on accuracy and reviewer-override rate.
- Generate larger synthetic months with known errors injected, to stress-test the matcher.
- Support one-to-many matches (one deposit covering several invoices).
- Add a small web UI for the review step.
