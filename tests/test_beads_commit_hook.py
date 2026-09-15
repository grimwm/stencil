"""The commit hook exports the tracker into the checkout being committed.

bd anchors `.beads/` where the Dolt database lives -- the main checkout -- so a
`bd` write from a worktree rewrote the main checkout's `.beads/issues.jsonl`
and never the worktree's. With worktrees mandated and main pull-request-only,
that left every worktree commit carrying a stale export and the main checkout
permanently dirty with one nothing could commit there (stn-nkvl).

These tests drive the real `.beads/hooks/pre-commit` through real `git commit`
calls, with `bd` and the pre-commit framework stubbed: what matters is which
file the export lands in and whether it is in the commit, and neither can be
answered without git doing the committing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = REPO_ROOT / ".beads" / "hooks"

STALE = '{"id":"x-1","status":"open"}\n'
FRESH = '{"id":"x-1","status":"closed"}\n{"id":"x-2","status":"open"}\n'


def git(cwd: Path, *args: str, env: dict | None = None, check: bool = True):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


def committed(cwd: Path, path: str) -> str:
    return git(cwd, "show", f"HEAD:{path}").stdout


class Harness:
    """A main checkout plus a worktree, both committing through the real hook."""

    def __init__(self, tmp_path: Path):
        stub = tmp_path / "stub"
        stub.mkdir()
        bd = stub / "bd"
        bd.write_text(
            textwrap.dedent(
                """\
                #!/bin/sh
                # `bd export -o PATH` writes $BD_EXPORT_PAYLOAD to PATH, or fails
                # with $BD_EXPORT_FAIL on stderr when that is set. Every other
                # subcommand (the managed hook block's `bd hooks run`) is a no-op.
                if [ "$1" = export ]; then
                  if [ -n "$BD_EXPORT_FAIL" ]; then
                    printf '%s\\n' "$BD_EXPORT_FAIL" >&2
                    exit 1
                  fi
                  printf '%s' "$BD_EXPORT_PAYLOAD" > "$3"
                fi
                exit 0
                """
            )
        )
        bd.chmod(0o755)
        python_stub = tmp_path / "python-stub"
        python_stub.write_text("#!/bin/sh\nexit 0\n")
        python_stub.chmod(0o755)

        self.env = {
            **os.environ,
            "PATH": f"{stub}:{os.environ['PATH']}",
            "PRE_COMMIT_PYTHON": str(python_stub),
            "BD_EXPORT_PAYLOAD": FRESH,
        }
        self.env.pop("BD_EXPORT_FAIL", None)

        self.main = tmp_path / "main"
        self.main.mkdir()
        git(self.main, "init", "-q", "-b", "main")
        git(self.main, "config", "user.email", "test@example.invalid")
        git(self.main, "config", "user.name", "Test")
        beads = self.main / ".beads"
        beads.mkdir()
        (beads / "issues.jsonl").write_text(STALE)
        (beads / "interactions.jsonl").write_text("A\n")
        git(self.main, "add", "-A")
        # Seeded before the hook is wired in, so the commit really holds STALE.
        git(self.main, "commit", "-qm", "seed")
        git(self.main, "config", "core.hooksPath", str(HOOKS))

        self.worktree = tmp_path / "wt"
        git(self.main, "worktree", "add", "-q", str(self.worktree), "-b", "topic")

    def commit(self, cwd: Path, message: str = "work", **env):
        (cwd / "work.txt").write_text(message + "\n")
        git(cwd, "add", "work.txt")
        return git(
            cwd, "commit", "-qm", message, env={**self.env, **env}, check=False
        )


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path)


def test_a_worktree_commit_carries_a_fresh_export(harness):
    result = harness.commit(harness.worktree)
    assert result.returncode == 0, result.stderr
    assert committed(harness.worktree, ".beads/issues.jsonl") == FRESH
    # The export was written where the commit was taken from, not where bd
    # would have put it on its own.
    assert (harness.main / ".beads" / "issues.jsonl").read_text() == STALE
    assert git(harness.worktree, "status", "--porcelain").stdout == ""


def test_a_main_checkout_commit_carries_a_fresh_export_too(harness):
    result = harness.commit(harness.main)
    assert result.returncode == 0, result.stderr
    assert committed(harness.main, ".beads/issues.jsonl") == FRESH
    assert git(harness.main, "status", "--porcelain").stdout == ""


def test_without_the_hook_the_worktree_commit_is_stale(harness):
    """The control: remove the hook and the same commit carries STALE.

    Without this the tests above could pass on a repository where something
    else already exported, and measure nothing about the hook.
    """
    git(harness.main, "config", "--unset", "core.hooksPath")
    result = harness.commit(harness.worktree)
    assert result.returncode == 0, result.stderr
    assert committed(harness.worktree, ".beads/issues.jsonl") == STALE


def test_lines_bd_appended_in_the_main_checkout_reach_the_worktree_commit(harness):
    # bd appends to the main checkout's copy; the branch has a line of its own.
    (harness.main / ".beads" / "interactions.jsonl").write_text("A\nB\n")
    (harness.worktree / ".beads" / "interactions.jsonl").write_text("A\nC\n")
    result = harness.commit(harness.worktree)
    assert result.returncode == 0, result.stderr
    assert committed(harness.worktree, ".beads/interactions.jsonl") == "A\nC\nB\n"
    assert not (harness.worktree / ".beads" / "interactions.jsonl.tmp").exists()


def test_a_failing_export_refuses_the_commit(harness):
    before = git(harness.worktree, "rev-parse", "HEAD").stdout
    result = harness.commit(harness.worktree, BD_EXPORT_FAIL="Error: dolt: table locked")
    assert result.returncode != 0
    assert "refusing to commit a stale" in result.stderr
    assert "table locked" in result.stderr
    assert git(harness.worktree, "rev-parse", "HEAD").stdout == before


def test_a_machine_without_a_database_commits_the_export_as_it_is(harness):
    result = harness.commit(
        harness.worktree, BD_EXPORT_FAIL="Error: no beads database found"
    )
    assert result.returncode == 0, result.stderr
    assert "no database on this machine" in result.stderr
    assert committed(harness.worktree, ".beads/issues.jsonl") == STALE


def test_a_machine_without_bd_commits_the_export_as_it_is(harness):
    entries = harness.env["PATH"].split(os.pathsep)
    without_bd = os.pathsep.join(e for e in entries if not (Path(e) / "bd").exists())
    assert shutil.which("bd", path=without_bd) is None
    result = harness.commit(harness.worktree, PATH=without_bd)
    assert result.returncode == 0, result.stderr
    assert committed(harness.worktree, ".beads/issues.jsonl") == STALE


def test_an_untracked_export_is_left_alone(harness):
    git(harness.main, "rm", "-q", "--cached", ".beads/issues.jsonl")
    git(harness.main, "commit", "-qm", "stop tracking the export", "--no-verify")
    git(harness.worktree, "rm", "-q", "--cached", ".beads/issues.jsonl")
    git(harness.worktree, "commit", "-qm", "stop tracking here too", "--no-verify")
    result = harness.commit(harness.worktree)
    assert result.returncode == 0, result.stderr
    assert git(harness.worktree, "ls-files", ".beads/issues.jsonl").stdout == ""
    assert (harness.worktree / ".beads" / "issues.jsonl").read_text() == STALE


def test_export_auto_is_off_so_bd_writes_never_touch_the_main_checkout():
    """The other half of stn-nkvl: with auto-export on, every bd write rewrote
    the main checkout's export and re-dirtied it between commits."""
    import yaml

    config = yaml.safe_load((REPO_ROOT / ".beads" / "config.yaml").read_text())
    assert config["export"]["auto"] is False


def test_interactions_log_merges_as_a_union():
    attributes = (REPO_ROOT / ".gitattributes").read_text()
    assert ".beads/interactions.jsonl merge=union" in attributes.splitlines()
