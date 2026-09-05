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

from stencil.generate import get_template_context

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
