"""The pre-push guard on .beads/issues.jsonl.

stn-cix. The bug this protects against is silent by construction -- the working
tree is clean and only the export is behind -- so the detection logic is worth
testing on its own rather than trusting it to fire correctly the one time it
matters.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "check_bd_export_drift.py"


@pytest.fixture(scope="module")
def drift_check():
    spec = importlib.util.spec_from_file_location("check_bd_export_drift", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def jsonl(*issues: dict) -> str:
    return "".join(json.dumps(issue) + "\n" for issue in issues)


OPEN = {"id": "stn-1", "title": "One", "status": "open"}
CLOSED = {"id": "stn-1", "title": "One", "status": "closed"}
OTHER = {"id": "stn-2", "title": "Two", "status": "open"}


def test_identical_exports_are_in_sync(drift_check):
    assert drift_check.describe_drift(jsonl(OPEN), jsonl(OPEN)) is None


def test_an_issue_closed_after_the_commit_is_reported(drift_check):
    """The first cause from the bug: a bd write that lands after the commit."""
    drift = drift_check.describe_drift(jsonl(OPEN), jsonl(CLOSED))

    assert drift is not None
    assert "changed since the commit" in drift
    assert "stn-1" in drift


def test_an_issue_created_after_the_commit_is_reported(drift_check):
    """The case that actually happened: 87 committed against 90 in the database."""
    drift = drift_check.describe_drift(jsonl(OPEN), jsonl(OPEN, OTHER))

    assert drift is not None
    assert "missing from the commit" in drift
    assert "stn-2" in drift
    assert "1 issues" in drift and "2" in drift


def test_an_issue_only_in_the_commit_is_reported(drift_check):
    """The other direction -- a stale export naming something since deleted."""
    drift = drift_check.describe_drift(jsonl(OPEN, OTHER), jsonl(OPEN))

    assert drift is not None
    assert "in the commit but not the database" in drift
    assert "stn-2" in drift


def test_reordering_alone_is_called_out_as_formatting(drift_check):
    """Worth distinguishing: nothing is missing, so the advice is different."""
    drift = drift_check.describe_drift(jsonl(OPEN, OTHER), jsonl(OTHER, OPEN))

    assert drift is not None
    assert "same 2 issues" in drift


def test_a_long_list_of_ids_is_truncated(drift_check):
    """A first-time export should not print several hundred ids at a failed push."""
    many = [{"id": f"stn-{n}", "title": "x"} for n in range(20)]
    drift = drift_check.describe_drift(jsonl(), jsonl(*many))

    assert drift is not None
    assert "+12 more" in drift


def test_an_unparseable_commit_is_reported_rather_than_crashing(drift_check):
    """A truncated export should fail the push, not the hook."""
    drift = drift_check.describe_drift("{not json\n", jsonl(OPEN))

    assert drift is not None
    assert "not valid JSONL" in drift


# The comparison basis --------------------------------------------------------
#
# describe_drift above is pure and was well covered from the start. What was
# not covered is which two exports main() hands it, and that is where the guard
# leaked: it read .beads/issues.jsonl from the working tree, which beads
# rewrites outside of any commit. A push could therefore be allowed while HEAD
# and the database genuinely disagreed -- the precise silence stn-cix exists to
# break.


@pytest.fixture
def repo(tmp_path):
    """A git repo with a committed export, and helpers to move each side."""
    import subprocess

    def git(*args):
        subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True
        )

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    beads = tmp_path / ".beads"
    beads.mkdir()
    (beads / "issues.jsonl").write_text(jsonl(OPEN))
    git("add", "-A")
    git("commit", "-qm", "commit the export")
    return tmp_path


@pytest.fixture
def bd_installed(monkeypatch):
    """Report bd as present regardless of the machine.

    main() short-circuits when bd is not on PATH, which is the case on CI and
    on any contributor's box that has never installed it. These tests are about
    what the check does once it is able to run at all, so requiring the real
    binary would make them assert nothing wherever it is missing.
    """
    import shutil

    real = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, *a, **kw: "/usr/bin/bd" if name == "bd" else real(name, *a, **kw),
    )


def test_the_committed_export_comes_from_head(drift_check, repo, monkeypatch):
    """Not from the working tree, which beads rewrites between commits."""
    monkeypatch.chdir(repo)
    (repo / ".beads" / "issues.jsonl").write_text(jsonl(CLOSED))

    assert drift_check.committed_export() == jsonl(OPEN)


def test_an_uncommitted_export_cannot_hide_a_stale_head(
    drift_check, repo, monkeypatch, bd_installed
):
    """The regression. The working tree agreeing with the database is not proof.

    Before this, main() compared the working-tree file against a fresh export.
    Running `bd export` without committing made those two agree, so the guard
    passed while the commit being pushed still carried the old export.
    """
    monkeypatch.chdir(repo)
    # Exactly the masking state: working tree matches the database, HEAD does not.
    (repo / ".beads" / "issues.jsonl").write_text(jsonl(CLOSED))
    monkeypatch.setattr(drift_check, "fresh_export", lambda: (jsonl(CLOSED), ""))

    assert drift_check.main() == 1


def test_a_committed_export_matching_the_database_passes(
    drift_check, repo, monkeypatch, bd_installed
):
    monkeypatch.chdir(repo)
    monkeypatch.setattr(drift_check, "fresh_export", lambda: (jsonl(OPEN), ""))

    assert drift_check.main() == 0


def test_an_export_never_committed_is_left_alone(
    drift_check, tmp_path, monkeypatch, bd_installed
):
    """Outside a repo, or before the export is tracked, there is no push to guard."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".beads").mkdir()
    (tmp_path / ".beads" / "issues.jsonl").write_text(jsonl(OPEN))
    monkeypatch.setattr(drift_check, "fresh_export", lambda: (jsonl(CLOSED), ""))

    assert drift_check.committed_export() is None
    assert drift_check.main() == 0


