"""package_sources: the one key naming what `pkg` puts into a submission.

Two things here are worth a test rather than a comment. The expansion rule is
deliberately dumb -- a glob expands sorted, anything else is used verbatim in
the position it was listed -- because a build system that guesses from a path's
shape is a build system you cannot predict. And `package_folder`, the zip-only
spelling this replaced, has to fail loudly: left as a silently ignored key it
would package the default htdocs instead of the db or . a package asked for,
producing a plausible-looking archive with the wrong contents in it.
"""

from __future__ import annotations

import pytest

from stencil.generate import (
    SHARED_PAGE_TEMPLATES,
    get_generated_files,
    get_template_context,
    template_dest,
)

from test_makefile import recipe

MAKEFILE_TEMPLATES = [{"src": "Makefile.j2"}, {"src": "docker-compose.yml.j2"}]


def context(**package):
    package.setdefault("name", "Demo")
    return get_template_context("demo", {"packages": {"demo": package}})


@pytest.fixture
def makefile(generate_package):
    def _makefile(**package):
        package.setdefault("name", "Demo")
        pkg = generate_package(
            {"templates": MAKEFILE_TEMPLATES, "packages": {"demo": package}}
        )
        return (pkg / "Makefile").read_text()

    return _makefile


@pytest.fixture
def doc_pkg_makefile(makefile):
    return makefile(
        package_type="doc",
        package_name="hs2.pdf",
        package_sources=["md/*.md"],
        docs=["README.md"],
    )


# --- the migration ---------------------------------------------------------


def test_package_folder_is_a_hard_error():
    """Silently ignoring it would zip htdocs for a package that asked for db."""
    with pytest.raises(ValueError, match="renamed to 'package_sources'"):
        context(package_type="zip", package_name="hs3.zip", package_folder="db")


def test_the_error_names_the_replacement_spelling():
    """The fix should be copy-pasteable out of the message."""
    with pytest.raises(ValueError) as excinfo:
        context(package_type="zip", package_name="hs3.zip", package_folder="db")
    assert "package_sources: [db]" in str(excinfo.value)


def test_zip_still_defaults_to_htdocs():
    """What package_folder defaulted to, so an unset package is unchanged."""
    assert context(package_type="zip", package_name="hs3.zip")[
        "package_sources"
    ] == ["htdocs"]


# --- validation ------------------------------------------------------------


def test_a_doc_building_a_pdf_needs_a_name_to_build_it_under():
    with pytest.raises(ValueError, match="missing"):
        context(package_type="doc", package_sources=["md/*.md"])


def test_a_doc_submission_must_be_a_pdf():
    """The stem names the intermediate HTML too, so .zip would be incoherent."""
    with pytest.raises(ValueError, match="must end in .pdf"):
        context(
            package_type="doc", package_name="hs2.zip", package_sources=["md/*.md"]
        )


def test_sources_without_a_pkg_target_is_a_configuration_error():
    with pytest.raises(ValueError, match="package_type: none"):
        context(package_type="none", package_sources=["md/*.md"])


def test_a_bare_string_is_accepted_as_a_one_entry_list():
    assert context(
        package_type="doc", package_name="hs2.pdf", package_sources="md/*.md"
    )["package_sources"] == ["md/*.md"]


# --- the expansion rule ----------------------------------------------------


def test_doc_expansion_takes_every_file_under_a_directory(doc_pkg_makefile):
    """find, not a wildcard: make has no recursive glob, and zip -r recurses."""
    assert "find $(1) -type f" in doc_pkg_makefile
    assert "$(wildcard $(1)/*.md)" not in doc_pkg_makefile, "no extension filter"


def test_doc_expansion_skips_dotfiles(doc_pkg_makefile):
    """zip -r keeps them harmlessly; pandoc handed a .DS_Store fails the build.
    -path rather than -name so a file under a dot-directory goes too."""
    assert "-not -path '*/.*'" in doc_pkg_makefile


def test_the_walk_has_a_branch_for_each_shell(doc_pkg_makefile):
    """make runs recipes through cmd on Windows, which has no find -- and whose
    own find.exe is a text search that would fail obscurely rather than
    cleanly. Both branches must yield relative, forward-slashed, sorted files."""
    assert "ifeq ($(OS),Windows_NT)" in doc_pkg_makefile
    assert "Get-ChildItem" in doc_pkg_makefile
    assert "find $(1) -type f" in doc_pkg_makefile


