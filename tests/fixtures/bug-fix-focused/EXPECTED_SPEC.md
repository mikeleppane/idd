---
id: 2026-05-03-csv-cell-trim
status: draft
tier: focused
created: 2026-05-03
capability: csv-import
---

# Intent

Trim leading and trailing whitespace from CSV cells when importing customer records, so that downstream email-matching and ID-based joins work correctly. Internal whitespace must be preserved.

# Context

Customer records ingested via CSV currently store cells verbatim. Cells such as `"  alice@example.com  "` break email-matching joins because the comparison key contains the surrounding spaces. The bug only affects the import path; existing records can be cleaned in a follow-up.

# Domain

| Term | Definition | Example |
|---|---|---|
| Cell | One delimited value within a CSV row. | `"alice@example.com"` |
| Trim | Remove leading and trailing whitespace, leave internal whitespace untouched. | `"  alice@example.com  "` → `"alice@example.com"` |

# Codebase Anchors

- `src/import/csv.py:import_row` — entry point for per-row CSV handling.
- `tests/test_csv_import.py` — existing test suite for the importer.

# Scope

## In scope

- Trim every cell value during CSV import before persistence.
- Preserve internal whitespace within a cell.

## Out of scope (Non-goals)

- Backfilling already-imported records.
- Trimming on export.
- Changing the CSV parser used.

# Scenarios (BDD)

```gherkin
Scenario: Trim leading and trailing whitespace from a cell
  Given a CSV row with cell value "  alice@example.com  "
  When the import path processes the row
  Then the stored cell value is "alice@example.com"

Scenario: Preserve internal whitespace
  Given a CSV row with cell value "Alice  Smith"
  When the import path processes the row
  Then the stored cell value is "Alice  Smith"
```

# Test Strategy

| Criterion | Test type | Where it will live |
|---|---|---|
| crit-1 | unit | tests/test_csv_import.py |
| crit-2 | unit | tests/test_csv_import.py |
| crit-3 | unit | tests/test_csv_import.py |

# Acceptance Criteria

1. Cells with leading whitespace have it removed during import.
2. Cells with trailing whitespace have it removed during import.
3. Cells with only internal whitespace are unchanged during import.

# Negative Requirements

- MUST NOT mutate stored cells beyond strip() (no case folding, no Unicode normalization).
- MUST NOT alter CSV cells on export.

# Open Questions

(none)

# Decisions

See `decisions.md`.
