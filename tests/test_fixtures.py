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

    # .slide, not section.slide: pandoc promotes a slide Div to <section> only
    # when its first block is a Header, so a headingless card stays a <div>.
    slides = soup.select(".slide")
    assert len(slides) > 1, "the deck should split into several slides"


def test_the_deck_puts_inline_code_on_the_title_slide(render_soup):
    """stn-7i8's pairing, rendered rather than reasoned about.

    The title slide is built entirely from front matter, and
    frontmatter-filter.lua hands every field to pandoc as Inlines -- so
    backticks in `title:` and `subtitle:` put a <code> on the accent gradient.
    Without this the fixture never produced the element and no rendered page
    in the suite exercised the rule that makes it legible.

    WHAT check-access DOES WITH IT, measured rather than assumed: pa11y does
    NOT flag the pairing, with the rule reverted, at 1.06:1. .slide--title is
    painted with `background: linear-gradient(...)`, and the shorthand resets
    background-color to transparent -- so the checker walks up for a colour to
    composite against, finds none it can use, and skips the element instead of
    failing it. _page-style.css.j2 already records the same blind spot for the
    deck toolbar. The unit measurement in tests/test_theme.py is therefore the
    WHOLE guard here; this test only keeps the element on the page so the
    stylesheet rule has something to apply to.
    """
    soup = render_soup("slide", "deck.md")

    title_code = soup.select(".deck-title code")
    subtitle_code = soup.select(".deck-subtitle code")
    assert title_code, (
        "the deck fixture's title lost its backticks; stn-7i8's rule now "
        "applies to nothing any rendered test produces"
    )
    assert subtitle_code, "the deck fixture's subtitle lost its backticks"


def test_the_document_puts_inline_code_in_a_table_header(render_soup):
    """stn-1y7, and the reason it went unseen for four releases.

    `make check-access` runs pa11y over whatever the fixture renders, so it
    measures a colour pairing only if some element actually produces it. No
    fixture had a table, let alone a backticked one, so `--code-inline` on the
    `thead` fill -- 4.07:1, the one pairing in the palette that failed AA --
    was never put in front of the checker. The token is guarded by measurement
    in tests/test_theme.py; this is the half that makes pa11y look.

    A `thead code` and a `caption code`, because they are two different fills:
    --surface-accent-on and --surface-accent.
    """
    soup = render_soup("doc", "document.md")

    assert soup.select_one("thead code") is not None, (
        "the fixture lost its backticked table header; check-access no longer "
        "renders the pairing stn-1y7 was about"
    )
    assert soup.select_one("caption code") is not None, (
        "the fixture lost its backticked caption"
    )


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