def test_neither_walk_keeps_dotfiles(doc_pkg_makefile):
    """Same rule on both platforms, spelled in each one's own syntax."""
    assert "-not -path '*/.*'" in doc_pkg_makefile
    assert "-notmatch '(^|/)\\.'" in doc_pkg_makefile


def test_doc_expansion_sorts_by_byte_not_by_locale(doc_pkg_makefile):
    """find's output order is unspecified, so reading order needs the sort."""
    assert "LC_ALL=C sort" in doc_pkg_makefile


def test_doc_expansion_drops_the_directories_themselves(doc_pkg_makefile):
    """pandoc would fail on one, and a tree walk turns them up."""
    assert "-type f" in doc_pkg_makefile


def test_zip_hands_a_directory_over_whole(makefile):
    """zip -r walks it itself, and picks up the dotfiles a glob would miss."""
    text = makefile(package_type="zip", package_name="hs3.zip")
    assert "$(wildcard $(1)/.)" not in text, "zip should not expand a directory"
    # Not recipe(): the pkg body is split across an ifeq/else, and the helper
    # stops at the first line that is not a tab-indented recipe line.
    assert "zip -r $(PKG) $(PKG_SOURCES)" in text


# --- the zip pkg target, and the hidden .git it has to carry ----------------
#
# A course that grades the repository needs the repository in the submission,
# and Compress-Archive cannot put it there. `git init` on Windows sets the
# Hidden attribute on .git, Compress-Archive expands a directory with
# Get-ChildItem and no -Force, and the subtree is dropped without a word --
# not even under -Verbose. So a student who initialized a repo inside the
# packaged directory submitted an archive with no .git on Windows and a
# complete one on Unix. Windows uses bsdtar now, which has no notion of hidden.


def test_windows_does_not_archive_with_compress_archive(makefile):
    """It silently drops every hidden entry and has no switch to stop it.

    Over the recipe lines rather than the whole file, because the comment
    above the target explains at length what is wrong with the cmdlet and
    would otherwise be the thing keeping this test green.
    """
    text = makefile(package_type="zip", package_name="hs3.zip")
    recipes = [line for line in text.splitlines() if line.startswith("\t")]
    assert not [line for line in recipes if "Compress-Archive" in line]


def test_windows_archives_with_tar(makefile):
    """bsdtar, tar.exe since Windows 10 1803. Not recipe(): the pkg body is
    split across an ifeq/else, and the helper stops at the first line that is
    not a tab-indented recipe line."""
    text = makefile(package_type="zip", package_name="hs3.zip")
    assert 'tar --format zip -cf "$(PKG)" $(PKG_SOURCES)' in text


def test_the_archive_format_is_explicit_rather_than_inferred(makefile):
    """-a reads the format off the suffix, and gets it wrong quietly twice.

    In Git Bash and MSYS2 $(OS) is Windows_NT but `tar` is GNU tar, whose -a
    does not know .zip: measured on GNU tar 1.35, `tar -a -cf out.zip dir`
    exits 0 and writes a POSIX tar archive under the .zip name. --format zip is
    'Invalid archive format' there, exit 2, so make stops instead. And
    package_name is not required to end in .zip -- bsdtar 3.8.3 given
    `-a --format zip -cf d.tar.gz` writes a GZIP-compressed zip, where the
    format alone writes a zip whatever the archive is called.
    """
    text = makefile(package_type="zip", package_name="hs3.zip")
    assert "--format zip" in text
    assert "tar -a" not in text, "-a would infer the format from the suffix"


def test_the_archive_has_a_name_without_a_consumers_help(makefile):
    """The bundled zip branch never defined PKG -- only a consumer's own
    Makefile.j2 did -- so a package generated from the bundled templates alone
    had an empty $(PKG): `clean-pkg` removed nothing, and `tar -cf ""` writes
    the archive to stdout where Compress-Archive had at least refused an empty
    -DestinationPath. `?=` so a composition that sets PKG first still wins.
    """
    text = makefile(package_type="zip", package_name="hs3.zip")
    assert "PKG ?= hs3.zip" in text
    assert "PKG = hs3.zip" not in text, "a consumer's own PKG must survive"


