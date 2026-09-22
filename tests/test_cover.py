"""`cover` / `cover-alt`: a figure on the generated deck title slide.

Documents ignore both keys. On a deck, `cover` is always a picture -- unlike
`brand`, a bare name has nothing useful to put on the cover -- and
`cover-alt` is required: it is both the image alt and the visible figcaption.
"""

from __future__ import annotations

import pytest

COVER = "cover.svg"
COVER_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80">'
    '<rect width="120" height="80" fill="#4a90d9"/></svg>\n'
)


def document(front_matter: str) -> str:
    return f"---\n{front_matter}---\n\n## Body\n\nSome prose.\n"


def deck(front_matter: str) -> str:
    return f"---\n{front_matter}---\n\n## A slide\n\nSome prose.\n"


@pytest.fixture
def with_cover(doc_package):
    (doc_package / COVER).write_text(COVER_SVG)
    return doc_package


def cover_of(soup):
    return soup.select_one(".slide--title .deck-cover")


@pytest.mark.integration
@pytest.mark.parametrize(
    "value", [f"file://{COVER}", COVER], ids=["file-uri", "relative-path"]
)
def test_a_cover_renders_as_an_inlined_figure(render_soup, with_cover, value):
    soup = render_soup(
        "slide",
        "c.md",
        text=deck(
            f'title: "T"\ncover: "{value}"\n'
            'cover-alt: "Abstract architectural curves against a blue sky"\n'
        ),
    )
    figure = cover_of(soup)
    assert figure is not None, "the title slide rendered no cover figure"
    img = figure.select_one("img")
    assert img is not None
    assert img["src"].startswith("data:"), (
        f"the cover was not inlined: {img['src'][:60]!r}"
    )
    assert img["alt"] == "Abstract architectural curves against a blue sky"
    caption = figure.select_one("figcaption")
    assert caption is not None
    assert " ".join(caption.get_text().split()) == (
        "Abstract architectural curves against a blue sky"
    )


@pytest.mark.integration
def test_a_cover_without_alt_fails_the_build(render, with_cover):
    result, _ = render(
        "slide", "c.md", text=deck(f'title: "T"\ncover: "{COVER}"\n')
    )
    assert result.returncode != 0, "a cover with no alt text built anyway"
    assert "cover-alt" in result.stderr, (
        f"the error does not name the missing key: {result.stderr[-400:]}"
    )


@pytest.mark.integration
def test_a_blank_cover_alt_counts_as_missing(render, with_cover):
    result, _ = render(
        "slide",
        "c.md",
        text=deck(f'title: "T"\ncover: "{COVER}"\ncover-alt:\n'),
    )
    assert result.returncode != 0, "an empty cover-alt built anyway"


@pytest.mark.integration
def test_a_non_image_cover_fails_the_build(render):
    result, _ = render(
        "slide",
        "c.md",
        text=deck(
            'title: "T"\ncover: "not a picture"\ncover-alt: "unused"\n'
        ),
    )
    assert result.returncode != 0, "a non-image cover built anyway"
    assert "cover" in result.stderr


@pytest.mark.integration
def test_a_document_ignores_cover(render_soup, with_cover):
    soup = render_soup(
        "doc",
        "c.md",
        text=document(
            f'title: "T"\ncover: "{COVER}"\ncover-alt: "caption text"\n'
        ),
    )
    assert soup.select_one(".deck-cover") is None
    assert soup.select_one("figure.deck-cover") is None
