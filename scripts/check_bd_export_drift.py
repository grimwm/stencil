#!/usr/bin/env python3
"""Refuse to push when the committed issue export disagrees with the database.

Issues live in a Dolt database that is not tracked by git; .beads/issues.jsonl
is a passive export of it, and the only view a reviewer gets. Twice the two have
been committed out of step, for two different reasons:

  - a `bd close` that ran after the last commit was simply never exported into
    it, which happens every time issues are closed in the same shell invocation
    as a merge; and

  - the pre-commit framework stashes unstaged changes while hooks run, and
    beads' own hook rewrites the export inside that window, so the staged
    snapshot can be taken before beads finishes.

The second is the dangerous one: the working tree looks clean afterwards, and
nothing says git and the database disagree.

This turns that silence into a failed push, which is all it needs to be. It runs
at pre-push rather than pre-commit deliberately -- pre-commit is the window the
race happens in, and a check that runs inside it would be racing too.

The database is compared against the export in HEAD, never against the file in
the working tree. beads rewrites .beads/issues.jsonl outside of any commit, so
the working copy can agree with the database while the commit being pushed
still carries a stale export -- which is the same silence in a new place.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EXPORT = Path(".beads/issues.jsonl")


def issues_by_id(payload: str) -> dict[str, dict]:
    """Parse a JSONL export into {id: issue}, ignoring blank lines."""
    issues = {}
    for line in payload.splitlines():
        line = line.strip()
        if not line:
            continue
        issue = json.loads(line)
        issues[issue.get("id", "")] = issue
    return issues


def describe_drift(
    committed: str, exported: str, working_tree: str | None = None
) -> str | None:
    """Describe how the two exports differ, or None when they agree.

    Reports which issues differ rather than that the files do, because the
    useful question at a failed push is always "what did I forget to commit".

    `working_tree` is the export on disk, when it is known. It only changes the
    advice: if it already matches the database, `bd export` has been run and the
    fix is to commit it. Telling someone to re-export a file they just exported
    is how a guard teaches people to ignore it.
    """
    if committed == exported:
        return None

    try:
        before = issues_by_id(committed)
        after = issues_by_id(exported)
    except json.JSONDecodeError:
        return "the committed export is not valid JSONL"

    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(i for i in set(before) & set(after) if before[i] != after[i])

    if not (added or removed or changed):
        return _with_advice(
            f"the export is byte-different but describes the same "
            f"{len(after)} issues -- probably only formatting",
            exported,
            working_tree,
        )

    lines = [
        f"the committed export has {len(before)} issues, "
        f"the database has {len(after)}"
    ]
    for label, ids in (
        ("missing from the commit", added),
        ("in the commit but not the database", removed),
        ("changed since the commit", changed),
    ):
        if ids:
            shown = ", ".join(ids[:8])
            more = f" (+{len(ids) - 8} more)" if len(ids) > 8 else ""
            lines.append(f"  {label}: {shown}{more}")
    return _with_advice("\n".join(lines), exported, working_tree)


def _with_advice(message: str, exported: str, working_tree: str | None) -> str:
    """Append the one remediation that actually applies.

    Two different states reach here, and they need opposite instructions: an
    export that was never refreshed has to be regenerated, while one that is
    current but uncommitted must not be -- re-running `bd export` there is a
    no-op that leaves the push failing for the same reason.
    """
    if working_tree is not None and working_tree == exported:
        return message + (
            f"\n\nThe export on disk already matches the database -- it is just "
            f"uncommitted.\nCommit {EXPORT} rather than exporting again."
        )
    return message + (
        f"\n\nRun `bd export -o {EXPORT}` and commit the result, then push again."
    )


# git failures that mean "there is no committed export here" rather than
# "something is wrong". Each is a state the check has nothing to say about;
# anything else -- a corrupt object, an unreadable pack -- is not.
NO_ARTIFACT = (
    "does not exist",
    "exists on disk, but not in",
    "not a git repository",
    "unknown revision",
    "ambiguous argument",
    "invalid object name",
)


def pushed_revision() -> str:
    """The revision whose export the push would publish.

    HEAD is only that revision when you happen to be standing on the branch you
    are pushing; `git push origin other-branch` from main would otherwise check
    main's export and let the pushed one through unexamined.

    Git names the refs on stdin, but `pre-commit hook-impl` consumes that before
    this hook runs. pre-commit re-publishes the local end of the update as
    PRE_COMMIT_TO_REF, which is the same information. It arrives from the
    environment, so it is validated as a hex object name before it is used --
    an all-zero sha means a branch deletion, which has no revision to read.
    """
    candidate = os.environ.get("PRE_COMMIT_TO_REF", "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{7,64}", candidate) and candidate.strip("0"):
        return candidate
    return "HEAD"


def committed_export() -> tuple[str | None, str]:
    """The export as of the revision being pushed.

    Returns the export and, when it could not be read, what git said. The
    caller has to tell an export that was never tracked apart from a repository
    it could not read, and only git knows which it is.

    Decoded as UTF-8 explicitly. The export carries em-dashes and middots, and
    text mode otherwise decodes with the locale encoding -- which is ASCII on a
    runner with no LANG set, turning a drift check into a UnicodeDecodeError.
    """
    try:
        result = subprocess.run(
            ["git", "show", f"{pushed_revision()}:{EXPORT.as_posix()}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError:
        # No git on this machine. subprocess raises rather than returning
        # non-zero, and an exception out of a hook is worse than the drift.
        return None, ""
    if result.returncode != 0:
        return None, (result.stderr or "").strip()
    return result.stdout, ""


# What bd prints when the workspace has no database at all. That state is
# genuinely empty rather than broken -- a contributor who has bd on PATH but has
# never run `bd init` has nothing to compare -- so it is the one export failure
# the check steps out of the way for. Every other failure is a database it
# could not read, which is not something to push past.
NO_DATABASE = "no beads database"


def fresh_export() -> tuple[str | None, str]:
    """Export the database to a temp file and read it back.

    Returns the export and, when it could not be produced, whatever bd said
    about why -- the caller has to tell an empty workspace apart from a broken
    one, and bd is the only thing that knows which it is.

    A temp file rather than stdout because `bd export -o -` writes a file
    literally named "-".
    """
    with tempfile.TemporaryDirectory() as tmp:
        destination = Path(tmp) / "issues.jsonl"
        try:
            result = subprocess.run(
                ["bd", "export", "-o", str(destination)],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        except OSError as error:
            return None, str(error)
        if result.returncode != 0 or not destination.exists():
            return None, (result.stderr or result.stdout or "").strip()
        return destination.read_text(encoding="utf-8"), ""


def main() -> int:
    if shutil.which("bd") is None:
        return 0

    committed, unreadable = committed_export()
    if committed is None:
        if not unreadable or any(h in unreadable for h in NO_ARTIFACT):
            # No git, no revision, or the export has never been tracked. There
            # is no pushed state to hold the database against.
            return 0
        # The revision exists and git could not read the export out of it.
        print(
            f"cannot read {EXPORT} from the revision being pushed:"
            f"\n\n{unreadable}",
            file=sys.stderr,
        )
        return 1

    exported, why = fresh_export()
    if exported is None:
        if NO_DATABASE in why.lower():
            return 0
        # bd is installed and HEAD carries an export, but the database will not
        # read. Returning 0 here would look exactly like agreement.
        print(
            f"cannot check {EXPORT} against the beads database:\n\n{why}",
            file=sys.stderr,
        )
        return 1

    # Deliberately not gated on the file existing: deleting it must not be a
    # way to push a stale commit. Absent, there is simply no working copy for
    # the advice to take into account.
    working_tree = (
        EXPORT.read_text(encoding="utf-8") if EXPORT.exists() else None
    )

    drift = describe_drift(committed, exported, working_tree=working_tree)
    if drift is None:
        return 0

    revision = pushed_revision()
    where = "HEAD" if revision == "HEAD" else f"{revision[:12]}, the revision being pushed"
    print(f"{EXPORT} in {where} disagrees with the beads database:\n", file=sys.stderr)
    print(drift, file=sys.stderr)
    print(
        "\nIf the difference is deliberate, commit it deliberately -- the point"
        "\nof this check is that the two never diverge without someone saying so.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