def test_the_comma_joining_left_with_the_cmdlet_that_needed_it(makefile):
    """Compress-Archive -Path took a comma-separated list; tar takes a plain
    argument list, so nothing is left to subst spaces in."""
    text = makefile(package_type="zip", package_name="hs3.zip")
    for helper in ("pkg_empty", "pkg_space", "pkg_comma"):
        assert helper not in text


def test_specs_keep_the_order_they_were_listed_in(makefile):
    """A literal holds its position, so [preface, glob, colophon] reads so."""
    text = makefile(
        package_type="doc",
        package_name="hs2.pdf",
        package_sources=["preface.md", "md/*.md", "colophon.md"],
    )
    assert "PKG_SOURCE_SPECS = preface.md md/*.md colophon.md" in text


# --- the doc pkg target ----------------------------------------------------


def test_pkg_renders_the_sources_then_prints_that_html(doc_pkg_makefile):
    """html first, pdf from that html -- not two independent conversions."""
    body = recipe(doc_pkg_makefile, "pkg")
    render = next(line for line in body if "run --rm doc" in line)
    convert = next(line for line in body if "run --rm pdf" in line)
    assert render.endswith("-o $(PKG_HTML)")
    assert "$(PKG_SOURCES)" in render
    assert convert.endswith("$(PKG_HTML) $(PKG)")


def test_the_suffix_reaches_both_the_html_and_the_pdf(doc_pkg_makefile):
    """`make pkg with=hidden` must not pair an answer key with a plain build."""
    assert "PKG_HTML = hs2$(OUTPUT_SUFFIX).html" in doc_pkg_makefile
    assert "PKG = hs2$(OUTPUT_SUFFIX).pdf" in doc_pkg_makefile


def test_pkg_refuses_an_empty_expansion(doc_pkg_makefile):
    """Otherwise pandoc reads stdin and the build hangs with no output."""
    assert "$(error package_sources matched nothing" in "\n".join(
        recipe(doc_pkg_makefile, "pkg")
    )


def test_pkg_checks_for_the_image_it_actually_runs(doc_pkg_makefile):
    """A hardcoded tag here would drift from the pinned one in pipeline.py."""
    from stencil import pipeline

    assert pipeline.PANDOC_IMAGE in "\n".join(recipe(doc_pkg_makefile, "pkg"))


def test_clean_removes_both_outputs_and_their_variants(doc_pkg_makefile):
    removed = " ".join(recipe(doc_pkg_makefile, "clean-pkg"))
    for pattern in ("hs2.html", "hs2-*.html", "hs2.pdf", "hs2-*.pdf"):
        assert pattern in removed, f"clean-pkg leaves {pattern} behind"


def test_a_doc_without_sources_gets_no_pkg_target(makefile):
    """The existing doc packages are untouched by this."""
    assert "\npkg:" not in makefile(package_type="doc", docs=["README.md"])


# --- package_sources with no docs and no slides ----------------------------
#
# generate_package injects the shared page files whenever a package renders
# markdown -- has_pages, which counts package_sources. get_generated_files and
# the doc-template injection keyed off docs/slides instead, so a doc package
# that builds only from package_sources fell through both: five files nothing
# could clean, and a pkg target naming a pandoc template that was never
# written.

# Derived rather than listed. This was a hand-written subset of the files
# generate.py injects, which is the third spelling of that list and the one
# most likely to go quietly stale -- it is a test fixture, so nothing fails
# when it falls behind, it just stops checking the file that was added.
SHARED_PAGE_FILES = [template_dest(src) for src in SHARED_PAGE_TEMPLATES]


def sources_only_config():
    """A doc package whose only markdown comes in through package_sources."""
    return {
        "templates": MAKEFILE_TEMPLATES,
        "packages": {
            "demo": {
                "name": "Demo",
                "package_type": "doc",
                "package_name": "hs2.pdf",
                "package_sources": ["md/*.md"],
            }
        },
    }


@pytest.mark.parametrize("filename", SHARED_PAGE_FILES)
def test_sources_only_shared_page_files_are_cleanable(filename):
    """generate writes them off has_pages, so clean and .gitignore must agree.
    Left out, they sit untracked in a course repo and nothing removes them."""
    assert f"demo/{filename}" in get_generated_files(sources_only_config())


