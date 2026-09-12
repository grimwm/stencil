"""A package can send its build products somewhere other than beside its sources.

stn-d81. The package directory held sources, generated scaffolding and build
output together, and there was no way to separate them: the `-o` path was
derived from the source path inside a single mounted directory.

Two things the ticket said to settle deliberately, settled here:

1. THE BASE. `output_dir` used to be top-level only and CWD-relative -- the
   only path in a config that was, since templates_dir and every
   `brand: file://` resolve against the config file. Adding a second key of the
   same name with a different base would have been a trap, so instead there is
   ONE key with ONE base: relative to the .config.yaml, at both levels. Checked
   across cs234 and cs425 first: no config sets the top-level key, so nothing
   depended on the old behaviour.

2. `dir` VS `output_dir`. They are orthogonal and stay so. `dir` says where the
   package's sources and scaffolding live; `output_dir` says where its build
   products go. Unset, products land beside the sources, exactly as before.
"""

from __future__ import annotations

import shutil

import pytest
import yaml

from stencil import pipeline
from stencil.generate import package_contexts

from test_cli import run_cli, write_config
from test_path_containment import C0_CONTROLS



@pytest.fixture
def elsewhere(demo_config, generate_package):
    """A package whose products go to a sibling of the package directory."""
    config = demo_config
    config["packages"]["demo"]["dir"] = "demo"
    config["packages"]["demo"]["output_dir"] = "build/demo"
    return generate_package(config)


def makefile(package):
    return (package / "Makefile").read_text()


def compose(package):
    return (package / "docker-compose.yml").read_text()


def test_the_html_is_written_to_the_output_directory(elsewhere):
    text = makefile(elsewhere)
    doc_lines = [l for l in text.splitlines() if "run --rm doc " in l]
    assert doc_lines, "no doc rule"
    for line in doc_lines:
        assert "-o $(OUT)/" in line, line


def test_the_pdf_reads_and_writes_the_output_directory(elsewhere):
    text = makefile(elsewhere)
    pdf_lines = [l for l in text.splitlines() if "run --rm pdf " in l]
    assert pdf_lines, "no pdf rule"
    for line in pdf_lines:
        # Both the input HTML and the output PDF, or `make pdf` reads a file
        # `make doc` did not write.
        assert line.count("$(OUT)/") == 2, line


def test_check_pdf_looks_in_the_output_directory(elsewhere):
    line = next(l for l in makefile(elsewhere).splitlines() if "check-pdf" in l and "run --rm" in l)
    names = [w for w in line.split() if w.endswith(".pdf")]
    assert names, line
    assert all(n.startswith("$(OUT)/") for n in names), line


def test_the_output_directory_is_mounted(elsewhere):
    """One mount cannot reach a sibling: `-o ../build/foo.html` escapes it.

    PARSED, not string-matched. The first version of this test asserted the
    substring was present -- it was, inside a line that had been glued to its
    neighbour by Jinja whitespace control, producing
    `- .:/workspace:z      # comment` and a compose file that would not load:
    "services.slide.volumes.[1] is missing a mount target". The string was
    there and the file was broken, which is the difference between checking
    the text and checking the artifact.
    """
    import yaml

    doc = yaml.safe_load(compose(elsewhere))
    for name, service in doc["services"].items():
        volumes = service.get("volumes")
        if not volumes:
            continue
        assert ".:/workspace:z" in volumes, (name, volumes)
        assert "../../build/demo:/out:z" in volumes, (name, volumes)


def test_the_generated_compose_file_is_valid_yaml(elsewhere):
    """The guard the string assertions could not give.

    Every service has to keep the keys compose needs; a mangled list item
    silently becomes part of the line above it.
    """
    import yaml

    doc = yaml.safe_load(compose(elsewhere))
    assert doc["services"], "no services survived"
    for name, service in doc["services"].items():
        assert "image" in service or "build" in service, (name, service)
        if service.get("volumes"):
            assert service.get("working_dir"), (
                f"{name} lost its working_dir; a volume entry probably "
                "swallowed the line after it"
            )


