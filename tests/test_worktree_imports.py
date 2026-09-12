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

import os
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
    # Comment lines immediately before that next table introduce IT, not this
    # one -- pyproject.toml has three such lines about setuptools' flat-layout
    # discovery. Inert as TOML, but copying them means this fixture quietly
    # stops being a copy of the block it claims to reproduce.
    while body and (not body[-1].strip() or body[-1].lstrip().startswith("#")):
        body.pop()
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


def test_the_guard_survives_a_symlinked_checkout(tmp_path):
    """macOS hands this suite a symlink every time it runs.

    `/tmp` is a symlink to `/private/tmp` on macOS, and this repository's own
    convention puts worktrees under `.claude/worktrees/`, which a contributor
    may well reach through one. The guard resolves BOTH sides for that reason
    and for no other; a later simplification -- "the checkout path is already
    absolute, it needs no resolve()" -- would start refusing perfectly good
    runs on one developer's machine and nobody else's.

    That correctness currently rests entirely on one expression. This is the
    test that makes it rest on something.
    """
    real = tmp_path / "real-checkout"
    (real / "stencil").mkdir(parents=True)
    stencil_file = real / "stencil" / "__init__.py"
    stencil_file.write_text("")

    link = tmp_path / "reached-by-symlink"
    link.symlink_to(real, target_is_directory=True)
    assert link.resolve() == real.resolve(), "the fixture must really symlink"

    assert foreign_stencil_note(link, stencil_file, link) is None, (
        "a checkout reached through a symlink is the same checkout"
    )
    assert foreign_stencil_note(real, link / "stencil" / "__init__.py", real) is None, (
        "and so is a stencil reached through one"
    )


def test_the_door_is_loud(tmp_path):
    """The override lets a run proceed, and makes it say so.

    A guard with no way past it gets deleted whole the first time it blocks
    something legitimate. A guard that can be waved through in silence is not
    a guard. So the override exists and costs a line at the top of every run's
    log -- the same place `basetemp` already reports itself.
    """
    fake = tmp_path / "some-other-checkout"
    (fake / "tests").mkdir(parents=True)
    shutil.copyfile(CONFTEST, fake / "tests" / "conftest.py")
    (fake / "tests" / "test_trivial.py").write_text("def test_trivial(): pass\n")

    result = _run_inner(
        [str(fake / "tests")],
        cwd=tmp_path,
        PYTHONPATH=str(CHECKOUT),
        STENCIL_ALLOW_FOREIGN_STENCIL="1",
    )
    combined = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0, f"the door should open:\n{combined}"
    assert "STENCIL_ALLOW_FOREIGN_STENCIL is set" in combined, (
        f"and it should be impossible to miss that it was used:\n{combined}"
    )
    assert str(CHECKOUT) in combined, (
        f"including which tree is actually under test:\n{combined}"
    )


