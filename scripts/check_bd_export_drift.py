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


def describe_drift(committed: str, exported: str) -> str | None:
    """Describe how the two exports differ, or None when they agree.

    Reports which issues differ rather than that the files do, because the
    useful question at a failed push is always "what did I forget to commit".
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
        return (
            f"the export is byte-different but describes the same "
            f"{len(after)} issues -- probably only formatting"
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
    return "\n".join(lines)


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

    exported = fresh_export()
    if exported is None:
        # No database on this machine, or bd could not read it. Not this
        # check's business to fail the push over.
        return 0

    drift = describe_drift(EXPORT.read_text(), exported)
    if drift is None:
        return 0

    print(f"{EXPORT} disagrees with the beads database:\n", file=sys.stderr)
    print(drift, file=sys.stderr)
    print(
        f"\nRun `bd export -o {EXPORT}` and commit the result, then push again."
        "\nIf the difference is deliberate, commit it deliberately -- the point"
        "\nof this check is that the two never diverge without someone saying so.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
