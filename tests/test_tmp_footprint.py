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

import os
import subprocess
import sys
import time
from pathlib import Path

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def _inner_env(**overrides):
    """See conftest.inner_pytest_env: an inner pytest must not inherit this
    run's xdist worker identity, or it exempts itself from the guard these
    tests exist to check."""
    from conftest import inner_pytest_env

    return inner_pytest_env(**overrides)

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
        env=_inner_env(),
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
    from conftest import DEMO_CONFIG, make_package

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
    from conftest import _low_space_note

    note = _low_space_note(tmp_path, threshold=10**18)
    assert note is not None
    assert str(tmp_path) in note, "the note should name the tree it is about"
    assert "--basetemp" in note, "and the way out of it"


def test_there_is_no_note_when_the_disk_is_fine(tmp_path):
    """The load-bearing half. An annotation on every failure is noise, and
    noise is how a reader learns to skip the section that will one day be the
    answer."""
    from conftest import _low_space_note

    assert _low_space_note(tmp_path, threshold=0) is None


def test_the_helper_survives_a_basetemp_that_does_not_exist(tmp_path):
    """With retention="failed" the tree may be gone by the time this is asked,
    and a guard that raises while explaining a failure is worse than one that
    stays quiet. It walks up until something answers."""
    from conftest import _free_bytes

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
    import conftest

    generator = conftest.pytest_runtest_makereport(item, None)
    next(generator)
    try:
        generator.send(report)
    except StopIteration as stop:
        return stop.value
    raise AssertionError("the hook did not stop after one yield")


def test_a_failing_report_is_annotated_when_space_is_low(tmp_path, monkeypatch):
    import conftest

    monkeypatch.setattr(conftest, "LOW_SPACE_BYTES", 10**18)
    report = _Report()
    _drive(_Item(tmp_path), report)

    assert [name for name, _ in report.sections] == ["Disk space"]
    assert str(tmp_path) in report.sections[0][1]


def test_a_failing_report_is_left_alone_when_there_is_room(tmp_path, monkeypatch):
    """An annotation on every failure is noise, and noise is how a reader
    learns to skip the section that will one day be the answer."""
    import conftest

    monkeypatch.setattr(conftest, "LOW_SPACE_BYTES", 0)
    report = _Report()
    _drive(_Item(tmp_path), report)

    assert report.sections == []


def test_a_passing_report_is_left_alone(tmp_path, monkeypatch):
    """Only failures are annotated; a passing run has nothing to explain."""
    import conftest

    monkeypatch.setattr(conftest, "LOW_SPACE_BYTES", 10**18)
    report = _Report(failed=False)
    _drive(_Item(tmp_path), report)

    assert report.sections == []


def test_a_setup_error_is_left_alone(tmp_path, monkeypatch):
    """`when` is "setup", "call" or "teardown", and annotating all three would
    put the same note on a test three times."""
    import conftest

    monkeypatch.setattr(conftest, "LOW_SPACE_BYTES", 10**18)
    report = _Report(when="setup")
    _drive(_Item(tmp_path), report)

    assert report.sections == []


# --- two runs at once (stn-zim) ---------------------------------------------
#
# The neighbouring ticket. stn-7im's own workaround is "pass --basetemp
# somewhere with room", and doing that from two worktrees is what turned up
# the first of these.


def test_the_browser_image_tag_is_per_run_by_default(monkeypatch):
    """pipeline's browser helpers built and ran ONE fixed tag, so a second
    run could rebuild the image out from under a first still using it."""
    from stencil import pipeline

    monkeypatch.delenv(pipeline.BROWSER_IMAGE_TAG_ENV, raising=False)
    assert pipeline.browser_image_tag() == pipeline.BROWSER_IMAGE_TAG

    monkeypatch.setenv(pipeline.BROWSER_IMAGE_TAG_ENV, "localhost/x:run-1")
    assert pipeline.browser_image_tag() == "localhost/x:run-1"


def test_the_tag_is_resolved_at_call_time_not_at_import(monkeypatch):
    """The mistake this replaces, asserted directly.

    `def build_browser_image(..., tag=BROWSER_IMAGE_TAG)` binds the default at
    IMPORT, so setting the environment or the module attribute afterwards
    looks like it works and changes nothing. The same late-binding bug was
    made and caught in this file's low-space threshold; asserting it here
    stops the next person reintroducing it by 'tidying' the signature.
    """
    import inspect

    from stencil import pipeline

    for name in ("build_browser_image", "run_in_browser", "html_to_pdf",
                 "check_access"):
        signature = inspect.signature(getattr(pipeline, name))
        default = signature.parameters["tag"].default
        assert default is None, (
            f"pipeline.{name} binds its tag default at import ({default!r}); "
            f"an environment override set later cannot reach it"
        )