def test_the_door_does_not_open_on_zero(tmp_path):
    """`=0` must not read as "yes".

    An override that treats any non-empty value as consent turns
    `STENCIL_ALLOW_FOREIGN_STENCIL=0` -- which a person writes meaning the
    opposite -- into a silent bypass of the one guard standing between them
    and a run that measures somebody else's source. The refusal message says
    to set it to 1, so 1 is what it takes.
    """
    fake = tmp_path / "some-other-checkout"
    (fake / "tests").mkdir(parents=True)
    shutil.copyfile(CONFTEST, fake / "tests" / "conftest.py")
    (fake / "tests" / "test_trivial.py").write_text("def test_trivial(): pass\n")

    result = _run_inner(
        [str(fake / "tests")],
        cwd=tmp_path,
        PYTHONPATH=str(CHECKOUT),
        STENCIL_ALLOW_FOREIGN_STENCIL="0",
    )

    assert result.returncode != 0, (
        "setting the override to 0 opened the door:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_the_guard_speaks_for_a_namespace_package(monkeypatch):
    """A `stencil` with no `__init__.py` must get the message, not a TypeError.

    `stencil.__file__` is None for a namespace package -- a bare directory,
    which is what a half-deleted or badly-built install leaves behind. The
    guard exists to be the thing that speaks clearly when imports are
    confused; it must not be the thing that raises `TypeError: argument should
    be a str or an os.PathLike` in one.
    """
    from conftest import stencil_location

    monkeypatch.setattr(stencil, "__file__", None)
    monkeypatch.setattr(stencil, "__path__", ["/checkouts/main/stencil"])

    located = stencil_location()
    assert located.parent == Path("/checkouts/main/stencil"), located

    note = foreign_stencil_note(Path("/checkouts/branch"), located, Path("."))
    assert note is not None and "/checkouts/main/stencil" in note, note


def test_no_tracked_file_at_the_root_shadows_a_dependency():
    """The cost of putting the repository root on sys.path, made visible.

    `pythonpath = ["."]` is what fixes stn-2et, and it widens what a file at
    the TOP of this tree can do: the root now precedes site-packages for every
    pytest run, where before only `tests/` did. A file called `yaml.py` or
    `filelock.py` added at the root would quietly become the one this suite
    imports -- and `tests/conftest.py` imports both at module scope.

    That is not a new capability. Anyone who can land a test file in a PR can
    already run code in CI, which builds fork pull requests. What it changes
    is how innocuous the file gets to look, and this repository's answer to an
    accepted risk is to monitor it rather than to argue about it.

    Only git-TRACKED entries are checked, because untracked ones are what a
    contributor's own machine generates -- `build/`, `.venv/` -- and a test
    that fails on a local build directory is a test people learn to ignore.
    """
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=CHECKOUT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()

    candidates = sorted(
        {
            top.removesuffix(".py")
            for top in {entry.split("/")[0] for entry in tracked}
            # What Python could actually import from the root: a module file
            # or a package/namespace directory. `LICENSE` is neither.
            if (top.endswith(".py") or (CHECKOUT / top).is_dir())
            and top.removesuffix(".py").isidentifier()
            # `stencil` IS this repository's package; it is the one name that
            # is supposed to resolve here rather than in site-packages.
            and top != "stencil"
        }
    )
    assert candidates, "the scan found nothing; it has stopped testing anything"

    # An isolated interpreter, from a neutral cwd: `-I` drops PYTHONPATH and
    # the user site directory, and the cwd is not this tree, so anything found
    # is genuinely installed rather than this checkout answering about itself.
    probe = (
        "import importlib.util, sys\n"
        "for name in sys.argv[1:]:\n"
        # No `except: spec = None` here. Swallowing the error would report
        # "nothing shadows anything" for a name that could not be inspected,
        # which is the guard passing precisely when it cannot see.
        "    spec = importlib.util.find_spec(name)\n"
        "    if spec is not None:\n"
        "        print(f'{name} {spec.origin}')\n"
    )
    found = subprocess.run(
        [sys.executable, "-I", "-c", probe, *candidates],
        cwd=Path(sys.prefix),
        capture_output=True,
        text=True,
        timeout=INNER_TIMEOUT,
    )

    assert found.returncode == 0, f"{found.stdout}\n{found.stderr}"
    shadowed = [
        line
        for line in found.stdout.splitlines()
        if str(CHECKOUT) not in line  # this tree answering about itself
    ]
    assert not shadowed, (
        "these top-level names now shadow an installed module for every "
        f"pytest run, because the repository root is on sys.path: {shadowed}"
    )


def test_an_inner_harness_run_also_tests_this_checkout(tmp_path):
    """stn-2et one level down, and it was live rather than theoretical.

    Several tests here answer questions that can only be answered from outside
    the process, by running an inner pytest that loads THIS conftest as a
    plugin -- tests/test_parallel_harness.py and tests/test_tmp_footprint.py
    both do. Such a run gets no ini file of its own, so `pythonpath = ["."]`
    never reaches it, and its `import stencil` fell through to whatever the
    interpreter's install pointed at. Under the borrowed-venv arrangement
    AGENTS.md now describes, that is another checkout: the inner runs were
    testing the wrong tree in exactly the way the outer ones were.

    The guard made it visible rather than silent -- `UsageError`, exit 4, on
    two harness tests that had nothing to do with this change. The refusal was
    right and the harness was what needed fixing, so `inner_pytest_env` now
    puts this checkout on the inner run's path.

    THE COMPETITOR IS MODELLED AS A META-PATH FINDER, not as a PYTHONPATH
    entry, because that is what an editable install actually is: setuptools
    APPENDS its finder to `sys.meta_path`, so it answers only when the sys.path
    search has already failed. Modelling it as a path entry would make it
    stronger than the real thing and demand a fix stricter than the real one
    needs -- which is how the first draft of this test failed against a
    correct implementation.
    """
    elsewhere = tmp_path / "borrowed-venv-checkout"
    (elsewhere / "stencil").mkdir(parents=True)
    shutil.copyfile(
        CHECKOUT / "stencil" / "__init__.py", elsewhere / "stencil" / "__init__.py"
    )

    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "borrowed_install.py").write_text(
        "import os\n"
        "import sys\n"
        "from importlib.machinery import PathFinder\n"
        "\n"
        "\n"
        "class _EditableInstallElsewhere:\n"
        "    search = [os.environ['BORROWED_CHECKOUT']]\n"
        "\n"
        "    @classmethod\n"
        "    def find_spec(cls, fullname, path=None, target=None):\n"
        "        if fullname.split('.')[0] != 'stencil':\n"
        "            return None\n"
        "        return PathFinder.find_spec(\n"
        "            fullname, cls.search if path is None else path\n"
        "        )\n"
        "\n"
        "\n"
        "# The venv's OWN editable finder has to go first, or it answers for\n"
        "# stencil and this process is not a borrowed venv at all -- which is\n"
        "# precisely how the first draft of this test passed against a build\n"
        "# with the fix removed.\n"
        "sys.meta_path[:] = [\n"
        "    f for f in sys.meta_path\n"
        "    if '__editable__' not in getattr(f, '__module__', '')\n"
        "    and '__editable__' not in getattr(f, '__name__', '')\n"
        "]\n"
        "\n"
        "# Appended, exactly as setuptools does it: consulted only after the\n"
        "# sys.path search comes up empty.\n"
        "sys.meta_path.append(_EditableInstallElsewhere)\n"
    )

    project = tmp_path / "inner"
    project.mkdir()
    (project / "test_report.py").write_text(
        "import stencil\n"
        "import pathlib\n"
        "import os\n"
        "\n"
        "\n"
        "def test_report(tmp_path):\n"
        "    (tmp_path / 'touched').write_text('x')\n"
        "    pathlib.Path(os.environ['STENCIL_PROBE_OUT']).write_text(stencil.__file__)\n"
    )

    out = tmp_path / "imported.txt"
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "test_report.py",
            "-p", "borrowed_install", "-p", "conftest", "-p", "no:cacheprovider",
            f"--basetemp={tmp_path / 'bt'}", "-q",
        ],
        cwd=project,
        env=inner_pytest_env(
            PYTHONPATH=os.pathsep.join([str(CHECKOUT / "tests"), str(plugins)]),
            BORROWED_CHECKOUT=str(elsewhere),
            STENCIL_PROBE_OUT=str(out),
        ),
        capture_output=True,
        text=True,
        timeout=INNER_TIMEOUT,
    )
    combined = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0, (
        f"an inner harness run must test this checkout, not refuse:\n{combined}"
    )
    assert Path(out.read_text()).resolve() == (
        CHECKOUT / "stencil" / "__init__.py"
    ).resolve(), (
        "the inner run imported the borrowed checkout's stencil; every harness "
        "test that loads this conftest was measuring the wrong tree"
    )
