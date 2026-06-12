"""Shared statement balance predicates (ISSUE-021).

The single source of truth for which document statuses are excluded from
statement opening balances, applied symmetrically to the debit side (the
documents) and the credit side (payments recorded against them) in BOTH the
customer and vendor statement services. Asymmetric or per-service copies of
this predicate are exactly how opening balances drifted negative after
cancellations.
"""

#: Statuses excluded from statement balances: drafts are not yet
#: receivable/payable, and canceled documents (and any payments recorded
#: against them) must not move the balance. Plain strings so the predicate
#: applies to invoices (draft/canceled) and expenses (canceled) alike.
EXCLUDED_STATEMENT_STATUSES: tuple[str, ...] = ("draft", "canceled")
