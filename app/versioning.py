"""Immutable identifiers for the deterministic logic used to create decisions.

`ENGINE_VERSION` and its siblings describe *logic* and change only when the rules
change. Build identity is separate: it answers "which commit is this process
running?" and changes on every deployment. Never conflate the two.
"""
import os
import subprocess
from functools import lru_cache
from pathlib import Path

ENGINE_VERSION = "2.0.0"
SETUP_RULES_VERSION = "1.0.0"
PARAMETER_VERSION = "1.1.0"

UNKNOWN = "unknown"
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _git(*arguments: str) -> str | None:
    """Read git metadata for local runs. Containers have no .git and skip this."""
    try:
        completed = subprocess.run(("git", *arguments), cwd=_REPO_ROOT, timeout=2,
                                   capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return _clean(completed.stdout) if completed.returncode == 0 else None


@lru_cache(maxsize=1)
def build_identity() -> dict[str, str]:
    """Identify the running build so a deployment can be compared against a branch.

    `GIT_SHA`/`GIT_REF`/`BUILD_TIME` are baked into the image at build time. Local
    development has no such environment, so fall back to the working tree's git
    state and mark a dirty tree, which is never a deployable commit.
    """
    revision = _clean(os.getenv("GIT_SHA"))
    reference = _clean(os.getenv("GIT_REF"))
    built_at = _clean(os.getenv("BUILD_TIME"))
    if revision:
        return {"revision": revision[:12], "ref": reference or UNKNOWN,
                "built_at": built_at or UNKNOWN, "source": "image"}
    revision = _git("rev-parse", "--short=12", "HEAD")
    if not revision:
        return {"revision": UNKNOWN, "ref": UNKNOWN, "built_at": UNKNOWN, "source": UNKNOWN}
    if _git("status", "--porcelain"):
        revision = f"{revision}-dirty"
    return {"revision": revision, "ref": _git("rev-parse", "--abbrev-ref", "HEAD") or UNKNOWN,
            "built_at": _git("log", "-1", "--format=%cI") or UNKNOWN, "source": "git"}
