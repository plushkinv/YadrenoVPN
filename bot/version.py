"""Runtime identity of the installed YadrenoVPN bot release."""

from __future__ import annotations

import logging
import re

from bot.utils.git_utils import run_git_command


logger = logging.getLogger(__name__)

UNKNOWN_BOT_VERSION = "unknown"
_RELEASE_PREFIX_RE = re.compile(
    r"^[!?]?\s*версия\s+([0-9]+(?:\.[0-9]+)*)\b",
    flags=re.IGNORECASE,
)


def parse_bot_release(commit_subject: str) -> str | None:
    """Extract a release number from the version prefix of a commit subject."""
    match = _RELEASE_PREFIX_RE.match((commit_subject or "").strip())
    return match.group(1) if match else None


def resolve_bot_version() -> tuple[str, str]:
    """Return ``(release, short_commit)`` for the current Git HEAD."""
    success, output = run_git_command(["log", "-1", "--format=%h%x09%s"])
    if not success or "\t" not in output:
        logger.warning("Cannot resolve YadrenoVPN bot release from Git HEAD")
        return UNKNOWN_BOT_VERSION, UNKNOWN_BOT_VERSION

    commit, subject = output.split("\t", 1)
    commit = commit.strip() or UNKNOWN_BOT_VERSION
    release = parse_bot_release(subject)
    if release is None:
        logger.warning(
            "Current Git commit subject has no YadrenoVPN release prefix: %s",
            subject[:160],
        )
        release = UNKNOWN_BOT_VERSION
    return release, commit


BOT_RELEASE, BOT_COMMIT = resolve_bot_version()
