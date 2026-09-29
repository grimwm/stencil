"""The committed issue export is published; keep a home directory out of it.

`.beads/issues.jsonl` is committed and this repository is public -- AGENTS.md
says so, and the CI workflow is written around fork pull requests. Issue
descriptions and notes are written by agents that routinely paste absolute
paths, so the export accumulates strings like
``/Users/<user>/Documents/Repositories/stencil/.claude/worktrees/<branch>/``.

SCOPE, measured rather than assumed (stn-zcw): six such paths were already in
the export before anyone noticed, no credentials or tokens were found, and the
only identity involved is a username git log already carries. This is hygiene,
not an incident -- which is exactly why it needs a guard rather than a
one-time scrub. The volume grows with every agent-written note, and nobody is
going to re-run the check by hand.

The hyphenated form is the same directory. Claude Code names its
per-project temp folder by replacing slashes with hyphens, so the home
path is stored as ``-Users-<user>-Documents-Repositories-stencil`` under
``/private/tmp/claude-501/``. pytest's basetemp is ``pytest-of-<user>``.
An ``ls -l`` paste carries the owner column. None of those match a
slash-form home path, which is how they stayed in the export while the
original guard was green.

THE FIX IS IN THE DATABASE, NEVER IN THIS FILE'S SUBJECT. Editing
`.beads/issues.jsonl` by hand desyncs it from the Dolt database and trips the
pre-push drift guard, which is the whole reason that guard exists. When this
test fails, fix the ISSUE -- `bd update`, or `bd import` for a field too large
to pass through argv -- and re-export.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

EXPORT = Path(__file__).parent.parent / ".beads" / "issues.jsonl"

# An absolute home path, which is a real leak -- and NOT the leak-detection
# snippets the tracker also carries. Those journal commands like
# ``'/Users/' + U``, where the username is a variable rather than a literal,
# and a naive search for "/Users/" reports every one of them. The trailing
# path-character class is what separates a real path from a string being
# built: `/Users/someone/Documents` matches, `/Users/'+U` does not.
HOME_PATH = re.compile(r"/(?:Users|home)/[a-z][a-z0-9_.-]{1,31}/[A-Za-z0-9._-]")

# Claude Code's per-project temp directory is the same home path with
# slashes turned into hyphens:
# /Users/someone/Documents/Repositories/stencil ->
# -Users-someone-Documents-Repositories-stencil
# The snippet ``'-Users-' + U`` does not match, because a quote is not a
# username character.
MANGLED_HOME = re.compile(r"-Users-[a-z][a-z0-9_.-]{1,31}-")

# pytest's basetemp is /tmp/pytest-of-<username>. The placeholder form
# pytest-of-<user> does not match.
PYTEST_USER_TMP = re.compile(r"pytest-of-[A-Za-z0-9_][A-Za-z0-9_.-]*")

# An `ls -l` owner column pasted from a repro. `<user>` does not match.
LS_OWNER = re.compile(r"  1 [a-z][a-z0-9_-]+  wheel")

LEAKS = (
    (HOME_PATH, "absolute home path"),
    (MANGLED_HOME, "hyphenated home path"),
    (PYTEST_USER_TMP, "pytest per-user basetemp"),
    (LS_OWNER, "ls owner column"),
)

# Everything the tracker has legitimate reason to carry stays out of this:
# a GitHub noreply address is the issue owner and is public by construction.
SECRETS = [
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"), "a GitHub token"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{22,}"), "a fine-grained GitHub token"),
    (re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}"), "an AWS access key id"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"xox[abposr]-[A-Za-z0-9-]{10,}"), "a Slack token"),
]


def records():
    # A FAILURE, not a skip. The export is committed, so a checkout without
    # it is broken rather than export-less -- and a guard that skips itself
    # when its subject is missing is the shape AGENTS.md records for the
    # drift hook: it reads as protection while doing nothing.
    assert EXPORT.is_file(), f"{EXPORT} is committed and should be here"
    for line in EXPORT.read_text().splitlines():
        line = line.strip()
        if line:
            yield json.loads(line)


def test_no_absolute_home_path_reaches_the_public_export():
    """A developer's home directory is not part of an issue's meaning.

    The path that prompted this was a worktree path inside a quoted review
    finding -- which is exactly how they arrive: pasted verbatim, in a field
    nobody reads again afterwards.
    """
    offenders = {}
    for record in records():
        blob = json.dumps(record)
        found = []
        for pattern, kind in LEAKS:
            hits = pattern.findall(blob)
            if hits:
                found.append(f"{len(hits)} {kind}")
        if found:
            offenders[record.get("id")] = ", ".join(found)

    assert not offenders, (
        "the committed export carries home-directory leaks: "
        + "; ".join(f"{i} ({n})" for i, n in sorted(offenders.items()))
        + ". Fix the ISSUE and re-export -- `bd update <id> --notes ...`, or "
        "`bd import` for a field too large for argv -- never by editing "
        ".beads/issues.jsonl, which desyncs it from the database and trips "
        "the pre-push drift guard."
    )


def test_the_pattern_does_not_fire_on_a_path_being_built():
    """The guard's own false-positive case, asserted so a future tightening
    of the regex cannot quietly start reporting the tracker's leak-check
    snippets as leaks. Those are the reason a plain "/Users/" search is
    useless here."""
    assert not HOME_PATH.search("U=basename(expanduser('~')); p = '/Users/' + U")
    assert not HOME_PATH.search("checked /home/ and /Users/ prefixes")
    assert HOME_PATH.search("/Users/someone/Documents/x.md")
    assert HOME_PATH.search("/home/someone/code/y.py")
    assert not MANGLED_HOME.search("'-Users-' + U")
    assert MANGLED_HOME.search("-Users-someone-Documents-Repositories-stencil")
    assert not PYTEST_USER_TMP.search("pytest-of-<user>/pytest-38")
    assert PYTEST_USER_TMP.search("pytest-of-someone/pytest-38")
    assert not LS_OWNER.search("-rw-r--r--  1 <user>  wheel")
    assert LS_OWNER.search("-rw-r--r--  1 someone  wheel")


@pytest.mark.parametrize("pattern,what", SECRETS, ids=[w for _, w in SECRETS])
def test_no_credential_shaped_string_reaches_the_public_export(pattern, what):
    """Nothing has ever matched these, and that is the point of adding them
    while the file is being written: the export is published, an agent writes
    into it unattended, and the cost of the check is one regex per run."""
    for record in records():
        assert not pattern.search(json.dumps(record)), (
            f"{record.get('id')} looks like it carries {what}"
        )


# Built at runtime rather than written down: a literal in the documented
# example shape is exactly what GitHub's secret scanning is for, and it filed
# an alert on this file the first time these were spelled out (2026-09-12).
# None of these is a credential; the AWS ones are Amazon's own example id.
_EXAMPLES = [
    ("ghp_", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "a GitHub token"),
    ("github_pat_", "11ABCDEFG0123456789_abcdefghij", "a fine-grained GitHub token"),
    ("AKIA", "IOSFODNN7EXAMPLE", "an AWS access key id"),
    ("ASIA", "IOSFODNN7EXAMPLE", "an AWS access key id"),
    ("xoxb-", "0123456789-abcdefghij", "a Slack token"),
    ("-----BEGIN ", "RSA PRIVATE KEY-----", "a private key"),
]


@pytest.mark.parametrize(
    "sample,what",
    [(prefix + rest, what) for prefix, rest, what in _EXAMPLES],
    ids=[prefix.strip("-_ ") for prefix, _, _ in _EXAMPLES],
)
def test_each_credential_shape_is_recognised(sample, what):
    """The patterns' own positive cases, so a regex loosened by accident
    cannot quietly stop matching the form it was written for."""
    assert any(p.search(sample) for p, w in SECRETS if w == what), sample