def test_sources_only_doc_template_is_cleanable():
    assert "demo/html-template.html" in get_generated_files(sources_only_config())


def test_sources_only_pkg_gets_the_template_its_pandoc_run_names(generate_package):
    """Makefile-pkg builds PKG_HTML through the doc service, and pipeline.py
    makes that service pass --template=html-template.html. Without the file the
    generated target exists and fails."""
    assert (generate_package(sources_only_config()) / "html-template.html").exists()


def test_sources_only_gets_no_slide_template(generate_package):
    """Nothing here renders a deck, so the deck templates stay out."""
    package = generate_package(sources_only_config())
    listed = get_generated_files(sources_only_config())
    for unwanted in ("slide-template.html", "slide-sections.lua"):
        assert not (package / unwanted).exists()
        assert f"demo/{unwanted}" not in listed


def test_a_docs_package_still_lists_every_page_file():
    """The other half of the fix: packages that do declare docs are unchanged."""
    config = sources_only_config()
    config["packages"]["demo"]["docs"] = ["README.md"]
    listed = get_generated_files(config)
    for filename in SHARED_PAGE_FILES + ["html-template.html"]:
        assert f"demo/{filename}" in listed


# --- the general form of the bug above --------------------------------------


@pytest.mark.parametrize(
    "package",
    [
        pytest.param({"package_type": "none"}, id="renders-nothing"),
        pytest.param({"package_type": "none", "docs": ["README.md"]}, id="docs"),
        pytest.param({"package_type": "none", "slides": ["Deck.md"]}, id="slides"),
        pytest.param(
            {"package_type": "none", "docs": ["README.md"], "slides": ["Deck.md"]},
            id="both",
        ),
        pytest.param(
            {
                "package_type": "doc",
                "package_name": "hs2.pdf",
                "package_sources": ["md/*.md"],
            },
            id="package-sources-only",
        ),
    ],
)
def test_every_file_a_package_holds_is_one_clean_can_see(generate_package, package):
    """The property, rather than one more hand-written list.

    `stencil clean` and the managed `.gitignore` section are both driven by
    `get_generated_files`, which used to spell the injected template list a
    second time -- and the two drifted, leaving a package_sources-only doc
    package with five files nothing could remove and git happily tracked. The
    lists are one list now, but a predicate can still disagree: a template
    injected under one condition and listed under another reproduces the same
    bug with none of the duplication.

    So this asserts the outcome instead. Generate a package, look at what is on
    disk, and require `get_generated_files` to name every single file. It
    catches the NEXT injected file, whoever adds it and whatever they forget.
    """
    package.setdefault("name", "Demo")
    config = {"templates": MAKEFILE_TEMPLATES, "packages": {"demo": package}}
    generated = generate_package(config)

    listed = {entry.removeprefix("demo/") for entry in get_generated_files(config)}
    # rglob, and relative paths. A template may name a nested destination --
    # `dest: .vscode/settings.json` is in the config's own documentation, and
    # render_templates creates the parent directories for it -- so a top-level
    # iterdir() would compare the DIRECTORY against a list holding the file
    # inside it, and pass while the file it was meant to catch went unlisted.
    on_disk = {
        path.relative_to(generated).as_posix()
        for path in generated.rglob("*")
        if path.is_file()
    }

    assert on_disk - listed == set(), (
        "stencil wrote files get_generated_files does not name, so `stencil "
        "clean` leaves them behind and the managed .gitignore section does not "
        "cover them"
    )


def test_a_nested_destination_is_named_by_its_path(generate_package):
    """The case the check above is walked recursively for.

    `dest:` may carry a directory -- `.vscode/settings.json` is the example in
    the config's own documentation, and render_templates creates the parent for
    it. A comparison over basenames would ask whether `settings.json` is
    listed while `get_generated_files` had named `.vscode/settings.json`, and a
    comparison that included directories would ask about `.vscode` itself.
    """
    config = {
        "templates": [{"src": "Makefile.j2", "dest": "build/Makefile"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)

    assert (generated / "build" / "Makefile").is_file()
    assert "demo/build/Makefile" in get_generated_files(config)

    listed = {entry.removeprefix("demo/") for entry in get_generated_files(config)}
    on_disk = {
        path.relative_to(generated).as_posix()
        for path in generated.rglob("*")
        if path.is_file()
    }
    assert on_disk - listed == set()
