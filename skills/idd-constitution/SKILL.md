---
name: idd-constitution
description: Loader + relevance filter for `.idd/CONSTITUTION.md`. Doc-only skill — phase skills invoke `tools.constitution.load_and_filter` directly. Documents the `articles[]` budget shape so all consumers stay aligned.
disable-model-invocation: true
---

# IDD Constitution Loader

## When this skill applies

This skill is documentation. Phase skills (`idd-spec`, `idd-scenarios`,
`idd-plan`, `idd-crucible`, `idd-execute`, `idd-review`, `idd-verify`)
invoke the loader directly via `tools.constitution.load_and_filter`. There
is no orchestrator-side entry point — `disable-model-invocation: true` is
explicit.

## Loader contract

`tools.constitution.load_and_filter(repo_root, *, idea_text="", files_in_scope=()) -> tuple[list[Article], list[str]]`

- Returns `([], [])` when `.idd/CONSTITUTION.md` is absent.
- Otherwise parses the Constitution and applies the M3 minimal relevance
  filter (D-9): all CRITICAL articles kept regardless of score; SHOULD
  articles below the 25th percentile dropped; MAY articles below the median
  dropped; cumulative kept body word count ≤ `MAX_INJECTED_WORDS` (= 1153,
  derived from 1500 tokens × 1/1.3 word→token ratio). CRITICAL articles
  are exempt from the cumulative cap.

## Dispatch budget shape

Each kept Article is serialized via `Article.to_budget_dict()`:

```json
{
  "id": "A1",
  "title": "Secrets via vault only",
  "level": "CRITICAL",
  "rule": "Secrets, API keys, ...",
  "reference": "OWASP A02:2021",
  "rationale": "Hard-coded credentials..."
}
```

`body_words` is loader-internal; never appears in the dispatch contract.

## Relevance scoring

`tools.constitution.score_article(article, scope_keywords)` returns the
overlap count between `tokenize(article.rule + " " + article.reference)`
and `scope_keywords`. Stopwords + tokens shorter than 3 characters are
dropped before scoring. Scope keywords are unioned across:

1. The active feature's idea text (CLI arg).
2. Top-level dependency names from `pyproject.toml` and `package.json`.
3. The phase's `files_in_scope` paths (lowercased path tokens).

## M4 evolution

M4 will swap the keyword-overlap filter for an intel-driven one
(`.idd/intel/`). M4 will also tighten the relevance cap with a
`tiktoken`-precise token count if the budget pressure materializes. M3
ships the minimal version per Decision 16.
