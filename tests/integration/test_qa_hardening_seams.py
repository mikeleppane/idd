"""End-to-end tests pinning cross-slice integration seams.

Each test exercises a workflow that spans multiple modules and validates
that fixes binding multiple surfaces hold up under realistic combinations:

* Concurrent ``persist_drafted_constitution`` against a shared
  ``decisions.md`` across two different repo roots — flock semantics keep
  both ADR rows whole.
* Hook carve-out of the ``context_budget:`` block when a ``traps[]`` entry
  literally quotes the canonical forbidden trailer pattern.
* Mixed ``[constitution:A<n>] [lesson:L<NNN>]`` tag rows walked through the
  full parse + partition + acknowledgement-hook pipeline.
* Well-formed dispatch with both ``articles[]`` and ``traps[]`` populated.
* Stray ``[lesson:L<NNN>]`` typo recovery via ``info`` bucket + diagnostic.
* Symlink containment in the ``forge-resync-agents`` signal collector.
* CLI subprocess round-trip of the dogfood trailer-ban rule against a
  fake-runner ``state.commits[]`` payload.

These tests do not duplicate unit-level coverage; each one binds at least
two modules that the surrounding test files exercise in isolation.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import threading
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any

import pytest

from tools import constitution_amend as am
from tools import ship_gate as sg
from tools.constitution import Article
from tools.intel.lessons import Lesson
from tools.validate import git_conventions as gc_mod
from tools.validate import main as validate_main
from tools.validate.git_conventions import validate_git_conventions

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = REPO_ROOT / "hooks" / "check_budget.py"


def _load_hook() -> ModuleType:
    """Side-load the hook module the way the unit tests do."""
    spec = importlib.util.spec_from_file_location("check_budget_qa_seams", HOOK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_check_budget = _load_hook()


# ---------------------------------------------------------------------------
# Shared fixture builders
# ---------------------------------------------------------------------------


_DRAFT_BODY = (
    "---\n"
    "version: 0.1.0\n"
    'created: "2026-05-11"\n'
    "---\n"
    "\n"
    "# Project Constitution\n"
    "\n"
    "Intro paragraph for the fixture project.\n"
    "\n"
    "## Article 1 — Secrets via vault [CRITICAL]\n"
    "**Rule:** All credentials live in the team vault; never commit them.\n"
    "**Reference:** Team consensus 2026-05.\n"
    "**Rationale:** Prevents secret leaks via git history.\n"
    "**Exception:** None.\n"
    "\n"
    "## Article 2 — Tests gate merges [SHOULD]\n"
    "**Rule:** Every PR must show green tests before merge.\n"
    "**Reference:** Team consensus 2026-05.\n"
    "**Rationale:** Keeps main green for downstream branches.\n"
    "**Exception:** Hotfixes for production outages may bypass with sign-off.\n"
    "\n"
    "## Article 3 — Document deviations [MAY]\n"
    "**Rule:** Add an ADR when intentionally deviating from a stated convention.\n"
    "**Reference:** Team consensus 2026-05.\n"
    "**Rationale:** Future maintainers see the why, not just the what.\n"
    "**Exception:** None.\n"
)


def _articles_fixture() -> list[Article]:
    return [
        Article(
            id="A1",
            title="Repository pattern",
            level="CRITICAL",
            rule="ORM via repository",
            reference=None,
            rationale=None,
            body_words=4,
        ),
        Article(
            id="A2",
            title="No swallowed stacks",
            level="CRITICAL",
            rule="Service swallowed exception stack",
            reference=None,
            rationale=None,
            body_words=4,
        ),
    ]


def _lessons_fixture() -> list[Lesson]:
    return [
        Lesson(
            id="L007",
            captured=date(2026, 5, 11),
            captured_from="2026-05-11-demo",
            resolved_by="1" * 40,
            trap="trap text",
            avoidance="avoidance text",
            tags=("imports",),
            severity="HIGH",
            status="active",
            body_words=4,
        ),
        Lesson(
            id="L020",
            captured=date(2026, 5, 11),
            captured_from="2026-05-11-demo",
            resolved_by="2" * 40,
            trap="another trap",
            avoidance="another avoidance",
            tags=("imports",),
            severity="CRITICAL",
            status="active",
            body_words=4,
        ),
    ]


def _write_state(folder: Path, commits: list[dict[str, Any]]) -> None:
    payload: dict[str, Any] = {
        "feature_id": folder.name,
        "tier": "focused",
        "current_phase": "refine",
        "phases": {},
        "skipped": [],
        "deviations": [],
        "commits": commits,
    }
    (folder / "state.json").write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# Test 1: concurrent persist_drafted_constitution sharing a decisions.md
# ---------------------------------------------------------------------------


def test_concurrent_persist_drafted_constitution_both_adrs_land(
    tmp_path: Path,
) -> None:
    """Two ``persist_drafted_constitution`` calls in distinct repo roots
    sharing one ``decisions.md`` parent path must both land ADR rows.

    The cross-repo shared-decisions-path scenario is contrived but the
    ``append_decisions_atomic`` ``fcntl.flock`` advisory lock should
    serialize the read-modify-write pair regardless of which Constitution
    file each writer is creating. Pin defense-in-depth so a future refactor
    that scopes the lock to the Constitution path (instead of the
    decisions.md path) cannot silently regress.
    """
    pytest.importorskip("fcntl")

    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    (repo_a / ".forge").mkdir(parents=True)
    (repo_b / ".forge").mkdir(parents=True)
    shared_decisions = tmp_path / "shared_decisions.md"

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def _writer(repo: Path, today: date) -> None:
        try:
            barrier.wait()
            am.persist_drafted_constitution(
                repo_root=repo,
                body=_DRAFT_BODY,
                decisions_path=shared_decisions,
                today=today,
            )
        except BaseException as exc:
            with errors_lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=_writer, args=(repo_a, date(2026, 5, 11))),
        threading.Thread(target=_writer, args=(repo_b, date(2026, 5, 12))),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"writer threads raised: {errors!r}"
    # Both Constitutions exist.
    assert (repo_a / ".forge" / "CONSTITUTION.md").read_text(encoding="utf-8") == _DRAFT_BODY
    assert (repo_b / ".forge" / "CONSTITUTION.md").read_text(encoding="utf-8") == _DRAFT_BODY
    # Both ADR rows landed in the shared decisions.md.
    body = shared_decisions.read_text(encoding="utf-8")
    assert body.startswith("# Decisions\n\n")
    assert body.count("Skill-drafted starter Constitution with 3 article(s)") == 2
    assert "2026-05-11" in body
    assert "2026-05-12" in body


# ---------------------------------------------------------------------------
# Test 2 + 4: hook carve-out + traps[]/articles[] permissiveness
# ---------------------------------------------------------------------------


_CITATION_SLUGS = (
    "test-driven-development",
    "coding-guidance-python",
    "git-conventions",
    "code-review-and-quality",
)


def _citation_tail() -> str:
    """Render the citation line shape the FORGE dogfood rules require."""
    return "\nCite: " + ", ".join(_CITATION_SLUGS) + ".\n"


def _trailer_ban_conventions() -> list[dict[str, Any]]:
    return [
        {
            "id": "no-claude-coauthor",
            "source_file": "AGENTS.md",
            "source_line": 75,
            "pattern_kind": "forbidden_text",
            "pattern": "Co-Authored-By: Claude.*",
            "scope": ["dispatch_brief"],
            "severity": "HIGH",
        },
    ]


def _write_conventions(repo_root: Path, rules: list[dict[str, Any]]) -> None:
    forge = repo_root / ".forge"
    forge.mkdir(exist_ok=True)
    (forge / "conventions.json").write_text(json.dumps(rules), encoding="utf-8")


def test_hook_allows_dispatch_when_forbidden_text_lives_only_in_traps_block(
    tmp_path: Path,
) -> None:
    """A trap quoting the canonical banned trailer must not fire the gate.

    Walks the carve-out end-to-end: the trap body lives inside the JSON
    ``context_budget:`` block, the citation tail satisfies any required_text
    rules, and the only forbidden-text echo is inside ``traps[]``. The
    carve-out should strip the budget block before scanning, so dispatch
    passes.
    """
    _write_conventions(tmp_path, _trailer_ban_conventions())
    budget = {
        "files_in_scope": ["tools/example.py"],
        "forbidden": ["tests/**"],
        "articles": [],
        "traps": [
            {
                "id": "L007",
                "trap": ("Do not paste Co-Authored-By: Claude trailer when committing."),
                "avoidance": "Use the explicit no-trailer flag.",
                "tags": ["dispatch"],
                "severity": "CRITICAL",
                "status": "active",
            },
        ],
    }
    prompt = "context_budget:\n" + json.dumps(budget, indent=2) + "\n" + _citation_tail()
    allow, reason = _check_budget._check_dispatch_brief_conventions(prompt, repo_root=tmp_path)
    assert allow, reason
    assert reason == "ok"


def test_hook_denies_dispatch_when_forbidden_text_appears_outside_budget_block(
    tmp_path: Path,
) -> None:
    """Same conventions + same trap body, but the trailer also appears in
    the human-author tail outside the carved-out budget. Must deny —
    confirms the carve-out is scope-limited to the JSON block, not the
    whole prompt.
    """
    _write_conventions(tmp_path, _trailer_ban_conventions())
    budget = {
        "files_in_scope": ["tools/example.py"],
        "forbidden": ["tests/**"],
        "articles": [],
        "traps": [
            {
                "id": "L007",
                "trap": ("Do not paste Co-Authored-By: Claude trailer when committing."),
                "avoidance": "Use the explicit no-trailer flag.",
                "tags": ["dispatch"],
                "severity": "CRITICAL",
                "status": "active",
            },
        ],
    }
    prompt = (
        "context_budget:\n"
        + json.dumps(budget, indent=2)
        + "\n"
        + _citation_tail()
        + "\nplease add Co-Authored-By: Claude <noreply@anthropic.com>\n"
    )
    allow, reason = _check_budget._check_dispatch_brief_conventions(prompt, repo_root=tmp_path)
    assert not allow
    assert "no-claude-coauthor" in reason


def test_hook_allows_dispatch_with_well_formed_articles_and_traps(
    tmp_path: Path,
) -> None:
    """Articles[] and traps[] both populated with the locked budget shape
    pass through the hook end-to-end.

    Uses the production ``to_budget_dict`` shape from the dataclasses to
    catch a future schema drift that would invalidate every shipping
    dispatch.
    """
    _write_conventions(tmp_path, _trailer_ban_conventions())
    article_entry = _articles_fixture()[0].to_budget_dict()
    lesson_entry = _lessons_fixture()[0].to_budget_dict()
    budget = {
        "files_in_scope": ["tools/example.py"],
        "forbidden": ["tests/**"],
        "articles": [article_entry],
        "traps": [lesson_entry],
    }
    prompt = "context_budget:\n" + json.dumps(budget, indent=2) + "\n" + _citation_tail()
    allow, reason = _check_budget._check_dispatch_brief_conventions(prompt, repo_root=tmp_path)
    assert allow, reason
    assert reason == "ok"


def test_hook_allows_dispatch_when_traps_field_is_not_a_list(tmp_path: Path) -> None:
    """The hook is permissive on the ``traps`` field — it carves the budget
    block out before scanning, so a malformed ``traps`` value cannot escape
    the carve-out and trigger the dispatch_brief rule on its stringified
    form. Pin the contract so a future schema-tightening refactor does not
    silently break shipping prompts that pass schema checks elsewhere.
    """
    _write_conventions(tmp_path, _trailer_ban_conventions())
    budget = {
        "files_in_scope": ["tools/example.py"],
        "forbidden": ["tests/**"],
        "articles": [],
        # Deliberately malformed: a dict where the budget contract expects a list.
        "traps": {"oops": "not a list"},
    }
    prompt = "context_budget:\n" + json.dumps(budget, indent=2) + "\n" + _citation_tail()
    allow, reason = _check_budget._check_dispatch_brief_conventions(prompt, repo_root=tmp_path)
    assert allow, reason
    assert reason == "ok"


# ---------------------------------------------------------------------------
# Test 3: mixed-tag REVIEW row through ship-gate end-to-end
# ---------------------------------------------------------------------------


_REVIEW_FIXTURE_MIXED_TAG = """---
spec: 2026-05-11-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |
|----|----------|--------|-------------|----------|---------|-----------------|--------|
| F-1 | HIGH | open | | src/x.py:1 | [constitution:A2] [lesson:L007] Service swallowed exception stack | fix the swallow | self |
"""


def test_mixed_tag_review_row_through_ship_gate_clean_ack(tmp_path: Path) -> None:
    """A single REVIEW row carrying both ``[constitution:A2]`` and
    ``[lesson:L007]`` walks the full pipeline:

    1. ``parse_review_findings`` emits TWO ShipFindings (one article-kind,
       one lesson-kind) from the single row.
    2. ``partition_by_article_level`` routes A2 (CRITICAL article fixture)
       into ``gate``.
    3. ``partition_by_lesson_severity`` routes L007 (HIGH lesson fixture)
       into ``gate``.
    4. ``make_acknowledgement_hook`` records both with the generic
       ``_ACK_PREFIX`` ("Ship-gate finding acknowledged at ship") so the
       heading reads accurately for the mixed-kind ACK.
    5. Each ADR bullet retains only its OWN tag — neither bullet leaks the
       other kind of tag into its body.
    """
    review = tmp_path / "REVIEW.code.md"
    review.write_text(_REVIEW_FIXTURE_MIXED_TAG, encoding="utf-8")

    findings = sg.parse_review_findings(review)
    assert len(findings) == 2, findings
    article_findings = [f for f in findings if f.is_article]
    lesson_findings = [f for f in findings if f.is_lesson]
    assert len(article_findings) == 1
    assert len(lesson_findings) == 1
    assert article_findings[0].article_id == "A2"
    assert lesson_findings[0].lesson_id == "L007"

    articles = _articles_fixture()
    lessons = _lessons_fixture()
    art_gate, _aw, _ai = sg.partition_by_article_level(article_findings, articles)
    les_gate, _lw, _li = sg.partition_by_lesson_severity(lesson_findings, lessons)
    assert len(art_gate) == 1
    assert len(les_gate) == 1

    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "feature_id": "2026-05-11-demo",
                "tier": "full",
                "current_phase": "ship",
                "phases": {
                    "ship": {
                        "status": "in_progress",
                        "started_at": "2026-05-11T00:00:00Z",
                    }
                },
                "skipped": [],
                "deviations": [],
                "commits": [],
            }
        ),
        encoding="utf-8",
    )
    decisions_path = tmp_path / "decisions.md"
    decisions_path.write_text("# Decisions\n\n", encoding="utf-8")

    hook = sg.make_acknowledgement_hook(
        state_path=state_path,
        decisions_path=decisions_path,
        gate_findings=[*art_gate, *les_gate],
        articles=articles,
        lessons=lessons,
        now=datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC),
    )
    hook(tmp_path)

    decisions = decisions_path.read_text(encoding="utf-8")
    assert "## 2026-05-11 — Ship-gate finding acknowledged at ship" in decisions
    assert sg._ACK_PREFIX == "Ship-gate finding acknowledged at ship"

    bullet_lines = [line for line in decisions.splitlines() if line.startswith("- ")]
    assert len(bullet_lines) == 2, bullet_lines
    article_bullet = next(b for b in bullet_lines if b.startswith("- [constitution:A2]"))
    lesson_bullet = next(b for b in bullet_lines if b.startswith("- [lesson:L007]"))
    assert "[lesson:L007]" not in article_bullet, article_bullet
    assert "[constitution:A2]" not in lesson_bullet, lesson_bullet
    # Each bullet keeps its OWN tag exactly once (the rebuilt prefix).
    assert article_bullet.count("[constitution:A2]") == 1
    assert lesson_bullet.count("[lesson:L007]") == 1


# ---------------------------------------------------------------------------
# Test 5: stray lesson tag routes to info + diagnostic, no block
# ---------------------------------------------------------------------------


_REVIEW_FIXTURE_TYPO_LESSON = """---
spec: 2026-05-11-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |
|----|----------|--------|-------------|----------|---------|-----------------|--------|
| F-1 | LOW | open | | src/x.py:1 | [lesson:L999] typo'd lesson id | fix the typo | self |
"""


def test_stray_lesson_tag_does_not_block_ship_routes_to_info(tmp_path: Path) -> None:
    """A REVIEW.code.md row tagged with an absent ``[lesson:L999]`` walks
    parse + partition without blocking ship.

    The partitioner downgrades the unknown id to a synthetic LOW finding
    in the ``info`` bucket and surfaces a ``routing_warnings`` diagnostic
    naming the typo. ``partition.gate`` is empty so ship can proceed.
    """
    review = tmp_path / "REVIEW.code.md"
    review.write_text(_REVIEW_FIXTURE_TYPO_LESSON, encoding="utf-8")

    findings = sg.parse_review_findings(review)
    assert len(findings) == 1
    assert findings[0].is_lesson
    assert findings[0].lesson_id == "L999"

    lessons = _lessons_fixture()
    partition = sg.partition_by_lesson_severity(findings, lessons)
    gate, warn, info = partition
    assert gate == []
    assert warn == []
    assert len(info) == 1
    synthetic = info[0]
    assert synthetic.lesson_id == "L999"
    assert synthetic.severity == "LOW"
    assert "L999" in synthetic.message

    warnings = sg.routing_warnings(partition)
    assert any("L999" in w for w in warnings), warnings
    assert any("src/x.py:1" in w for w in warnings), warnings


# ---------------------------------------------------------------------------
# Test 6: symlink containment in collect_resync_signals
# ---------------------------------------------------------------------------


def test_symlinked_agents_md_outside_repo_dropped_from_resync_signals(
    tmp_path: Path,
) -> None:
    """End-to-end: an ``AGENTS.md`` symlink pointing outside the repo root
    must be refused by ``collect_resync_signals`` and recorded in
    ``dropped_for_escape``. An in-tree symlink to a real file must still
    be collected normally.
    """
    outside_root = tmp_path.parent / f"{tmp_path.name}-escape"
    outside_root.mkdir()
    outside_secret = outside_root / "secrets-agents.md"
    outside_secret.write_text("# Out-of-tree agents\nOUT_OF_TREE_BODY\n", encoding="utf-8")

    repo = tmp_path / "repo"
    repo.mkdir()
    # AGENTS.md is a symlink that escapes the repo root.
    (repo / "AGENTS.md").symlink_to(outside_secret)
    # In-tree real file + in-tree symlink to it.
    real_readme = repo / "README-real.md"
    real_readme.write_text("# Real readme\nIn-tree real content.\n", encoding="utf-8")
    (repo / "README.md").symlink_to(real_readme)

    result = am.collect_resync_signals(repo)

    rels = [f.relative_path for f in result.files]
    assert PurePosixPath("AGENTS.md") not in rels
    assert PurePosixPath("AGENTS.md") in result.dropped_for_escape
    # In-tree symlink still resolves and is collected.
    assert PurePosixPath("README.md") in rels
    readme_body = next(
        f.body for f in result.files if f.relative_path == PurePosixPath("README.md")
    )
    assert "In-tree real content" in readme_body
    # Out-of-tree body never appears anywhere in the payload.
    assert all("OUT_OF_TREE_BODY" not in f.body for f in result.files)


# ---------------------------------------------------------------------------
# Test 7: validate CLI fires the dogfood trailer-ban on a synthetic commit
# ---------------------------------------------------------------------------


class _ScriptedRunner:
    """Test seam returning canned ``CompletedProcess`` results per git argv.

    Mirrors the fake-runner shim used by ``test_validate_git_conventions``
    so the integration test exercises the same injection surface that the
    unit suite pins.
    """

    def __init__(self, scripts: dict[tuple[str, ...], tuple[int, str, str]]) -> None:
        self._scripts = scripts
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self,
        args: list[str],
        **_kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        # Production runner exposes capture_output/text/check/timeout/cwd; the
        # test seam absorbs them via **_kwargs and asserts on argv only.
        key = tuple(args)
        self.calls.append(key)
        if key not in self._scripts:
            raise AssertionError(f"unscripted git invocation: {key}")
        returncode, stdout, stderr = self._scripts[key]
        return subprocess.CompletedProcess(
            args=args, returncode=returncode, stdout=stdout, stderr=stderr
        )


def _script_commit(sha: str, message: str) -> dict[tuple[str, ...], tuple[int, str, str]]:
    """Return the scripted (verify, show) pair for one fake commit.

    Mirrors ``_script_commit`` in ``test_validate_git_conventions`` —
    ``rev-parse --verify`` excludes ``--``; ``show`` includes it because the
    production code threads the separator through that call.
    """
    return {
        ("git", "rev-parse", "--verify", f"{sha}^{{commit}}"): (0, sha + "\n", ""),
        ("git", "show", "-s", "--format=%B", "--", sha): (0, message, ""),
    }


def _write_dogfood_config(repo_root: Path) -> None:
    """Drop the same trailer-ban shape FORGE ships in ``.forge/config.json``.

    Pinned to the same regex (``Co-Authored-By:\\s*Claude.*``) so the test
    proves the dogfood rule actually fires against a commit body — not
    just that some pattern fires.
    """
    forge = repo_root / ".forge"
    forge.mkdir(exist_ok=True)
    (forge / "config.json").write_text(
        json.dumps(
            {
                "git_conventions": {
                    "subject": {
                        "max_length": 72,
                        "require_conventional_commits": True,
                        "allowed_scopes": ["tools", "tests"],
                    },
                    "trailers": {"ban_patterns": ["Co-Authored-By:\\s*Claude.*"]},
                }
            }
        ),
        encoding="utf-8",
    )


def _build_feature_folder(repo_root: Path, sha: str, subject: str) -> Path:
    """Seed ``.forge/features/<id>/state.json`` with one fake commit."""
    feature_folder = repo_root / ".forge" / "features" / "2026-05-12-demo"
    feature_folder.mkdir(parents=True)
    _write_state(
        feature_folder,
        [{"sha": sha, "phase": "spec", "subject": subject}],
    )
    return feature_folder


def test_conventions_cli_fires_dogfood_trailer_rule_on_synthetic_commit(
    tmp_path: Path,
) -> None:
    """Walk the dogfood trailer-ban end-to-end with a fake-runner shim.

    Feeds a synthetic commit body carrying ``Co-Authored-By: Claude`` into
    ``validate_git_conventions`` via a ``_ScriptedRunner`` (same shim used
    by the unit suite). Asserts the BLOCK finding fires against the
    FORGE-shipped pattern shape.
    """
    _write_dogfood_config(tmp_path)
    sha = "a" * 40
    feature_folder = _build_feature_folder(tmp_path, sha, "feat(tools): add x")
    message = (
        "feat(tools): add x\n"
        "\n"
        "Body explaining the change.\n"
        "\n"
        "Co-Authored-By: Claude <noreply@anthropic.com>\n"
    )
    runner = _ScriptedRunner(_script_commit(sha, message))

    findings = validate_git_conventions(feature_folder, runner=runner)

    trailer_blocks = [
        f
        for f in findings
        if f.severity == "BLOCK"
        and ("trailer" in f.message.lower() or "co-authored-by" in f.message.lower())
    ]
    assert trailer_blocks, [f.message for f in findings]


def test_conventions_cli_silent_on_clean_commit_with_dogfood_config(
    tmp_path: Path,
) -> None:
    """Same dogfood config, clean commit body → no trailer-related finding.

    Confirms the dogfood pattern is not over-eager: a commit without the
    banned trailer must pass without leaking false-positives from the same
    rule that fires in the positive case above.
    """
    _write_dogfood_config(tmp_path)
    sha = "b" * 40
    feature_folder = _build_feature_folder(tmp_path, sha, "feat(tools): add x")
    message = (
        "feat(tools): add x\n"
        "\n"
        "Body without any banned trailers.\n"
        "\n"
        "Signed-off-by: Reviewer <r@example.com>\n"
    )
    runner = _ScriptedRunner(_script_commit(sha, message))

    findings = validate_git_conventions(feature_folder, runner=runner)

    trailer_findings = [
        f
        for f in findings
        if "trailer" in f.message.lower() or "co-authored-by" in f.message.lower()
    ]
    assert trailer_findings == [], [f.message for f in findings]


def test_conventions_cli_dogfood_trailer_rule_via_validate_pkg_main(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defense-in-depth: route the same scenario through ``validate.main``
    (the in-process CLI entry point) so argparse + dispatcher + downstream
    pipeline all participate.

    Patches the production ``_default_runner`` so the CLI path uses the
    fake-runner shim — the existing slice 7 CLI tests prove the real-git
    path works on real commits; this test pins the dogfood config + state
    threading through the CLI without depending on a side-loaded git
    binary.
    """
    _write_dogfood_config(tmp_path)
    sha = "c" * 40
    feature_folder = _build_feature_folder(tmp_path, sha, "feat(tools): add x")
    message = (
        "feat(tools): add x\n"
        "\n"
        "Body explaining the change.\n"
        "\n"
        "Co-Authored-By: Claude <noreply@anthropic.com>\n"
    )
    runner = _ScriptedRunner(_script_commit(sha, message))
    monkeypatch.setattr(gc_mod, "_default_runner", runner)

    rc = validate_main(
        [
            "--target",
            "git-conventions",
            "--repo-root",
            str(tmp_path),
            str(feature_folder),
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    findings = payload["findings"]
    trailer_blocks = [
        f
        for f in findings
        if f["severity"] == "BLOCK"
        and ("trailer" in f["message"].lower() or "co-authored-by" in f["message"].lower())
    ]
    assert trailer_blocks, payload
    # BLOCK is in EXIT_NONZERO_SEVERITIES → non-zero exit.
    assert rc != 0, payload
