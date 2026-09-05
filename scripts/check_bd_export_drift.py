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


def committed_export() -> str | None:
    """The export as HEAD has it -- what the push would actually deliver.

    None when there is nothing to compare against: outside a git repo, before
    the first commit, or when the export has never been tracked.
    """
    result = subprocess.run(
        ["git", "show", f"HEAD:{EXPORT.as_posix()}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def fresh_export() -> str | None:
    """Export the database to a temp file and read it back.

    A temp file rather than stdout because `bd export -o -` writes a file
    literally named "-".
    """
    with tempfile.TemporaryDirectory() as tmp:
        destination = Path(tmp) / "issues.jsonl"
        result = subprocess.run(
            ["bd", "export", "-o", str(destination)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not destination.exists():
            return None
        return destination.read_text()


def main() -> int:
    if shutil.which("bd") is None:
        return 0
    if not EXPORT.exists():
        return 0

    committed = committed_export()
    if committed is None:
        # Not a git repo, no HEAD yet, or the export has never been committed.
        # There is no pushed state to hold the database against.
        return 0

    exported = fresh_export()
    if exported is None:
        # No database on this machine, or bd could not read it. Not this
        # check's business to fail the push over.
        return 0

    drift = describe_drift(committed, exported, working_tree=EXPORT.read_text())
    if drift is None:
        return 0

    print(f"{EXPORT} in HEAD disagrees with the beads database:\n", file=sys.stderr)
    print(drift, file=sys.stderr)
    print(
        "\nIf the difference is deliberate, commit it deliberately -- the point"
        "\nof this check is that the two never diverge without someone saying so.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
