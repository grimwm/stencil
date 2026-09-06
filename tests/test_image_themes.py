"""A figure with a -dark sibling follows the theme; the PDF keeps the light one.

stn-qks. Since 0.12.0 mermaid diagrams redraw to follow the theme while static
figures did not, so a deck with both went half-themed in dark mode: recoloured
diagrams beside white-plate SVGs.

embed-images.lua now emits both variants when `images/foo-dark.svg` sits beside
`images/foo.svg`, and CSS shows one. The author's markup does not change.

The print guarantee is the part worth testing properly, and it is the part a
stylesheet-text assertion cannot reach: the dark rule lives inside the sealed
@media screen block, print cannot match it, so a PDF gets the light figure.

The fixtures are therefore pure red and pure green -- colours nothing in this
project's palette uses -- because Chromium draws an SVG from an <img> as vector
operators straight into the page content stream. There is no image XObject to
inspect (measured: the PDF has none at all), so the fill operator is what says
which variant was drawn.
"""

from __future__ import annotations

import re

import pytest
from pypdf import PdfReader

from test_theme import screen_blocks, source, strip_comments

pytestmark = pytest.mark.integration

THEMED = """---
title: "Themed figure"
lang: en
---

## Figure

![A bar chart](images/themed.svg)
"""

PLAIN = """---
title: "Plain figure"
lang: en
---

## Figure

![A flow diagram](images/flow.svg)
"""


def images_in(soup):
    return soup.select("figure img")


def test_a_dark_sibling_produces_both_variants(render_soup):
    soup = render_soup("doc", "themed.md", text=THEMED)
    imgs = images_in(soup)
    assert len(imgs) == 2, f"expected a light/dark pair, got {len(imgs)}"

    light, dark = imgs
    assert "doc-img--light" in light["class"]
    assert "doc-img--dark" in dark["class"]
    assert light["src"] != dark["src"], "both variants embedded the same bytes"
    assert light["src"].startswith("data:image/svg+xml")
    assert dark["src"].startswith("data:image/svg+xml")


def test_both_variants_carry_the_same_alt_text(render_soup):
    """One image, one description. The hidden variant is display:none, which
    takes it out of the accessibility tree, so a reader never meets the pair."""
    light, dark = images_in(render_soup("doc", "themed.md", text=THEMED))
    assert light.get("alt") == dark.get("alt") == "A bar chart"


def test_a_figure_with_no_dark_sibling_is_unchanged(render_soup):
    """The whole convention has to be invisible to a document that does not use
    it. A stray second <img>, or a class where there was none, is a regression
    for every existing handout."""
    imgs = images_in(render_soup("doc", "flow.md", text=PLAIN))
    assert len(imgs) == 1
    assert not imgs[0].get("class"), (
        f"an unthemed figure picked up a variant class: {imgs[0].attrs!r}"
    )


def test_looking_for_an_absent_sibling_does_not_warn(render):
    """--fail-if-warnings is on, and this filter now probes for a file that
    usually is not there. A probe that goes through pandoc's warning system
    rather than failing quietly would break every build that has ever used a
    plain figure."""
    result, _ = render("doc", "flow.md", text=PLAIN)
    assert result.returncode == 0, result.stderr
    assert "themed-dark" not in result.stderr
    assert "could not read" not in result.stderr, (
        f"the absent-sibling probe reported itself: {result.stderr!r}"
    )


def test_a_deck_gets_the_variants_too(render_soup):
    """slide-template includes the same stylesheet and the same filter runs for
    both kinds, but the deck is where this was noticed, so it is asserted."""
    imgs = images_in(render_soup("slide", "themed.md", text=THEMED))
    assert len(imgs) == 2


def test_the_rule_that_hides_the_dark_variant_stays_outside_media_screen():
    """The half of the containment rule that is new here.

    test_theme.py already asserts the other half generically: nothing
    theme-conditional may sit OUTSIDE @media screen, and the rule showing the
    dark variant is theme-conditional, so it is covered there.

    This is the inverse, and nothing covered it. The rule that HIDES the dark
    variant must sit outside the wrapper, because print has to match it -- it
    is the only thing keeping a second figure off a printed page. Move it in
    with the others, which looks tidier, and every PDF prints both variants
    stacked.

    Brace-matched via test_theme's own extractor rather than by comparing
    string offsets: a first attempt at this anchored on the text "@media
    screen" and matched a CSS comment discussing the block, hundreds of lines
    above the block itself.
    """
    css = strip_comments(source("_page-style.css.j2"))
    inside = "\n".join(screen_blocks(css))
    outside = css
    for block in screen_blocks(css):
        outside = outside.replace(block, "")

    def declarations(css_text, selector):
        m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css_text)
        return m.group(1) if m else None

    # Placement AND declaration. Checking only where a selector sits would
    # pass for a rule that had been placed correctly and then changed to say
    # display:block, which is the same broken page with a tidier stylesheet.
    hidden = declarations(outside, ".doc-img--dark")
    assert hidden and "display: none" in hidden, (
        "the rule hiding the dark variant is not outside @media screen, or no "
        f"longer hides it, so both figures reach a printed page: {hidden!r}"
    )
    shown = declarations(inside, ':root[data-theme="dark"] .doc-img--dark')
    assert shown and "display: inline" in shown, (
        f"the dark variant is not shown under a dark theme: {shown!r}"
    )
    swapped = declarations(inside, ':root[data-theme="dark"] .doc-img--light')
    assert swapped and "display: none" in swapped, (
        f"the light variant is not hidden under a dark theme: {swapped!r}"
    )


def test_the_pdf_draws_the_light_variant(to_pdf):
    """The acceptance criterion, asked of the artifact rather than inferred.

    Chromium draws an SVG from an <img> as vector operators in the page content
    stream -- measured, the PDF holds no image XObject at all -- so this reads
    the fill colour it emitted. The fixtures are pure red and pure green, which
    nothing in this project's palette uses, so neither operator can arrive from
    the page around the figure.
    """
    result, pdf = to_pdf("doc", "themedpdf.md", text=THEMED, stem="themedpdf")
    assert result.returncode == 0, result.stderr[-3000:]

    stream = b"".join(
        page.get_contents().get_data() for page in PdfReader(pdf).pages
    ).decode("latin-1")

    assert re.search(r"\b1 0 0 (?:rg|scn)\b", stream), (
        "the light figure's fill is not in the printed page at all"
    )
    assert not re.search(r"\b0 1 0 (?:rg|scn)\b", stream), (
        "the dark variant was printed; the rule showing it is reachable from "
        "print media, which breaks the guarantee that PDFs are always light"
    )