def check_access_directory(text: str) -> str:
    """The directory the generated check-access service is told to search.

    Read off the parsed service rather than grepped out of the script, because
    stn-s5b moved the script into pipeline.py and the directory became its
    argument. Asserting the argument is also the stronger assertion: the script
    builds its file:// URL from this one path, so there is no second place for
    it to disagree with -- which is exactly how check-access came to search
    /out while asking Chromium for file:///workspace//out/... and could not
    pass at all for a package with an output_dir.
    """
    service = yaml.safe_load(text)["services"]["check-access"]
    return service["entrypoint"][-1]


def test_check_access_looks_in_the_output_directory(elsewhere):
    """The silent one. It loops over a glob, and a glob that matches nothing
    leaves $failed at 0 -- which exits 0 and reads exactly like passing."""
    assert check_access_directory(compose(elsewhere)) == "/out"


def test_check_access_fails_when_it_checks_nothing(elsewhere):
    """Asserted by COUNTING, which is what the ticket asked for, because
    checking zero files exits 0."""
    text = compose(elsewhere)
    assert "checked=$$((checked + 1))" in text
    assert 'if [ "$$checked" -eq 0 ]' in text
    assert "found no HTML to check" in text


def test_clean_removes_products_from_the_output_directory(elsewhere):
    line = next(l for l in makefile(elsewhere).splitlines() if l.strip().startswith("rm -f"))
    products = [w for w in line.split() if w.endswith((".html", ".pdf", "*.html", "*.pdf"))]
    assert products, line
    assert all(p.startswith("$(OUT_HOST)/") for p in products), line


def test_the_output_directory_is_created_before_it_is_mounted(elsewhere):
    """A bind mount to a path that does not exist is created by the daemon and
    owned by root, after which nothing on the host can write to it."""
    text = makefile(elsewhere)
    assert "mkdir -p $(OUT_HOST)" in text
    doc = next(l for l in text.splitlines() if l.startswith("doc:"))
    assert "out-dir" in doc, doc


def test_a_package_without_output_dir_is_unchanged(doc_package):
    """Every existing package sets no output_dir and must build exactly where
    it always did. This is the guard that makes the feature safe to add."""
    package = doc_package
    text = makefile(package)

    assert "OUT := ." in text
    assert "OUT_HOST := ." in text
    assert ":/out:z" not in compose(package), "an unused second mount was added"
    assert check_access_directory(compose(package)) == "/workspace"


def test_output_dir_is_resolved_against_the_config_not_the_cwd(tmp_path, demo_config):
    """The behaviour change, asserted rather than described in a release note.

    Generating from a different working directory must land in the same place.
    """
    from stencil.generate import build_environment, generate_package, load_config
    import os
    import yaml

    config = demo_config
    config["output_dir"] = "out"
    project = tmp_path / "project"
    project.mkdir()
    (project / ".config.yaml").write_text(yaml.safe_dump(config))

    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()

    loaded = load_config(project / ".config.yaml")
    env = build_environment(loaded, project)
    here = os.getcwd()
    try:
        os.chdir(elsewhere)
        generate_package(env, loaded, project / "out", "demo", False, project)
    finally:
        os.chdir(here)

    assert (project / "out" / "demo" / "Makefile").exists(), (
        "output landed relative to the working directory rather than the config"
    )
    assert not (elsewhere / "out").exists(), "output followed the shell's cwd"


def test_the_path_climbs_out_of_the_top_level_output_dir_too():
    """A bug a string assertion could not see, found by a real build.

    The package directory is <top-level output_dir>/<dir>, so the path back out
    to a config-relative output directory has to climb BOTH. Computing it
    against `dir` alone resolved `build/demo` to `out/build/demo` -- the
    Makefile still said `$(OUT)/Guide.html`, the compose file still had a
    second mount, every string assertion still passed, and `make doc` put the
    products in the wrong directory.
    """
    from stencil.generate import get_template_context

    package = {
        "package_type": "doc",
        "dir": "demo",
        "output_dir": "build/demo",
        "docs": ["Guide.md"],
    }

    with_top = get_template_context(
        "demo", {"output_dir": "out", "packages": {"demo": package}}
    )
    assert with_top["package_output_dir"] == "../../build/demo", (
        "the path did not climb out of the top-level output_dir"
    )

    without_top = get_template_context("demo", {"packages": {"demo": package}})
    assert without_top["package_output_dir"] == "../build/demo"


