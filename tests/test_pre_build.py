"""A package can declare a step that runs before its documents build.

stn-gln. cs425 generates sixteen SVGs from a matplotlib script that nothing in
the build knew about, so `make doc` built documents from stale figures and
never said so. The operator hit it while restructuring and had to ask how the
images were produced at all, because nothing in the build said.

THE HOOK IS A DEPENDENCY, NOT A PRELUDE. Running the command on every build
would be worse than none: matplotlib output is not stable across versions, so
an unconditional regeneration rewrites all sixteen files and buries the real
change in thousands of lines of diff. That is why the request was "only when
they're non-existent or stale", and why every test here drives a real `make`
rather than asserting on the text of the rule.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest


GENERATOR = """\
#!/bin/sh
mkdir -p figures
for i in 1 2 3; do echo "generated $i" > figures/f$i.txt; done
echo "GENERATOR-RAN"
"""


def run_make(package, target="pre-build"):
    return subprocess.run(
        ["make", "--no-print-directory", target],
        cwd=package,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def hooked(demo_config, generate_package):
    """A generated package whose pre_build step writes three files."""
    if shutil.which("make") is None:
        pytest.skip("make is not installed")

    config = demo_config
    config["packages"]["demo"]["pre_build"] = [
        {"run": "sh gen.sh", "outputs": "figures/*.txt", "inputs": "gen.sh"}
    ]
    package = generate_package(config)
    (package / "gen.sh").write_text(GENERATOR)
    return package


def test_the_step_runs_when_nothing_has_been_built(hooked):
    result = run_make(hooked)
    assert "GENERATOR-RAN" in result.stdout, result.stdout + result.stderr
    assert sorted(p.name for p in (hooked / "figures").iterdir()) == [
        "f1.txt",
        "f2.txt",
        "f3.txt",
    ]


def test_the_step_does_not_run_again_when_nothing_changed(hooked):
    """The whole point of the feature.

    An unconditional hook rewrites sixteen files on every build and buries the
    real change. If this ever fails, the feature has become the thing it was
    built to avoid.
    """
    run_make(hooked)
    again = run_make(hooked)
    assert "GENERATOR-RAN" not in again.stdout, (
        f"the step ran a second time with nothing changed:\n{again.stdout}"
    )


def test_touching_an_input_makes_it_stale(hooked):
    """Ageing the stamp rather than sleeping, deliberately.

    make 3.81 -- what macOS ships -- compares whole seconds, so a test that
    rewrites the input immediately after building sees an equal timestamp and
    no rebuild. `sleep 1` would paper over that and make the suite a second
    slower per case; moving the stamp into the past states the condition
    exactly and is instant.

    Worth knowing as a real caveat, not just a test detail: a generator edited
    within the same second as its stamp does not rebuild. That is make's
    timestamp model, not something this feature introduced.
    """
    run_make(hooked)

    stamp = hooked / ".stencil-pre-build-0.stamp"
    old = stamp.stat().st_mtime - 60
    os.utime(stamp, (old, old))

    (hooked / "gen.sh").write_text(GENERATOR + "# edited\n")
    again = run_make(hooked)
    assert "GENERATOR-RAN" in again.stdout, (
        f"editing the generator did not rebuild:\n{again.stdout}"
    )


def test_a_deleted_output_counts_as_stale(hooked):
    """THE CASE A BARE STAMP SILENTLY FAILS, and the reason for the manifest.

    A stamp records WHEN the command last ran and says nothing about whether
    its outputs still exist -- delete one file and the stamp is still newer
    than the script, so make reports nothing to do and the document builds with
    a hole in it. The stamp here holds the produced filenames, so a missing one
    invalidates it. That also works with a glob, which an explicit list of
    sixteen names does not.
    """
    run_make(hooked)
    (hooked / "figures" / "f2.txt").unlink()

    again = run_make(hooked)
    assert "GENERATOR-RAN" in again.stdout, (
        f"a deleted output did not trigger a rebuild:\n{again.stdout}"
    )
    assert (hooked / "figures" / "f2.txt").exists(), "the file did not come back"


def test_a_failing_step_fails_the_build(hooked):
    """A document built from stale figures is the defect being fixed, so a hook
    that cannot run must stop the build rather than warn."""
    (hooked / "gen.sh").write_text("#!/bin/sh\nexit 3\n")
    result = run_make(hooked)
    assert result.returncode != 0, (
        f"a failing pre_build step did not fail the build:\n{result.stdout}"
    )


def test_a_step_that_produces_nothing_is_an_error_and_leaves_no_stamp(hooked):
    """Otherwise the next build sees a stamp and skips the step for good."""
    (hooked / "gen.sh").write_text("#!/bin/sh\nrm -rf figures\n")
    result = run_make(hooked)

    assert result.returncode != 0
    assert "produced none of" in result.stdout + result.stderr
    assert not (hooked / ".stencil-pre-build-0.stamp").exists(), (
        "a stamp survived a step that produced nothing; the next build would "
        "skip the step entirely"
    )


def test_doc_and_slide_depend_on_the_hook(hooked):
    """The edge that was missing. `make doc` has to pull the step in."""
    makefile = (hooked / "Makefile").read_text()
    doc = next(l for l in makefile.splitlines() if l.startswith("doc:"))
    slide = next(l for l in makefile.splitlines() if l.startswith("slide:"))
    assert "pre-build" in doc, doc
    assert "pre-build" in slide, slide


def test_a_package_without_the_key_gets_no_hook_machinery(doc_package):
    """Every existing package declares no pre_build and must be unchanged."""
    makefile = (doc_package / "Makefile").read_text()
    assert "pre-build" not in makefile
    assert ".stencil-pre-build" not in makefile


def test_outputs_are_required(demo_config):
    """Without them the step has nothing to be stale against and would run on
    every build -- the behaviour the feature exists to avoid. Better to refuse
    at generation time than to quietly become a prelude."""
    from stencil.generate import get_template_context

    config = demo_config
    config["packages"]["demo"]["pre_build"] = [{"run": "sh gen.sh"}]
    with pytest.raises(ValueError) as caught:
        get_template_context("demo", config)
    assert "outputs" in str(caught.value)


def test_run_is_required(demo_config):
    from stencil.generate import get_template_context

    config = demo_config
    config["packages"]["demo"]["pre_build"] = [{"outputs": "figures/*.txt"}]
    with pytest.raises(ValueError) as caught:
        get_template_context("demo", config)
    assert "run" in str(caught.value)


# ---------------------------------------------------------------------------
# stn-al9: the declarative fields are validated before they reach Make.
#
# Raised by CodeRabbit on the 0.29.0 PR and merged before I read the review --
# I checked the check STATUS and not the review itself. Both findings were
# reproduced against the real generator before being accepted.
#
# SCOPE IT HONESTLY: this is not a privilege boundary. `run` is arbitrary
# command execution by design, so anyone who can edit .config.yaml can already
# run anything, and `run` is deliberately NOT validated -- pretending to
# sanitise a command would invite the belief that a hostile config is
# contained, which it is not.
#
# What makes it worth fixing is that outputs, inputs and name are DECLARATIVE.
# An author writing a glob does not expect it to execute, and a `..` breaks the
# manifest and mount assumptions quietly rather than loudly.


def context_for(pre_build):
    from stencil.generate import get_template_context

    return get_template_context(
        "demo",
        {"packages": {"demo": {"package_type": "doc", "docs": ["g.md"],
                               "pre_build": pre_build}}},
    )


def test_an_ordinary_glob_is_accepted():
    """The validator must not be a wall. Globs are the whole point of outputs."""
    context = context_for([{"run": "x", "outputs": "figures/*.svg", "inputs": "gen.py"}])
    assert context["pre_build"][0]["outputs"] == ["figures/*.svg"]


def test_a_make_expansion_in_outputs_is_refused():
    """`$(shell echo INJECTED)` in outputs reached the Makefile verbatim and
    make expanded it. Reproduced before the fix."""
    with pytest.raises(ValueError) as caught:
        context_for([{"run": "x", "outputs": "$(shell echo INJECTED)a/*.txt"}])
    assert "metacharacter" in str(caught.value)


def test_an_absolute_output_is_refused():
    with pytest.raises(ValueError) as caught:
        context_for([{"run": "x", "outputs": "/etc/*.conf"}])
    assert "absolute" in str(caught.value)


def test_an_output_that_escapes_the_package_is_refused():
    with pytest.raises(ValueError) as caught:
        context_for([{"run": "x", "outputs": "../../out/*.txt"}])
    assert "escapes" in str(caught.value)


def test_inputs_are_validated_the_same_way():
    """Both fields land in Make text; validating one would be theatre."""
    with pytest.raises(ValueError):
        context_for([{"run": "x", "outputs": "a/*", "inputs": "$(shell id)"}])


def test_two_steps_with_the_same_name_are_refused():
    """The silent one.

    Two entries with the same name emit the same target and the same stamp
    variable. GNU Make keeps the later recipe, so the earlier hook never runs
    and nothing says so -- a build that reports success having skipped work.
    """
    with pytest.raises(ValueError) as caught:
        context_for([
            {"name": "dup", "run": "a", "outputs": "a/*"},
            {"name": "dup", "run": "b", "outputs": "b/*"},
        ])
    assert "dup" in str(caught.value)
    assert "never run" in str(caught.value)


def test_a_name_that_is_not_a_valid_make_target_is_refused():
    with pytest.raises(ValueError) as caught:
        context_for([{"name": "a b", "run": "x", "outputs": "a/*"}])
    assert "Make target" in str(caught.value)


def test_run_is_not_validated_and_that_is_deliberate():
    """`run` is the executable field. Sanitising it would suggest containment
    that does not exist -- the author already controls the whole command."""
    context = context_for([{"run": "sh -c 'echo $HOME && ls | wc -l'", "outputs": "a/*"}])
    assert "|" in context["pre_build"][0]["run"]