def test_an_export_that_is_current_but_uncommitted_says_so(drift_check):
    """The remediation differs: `bd export` has already been run, so commit it.

    Telling someone to re-export a file they just exported is how a guard
    trains people to ignore it.
    """
    drift = drift_check.describe_drift(
        jsonl(OPEN), jsonl(CLOSED), working_tree=jsonl(CLOSED)
    )

    assert drift is not None
    assert "uncommitted" in drift


def test_a_stale_export_is_told_to_re_export(drift_check):
    """The opposite state, and the opposite instruction."""
    drift = drift_check.describe_drift(
        jsonl(OPEN), jsonl(CLOSED), working_tree=jsonl(OPEN)
    )

    assert drift is not None
    assert "bd export" in drift
    assert "uncommitted" not in drift


def test_a_missing_git_is_left_alone(drift_check, monkeypatch):
    """The hook must not crash where git cannot be run.

    subprocess raises rather than returning non-zero when the binary is absent,
    and an exception out of a pre-push hook is a worse failure than the drift
    it was looking for.
    """
    import subprocess

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", no_git)

    assert drift_check.committed_export() is None


def test_a_non_ascii_export_is_read_as_utf8(drift_check, repo, monkeypatch):
    """Issue text carries em-dashes, so a hook running under LANG=C must not die.

    text=True and read_text() both default to the locale encoding, which is
    ASCII on a bare CI runner.
    """
    monkeypatch.chdir(repo)
    import subprocess

    # ensure_ascii=False, which is what `bd export` writes -- the real export
    # carries several hundred raw em-dash and middot bytes.
    raw = json.dumps(
        {"id": "stn-1", "title": "Résumé — dash", "status": "open"},
        ensure_ascii=False,
    )
    (repo / ".beads" / "issues.jsonl").write_text(raw + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "unicode"], cwd=repo, check=True, capture_output=True
    )

    assert "Résumé — dash" in drift_check.committed_export()


# Failing open ---------------------------------------------------------------
#
# The states where the check cannot answer the question. A guard that returns
# success because it could not look is indistinguishable, at the terminal, from
# one that looked and found nothing.


def test_a_deleted_export_still_compares_head(
    drift_check, repo, monkeypatch, bd_installed
):
    """Removing the file must not be a way to get a stale commit pushed."""
    monkeypatch.chdir(repo)
    (repo / ".beads" / "issues.jsonl").unlink()
    monkeypatch.setattr(drift_check, "fresh_export", lambda: (jsonl(CLOSED), ""))

    assert drift_check.main() == 1


def test_a_broken_database_blocks_the_push(
    drift_check, repo, monkeypatch, bd_installed
):
    """bd is installed and HEAD carries an export, but the database will not read.

    Nothing about that is benign, and it is the one case where returning 0
    means "did not check" while looking exactly like "checked and agreed".
    """
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        drift_check, "fresh_export", lambda: (None, "panic: dolt: corrupt chunk")
    )

    assert drift_check.main() == 1


def test_no_database_on_this_machine_is_left_alone(
    drift_check, repo, monkeypatch, bd_installed
):
    """A contributor who has bd but has never run `bd init` can still push.

    Distinguished from a broken database by what bd itself reports, so the
    check stays out of the way in the one state that is genuinely empty.
    """
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        drift_check,
        "fresh_export",
        lambda: (None, "Error: no beads database found\nHint: run 'bd init'"),
    )

    assert drift_check.main() == 0
