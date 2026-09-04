"""The pandoc argv, and the compose file that has to agree with it.

These are the cheap half of the epic: no container, no pandoc, just the two
ordering constraints that used to be defended by a comment. If one of these
fails, a build is about to produce a plausible-looking document that is wrong.
"""

from __future__ import annotations

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