# --- stn-1a4: the package-level output_dir was the one path key with no ----
# validation at all
#
# `raw_output = package.get("output_dir")` goes through os.path.relpath and
# straight into `package_output_dir`. It never sees check_config_path,
# check_no_glob, or any other check -- and unlike every other package path
# (dir, dest, docs, slides, brand) it lands in a Make VARIABLE, OUT_HOST,
# that recipes expand rather than pass as one argument. A value with a shell
# metacharacter in it is therefore a second shell command, not a bad
# filename.
#
# check_config_path already encodes the class this needs, minus one clause:
# its `..` refusal has to stay OFF for this key, because a package-level
# output_dir sending build products outside the package directory is a
# documented feature (STENCIL.md, and test_the_path_climbs_out_of_the_top_
# level_output_dir_too above), not a mistake.

def package(**overrides):
    base = {"name": "Demo", "package_type": "none", "docs": ["README.md"]}
    base.update(overrides)
    return base


def config(**overrides):
    return {"packages": {"demo": package(**overrides)}}


_TEMPLATES = [{"src": "Makefile.j2"}, {"src": "docker-compose.yml.j2"}]


def cli_config(output_dir) -> dict:
    """A config that reaches the real Makefile.j2, the way the ticket's
    reproduction did -- `package_contexts` alone proves the value is
    refused, this proves the CLI a person actually runs refuses it too."""
    return {
        "output_dir": "out",
        "templates": _TEMPLATES,
        "packages": {"demo": package(output_dir=output_dir)},
    }


TICKET_VALUE = "../../../../../../tmp/pwn; echo OWNED"


def test_the_tickets_value_is_refused_by_name():
    """Verbatim reproduction from stn-1a4. Today `package_contexts` builds a
    context for this value without complaint -- output_dir is not checked at
    all -- so this fails by not raising. It has to raise AND name the key,
    or a config with several packages sends the author hunting for which one
    and which setting is the problem."""
    with pytest.raises(ValueError, match="output_dir") as exc:
        package_contexts(config(output_dir=TICKET_VALUE))
    assert "demo" in str(exc.value)


