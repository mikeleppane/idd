"""Cross-AI CLI detection and reviewer routing.

Scans ``PATH`` for the supported peer-review CLIs (``codex``, ``claude``,
``gemini``) and picks a reviewer using a family-aware routing rule that
prefers a CLI from a *different* model family than the executor.

PATH resolution mirrors :func:`shutil.which`: callers may inject either a
fully-formed ``path`` string or an ``env`` mapping (whose ``"PATH"`` entry is
read). When both are absent, the process ``PATH`` is used. Tests pass
``tmp_path`` based directories so the host environment cannot bleed in.

Detection order is the :class:`CLI` enum's declaration order — not the order
the binaries happen to live on ``PATH`` — so two callers with different ``PATH``
shapes still see a stable result tuple.

The routing rule (:func:`pick_reviewer`) intentionally tolerates unparseable
``executor_model`` strings (e.g. ``"OpenAI-GPT-5"``): rather than fail loudly,
it falls through to the "no executor family known" branch where every CLI is
treated as different-family and the first available CLI wins. Loud failure
here would block peer review in exactly the cases (new model labels) where it
is most useful as a smoke test.
"""

from __future__ import annotations

import shutil
from enum import StrEnum


class CLI(StrEnum):
    """Supported peer-review CLI binaries, in detection order."""

    codex = "codex"
    claude = "claude"
    gemini = "gemini"


CLI_FAMILY: dict[CLI, str] = {
    CLI.codex: "openai",
    CLI.claude: "anthropic",
    CLI.gemini: "google",
}


def _resolve_path(env: dict[str, str] | None, path: str | None) -> str | None:
    """Pick the PATH string for :func:`shutil.which`.

    Explicit ``path`` wins; otherwise fall through to ``env["PATH"]`` when
    ``env`` is supplied; otherwise return ``None`` so :func:`shutil.which`
    uses the process ``PATH``.
    """
    if path is not None:
        return path
    if env is not None:
        return env.get("PATH")
    return None


def detect_clis(
    env: dict[str, str] | None = None,
    path: str | None = None,
) -> tuple[CLI, ...]:
    """Return CLIs present on ``PATH`` in :class:`CLI` declaration order.

    Args:
        env: Optional environment mapping. ``env["PATH"]`` is consulted when
            ``path`` is not supplied.
        path: Optional explicit ``PATH`` string. When supplied, takes
            precedence over ``env``.

    Returns:
        Tuple of detected CLIs in enum declaration order, never the order
        directories appear on ``PATH``.
    """
    resolved = _resolve_path(env, path)
    return tuple(cli for cli in CLI if shutil.which(cli.value, path=resolved) is not None)


def pick_reviewer(
    executor_model: str | None,
    available: tuple[CLI, ...],
    allowed_clis: tuple[str, ...] = (),
) -> CLI | None:
    """Pick a peer-review CLI, preferring a different family than the executor.

    Args:
        executor_model: Identifier for the model that produced the artifact
            under review (e.g. ``"claude"``). When ``None`` or unparseable as
            a :class:`CLI` member, no executor family is known and every
            available CLI is treated as different-family.
        available: CLIs detected on ``PATH``, typically from
            :func:`detect_clis`.
        allowed_clis: Optional allow-list. When non-empty, CLIs absent from
            this tuple are filtered out before routing. An empty tuple means
            "no filter".

    Returns:
        The first different-family CLI when one exists; otherwise the first
        same-family CLI; otherwise ``None``.
    """
    if allowed_clis:
        allow_set = set(allowed_clis)
        filtered: tuple[CLI, ...] = tuple(cli for cli in available if cli.value in allow_set)
    else:
        filtered = available

    if not filtered:
        return None

    executor_family: str | None = None
    if executor_model is not None:
        try:
            executor_cli = CLI(executor_model.lower())
        except ValueError:
            executor_family = None
        else:
            executor_family = CLI_FAMILY.get(executor_cli)

    different_family: list[CLI] = []
    same_family: list[CLI] = []
    for cli in filtered:
        if executor_family is not None and CLI_FAMILY[cli] == executor_family:
            same_family.append(cli)
        else:
            different_family.append(cli)

    if different_family:
        return different_family[0]
    if same_family:
        return same_family[0]
    return None
