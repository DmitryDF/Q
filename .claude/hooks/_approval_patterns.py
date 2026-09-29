# Shared approval-prompt patterns — canonical phrasings the user types to
# advance a confirmation gate. Matched as normalized substrings.
# Single source of truth imported by watchlist-present-gate.py and
# confirm-workflow-draft path in pre_plan_gates.py.
APPROVAL_PROMPT_PATTERNS = (
    'approved',
    'approve',
    'ok to store',
    'ready to store',
    'store it',
    'awaiting approval',
    'reply ok / approved / store it / go',
    'reply with ok',
    'say ok',
    'when you say ok',
    'approve to store',
    'awaiting your approval',
    'please approve',
)
