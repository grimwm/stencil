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
