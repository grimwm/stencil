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

    assert drift_check.committed_export()[0] == jsonl(OPEN)


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

    assert drift_check.committed_export()[0] is None
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

    assert drift_check.committed_export()[0] is None


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

    assert "Résumé — dash" in drift_check.committed_export()[0]


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


# Which revision is being pushed ---------------------------------------------
#
# HEAD is only the revision a push delivers when you happen to be standing on
# the branch you are pushing. `git push origin other-branch` from main would
# otherwise validate main's export against the database and pass, while the
# export actually being published went unchecked.
#
# Git names the refs on stdin, but `pre-commit hook-impl` consumes that before
# this hook runs. pre-commit re-publishes the local end as PRE_COMMIT_TO_REF.


def test_the_pushed_revision_is_preferred_over_head(drift_check, repo, monkeypatch):
    """The export as of the commit being pushed, not the one checked out."""
    import subprocess

    monkeypatch.chdir(repo)
    first = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

    (repo / ".beads" / "issues.jsonl").write_text(jsonl(CLOSED))
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "move on"], cwd=repo, check=True, capture_output=True
    )

    monkeypatch.setenv("PRE_COMMIT_TO_REF", first)

    (revision,) = drift_check.pushed_revisions()
    assert revision == first
    assert drift_check.committed_export(revision)[0] == jsonl(OPEN)


def test_a_deleted_branch_falls_back_to_head(drift_check, repo, monkeypatch):
    """Git names an all-zero sha for a deletion; there is no revision to read."""
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PRE_COMMIT_TO_REF", "0" * 40)

    assert drift_check.pushed_revisions() == ["HEAD"]


def test_a_junk_ref_is_not_passed_to_git(drift_check, repo, monkeypatch):
    """The value arrives from the environment, so it is validated before use."""
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PRE_COMMIT_TO_REF", "HEAD; rm -rf /")

    assert drift_check.pushed_revisions() == ["HEAD"]


def test_an_unreadable_repository_blocks_the_push(
    drift_check, repo, monkeypatch, bd_installed
):
    """A corrupt object is not the same as an export that was never tracked.

    Both leave nothing to compare; only one of them is a state worth pushing
    past, and treating them alike is how the check ends up silent about a
    repository it could not read.
    """
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        drift_check,
        "committed_export",
        lambda revision="HEAD": (None, "fatal: bad object HEAD"),
    )
    monkeypatch.setattr(drift_check, "fresh_export", lambda: (jsonl(OPEN), ""))

    assert drift_check.main() == 1


def test_an_untracked_export_still_passes(drift_check, repo, monkeypatch, bd_installed):
    """The benign half of the same branch: git says the path is not in there."""
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        drift_check,
        "committed_export",
        lambda revision="HEAD": (
            None,
            "fatal: path '.beads/issues.jsonl' does not exist in 'HEAD'",
        ),
    )
    monkeypatch.setattr(drift_check, "fresh_export", lambda: (jsonl(CLOSED), ""))

    assert drift_check.main() == 0


# Several refs in one push ----------------------------------------------------
#
# `git push --all`, or `git push origin a b`, sends one update line per ref.
# pre-commit collapses those into a single from/to pair, so the wrapper captures
# the local shas itself and passes them on. Branches can carry different
# historical exports; checking only one of them lets the others through.


def test_every_pushed_revision_is_checked(drift_check, repo, monkeypatch, bd_installed):
    """One stale branch in a push of two must still fail it."""
    import subprocess

    monkeypatch.chdir(repo)
    stale = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

    (repo / ".beads" / "issues.jsonl").write_text(jsonl(CLOSED))
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "current"], cwd=repo, check=True, capture_output=True
    )
    current = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

    # The database agrees with `current`, so a check that looked only there
    # would pass the push.
    monkeypatch.setattr(drift_check, "fresh_export", lambda: (jsonl(CLOSED), ""))
    monkeypatch.setenv("BD_PUSHED_REVISIONS", f"{current} {stale}")

    assert drift_check.main() == 1


def test_a_push_of_current_revisions_passes(
    drift_check, repo, monkeypatch, bd_installed
):
    monkeypatch.chdir(repo)
    head = drift_check.subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    monkeypatch.setattr(drift_check, "fresh_export", lambda: (jsonl(OPEN), ""))
    monkeypatch.setenv("BD_PUSHED_REVISIONS", f"{head} {head}")

    assert drift_check.main() == 0


def test_the_pushed_revisions_list_is_validated(drift_check, repo, monkeypatch):
    """Values arrive from the environment, so junk is dropped, not handed to git."""
    monkeypatch.chdir(repo)
    monkeypatch.setenv("BD_PUSHED_REVISIONS", "HEAD;rm -rf / 0000000000 zzzz")

    assert drift_check.pushed_revisions() == ["HEAD"]


def test_the_wrapper_hands_over_only_real_local_shas():
    """The awk in .beads/hooks/pre-push, exercised as git would drive it."""
    import subprocess
    import textwrap

    updates = textwrap.dedent("""\
        refs/heads/a aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa refs/heads/a 1111111111111111111111111111111111111111
        refs/heads/gone 0000000000000000000000000000000000000000 refs/heads/gone 2222222222222222222222222222222222222222
        refs/heads/b bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb refs/heads/b 0000000000000000000000000000000000000000
        """)
    extract = (
        "awk '$2 ~ /^[0-9a-fA-F]+$/ && $2 !~ /^0+$/ { print $2 }' | sort -u | tr '\\n' ' '"
    )
    out = subprocess.run(
        ["sh", "-c", extract], input=updates, capture_output=True, text=True
    ).stdout.split()

    assert out == ["a" * 40, "b" * 40], "a deletion leaked through, or a sha was lost"