def test_the_tickets_value_is_refused_by_the_cli(tmp_path):
    """The CLI half of the same reproduction: run the way a person actually
    hits it, `stencil gen demo` in a directory holding this config. Today it
    exits 0 -- see the sibling test below for what it exits 0 having
    written."""
    write_config(tmp_path, cli_config(TICKET_VALUE))
    result = run_cli("gen", "demo", cwd=tmp_path)
    assert result.returncode != 0, (
        f"gen exited 0 with a shell metacharacter in output_dir:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "Traceback" not in result.stderr, result.stderr
    assert "demo" in result.stderr, result.stderr
    assert "output_dir" in result.stderr, result.stderr


def test_the_tickets_value_never_reaches_the_generated_makefile(tmp_path):
    """Asserting the refusal alone would not prove the Makefile is clean --
    that assertion would pass just as well if `gen` refused for some
    unrelated reason after already writing the file. This reads the
    artifact stn-1a4 quotes verbatim: today `stencil gen demo` writes
    `out/demo/Makefile` with `OUT_HOST := .../tmp/pwn; echo OWNED`, and
    `make doc` runs `mkdir -p .../tmp/pwn` and then `echo OWNED` as a
    second shell command. Once the fix lands nothing is written at all, so
    a missing Makefile satisfies this the same way a clean one would.
    """
    write_config(tmp_path, cli_config(TICKET_VALUE))
    run_cli("gen", "demo", cwd=tmp_path)
    makefile_path = tmp_path / "out" / "demo" / "Makefile"
    assert not makefile_path.exists() or "; echo" not in makefile_path.read_text(), (
        "the injected command reached the generated Makefile's OUT_HOST"
    )


PACKAGE_OUTPUT_DIR_ESCAPE_CASES = [
    pytest.param("a;b", "shell or Make metacharacter", id="metacharacter"),
    pytest.param("a b", "contains whitespace", id="whitespace"),
    pytest.param(f"a{C0_CONTROLS[2]}b", "control character", id="control-character"),
    pytest.param("~/escape", "starts with '~'", id="tilde"),
    pytest.param("/tmp/escape", "is absolute", id="absolute"),
]


@pytest.mark.parametrize("value, expected", PACKAGE_OUTPUT_DIR_ESCAPE_CASES)
def test_a_package_output_dir_character_class_is_refused(value, expected):
    """check_config_path's own classes, now applied to the one path key that
    skipped every one of them. Not the '..' clause -- that escape is
    deliberate and pinned separately below, at the declared value."""
    with pytest.raises(ValueError) as exc:
        package_contexts(config(output_dir=value))
    message = str(exc.value)
    assert expected in message, f"expected {expected!r} in: {message!r}"
    assert "demo" in message, f"the broken package is not named: {message!r}"


@pytest.mark.parametrize("value, expected", PACKAGE_OUTPUT_DIR_ESCAPE_CASES)
def test_a_package_output_dir_character_class_is_refused_by_the_cli(
    tmp_path, value, expected
):
    """The CLI half of the parametrized class: the message a person editing
    .config.yaml actually reads, not just the exception a library caller
    sees."""
    write_config(tmp_path, cli_config(value))
    result = run_cli("gen", "demo", cwd=tmp_path)
    assert result.returncode != 0, f"gen exited 0 for output_dir {value!r}"
    assert "Traceback" not in result.stderr, result.stderr
    assert "demo" in result.stderr, result.stderr


@pytest.mark.parametrize("value", ["build/demo", "build", ".", "../build/demo"])
def test_a_plain_package_output_dir_still_passes(value):
    """The guard must not start rejecting the ordinary cases this whole file
    is built around -- asserted through `package_contexts`, the path every
    command actually takes, rather than against the character check in
    isolation, which would still pass if the new check were wired in wrong.

    `.` is in the list because it is legal and surprising: it passes
    `check_config_path` (`Path(".").parts` is empty) and `relpath(".",
    "demo")` makes `..` -- so it is the documented escape arrived at by a
    value that does not look like one. Pinned rather than discovered."""
    contexts = package_contexts(config(output_dir=value))
    assert contexts["demo"]["has_package_output_dir"]


@pytest.mark.parametrize("value", ["", None])
def test_an_absent_or_empty_package_output_dir_means_not_set(value):
    """`""` is a FALSY STRING, and it means what an absent key means: build
    products land beside the sources. That is the decision
    `check_output_dir`'s docstring records for the top-level key, and the
    two keys must not disagree about it -- refusing `""` here would make the
    same two characters mean "not set" at one level and "error" at the
    other.

    It matters more than it looks because the new type check has to run
    ABOVE `if raw_output:` (that is the only way `output_dir: 0` is
    refusable at all), so `""` now reaches a check that never saw it
    before."""
    overrides = {} if value is None else {"output_dir": value}
    contexts = package_contexts(config(**overrides))
    assert contexts["demo"]["package_output_dir"] == ""
    assert not contexts["demo"]["has_package_output_dir"]


def test_a_false_package_output_dir_is_refused_by_type():
    """The third falsy non-string, beside `0` and `[]`. `False` is an
    ordinary thing to get from YAML (`output_dir: no` parses as one), and
    reading it as "not set" is the same silent misread stn-40a closed for
    the top-level key."""
    with pytest.raises(ValueError, match="output_dir") as exc:
        package_contexts(config(output_dir=False))
    assert "bool" in str(exc.value), str(exc.value)


def test_a_package_output_dir_with_a_make_comment_is_refused():
    """`#` starts a comment in a Make `:=` assignment, and this key is the
    only checked path that lands in one.

    Measured on GNU Make 3.81 with `output_dir: "build#x"`: the generated
    `OUT_HOST := ../../build#x` reads as `../../build`, so `make out-dir`
    creates `build` and `make clean-pkg` runs `rm -f` there -- while the
    compose file's `../../build#x:/out:z` mount keeps the `#`, because YAML
    does not treat it as a comment mid-scalar. One declared value, two
    different directories, and nothing says so.

    Not added to `_UNSAFE_IN_PATH`, which every other path key shares: `#`
    is harmless in a recipe word and in a filename, and refusing it
    everywhere would reject `notes#1.md` for no reason."""
    with pytest.raises(ValueError, match="output_dir") as exc:
        package_contexts(config(output_dir="build#x"))
    assert "comment" in str(exc.value), str(exc.value)


@pytest.mark.parametrize("value", ["build/*", "build/?x", "build/[a-z]"])
def test_a_package_output_dir_with_a_glob_metacharacter_is_refused(value):
    """This names one directory, not a pattern -- the same argument
    `check_no_glob` makes for `docs` and `slides`.

    The sharper reason here is the generated compose file: a
    `package_output_dir` beginning with `*` or `!` makes the YAML
    unparseable, because those are the alias and tag indicators. Measured:
    a ScannerError out of a file stencil wrote, which is a worse failure
    than a refusal because it names neither stencil nor the key."""
    with pytest.raises(ValueError, match="output_dir") as exc:
        package_contexts(config(output_dir=value))
    assert "glob metacharacter" in str(exc.value), str(exc.value)


def test_a_relative_escape_in_the_declared_output_dir_still_generates(
    demo_config, generate_package
):
    """THE ESCAPE, PINNED AT THE DECLARED VALUE (operator ruling).

    Every existing pinned test in this file only pins the escape at the
    DERIVED package_output_dir --
    test_the_path_climbs_out_of_the_top_level_output_dir_too above asserts
    on '../../build/demo', which get_template_context COMPUTES from `dir`
    and the top-level output_dir, not on what a config author actually
    types. check_config_path VERBATIM would keep that test green -- it
    never sees the derived value, only the declared one -- while silently
    deleting a documented feature the moment a package's own output_dir
    starts with '..'. This test types the '..' at the source, so that
    "fix" cannot pass silently: it must still generate, at exit 0, and the
    value must still reach the Makefile.
    """
    cfg = demo_config
    cfg["packages"]["demo"]["output_dir"] = "../build/demo"
    package_dir = generate_package(cfg)
    text = (package_dir / "Makefile").read_text()
    line = next(l for l in text.splitlines() if l.startswith("OUT_HOST"))
    assert ".." in line, (
        f"the declared '..' escape did not reach OUT_HOST: {line!r}"
    )


def test_a_relative_escape_in_the_declared_output_dir_is_not_refused_by_the_cli(
    tmp_path,
):
    """The CLI half of the ruling above, checked the same way the ticket's
    injection is checked: `stencil gen demo` must exit 0, not merely avoid
    raising inside a direct call to package_contexts."""
    write_config(tmp_path, cli_config("../build/demo"))
    result = run_cli("gen", "demo", cwd=tmp_path)
    assert result.returncode == 0, (
        f"a documented '..' escape was refused:\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


def test_a_falsy_zero_output_dir_is_refused_by_type_not_silently_ignored():
    """`output_dir: 0` is a FALSY non-string. `if raw_output:` in
    get_template_context reads that as "not set" and silently produces
    `package_output_dir = ""` -- the exact misread check_output_dir's own
    docstring names for the top-level key, and for the same reason: falsy
    is checked before type is. A config author who typed `output_dir: 0` by
    mistake (a bare, unquoted number where a path belonged) gets no error
    at all."""
    with pytest.raises(ValueError, match="output_dir") as exc:
        package_contexts(config(output_dir=0))
    assert "demo" in str(exc.value)


def test_a_falsy_zero_output_dir_is_refused_by_the_cli(tmp_path):
    write_config(tmp_path, cli_config(0))
    result = run_cli("gen", "demo", cwd=tmp_path)
    assert result.returncode != 0, "gen exited 0 for output_dir: 0"
    assert "Traceback" not in result.stderr, result.stderr
    assert "demo" in result.stderr, result.stderr


def test_a_list_valued_output_dir_is_refused_by_type_not_a_bare_typeerror():
    """`output_dir: ['x']` is TRUTHY, so it reaches
    `os.path.relpath(Path(raw_output), package_root)` -- and `Path(['x'])`
    raises a bare TypeError ('argument should be a str, bytes or
    os.PathLike object, not list') from a codepath with no ValueError guard
    at all, the exact class package_contexts exists to convert.
    check_output_dir already runs its type check before its falsiness
    check for the top-level key, and its docstring says why; the
    package-level check needs the same order or it inherits the same hole
    from the other direction."""
    with pytest.raises(ValueError, match="output_dir") as exc:
        package_contexts(config(output_dir=["x"]))
    assert "demo" in str(exc.value)


def test_a_list_valued_output_dir_is_refused_by_the_cli_without_a_traceback(tmp_path):
    """The non-crash half already works today, through package_contexts'
    generic TypeError/AttributeError/KeyError catch-all -- but that catch-all
    names only the exception class, not the key, so the message a person
    reads says "TypeError" rather than "output_dir". Asserted here too so a
    future check_package_output_dir has to keep BOTH properties: no
    traceback, and the actual key named."""
    write_config(tmp_path, cli_config(["x"]))
    result = run_cli("gen", "demo", cwd=tmp_path)
    assert result.returncode != 0, "gen exited 0 for output_dir: ['x']"
    assert "Traceback" not in result.stderr, result.stderr
    assert "demo" in result.stderr, result.stderr
    assert "output_dir" in result.stderr, result.stderr


# --- and now the same thing, RUN rather than read (stn-ao5) ----------------
#
# test_the_pdf_reads_and_writes_the_output_directory above reads the generated
# Makefile's TEXT and asserts the rule hands /out paths to the pdf service. That
# is worth asserting and it is not enough, because it says nothing about what
# html-to-pdf.js then DOES with them -- and what it did was join the argument
# onto a hard-coded /workspace, so `make pdf` asked Chromium for
# file:///workspace//out/document.html and could not build at all for a package
# with an output_dir:
#
#     Failed to convert /out/document.html:
#       net::ERR_FILE_NOT_FOUND at file:///workspace//out/document.html
#
# That is the SAME defect check_access_directory's docstring records for the
# sibling service, in the sibling service, found the same way and fixed the same
# way: one path, built from the argument, rather than two that have to agree.
# It shipped because every test in this file reads text. This one runs.


@pytest.mark.integration
def test_make_pdf_reads_a_page_from_the_output_directory_mount(
    pdf_workspace, tmp_path
):
    """The two-mount layout a package with an output_dir actually gets.

    /workspace is the package directory and /out is a SEPARATE mount, because
    the products are a sibling of the sources and a sibling is `..` away, which
    escapes a bind mount. The page therefore is not reachable from /workspace at
    all, by any path -- which is what makes the hard-coded prefix fatal here and
    invisible everywhere else.
    """
    built = pipeline.render(
        "doc", "document.md", "document.html", workdir=pdf_workspace
    )
    assert built.returncode == 0, f"pandoc failed\n{built.stderr}"

    workspace = tmp_path / "package"
    workspace.mkdir()
    out = tmp_path / "build"
    out.mkdir()
    shutil.copy2(pdf_workspace / "document.html", out / "document.html")
    shutil.copy2(pdf_workspace / "html-to-pdf.js", workspace / "html-to-pdf.js")

    result = pipeline.html_to_pdf(
        "/out/document.html",
        "/out/document.pdf",
        workdir=workspace,
        out_dir=out,
        timeout=180,
    )

    assert result.returncode == 0, (
        f"the pdf service exited {result.returncode} for a page on the /out "
        f"mount\nstdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-3000:]}"
    )
    assert "ERR_FILE_NOT_FOUND" not in result.stdout + result.stderr, (
        "the page URL was still built by joining the argument onto a "
        f"hard-coded prefix\nstdout: {result.stdout[-2000:]}"
    )
    assert (out / "document.pdf").is_file(), (
        "the pdf service exited 0 but wrote no document.pdf to the /out mount"
    )