def _inner_run_against(basetemp: Path):
    """A real pytest over THIS repository's tests, so tests/conftest.py loads.

    Not a throwaway module in a temp directory: the guard lives in that
    conftest, and a directory with no conftest never runs it -- which is
    exactly how the first version of this test passed while proving nothing.
    `-k` selects no tests, so the run is a startup and a teardown.
    """
    return subprocess.run(
        [
            sys.executable, "-m", "pytest", "tests/test_theme.py",
            "-k", "no_test_matches_this_name",
            "-p", "no:cacheprovider", f"--basetemp={basetemp}", "-q",
        ],
        cwd=PYPROJECT.parent, env=_inner_env(), capture_output=True, text=True,
    )


def test_a_shared_basetemp_is_refused(tmp_path):
    """Measured before it was guarded: two runs given the same --basetemp
    delete each other's fixture trees, because pytest rotates that directory
    at startup. It read as `22 failed, 789 passed, 79 errors` -- a
    catastrophic-looking regression rather than two runs fighting.

    Refused rather than silently rewritten: the caller passed that path on
    purpose, and quietly substituting another is its own surprise.
    """
    basetemp = tmp_path / "shared"
    basetemp.mkdir()
    # An owner whose pid is certainly alive: this very process.
    (basetemp / ".pytest-run-owner").write_text(f"{os.getpid()}-1")

    result = _inner_run_against(basetemp)

    assert "in use by a running pytest" in result.stdout + result.stderr, (
        f"a second run on a live basetemp was allowed:\n{result.stdout}"
    )
    assert result.returncode == 4, "a UsageError should be pytest's exit 4"


def test_a_stale_owner_does_not_block(tmp_path):
    """The other half, and the one that would make this unusable: a run that
    crashed leaves its marker behind, and refusing forever afterwards would
    teach people to delete the guard rather than the file."""
    basetemp = tmp_path / "stale"
    basetemp.mkdir()
    (basetemp / ".pytest-run-owner").write_text("999999-1")

    result = _inner_run_against(basetemp)

    # On the message, not the exit code: `-k` matching nothing exits 5 ("no
    # tests collected"), which is not a refusal and asserting `== 0` made this
    # test fail for a reason that had nothing to do with the guard.
    assert "in use by a running pytest" not in result.stdout + result.stderr, (
        f"a stale marker blocked a new run:\n{result.stdout}\n{result.stderr}"
    )
    assert result.returncode != 4, "pytest reported a usage error"


# --- the guard has to survive pytest's own rotation (stn-6fs) ---------------
#
# test_a_shared_basetemp_is_refused above passes today with the guard
# completely dead, and that is not a criticism of it so much as the reason
# this section exists. It hand-writes `.pytest-run-owner` into a directory
# that no pytest ever rotates, so it proves the marker is READ. It cannot
# prove the marker is ever there to read.
#
# It is not: `TempPathFactory.getbasetemp()` rmtree()s the basetemp on first
# use and recreates it, `pytest_configure` runs strictly before that, and so
# run 1 deletes its own marker the moment any test asks for `tmp_path`. The
# window in which the marker exists is milliseconds, which makes the guard
# dead rather than merely racy.
#
# The only thing that can tell the difference is two runs genuinely
# overlapping in time, with the first already past a `tmp_path`. That is what
# the rest of this file does.

HOLDER = '''
import os, time
from pathlib import Path


def test_holds_the_basetemp(tmp_path):
    # Touching tmp_path is the whole point: it is what makes pytest build the
    # basetemp, which is what rotates it, which is what deletes the marker.
    (tmp_path / "touched").write_text("x")
    Path(os.environ["HOLDER_READY"]).write_text("ready")

    # Bounded, so a failed assertion in the outer test cannot leave a pytest
    # running until someone notices.
    stop = Path(os.environ["HOLDER_STOP"])
    deadline = time.time() + 30
    while time.time() < deadline and not stop.exists():
        time.sleep(0.02)
'''


def _start_holder(tmp_path: Path, basetemp: Path):
    """Run 1: a pytest that reaches a test, touches `tmp_path`, and waits.

    `-p conftest` with this repository's `tests/` on PYTHONPATH rather than a
    real repository test, and the distinction matters. The objection recorded
    against a throwaway module up in `_inner_run_against` is that a directory
    with no conftest never runs the guard -- so the guard must be loaded, not
    that the test beside it must be one of ours. `-p conftest` loads THE REAL
    `tests/conftest.py` as a plugin, hooks and all; verified by planting a
    live marker and watching this exact invocation refuse it. What a real
    repository test cannot give is the one thing this test is about: a run
    that stays alive, past `tmp_path`, for as long as run 2 needs.
    """
    project = tmp_path / "holder"
    project.mkdir()
    (project / "test_holder.py").write_text(HOLDER)

    ready = tmp_path / "ready"
    stop = tmp_path / "stop"

    env = _inner_env(
        PYTHONPATH=str(PYPROJECT.parent / "tests"),
        HOLDER_READY=str(ready),
        HOLDER_STOP=str(stop),
    )
    process = subprocess.Popen(
        [
            sys.executable, "-m", "pytest", "test_holder.py",
            "-p", "conftest", "-p", "no:cacheprovider",
            f"--basetemp={basetemp}", "-q",
        ],
        cwd=project, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True,
    )
    return process, ready, stop


