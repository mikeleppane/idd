# Cross-AI review (mock — with findings)

Reviewer surfaced 2 findings.

| ID | Severity | Status | Location | Problem | Fix | Source |
|---|---|---|---|---|---|---|
| F-1 | HIGH | open | src/auth/session.py:42 | [constitution:A1] direct DB call in service layer | move to repository | external-mock |
| F-2 | MEDIUM | open | src/auth/middleware.py:18 | missing nonce in OAuth state | add csrf_nonce field | external-mock |
