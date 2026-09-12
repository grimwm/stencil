"""What the container tier's harness has to get right to run in parallel.

The tier runs under `pytest -n auto --dist loadfile` in CI (stn-vda), and two
pieces of this suite's own state were written on the assumption of a single
process. Both are pinned here rather than in the integration job, because a
code path exercised only in CI is a code path that can silently stop working
-- the failure AGENTS.md records for the pre-push hook and for `_pid_alive`.

Nothing in this file needs a container runtime. The build lock is driven with
an injected callable, so the thing being tested is the locking rather than
docker.

Measured facts these tests encode, all verified against pytest-xdist 3.8
before anything was written:

- xdist hands every worker its own `--basetemp` of `<run-dir>/popen-gwN`, both
  when the top-level run passes `--basetemp` and when it does not.
- `getbasetemp().parent` is therefore run-scoped and identical across workers
  -- but in a SERIAL run it is the system temp root, shared by every run the
  user has ever made, which is why the build lock cannot simply always use it.
- an environment variable set in the controller's `pytest_configure` reaches
  every worker, because execnet passes `os.environ` down at spawn time.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"
TESTS = Path(__file__).parent


def _inner_env(**overrides):
    """See conftest.inner_pytest_env. An inner run that inherited this one's
    `$PYTEST_XDIST_WORKER` would exempt itself from the guard below, and these
    tests would pass while checking nothing."""
    from conftest import inner_pytest_env

    return inner_pytest_env(**overrides)

REPORTER = '''
import os
from pathlib import Path


def test_report(tmp_path):
    # Touching tmp_path is load-bearing: it is what makes pytest build (and
    # so rotate) this process's basetemp.
    (tmp_path / "touched").write_text("x")
    worker = os.environ.get("PYTEST_XDIST_WORKER", "none")
    (Path(os.environ["REPORT_DIR"]) / f"{worker}.txt").write_text(
        os.environ.get("STENCIL_BROWSER_IMAGE_TAG", "<unset>")
    )
'''


def _inner_xdist(tmp_path: Path, basetemp: Path, workers: int = 2):
    """An inner `pytest -n <workers> --dist each` that loads the REAL conftest.

    `-p conftest` with this repository's `tests/` on PYTHONPATH: the module
    under test is loaded as a plugin, hooks and all, so these are the real
    `pytest_configure` and `pytest_sessionstart` rather than a copy. A
    throwaway directory with no conftest would run neither, which is the
    mistake tests/test_tmp_footprint.py records against itself.

    `--dist each` rather than the default `load` because it sends every test
    to EVERY worker, so "both workers reported" is a fact about the run rather
    than a hope about how xdist happened to bin-pack two tests.
    """
    project = tmp_path / "inner"
    project.mkdir()
    (project / "test_report.py").write_text(REPORTER)
    reports = tmp_path / "reports"
    reports.mkdir()

    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "test_report.py",
            "-p", "conftest", "-p", "no:cacheprovider",
            "-n", str(workers), "--dist", "each",
            f"--basetemp={basetemp}", "-q",
        ],
        cwd=project,
        env=_inner_env(PYTHONPATH=str(TESTS), REPORT_DIR=str(reports)),
        capture_output=True,
        text=True,
        # An inner run that wedges must fail this test rather than hang the
        # job until the CI timeout kills it with nothing to read.
        timeout=300,
    )
    return result, reports


def test_every_worker_observes_the_same_browser_image_tag(tmp_path):
    """The premise the build lock rests on, and it was never tested.

    `pytest_configure` mints `$STENCIL_BROWSER_IMAGE_TAG` in the CONTROLLER,
    before any worker exists. Workers inherit it because execnet hands them
    `os.environ` at spawn. If that ever stopped being true, four workers would
    build four differently-tagged images and the lock below would serialize
    nothing -- silently, since every worker would still find an image under
    the tag it was looking for.
    """
    result, reports = _inner_xdist(tmp_path, tmp_path / "bt")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    observed = {path.name: path.read_text() for path in reports.iterdir()}
    assert set(observed) == {"gw0.txt", "gw1.txt"}, (
        f"both workers should have reported: {sorted(observed)}"
    )
    assert len(set(observed.values())) == 1, (
        f"workers disagreed about the image tag: {observed}"
    )
    tag = next(iter(observed.values()))
    assert tag.startswith("localhost/stencil_browser:run-"), (
        f"the controller did not mint a per-run tag: {tag!r}"
    )

    # The half that makes the rest of this test mean anything. `inner_pytest_env`
    # strips $STENCIL_BROWSER_IMAGE_TAG, so the inner controller has to MINT its
    # own; without that strip it inherited ours, took `pytest_configure`'s
    # "an explicit tag wins" branch, and the workers dutifully agreed about a tag
    # nobody in the inner run had minted -- this test passed with the variable
    # pre-set to `run-BOGUS-NOT-THIS-RUN`. Per-run uniqueness is the other half
    # of stn-zim: a second run must not rebuild the image out from under a first.
    from stencil import pipeline

    assert tag != os.environ.get(pipeline.BROWSER_IMAGE_TAG_ENV), (
        "the inner run reported this run's tag rather than minting its own"
    )


def test_a_worker_does_not_claim_its_own_basetemp(tmp_path):
    """xdist workers are exempt from the `--basetemp` ownership guard.

    Not for the reason it is tempting to assume. A worker does NOT see its
    controller's marker: worker basetemps are `<run-dir>/popen-gwN`, a
    different directory from the controller's, so the guard was never going
    to refuse a worker its controller's claim.

    What it would do is claim a path nobody chose. xdist invents a
    `--basetemp` for every worker even when the top-level run passed none, so
    without the exemption a plain `pytest -n auto` starts writing owner
    markers where a plain `pytest` writes none. The guard answers one
    question -- did someone point two runs at one directory -- and that
    question cannot arise for a directory xdist made up and no second run can
    be pointed at.

    Asserted on the real thing rather than on a hand-planted marker, because
    a hand-planted marker in a directory nothing rotates is exactly how the
    stn-zim guard passed its own test while doing nothing (stn-6fs).
    """
    basetemp = tmp_path / "bt"
    result, _ = _inner_xdist(tmp_path, basetemp)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    from conftest import RUN_OWNER

    # Assert the workers were there before asserting what they did not do.
    # `assert not strays` over an empty glob passes just as happily when the
    # layout moved and there are no popen-gwN directories to look in at all --
    # a guard that silently stops guarding, which is the thing this repository
    # says it will not ship.
    workers = sorted(path.name for path in basetemp.glob("popen-gw*"))
    assert len(workers) == 2, f"expected two worker directories, found {workers}"

    strays = sorted(
        str(path.relative_to(basetemp))
        for path in basetemp.glob(f"popen-gw*/{RUN_OWNER}")
    )
    assert not strays, (
        f"a worker claimed ownership of the basetemp xdist invented for it: "
        f"{strays}"
    )
    assert (basetemp / RUN_OWNER).exists(), (
        "the controller should still own the basetemp it was given -- "
        "exempting workers must not switch the guard off for the run"
    )


def test_the_controller_of_a_parallel_run_is_still_refused(tmp_path):
    """The control, and the reason this one is allowed to pass from the start.

    A test that cannot fail proves nothing on its own; this one exists to
    catch the exemption being written too broadly. `PYTEST_XDIST_WORKER` is
    set in workers and NOT in the controller, and an exemption keyed on
    anything coarser -- the presence of the xdist plugin, `-n` on the command
    line -- would turn the guard off for exactly the runs most likely to be
    sharing a basetemp between worktrees.
    """
    basetemp = tmp_path / "bt"
    basetemp.mkdir()
    # An owner whose pid is certainly alive: this very process.
    (basetemp / ".pytest-run-owner").write_text(f"{os.getpid()}-1")

    result, _ = _inner_xdist(tmp_path, basetemp)

    assert "in use by a running pytest" in result.stdout + result.stderr, (
        f"a parallel run was allowed onto a live basetemp:\n{result.stdout}"
    )
    assert result.returncode == 4, "a UsageError should be pytest's exit 4"


# --- the browser image is built once per run, not once per worker -----------
#
# `pdf_workspace` is session-scoped, and under xdist "session" means per
# WORKER PROCESS. It installs Chromium, puppeteer and pa11y -- minutes -- so
# four workers would start four cold builds at once, with no layer cache to
# share because none of them has finished.
#
# Driven here with real subprocesses rather than threads, because that is the
# thing being modelled: xdist workers are separate processes, and a lock that
# only excludes threads would pass a threaded test and do nothing in CI.

BUILD_WORKER = '''
import sys, time
from pathlib import Path

import conftest

shared, log, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]


def build():
    with log.open("a") as handle:
        handle.write("built\\n")
    time.sleep(0.5)
    if mode == "fail":
        raise RuntimeError("the image did not build")


try:
    outcome = conftest.build_once(shared, "browser-image", build)
except conftest.BuildFailed as exc:
    print(f"FAILED {exc}")
    sys.exit(3)
print(f"OK {outcome}")
'''


def _race(tmp_path: Path, mode: str, callers: int = 4):
    script = tmp_path / "caller.py"
    script.write_text(BUILD_WORKER)
    shared = tmp_path / "shared"
    shared.mkdir()
    log = tmp_path / "builds.log"
    log.touch()

    env = _inner_env(PYTHONPATH=str(TESTS))
    processes = [
        subprocess.Popen(
            [sys.executable, str(script), str(shared), str(log), mode],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        for _ in range(callers)
    ]

    # `communicate`, not `wait` then `read`: with stdout=PIPE a child that
    # outgrew the pipe buffer would block writing while the parent blocked
    # waiting. The output here is two lines, so it has never happened -- which
    # is exactly the kind of thing that starts happening the day someone adds a
    # traceback to the child.
    #
    # And in a `finally`, because these are deliberately racing processes: if
    # one times out, the others are still running, and a test that leaves four
    # pytest-adjacent processes behind is worse than the one it was checking.
    results = []
    try:
        for process in processes:
            output, _ = process.communicate(timeout=120)
            results.append((process.returncode, output))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()
    return results, log.read_text().splitlines()


def test_the_build_runs_once_however_many_workers_ask(tmp_path):
    results, builds = _race(tmp_path, "ok")

    assert builds == ["built"], (
        f"the build ran {len(builds)} times across four concurrent callers"
    )
    assert all(code == 0 for code, _ in results), results
    assert all("OK" in output for _, output in results), results


def test_a_failed_build_fails_every_worker(tmp_path):
    """The half that matters more.

    If only the worker that attempted the build learns it failed, the other
    three carry on against an image that is not there and report a wall of
    unrelated container errors. Recording the failure and re-raising it to
    every caller makes four workers fail identically, once, with the reason.
    """
    results, builds = _race(tmp_path, "fail")

    assert builds == ["built"], (
        f"a failed build was retried by later callers: {len(builds)} attempts"
    )
    assert all(code == 3 for code, _ in results), results
    assert all("the image did not build" in output for _, output in results), (
        f"a caller was not told why the build failed: {results}"
    )


# --- the paths that only open when something else goes wrong ----------------
#
# Each of these guards a failure mode found by review rather than by a test
# failing, which is exactly the kind that gets written once and then quietly
# stops working. Driven directly, with injected objects, so none of them needs
# a second process to reach.


def test_a_basetemp_that_cannot_be_rotated_is_refused_not_crashed():
    """`getbasetemp()` mkdir()s without `exist_ok`, so a directory recreated
    under it raises FileExistsError -- out of a session hook, which pytest
    reports as an INTERNALERROR with a traceback and no mention of a basetemp.

    That is strictly worse than the corruption the guard exists to prevent,
    because at least the corruption was attributable once you knew to look.
    """
    import conftest

    class _AlwaysCollides:
        def getbasetemp(self):
            raise FileExistsError(17, "File exists")

    with pytest.raises(pytest.UsageError, match="in use by a running pytest"):
        conftest._rotate(_AlwaysCollides(), Path("/tmp/whatever"))


def test_a_transient_collision_is_retried_rather_than_refused():
    """The other half. A racer that has finished by the second attempt must
    not cost the run a refusal it did not earn."""
    import conftest

    class _CollidesOnce:
        def __init__(self):
            self.calls = 0

        def getbasetemp(self):
            self.calls += 1
            if self.calls == 1:
                raise FileExistsError(17, "File exists")

    factory = _CollidesOnce()
    conftest._rotate(factory, Path("/tmp/whatever"))
    assert factory.calls == 2


def test_a_torn_sentinel_rebuilds_instead_of_failing_forever(tmp_path):
    """A process killed mid-write leaves a zero-byte sentinel.

    Read as a recorded failure it would have an empty reason, skip the build,
    and fail every container test in the run with a blank message that nothing
    clears. An outcome nobody can explain has to mean "no outcome".
    """
    import conftest

    sentinel = tmp_path / ".browser-image.outcome"
    sentinel.write_text("")
    assert conftest._read_outcome(sentinel) is None

    calls = []
    assert conftest.build_once(tmp_path, "browser-image", lambda: calls.append(1)) == "ok"
    assert calls == [1], "a torn sentinel should have been rebuilt over"


def test_an_outcome_is_never_visible_half_written(tmp_path):
    """`write_text` truncates and then writes; `os.replace` does not."""
    import conftest

    sentinel = tmp_path / ".browser-image.outcome"
    conftest._write_outcome(sentinel, "ok")
    assert sentinel.read_text() == "ok"
    assert not list(tmp_path.glob("*.tmp")), "the staging file was left behind"


def test_the_shared_directory_refuses_to_guess(monkeypatch, tmp_path):
    """`_shared_run_dir` encodes xdist's private `popen-gwN` layout.

    If that moves, or if a pytest subprocess inherits PYTEST_XDIST_WORKER
    without going through `inner_pytest_env`, the parent is the system temp
    root -- and a sentinel there outlives every run on the machine. Failing
    loudly is the only version of this that stays true.
    """
    import conftest

    class _Factory:
        def __init__(self, path):
            self.path = path

        def getbasetemp(self):
            return self.path

    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    assert conftest._shared_run_dir(_Factory(tmp_path)) == tmp_path

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    worker = tmp_path / "popen-gw0"
    assert conftest._shared_run_dir(_Factory(worker)) == tmp_path

    with pytest.raises(RuntimeError, match="popen-gwN"):
        conftest._shared_run_dir(_Factory(tmp_path / "somewhere-else"))
