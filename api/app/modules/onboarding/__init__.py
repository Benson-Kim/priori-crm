"""Sales Desk onboarding module (issue #41).

Onboarding checklists are auto-created — inside the same transaction —
when a deal is closed WON (#39's close flow). Each checklist copies the
org's task template at create time (template versioning), so editing the
template never mutates existing checklists.
"""
