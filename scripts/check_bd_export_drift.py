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


def is_object_name(candidate: str) -> bool:
    """A hex object name that is not the all-zero sha git uses for a deletion.

    Every revision reaches this program through the environment, so none of them
    is handed to git before it has been shown to be an object name and nothing
    else.
    """
    return bool(re.fullmatch(r"[0-9a-fA-F]{7,64}", candidate)) and bool(
        candidate.strip("0")
    )


def pushed_revisions() -> list[str]:
    """Every revision whose export the push would publish.

    HEAD is only one of them when you happen to be standing on the single branch
    you are pushing; `git push origin other-branch` from main would otherwise
    check main's export, and `git push --all` would check one branch and let the
    rest through unexamined. Branches can carry different historical exports, so
    one of them agreeing proves nothing about the others.

    Git names every ref update on stdin, but `pre-commit hook-impl` consumes
    that stream and reduces it to a single from/to pair. .beads/hooks/pre-push
    captures the local shas before handing the stream on, and passes them here
    as BD_PUSHED_REVISIONS. PRE_COMMIT_TO_REF is the fallback for a hook chain
    without that wrapper, and HEAD the fallback for no hook at all.
    """
    for variable in ("BD_PUSHED_REVISIONS", "PRE_COMMIT_TO_REF"):
        revisions = [
            r for r in os.environ.get(variable, "").split() if is_object_name(r)
        ]
        if revisions:
            return revisions
    return ["HEAD"]


def committed_export(revision: str = "HEAD") -> tuple[str | None, str]:
    """The export as of one revision being pushed.

    Returns the export and, when it could not be read, what git said. The
    caller has to tell an export that was never tracked apart from a repository
    it could not read, and only git knows which it is.

    Decoded as UTF-8 explicitly. The export carries em-dashes and middots, and
    text mode otherwise decodes with the locale encoding -- which is ASCII on a
    runner with no LANG set, turning a drift check into a UnicodeDecodeError.
    """
    try:
        result = subprocess.run(
            ["git", "show", f"{revision}:{EXPORT.as_posix()}"],
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

    # One snapshot for the whole push: every pushed revision is held against the
    # same database, which is what "the export matches the database" has to mean
    # when a push carries more than one ref.
    exported, why = fresh_export()
    if exported is None:
        if NO_DATABASE in why.lower():
            return 0
        # bd is installed and the database will not read. Returning 0 here would
        # look exactly like agreement.
        print(
            f"cannot check {EXPORT} against the beads database:\n\n{why}",
            file=sys.stderr,
        )
        return 1

    # Deliberately not gated on the file existing: deleting it must not be a way
    # to push a stale commit. Absent, there is simply no working copy for the
    # advice to take into account.
    working_tree = EXPORT.read_text(encoding="utf-8") if EXPORT.exists() else None

    for revision in pushed_revisions():
        committed, unreadable = committed_export(revision)
        if committed is None:
            if not unreadable or any(h in unreadable for h in NO_ARTIFACT):
                # No git, no repository, or the export has never been tracked in
                # this revision. Nothing to hold the database against.
                continue
            print(
                f"cannot read {EXPORT} from {describe(revision)}:\n\n{unreadable}",
                file=sys.stderr,
            )
            return 1

        drift = describe_drift(committed, exported, working_tree=working_tree)
        if drift is None:
            continue

        print(
            f"{EXPORT} in {describe(revision)} disagrees with the beads "
            "database:\n",
            file=sys.stderr,
        )
        print(drift, file=sys.stderr)
        print(
            "\nIf the difference is deliberate, commit it deliberately -- the point"
            "\nof this check is that the two never diverge without someone saying so.",
            file=sys.stderr,
        )
        return 1

    return 0


def describe(revision: str) -> str:
    return "HEAD" if revision == "HEAD" else f"{revision[:12]}, a revision being pushed"


if __name__ == "__main__":
    sys.exit(main())
