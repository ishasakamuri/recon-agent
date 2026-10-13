"""The fixed vocabulary of resolutions an exception can get.

The agent must choose from this list (enforced by the tool schema), and the
checks module flags any choice that doesn't fit the exception type.
"""

ACTIONS = {
    "record_bank_item_in_ledger": "Book a bank-originated item (fee, interest, returned deposit) in the ledger",
    "timing_difference_no_action": "Timing difference expected to clear next period (outstanding check, deposit in transit)",
    "correct_ledger_amount": "Ledger amount is wrong; post a correcting entry",
    "reverse_duplicate_entry": "Reverse a ledger entry that was posted twice",
    "confirm_match": "Accept the paired records as the same transaction",
    "contact_bank": "Bank appears to be in error; raise it with the bank",
    "investigate_further": "Not enough information to decide; needs a person to look into it",
}

COMPATIBLE = {
    "AMOUNT_MISMATCH": {"correct_ledger_amount", "contact_bank", "investigate_further"},
    "POSSIBLE_MATCH": {"confirm_match", "investigate_further"},
    "DUPLICATE_ENTRY": {"reverse_duplicate_entry", "contact_bank", "investigate_further"},
    "MISSING_FROM_STATEMENT": {"timing_difference_no_action", "investigate_further"},
    "MISSING_FROM_LEDGER": {"record_bank_item_in_ledger", "contact_bank", "investigate_further"},
}

CONFIDENCE = ["high", "medium", "low"]
