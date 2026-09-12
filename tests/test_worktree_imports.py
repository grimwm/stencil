"""pytest must test the tree it was started in (stn-2et).

AGENTS.md tells every contributor and every agent to work in a git worktree.
A worktree shares nothing but history with the checkout beside it, and the
`pip install -e .` that made `stencil` importable points at exactly ONE of
them. So the documented arrangement is: several source trees, one editable
install.

Measured on this branch, from a fresh worktree using the main checkout's venv:

    $ cd <worktree> && <main>/.venv/bin/pytest tests/test_probe.py
    STENCIL:  <main>/stencil/__init__.py          <- NOT the worktree
    SYSPATH0: <worktree>/tests
    SYSPATH1: <main>/.venv/bin

pytest's default `prepend` import mode inserts the first directory ABOVE the
test file that has no `__init__.py`. `tests/` has none, so what lands on
`sys.path[0]` is `<worktree>/tests` -- never `<worktree>` itself. `import
stencil` finds nothing there and falls through to the editable install's
finder, which resolves by absolute path to the other checkout.

The suite then reports a confident pass or fail about source the contributor
did not change. The failing direction is the lucky one; the dangerous one is a
worktree whose change is BROKEN passing green because main's code is fine.

Two things hid this for a long time, and both are recorded here because they
are the reason a reader doubts the bug before believing it:

- `python -m pytest` behaves CORRECTLY from the same directory, because `-m`
  puts the cwd on `sys.path` first. Only the console script -- the one
  AGENTS.md documents -- was wrong, so the obvious sanity check passed.
- stn-12v's guard in tests/test_cli.py passes either way. It derives
  `REPO_ROOT` from `generate.__file__`, so it pins the subprocess to whatever
  the in-process import already chose. That is consistency, which is what it
  was written for, and it is not correctness: under this bug both halves agree
  on the wrong tree. Fixing the in-process import is what makes stn-12v's
  guarantee point at the right one.

The fix is `pythonpath = ["."]` in [tool.pytest.ini_options], which puts the
ROOTDIR on sys.path before tests/conftest.py is imported. The guard in that
conftest is the witness that it worked, for the same reason the pre-push drift
hook has a test: a mechanism that silently stops running is worse than none,
because it is also a belief that you are covered.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import stencil

from conftest import CHECKOUT, foreign_stencil_note, inner_pytest_env

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"
CONFTEST = Path(__file__).parent / "conftest.py"

# An inner run that wedges must fail this test rather than hang the job until
# the CI timeout kills it with nothing to read -- test_parallel_harness.py's
# reasoning, and its number.
INNER_TIMEOUT = 300

PROBE = '''
import os
import pathlib

import stencil


def test_probe():
    pathlib.Path(os.environ["STENCIL_PROBE_OUT"]).write_text(stencil.__file__)
'''


def _pytest_ini_block() -> str:
    """The REAL [tool.pytest.ini_options] block, as text.

    Copied rather than paraphrased, and read at run time rather than pasted,
    so the synthetic project below is configured exactly the way this
    repository is. A hand-written `pythonpath = ["."]` in the fixture would
    keep this test passing for years after the real setting was deleted --
    which is the failure it exists to catch.

    Parsed as text on purpose: `tomllib` is 3.11+, and CI's fast tier runs
    3.10 as well.
    """
    text = PYPROJECT.read_text()
    header = "[tool.pytest.ini_options]"
    start = text.index(header)
    body: list[str] = []
    for line in text[start + len(header) :].splitlines()[1:]:
        if line.startswith("["):  # the next table; a list's closing ] is `]`
            break
        body.append(line)
    return f"{header}\n" + "\n".join(body).rstrip() + "\n"


def _run_inner(args: list[str], cwd: Path, **env_overrides):
    """An inner pytest, from a NEUTRAL cwd.

    `cwd` is never the tree under test. `python -m pytest` puts the cwd on
    `sys.path` ahead of everything, which would hand the inner run the right
    answer for the wrong reason and make the assertion below unfalsifiable.
    The tree under test is named by an absolute argument instead, and pytest
    finds its rootdir from that.
    """
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args, "-p", "no:cacheprovider", "-q"],
        cwd=cwd,
        env=inner_pytest_env(**env_overrides),
        capture_output=True,
        text=True,
        timeout=INNER_TIMEOUT,
    )


def test_pytest_imports_the_tree_under_test(tmp_path):
    """The defect itself: two trees, one install, and pytest must pick ours.

    `here` stands for the worktree a contributor is in; `elsewhere` stands for
    the checkout that owns the editable install. `elsewhere` is put on
    PYTHONPATH, which is a STRICTLY STRONGER competitor than the
    site-packages finder the real bug goes through -- so the red direction is
    deterministic, and the test is hermetic in CI, where there is no second
    checkout at all.
    """
    here = tmp_path / "here"
    (here / "tests").mkdir(parents=True)
    (here / "stencil").mkdir()
    (here / "stencil" / "__init__.py").write_text('MARKER = "the tree under test"\n')
    (here / "pyproject.toml").write_text(_pytest_ini_block())
    (here / "tests" / "test_probe.py").write_text(PROBE)

    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "stencil").mkdir(parents=True)
    (elsewhere / "stencil" / "__init__.py").write_text('MARKER = "the other checkout"\n')

    out = tmp_path / "imported.txt"
    result = _run_inner(
        [str(here / "tests")],
        cwd=tmp_path,
        PYTHONPATH=str(elsewhere),
        STENCIL_PROBE_OUT=str(out),
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    imported = Path(out.read_text()).resolve()
    assert imported == (here / "stencil" / "__init__.py").resolve(), (
        f"pytest ran {here}'s tests but imported stencil from {imported}. "
        f"The tests measured a source tree the contributor did not change."
    )


def test_the_guard_refuses_a_run_that_escaped_its_checkout(tmp_path):
    """And when the import does escape, the run stops instead of reporting.

    THIS repository's conftest is copied into a checkout that owns no stencil
    of its own, and `stencil` is supplied from this repository instead. That
    is the shape of the bug -- the tests being run and the package being
    imported belong to different trees -- with the two halves swapped so the
    scenario can be built without a second install.

    The real conftest, not a copy of the guard: a guard that is written but
    never wired is the failure AGENTS.md already records against the pre-push
    hook, and only running the real hook can tell the two apart.
    """
    fake = tmp_path / "some-other-checkout"
    (fake / "tests").mkdir(parents=True)
    shutil.copyfile(CONFTEST, fake / "tests" / "conftest.py")
    (fake / "tests" / "test_trivial.py").write_text("def test_trivial(): pass\n")

    result = _run_inner(
        [str(fake / "tests")], cwd=tmp_path, PYTHONPATH=str(CHECKOUT)
    )
    combined = f"{result.stdout}\n{result.stderr}"

    assert result.returncode != 0, (
        f"the run was allowed to proceed against another tree's stencil:\n{combined}"
    )
    assert str(CHECKOUT) in combined, (
        f"the refusal must name where stencil was imported from:\n{combined}"
    )
    assert str(fake) in combined, (
        f"the refusal must name the checkout whose tests are running:\n{combined}"
    )
    assert "pip install -e" in combined, (
        f"the refusal must name the way out, not just the problem:\n{combined}"
    )


def test_the_guard_accepts_this_run(pytestconfig):
    """The positive control.

    Without it, a guard that refused unconditionally would satisfy both tests
    above -- and would take the whole suite down with it.
    """
    assert (
        foreign_stencil_note(CHECKOUT, Path(stencil.__file__), pytestconfig.rootpath)
        is None
    ), (
        f"the guard rejects this very run: stencil is at {stencil.__file__}, "
        f"the checkout under test is {CHECKOUT}"
    )


def test_the_guard_names_both_trees_and_the_way_out():
    """The message is the whole value of a refusal, so it is asserted.

    The person who hits this is, by design, in a worktree using a venv that
    belongs to another checkout. "Something is wrong with your paths" costs
    them the afternoon; the two paths and the command do not.
    """
    note = foreign_stencil_note(
        Path("/checkouts/branch"),
        Path("/checkouts/main/stencil/__init__.py"),
        Path("/checkouts/branch"),
    )

    assert note is not None, "a stencil from another checkout must be refused"
    assert "/checkouts/main/stencil" in note, "name where stencil came from"
    assert "/checkouts/branch" in note, "name the checkout whose tests are running"
    assert "pip install -e" in note, "name the fix"


def test_the_probe_would_have_caught_the_bug(tmp_path):
    """A control on the CONTROL: without the project's ini, the probe fails.

    The first test asserts that `pythonpath` is doing its job. This one shows
    the assertion is falsifiable -- that the same scaffolding, minus the one
    setting under test, lands on the other checkout. Without this, a bug that
    made the probe always report `here` would leave the first test passing
    while measuring nothing.
    """
    here = tmp_path / "here"
    (here / "tests").mkdir(parents=True)
    (here / "stencil").mkdir()
    (here / "stencil" / "__init__.py").write_text('MARKER = "the tree under test"\n')
    (here / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (here / "tests" / "test_probe.py").write_text(PROBE)

    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "stencil").mkdir(parents=True)
    (elsewhere / "stencil" / "__init__.py").write_text('MARKER = "the other checkout"\n')

    out = tmp_path / "imported.txt"
    result = _run_inner(
        [str(here / "tests")],
        cwd=tmp_path,
        PYTHONPATH=str(elsewhere),
        STENCIL_PROBE_OUT=str(out),
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert Path(out.read_text()).resolve() == (
        elsewhere / "stencil" / "__init__.py"
    ).resolve(), (
        "with no `pythonpath` the inner run should have imported the OTHER "
        "checkout; if it did not, the scaffolding above proves nothing"
    )
