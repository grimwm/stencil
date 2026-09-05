"""The pandoc argv, and the compose file that has to agree with it.

These are the cheap half of the epic: no container, no pandoc, just the two
ordering constraints that used to be defended by a comment. If one of these
fails, a build is about to produce a plausible-looking document that is wrong.
"""

from __future__ import annotations

import re

import pytest
import yaml

from stencil import pipeline


def index_of(argv: list[str], needle: str) -> int:
    assert needle in argv, f"{needle} missing from argv: {argv}"
    return argv.index(needle)


@pytest.mark.parametrize("kind", pipeline.KINDS)
def test_citeproc_runs_after_hidden_filter(kind):
    """Reversed, an answer key's sources leak into the handout's reference list.

    Citeproc builds the reference list from the citations still in the document,
    so hidden-filter has to have removed the presenter-only content first.
    """
    argv = pipeline.pandoc_argv(kind)
    assert index_of(argv, "--lua-filter=hidden-filter.lua") < index_of(
        argv, "--citeproc"
    )


def test_citeproc_runs_before_slide_sections():
    """Reversed, the reference list renders outside every slide card.

    Citeproc appends the list to the document's blocks, so it has to be there
    before slide-sections groups those blocks into slides -- otherwise it lands
    at container.deck > div#refs, where present mode never shows it.
    """
    argv = pipeline.pandoc_argv("slide")
    assert index_of(argv, "--citeproc") < index_of(
        argv, "--lua-filter=slide-sections.lua"
    )


@pytest.mark.parametrize("kind", pipeline.KINDS)
def test_warnings_are_fatal(kind):
    """A mistyped citation key must fail the build rather than ship '(**key?**)'."""
    assert "--fail-if-warnings" in pipeline.pandoc_argv(kind)


def test_slide_sections_is_deck_only():
    """Running the slide grouper over a flowing document would wrap it in cards."""
    assert "--lua-filter=slide-sections.lua" not in pipeline.pandoc_argv("doc")
    assert "--lua-filter=slide-sections.lua" in pipeline.pandoc_argv("slide")


@pytest.mark.parametrize(
    ("kind", "template"),
    [("doc", "html-template.html"), ("slide", "slide-template.html")],
)
def test_each_kind_uses_its_own_template(kind, template):
    assert f"--template={template}" in pipeline.pandoc_argv(kind)


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError, match="unknown render kind"):
        pipeline.pandoc_argv("pdf")


def test_the_pdf_converter_defers_to_the_print_stylesheet(doc_package):
    """preferCSSPageSize is the declaration that @page owns the page geometry.

    Asserted as template text rather than by printing something, because on
    current Chromium removing it does not change the output: the flag decides
    whether CSS overrides an explicit width/height, and page.pdf() is called
    with neither, so @page wins either way. That makes it a contract nothing
    would notice the loss of -- until a puppeteer or Chromium release restores
    the default and every handout silently repaginates.

    The behaviour it stands for is covered by the geometry tests in
    test_pdf.py, which do fail when an @page block is edited.
    """
    script = (doc_package / "html-to-pdf.js").read_text()

    assert "preferCSSPageSize: true" in script
    assert "printBackground: true" in script, (
        "without printBackground the PDF loses every background colour the "
        "print stylesheet sets"
    )


READY_FLAG = "window.__mermaidReady"
READY_EVENT = "mermaid-ready"


def test_the_ready_flag_is_set_by_the_page_and_waited_on_by_the_converter(
    doc_package,
):
    """A contract between two templates that no build step checks.

    _page-scripts.html.j2 sets the flag once mermaid has rendered, and only
    then assembles the tab panes. html-to-pdf.js waits on it before printing.
    Rename it on one side and nothing fails to build -- every PDF build just
    hangs for the full 120s timeout and then reports a timeout, which says
    nothing about the cause. That is why this is a test and not a comment.
    """
    page_scripts = (doc_package / "html-template.html").read_text()
    converter = (doc_package / "html-to-pdf.js").read_text()

    assert f"{READY_FLAG} = true" in page_scripts, (
        "the page never sets the ready flag, so every PDF build will hang "
        "until the converter times out"
    )
    assert READY_FLAG in converter, (
        "the converter no longer waits for the ready flag, so a PDF can be "
        "printed while mermaid is still a code block"
    )
    assert f"'{READY_EVENT}'" in page_scripts, (
        "the tab panes are assembled on the mermaid-ready event; without it "
        "a printed page shows only the first tab"
    )


def test_the_pandoc_image_is_pinned():
    """A floating tag makes a build depend on the day it ran.

    Worse than irreproducible output: --fail-if-warnings means a pandoc release
    that adds a warning breaks CI on a commit that changed nothing.
    """
    _, _, tag = pipeline.PANDOC_IMAGE.rpartition(":")

    assert tag, f"{pipeline.PANDOC_IMAGE} names no tag, so it resolves to latest"
    assert tag != "latest"
    assert re.fullmatch(r"\d+(\.\d+)+", tag), (
        f"{tag!r} is not a release version; a moving tag is not a pin"
    )


def test_the_compose_services_build_with_the_pinned_image(doc_package):
    compose = yaml.safe_load((doc_package / "docker-compose.yml").read_text())

    for kind in pipeline.KINDS:
        assert compose["services"][kind]["image"] == pipeline.PANDOC_IMAGE


def test_the_makefile_checks_for_the_image_compose_actually_runs(doc_package):
    """The doc and slide targets skip `pull` when the image is already local.

    Name a different tag there than the compose service uses and the check can
    never succeed, so every build pulls again -- or, worse, passes because some
    other tag is present while compose goes and fetches this one.
    """
    makefile = (doc_package / "Makefile").read_text()

    # The probe itself lives in one shared ensure_image, so the tag is passed to
    # it rather than sitting on the same line. Assert both halves: that the
    # helper still asks docker whether the image is there, and that every call
    # site naming pandoc names the tag compose runs.
    assert "docker images -q $(1)" in makefile, (
        "ensure_image no longer probes for the image, so every build pulls"
    )

    guards = [
        line
        for line in makefile.splitlines()
        if "ensure_image" in line and "pandoc" in line
    ]
    assert guards, "the pandoc pull guard is gone"

    for line in guards:
        assert pipeline.PANDOC_IMAGE in line, (
            f"the pull guard names an image compose does not run: {line.strip()}"
        )


def test_compose_entrypoint_matches_the_shared_argv(doc_package):
    """The whole point of the extraction: the compose file cannot drift.

    If someone edits the entrypoint in docker-compose-html.yml.j2 by hand
    instead of editing pipeline.py, the ordering tests above would still pass
    while the real build did something else. This is what closes that gap.
    """
    compose = yaml.safe_load((doc_package / "docker-compose.yml").read_text())

    for kind in pipeline.KINDS:
        entrypoint = compose["services"][kind]["entrypoint"]
        assert entrypoint[0] == "pandoc"
        assert entrypoint[1:] == pipeline.pandoc_argv(kind)
