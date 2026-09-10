"""What the test tier leaves on disk, and what it says when the disk runs out.

stn-7im: a full run exhausted a 7.7GB tmpfs `/tmp`, and the failure did not
look like a disk failure -- two runs reported a plain exit 1, and the harness
capturing their output hit ENOSPC of its own. Clearing the basetemp took /tmp
from 100% to 2%, so the tier's own temp trees were the whole of it.

The arithmetic, measured rather than estimated: each generated package is
about 10.75MB, almost all of it the inlined Bootstrap, highlight.js, Mermaid
and fonts in `html-template.html`; the unit tier alone generates a few hundred
of them; and pytest kept the last three runs. Nothing about that is wrong per
test -- it is the retention across passing tests, three runs deep, that does
not fit.

BOTH GUARDS MEASURE THE BASETEMP TREE, never `/tmp`. A guard that reads the
filesystem passes on a machine with a big tmpfs and fails on a small one,
which is nobody's useful signal -- and it would fail for a colleague whose
`/tmp` is busy with something else entirely.

The retention guard runs an inner pytest as a subprocess and looks at what is
left behind. tests/test_cli.py sets the precedent and gives the reasoning: some
questions are only answerable from outside the process. Asserting on
`config.getini("tmp_path_retention_policy")` would restate the setting rather
than test it, and AGENTS.md records what this repository thinks of a guard that
can silently stop guarding.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"

INNER = '''
def test_passes(tmp_path):
    (tmp_path / "artifact.txt").write_text("x" * 1024)


def test_fails(tmp_path):
    (tmp_path / "artifact.txt").write_text("x" * 1024)
    assert False, "kept on purpose"
'''


def _run_inner(tmp_path: Path) -> Path:
    """Run a throwaway two-test module under its own basetemp, and return it."""
    project = tmp_path / "inner"
    project.mkdir()
    (project / "test_inner.py").write_text(INNER)
    basetemp = tmp_path / "bt"

    # -c THIS repository's pyproject.toml, which is the point: an inner run
    # left to find its own rootdir would discover the throwaway directory,
    # inherit nothing, and cheerfully prove that pytest's DEFAULT retention
    # behaves the way pytest documents. The question is what THIS project
    # configures.
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "test_inner.py",
            "-c",
            str(PYPROJECT),
            "-p",
            "no:cacheprovider",
            f"--basetemp={basetemp}",
            "-q",
        ],
        cwd=project,
        capture_output=True,
        text=True,
    )
    return basetemp


def _trees(basetemp: Path) -> set[str]:
    return {p.name for p in basetemp.iterdir() if p.is_dir()} if basetemp.exists() else set()


def test_a_passing_test_leaves_nothing_behind(tmp_path):
    """The whole of stn-7im in one assertion.

    A passing test's tmp_path is 10.75MB of inlined assets nobody will read,
    and it was kept for three runs.
    """
    kept = _trees(_run_inner(tmp_path))
    assert not any(name.startswith("test_passes") for name in kept), (
        f"a passing test's temp tree was retained: {sorted(kept)}"
    )


def test_a_failing_test_keeps_its_tree(tmp_path):
    """The other half, and the reason the policy is `failed` rather than
    `none`: the output of a test that failed is the thing you actually want to
    look at, and deleting it would trade one problem for a worse one."""
    kept = _trees(_run_inner(tmp_path))
    assert any(name.startswith("test_fails") for name in kept), (
        f"a failing test's temp tree was discarded, leaving nothing to "
        f"inspect: {sorted(kept)}"
    )


# --- what one package costs -------------------------------------------------


def test_a_generated_package_stays_around_its_measured_size(tmp_path):
    """The other half of the arithmetic, guarded rather than remembered.

    A generated package is 10.75MB today, and almost all of it is two files:
    html-template.html at 5.30MB and slide-template.html at 5.32MB, each
    carrying an inlined copy of Bootstrap, highlight.js, Mermaid and the
    fonts. That is the deliberate trade recorded in AGENTS.md -- a handout
    that makes no network request -- so this cap is not an objection to it.

    What the cap protects is the multiplication. Every generated package in
    every test pays that size, so a change that doubles it doubles the whole
    tier's footprint, and stn-7im is what that looks like when it stops
    fitting. The headroom is generous enough that ordinary growth does not
    red it and tight enough that a doubling does.
    """
    from tests.conftest import DEMO_CONFIG, make_package

    package = make_package(tmp_path, DEMO_CONFIG)
    total = sum(p.stat().st_size for p in package.rglob("*") if p.is_file())

    cap = 20 * 1024 * 1024
    assert total < cap, (
        f"a generated package is {total / 1e6:.2f}MB, over the {cap / 1e6:.0f}MB "
        f"cap. It was 10.75MB when this guard was written, and every test that "
        f"generates one pays it -- see stn-7im for what that multiplies into."
    )


# --- the disk-space annotation ----------------------------------------------
#
# Exercised against a synthetic report and an injected threshold. Never by
# actually filling a disk, and never needing a container runtime: a guard that
# can only run on a full filesystem is a guard nobody runs.


class _Report:
    """The two attributes the hook reads, and the list it appends to."""

    def __init__(self, when="call", failed=True):
        self.when = when
        self.failed = failed
        self.sections = []


def test_the_note_names_the_path_and_the_space_when_low(tmp_path):
    from tests.conftest import _low_space_note

    note = _low_space_note(tmp_path, threshold=10**18)
    assert note is not None
    assert str(tmp_path) in note, "the note should name the tree it is about"
    assert "--basetemp" in note, "and the way out of it"


def test_there_is_no_note_when_the_disk_is_fine(tmp_path):
    """The load-bearing half. An annotation on every failure is noise, and
    noise is how a reader learns to skip the section that will one day be the
    answer."""
    from tests.conftest import _low_space_note

    assert _low_space_note(tmp_path, threshold=0) is None


def test_the_helper_survives_a_basetemp_that_does_not_exist(tmp_path):
    """With retention="failed" the tree may be gone by the time this is asked,
    and a guard that raises while explaining a failure is worse than one that
    stays quiet. It walks up until something answers."""
    from tests.conftest import _free_bytes

    missing = tmp_path / "gone" / "deeper" / "still-gone"
    assert _free_bytes(missing) is not None


class _Item:
    """Just enough of a pytest item for the hook to find a basetemp."""

    def __init__(self, basetemp):
        factory = type("F", (), {"getbasetemp": lambda self: basetemp})()
        self.config = type("C", (), {"_tmp_path_factory": factory})()


def _drive(item, report):
    """Run the real wrapper hook and hand it `report`.

    Driving the generator protocol by hand rather than rebuilding what the
    hook does: an earlier version of these two tests appended the section
    itself and asserted it was there, which tested the test.
    """
    from tests import conftest

    generator = conftest.pytest_runtest_makereport(item, None)
    next(generator)
    try:
        generator.send(report)
    except StopIteration as stop:
        return stop.value
    raise AssertionError("the hook did not stop after one yield")


def test_a_failing_report_is_annotated_when_space_is_low(tmp_path, monkeypatch):
    from tests import conftest

    monkeypatch.setattr(conftest, "LOW_SPACE_BYTES", 10**18)
    report = _Report()
    _drive(_Item(tmp_path), report)

    assert [name for name, _ in report.sections] == ["Disk space"]
    assert str(tmp_path) in report.sections[0][1]


def test_a_failing_report_is_left_alone_when_there_is_room(tmp_path, monkeypatch):
    """An annotation on every failure is noise, and noise is how a reader
    learns to skip the section that will one day be the answer."""
    from tests import conftest

    monkeypatch.setattr(conftest, "LOW_SPACE_BYTES", 0)
    report = _Report()
    _drive(_Item(tmp_path), report)

    assert report.sections == []


def test_a_passing_report_is_left_alone(tmp_path, monkeypatch):
    """Only failures are annotated; a passing run has nothing to explain."""
    from tests import conftest

    monkeypatch.setattr(conftest, "LOW_SPACE_BYTES", 10**18)
    report = _Report(failed=False)
    _drive(_Item(tmp_path), report)

    assert report.sections == []


def test_a_setup_error_is_left_alone(tmp_path, monkeypatch):
    """`when` is "setup", "call" or "teardown", and annotating all three would
    put the same note on a test three times."""
    from tests import conftest

    monkeypatch.setattr(conftest, "LOW_SPACE_BYTES", 10**18)
    report = _Report(when="setup")
    _drive(_Item(tmp_path), report)

    assert report.sections == []
