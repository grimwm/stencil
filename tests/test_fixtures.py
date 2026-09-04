"""The fixtures build, and carry what the authoring guide promises.

Everything downstream asserts on a detail of these two files. If a fixture stops
exercising mermaid, or loses its math, the tests that depend on it keep passing
while silently checking nothing -- so the fixtures get their own coverage.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_document_builds(render_soup):
    soup = render_soup("doc", "document.md")
    assert soup.title is not None
    assert "Flow, Limits, and Specifications" in soup.title.get_text()


def test_deck_builds(render_soup):
    soup = render_soup("slide", "deck.md")
    assert soup.select_one("div.container.deck") is not None


def test_document_exercises_the_authoring_contract(render_soup):
    """Front matter, math, a mermaid figure, a local image, and a hidden div."""
    soup = render_soup("doc", "document.md")

    assert soup.select_one("math") is not None, "math should render as MathML"

    figure = soup.select_one("figure")
    assert figure is not None, "the mermaid block should be wrapped in a figure"
    assert figure.select_one("figcaption") is not None, "and given a caption"

    images = soup.select("img")
    assert images, "the local image should survive to the page"
    assert any(img["src"].startswith("data:") for img in images), (
        "embed-images.lua should have inlined it as base64"
    )


def test_deck_exercises_the_layout_fences(render_soup):
    """The four fences the guide documents, and more than one slide."""
    soup = render_soup("slide", "deck.md")

    for fence in ("lead-in", "columns", "takeaway"):
        assert soup.select_one(f".{fence}") is not None, f"missing ::: {fence}"

    slides = soup.select("section.slide")
    assert len(slides) > 1, "the deck should split into several slides"


def test_hidden_content_is_dropped_by_default(render_soup):
    """The default build is the handout, not the answer key."""
    soup = render_soup("doc", "document.md")
    text = soup.get_text()
    assert "WIP limit forces the queue to drain" not in text


def test_hidden_content_returns_with_the_feature_flag(render_soup):
    """WITH=hidden is metadata to pandoc; hidden-filter keys off include-hidden."""
    soup = render_soup("doc", "document.md", metadata={"include-hidden": "true"})
    text = soup.get_text()
    assert "WIP limit forces the queue to drain" in text
