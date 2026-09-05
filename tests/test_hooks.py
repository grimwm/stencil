"""The git hooks in .beads/hooks are tracked, so they run on every clone.

They are maintained by hand -- `pre-commit install` refuses to write hooks while
core.hooksPath points at .beads/hooks -- which is exactly the arrangement that
lets one contributor's absolute paths reach everyone else's checkout.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / ".beads" / "hooks"

# An assignment whose value is a path into somebody's home directory. Written
# as an assignment rather than a bare path so a comment quoting one, like the
# one explaining this rule, does not trip it.
MACHINE_PATH = re.compile(r"^\s*[A-Za-z_][A-Za-z_0-9]*=\"?(/Users/|/home/)", re.M)


def hook_files() -> list[Path]:
    if not HOOKS.is_dir():
        pytest.skip("no .beads/hooks in this checkout")
    return sorted(p for p in HOOKS.iterdir() if p.is_file())


@pytest.mark.parametrize("hook", hook_files(), ids=lambda p: p.name)
def test_no_hook_hardcodes_one_machines_paths(hook: Path):
    """The reported bug: INSTALL_PYTHON named a macOS venv, in a Linux clone.

    Nothing broke loudly -- the hook fell through to whatever `pre-commit` was
    on PATH -- which is why it survived long enough to be filed.
    """
    found = MACHINE_PATH.search(hook.read_text())

    assert found is None, (
        f"{hook.name} assigns an absolute path from one machine: "
        f"{found.group(0).strip() if found else ''}"
    )


@pytest.mark.parametrize("hook", hook_files(), ids=lambda p: p.name)
def test_every_hook_parses(hook: Path):
    """A hook with a syntax error is reported by git as a failed commit."""
    shell = "bash" if hook.read_text().startswith("#!/usr/bin/env bash") else "sh"
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} not installed")

    result = subprocess.run(
        [shell, "-n", str(hook)], capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr


def test_the_commit_hook_finds_the_repo_venv_from_its_own_location():
    """Resolved relative to the hook, so a clone anywhere still finds its venv."""
    text = (HOOKS / "pre-commit").read_text()

    assert 'REPO="$(cd "$HERE/../.." && pwd)"' in text
    assert 'INSTALL_PYTHON="${PRE_COMMIT_PYTHON:-$REPO/.venv/bin/python3}"' in text


def test_the_commit_hook_still_fails_when_pre_commit_is_missing():
    """The venv lookup is a preference, not the guard.

    If neither the repo's venv nor a pre-commit on PATH exists, the hook must
    exit non-zero: silently skipping the checks is how unformatted markdown and
    a stale issue export reach the remote.
    """
    text = (HOOKS / "pre-commit").read_text()

    assert "exit 1" in text.rsplit("else", 1)[-1]


def test_the_push_hook_finds_the_repo_venv_from_its_own_location():
    """Same lookup as the commit hook, for the same reason.

    Without it the hook did nothing at all on a machine that had not activated
    the virtualenv: `command -v pre-commit` failed, the chain was skipped, and
    the export drift guard it exists to run was never reached.
    """
    text = (HOOKS / "pre-push").read_text()

    assert 'REPO="$(cd "$_here/../.." && pwd)"' in text
    assert 'INSTALL_PYTHON="${PRE_COMMIT_PYTHON:-$REPO/.venv/bin/python3}"' in text


def test_the_push_hook_still_fails_when_pre_commit_is_missing():
    """A push hook that skips silently is worse than no push hook."""
    text = (HOOKS / "pre-push").read_text()

    assert "exit 1" in text.rsplit("else", 1)[-1]


def test_the_push_hook_replays_stdin_to_pre_commit():
    """The ref updates are consumed to capture the shas, so they must be put back.

    pre-commit derives PRE_COMMIT_FROM_REF/TO_REF from that stream; swallowing
    it would leave pre-commit with nothing to work from.
    """
    text = (HOOKS / "pre-push").read_text()

    assert '_bd_updates="$(cat)"' in text
    assert text.count('printf \'%s\\n\' "$_bd_updates" |') == 2, (
        "both the venv and the PATH branch must replay the update stream"
    )
    assert "export BD_PUSHED_REVISIONS" in text
