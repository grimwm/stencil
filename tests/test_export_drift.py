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