def test_a_live_run_is_refused_by_a_second_one_that_actually_overlaps(tmp_path):
    """stn-6fs, and the test the guard shipped without.

    Two real runs, overlapping in time, on one `--basetemp`, with the first
    already past the `tmp_path` that rotates the directory. Run 2 must be
    refused. Against the conftest this replaced, run 2 was ALLOWED: run 1 had
    already deleted its own marker.
    """
    basetemp = tmp_path / "shared"
    process, ready, stop = _start_holder(tmp_path, basetemp)
    try:
        deadline = time.time() + 60
        while time.time() < deadline and not ready.exists():
            if process.poll() is not None:
                raise AssertionError(
                    f"run 1 exited before it held anything:\n{process.stdout.read()}"
                )
            time.sleep(0.02)
        assert ready.exists(), "run 1 never reached a test that touched tmp_path"

        result = _inner_run_against(basetemp)

        assert "in use by a running pytest" in result.stdout + result.stderr, (
            f"run 2 was allowed onto a basetemp a live run 1 was using. The "
            f"marker is written before pytest rotates the directory, so run 1 "
            f"deletes it itself (stn-6fs).\n{result.stdout}\n{result.stderr}"
        )
        assert result.returncode == 4, "a UsageError should be pytest's exit 4"
    finally:
        stop.write_text("stop")
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


RACER = '''
import time


def test_holds_briefly(tmp_path):
    (tmp_path / "touched").write_text("x")
    time.sleep(1.5)
'''


def test_only_one_of_several_simultaneous_runs_claims_the_basetemp(tmp_path):
    """The race the marker alone cannot win.

    Two runs starting together both read an empty basetemp, both find no
    owner, and both claim it -- the check and the write are two steps, and
    nothing made them one. There is a second instant of the same kind inside
    `pytest_sessionstart`: between the rmtree in `getbasetemp()` and the
    marker being written again, the directory is genuinely unclaimed.

    Both are closed by a lock that lives OUTSIDE the basetemp, because a lock
    inside it would be deleted by the rotation it is there to cover. The lock
    spans only those two transitions; the marker remains the long-lived claim,
    since a lock held for a ten-minute run adds nothing a marker does not
    already say and turns a killed run into a puzzle.

    Four starters, one directory, exactly one survivor. Without the lock this
    test is a coin toss rather than a failure, which is the honest reason it
    is written as four racers and not two.
    """
    project = tmp_path / "racer"
    project.mkdir()
    (project / "test_racer.py").write_text(RACER)
    basetemp = tmp_path / "contested"

    env = _inner_env(PYTHONPATH=str(PYPROJECT.parent / "tests"))
    processes = [
        subprocess.Popen(
            [
                sys.executable, "-m", "pytest", "test_racer.py",
                "-p", "conftest", "-p", "no:cacheprovider",
                f"--basetemp={basetemp}", "-q",
            ],
            cwd=project, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        for _ in range(4)
    ]
    outcomes = []
    try:
        for process in processes:
            output, _ = process.communicate(timeout=120)
            outcomes.append((process.returncode, output))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()

    transcript = "\n---\n".join(out for _, out in outcomes)

    # On the MESSAGE, not on a count of exit-4s. A loser that dies inside
    # `pytest_sessionstart` exits 3 with an INTERNALERROR, not 4, so counting
    # exit codes would call that a pass in some runs and a mystery in others.
    # Asserting no run crashed is the point rather than a nicety: an
    # unattributable INTERNALERROR is worse than the corruption being guarded
    # against, because nothing in it names a basetemp.
    assert "INTERNALERROR" not in transcript, (
        f"a run crashed instead of being refused:\n{transcript}"
    )
    refused = [out for _, out in outcomes if "in use by a running pytest" in out]
    assert len(refused) == 3, (
        f"expected three of four simultaneous runs to be refused, got "
        f"{len(refused)}:\n{transcript}"
    )
    assert all(code == 4 for code, out in outcomes if out in refused), (
        f"a refusal should be pytest's exit 4:\n{transcript}"
    )
