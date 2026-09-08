"""Painted gaps outside the header, measured in the PDF text layer.

stn-avj, following stn-40n. 0.13.0 fixed the document and deck headers: a
whitespace-only text node between two INLINE boxes never reaches the text
layer, so "Author Ada Lovelace" printed as "AuthorAda Lovelace" from a page
whose DOM had the space. The fix moved every one of those gaps inside a text
run that also carries a printing character.

Four more places paint a gap with no character behind it, and none of them had
ever been looked at in a PDF:

- ``.side-by-side { gap: 2rem }`` in _page-style.css.j2, between two tables;
- ``.columns { gap: 1.75rem }`` in _slide-style.css.j2, between two columns of
  prose on a slide;
- ``header.doc-title``, a grid with ``column-gap: 1.5rem``, between the
  identity and context columns;
- ``.doc-facts``, whose accessibility boundary comes from the flex container
  blockifying its children rather than from any character.

MEASURED, and this is the finding. None of the four has a character behind the
gap -- pandoc does emit a whitespace-only text node between the boxes and flex
and grid both discard it, which is precisely the stn-40n shape. At the tightest
arrangement each site can actually paint, Chromium emits the two sides as two
separate text objects with their own ``Tm`` origins and no space glyph between
them, and an extractor recovers the boundary from the advance alone:

- ``.columns``: the tightest same-baseline gap reachable, tuning the fill one
  character at a time, was 31.0px against the declared 28px -- one more
  character wraps the line, which moves the boxes off one baseline entirely.
  Extracted as "...LEFTEND RIGHTSTART...".
- ``.side-by-side``: a flat 58px whenever the tables fit on one row (2rem plus
  a table cell's padding on each side), and content wide enough to close that
  wraps to a stack instead. Extracted as "LEFTEND RIGHTSTART".
- ``header.doc-title``: identity and context never land adjacent in the content
  stream, in any arrangement, because source order is identity, byline,
  context while grid placement puts context back on row one. Even with a
  wrapping title and no byline -- the closest the two columns get -- the
  extractor breaks the line between them.

So what holds these four apart is geometry and blockification, not a character,
and that is a weaker guarantee than the one the header now has. It is still a
sound one: these are BLOCK boxes, and a block boundary is the only thing any
extractor has to work with between two paragraphs, two table cells or two list
items anywhere in any PDF. But it was unasserted, and the whole lesson of
stn-40n is that a page can be perfect in the DOM and wrong in the PDF with
nothing in the build to say so.

These tests pin that measurement. They deliberately do not assert a gap width:
font metrics drift, and a test that fails when a gap moves from 31px to 29px
fails for the wrong reason. Instead they sweep the fill from empty to wrapping,
demand the two sides never run together at any of them, and then demand that at
least one variant actually landed on one extracted line -- because a sweep that
never reaches the jam arrangement proves nothing about it.

WHAT THE SWEEPS DO AND DO NOT CATCH, measured by mutating the stylesheet and
rerunning rather than by reasoning about it. Narrowing a gap does not produce
a jam: with ``.columns { gap: 0 }`` every variant still extracted with a space,
because the two columns remain separate text objects and an extractor
synthesizes a space from any horizontal jump between them. So these assert the
OUTCOME, in the same way test_pdf.py's mermaid test does, and they are not on
their own a proof that the gap is safe at any particular width.

What they do catch is the arrangement ceasing to exist. Turning ``.columns``
into inline flow made every pair line-break, and the non-vacuity check failed
with "no variant put the two sides on one extracted line" -- which is the
honest failure for that change.

The MECHANISM is asserted directly, and only where it is load-bearing:
``.doc-facts`` alone has inline children, so blockification is the only thing
separating them and ``test_the_facts_line_children_are_block_boxes`` pins it.
The children of ``.side-by-side``, ``.columns`` and ``header.doc-title`` are
tables and divs, block-level whatever their container does, so the same
assertion there would be a tautology rather than a guard.
"""

from __future__ import annotations

import json
import re

import pytest
from pypdf import PdfReader

from stencil import pipeline

pytestmark = pytest.mark.integration

# Fourteen steps takes the columns from a gap of roughly 160px down to 31px and
# then past the wrap, so the sweep straddles the tight arrangement rather than
# hoping to land on it.
FILLS = "ABCDEFGHIJKLMN"
BASE = "alpha bravo charlie delta echo foxtrot golf"

# `.side-by-side` sizes its tables to content, so its sweep is one more word per
# variant rather than one more character -- and BASE has seven of them. Running
# all fourteen would just render the same seven widths twice.
SBS_FILLS = FILLS[: len(BASE.split())]


def text_of(path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


def between(text: str, left: str, right: str) -> str | None:
    """Whatever the extractor put between the two markers.

    ``None`` when they are not adjacent at all, which is a different failure
    from a jam and is worth reporting as one: it means the page did not render
    the arrangement under test, so the assertion that follows would be empty.
    """
    match = re.search(re.escape(left) + r"(.*?)" + re.escape(right), text, re.S)
    return None if match is None else match.group(1)


def assert_kept_apart(text: str, markers: list[tuple[str, str]]) -> None:
    """Every pair separated by real whitespace, and at least one on one line.

    The second half is the non-vacuity check, and it is the half that makes
    this mean anything. A jam can only happen where the two sides sit on ONE
    baseline: as soon as they are on different lines, every extractor's layout
    analysis breaks between them, and asserting that a line-broken pair did not
    run together asserts nothing at all. So at least one variant has to have
    landed on a single extracted line -- whitespace, no newline -- or the sweep
    never exercised the shape and the test is furniture.
    """
    on_one_line = []
    for left, right in markers:
        gap = between(text, left, right)
        assert gap is not None, (
            f"{left!r} and {right!r} are not adjacent in the extracted text, so "
            f"this variant never rendered the arrangement under test:\n{text}"
        )
        assert gap != "", (
            f"{left}{right} is jammed in the PDF text layer -- the painted gap "
            "left nothing behind and the extractor did not recover it"
        )
        assert gap.strip() == "", (
            f"expected only whitespace between {left!r} and {right!r}, got {gap!r}"
        )
        if "\n" not in gap:
            on_one_line.append(left)

    assert on_one_line, (
        "no variant put the two sides on one extracted line, so nothing here "
        "exercised the jam arrangement -- every pair was line-broken, which any "
        "extractor does for free. The sweep needs to reach the tight fill."
    )


# stn-avj.1 -- .side-by-side ---------------------------------------------------


def side_by_side_source() -> str:
    """One document, one `.side-by-side` per fill width.

    Single-cell tables: `.side-by-side` sizes its children to content and
    centres the row, so the cell text is what decides how much gap is left.

    The left cell ENDS with its marker and the right cell OPENS with one, so
    the two markers are the glyphs on either side of the gap. Put them the
    other way round and the assertion reads across the fill text instead of
    across the gap, which would pass for a jammed page.
    """
    blocks = []
    for i, tag in enumerate(SBS_FILLS):
        words = " ".join(BASE.split()[: i + 1])
        blocks.append(
            "::: {.side-by-side}\n\n"
            f"| {words} LEFTEND{tag} |\n| --- |\n\n"
            f"| RIGHTSTART{tag} {words} |\n| --- |\n\n"
            ":::\n"
        )
    body = "\n".join(blocks)
    return f'---\ntitle: "Side by side"\nlang: en\n---\n\n## Tables\n\n{body}'


def test_side_by_side_tables_do_not_jam_in_the_text_layer(to_pdf):
    """Two tables on one baseline behind `gap: 2rem`, with no character in it.

    A table cell's own padding sits inside the gap on both sides, so the
    narrowest glyph-to-glyph distance this can paint is 58px rather than the
    32px the stylesheet declares -- and a table wide enough to close that wraps
    to a stack instead, which is `flex-wrap: wrap` doing its job.
    """
    result, pdf = to_pdf("doc", "sidebyside.md", text=side_by_side_source())
    assert result.returncode == 0, result.stderr

    assert_kept_apart(
        text_of(pdf), [(f"LEFTEND{tag}", f"RIGHTSTART{tag}") for tag in SBS_FILLS]
    )


# stn-avj.2 -- .columns --------------------------------------------------------


def columns_source() -> str:
    """One deck, one slide per fill.

    The fill grows a character at a time rather than a word at a time. Word
    granularity bottoms out around 73px, because a line rarely breaks flush
    with its track; single characters walk the line end right up to the edge,
    which is where the declared 1.75rem is all that is left.
    """
    slides = []
    for i, tag in enumerate(FILLS):
        slides.append(
            f"## Fill {tag}\n\n"
            "::::: columns\n\n"
            f":::: column\n\n{BASE} {'m' * i}LEFTEND{tag}\n\n::::\n\n"
            f":::: column\n\nRIGHTSTART{tag} {BASE}\n\n::::\n\n"
            ":::::\n"
        )
    body = "\n".join(slides)
    return f'---\ntitle: "Columns"\nlang: en\n---\n\n{body}'


def test_deck_columns_do_not_jam_in_the_text_layer(to_pdf):
    """Two prose columns on one baseline behind `gap: 1.75rem`.

    The tightest arrangement measured was 31.0px against a declared 28px, one
    character short of wrapping. Nothing separates the two columns in the
    content stream: they are two text objects sharing a `Tm` y with different x
    origins, and the space an extractor hands back is synthesized from the
    jump rather than read from a glyph.

    Measured by mutation: `gap: 0` does NOT make this fail, because two text
    objects stay two text objects however close they are. Inline flow DOES,
    through the non-vacuity check. See the module docstring.
    """
    result, pdf = to_pdf("slide", "gapcolumns.md", text=columns_source())
    assert result.returncode == 0, result.stderr

    assert_kept_apart(
        text_of(pdf), [(f"LEFTEND{tag}", f"RIGHTSTART{tag}") for tag in FILLS]
    )


# stn-avj.3 -- header.doc-title ------------------------------------------------


# A wrapping title, a brand to give the context a second line, and no byline:
# the arrangement that brings the two grid columns as close to adjacent in the
# content stream as the header can. With a byline, the facts row sits between
# them in that stream, which is why the ordinary header never had a chance to
# jam and why the guard in test_pdf.py has always been the weak one.
HEADER_TIGHT = """---
title: "A Very Long Assignment Title That Wraps Across Several Lines In The Identity Column"
subtitle: "SUBTITLEEND"
brand: "CONTEXTSTART Institute"
program: "CS 425"
section: "001"
term: "Fall 2026"
---

## Body

Some prose.
"""


def test_the_header_columns_stay_apart_with_a_wrapped_title_and_no_byline(to_pdf):
    """The weakest of the four guards, now measured rather than assumed.

    test_pdf.py has asserted `"SimulationCS 425.001" not in text` since 0.13.0,
    with a comment saying the identity and context columns stay apart because
    they are far apart in the CONTENT STREAM rather than because a character
    separates them -- and that nothing had checked whether an arrangement
    exists in which they are not.

    This is that arrangement. Source order is identity, byline, context, and
    grid placement lifts context back onto row one; strip the byline and the
    context follows the subtitle immediately in the stream, while a title long
    enough to wrap pushes the subtitle down level with the context's second
    line. Measured, the extractor still breaks between them: the two columns
    are separate text objects and the y jump is backwards, which no layout
    analysis reads as one line.
    """
    result, pdf = to_pdf("doc", "headertight.md", text=HEADER_TIGHT)
    assert result.returncode == 0, result.stderr

    text = text_of(pdf)
    gap = between(text, "SUBTITLEEND", "CONTEXTSTART")
    assert gap is not None, (
        "the subtitle and the context did not both reach the PDF, so this "
        f"asserts nothing:\n{text}"
    )
    assert gap != "", (
        "the identity and context grid columns ran together in the text layer"
    )
    assert gap.strip() == "", (
        f"expected only whitespace between the two header columns, got {gap!r}"
    )


# stn-avj.4 -- .doc-facts ------------------------------------------------------


FACTS = """---
title: "Kanban Board Simulation"
date: 2026-09-05
due: 2026-09-12T23:59
points: 50
lang: en
---

## Body

Some prose.
"""

# `interestingOnly: false` keeps the generic wrappers, so the StaticText runs
# come back in document order with the aria-hidden separators already gone --
# which is what a screen reader is handed.
FACTS_PROBE = r"""
const puppeteer = require("puppeteer");
(async () => {
  const browser = await puppeteer.launch({args: ["--no-sandbox"]});
  const page = await browser.newPage();
  await page.setViewport({width: 1280, height: 720});
  await page.goto("file:///workspace/facts.html", {waitUntil: "networkidle0"});
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  const snapshot = await page.accessibility.snapshot({interestingOnly: false});
  const names = [];
  (function walk(n) {
    if (n.role === "StaticText") names.push(n.name);
    (n.children || []).forEach(walk);
  })(snapshot);
  const boxes = await page.evaluate(() => {
    const facts = document.querySelector(".doc-facts");
    if (!facts) return null;
    return Array.from(facts.children).map((k) => ({
      cls: k.className,
      display: getComputedStyle(k).display,
    }));
  });
  console.log("---JSON---");
  console.log(JSON.stringify({names, boxes}));
  await browser.close();
})();
"""

# Every display value that generates a block-level box. `inline` and
# `inline-block` are the two a careless edit would produce, and both would put
# the facts back into one inline run.
BLOCK_LEVEL = {"block", "flow-root", "flex", "grid", "list-item", "table"}


@pytest.fixture(scope="module")
def facts_probe(pdf_workspace):
    """Chromium's own accessibility tree and used display values for the facts."""
    (pdf_workspace / "facts.md").write_text(FACTS)
    built = pipeline.render("doc", "facts.md", "facts.html", workdir=pdf_workspace)
    assert built.returncode == 0, built.stderr

    result = pipeline.run_in_browser(FACTS_PROBE, workdir=pdf_workspace, timeout=300)
    assert result.returncode == 0, result.stderr[-2000:]
    payload = json.loads(result.stdout.split("---JSON---", 1)[1].strip())
    assert payload["boxes"] is not None, "the page rendered no .doc-facts at all"
    return payload


def test_the_facts_line_children_are_block_boxes(facts_probe):
    """`.doc-facts` being a flex container is load-bearing for accessibility.

    _page-style.css.j2 says so in a comment, and nothing enforced it. The fact
    separators are `aria-hidden`, which removes their whole subtree -- and with
    it their non-breaking spaces around the middot -- from the accessibility
    tree, so the accessible runs measured on this markup are:

        'Issued\\xa0', 'Sep 05', 'Due\\xa0', 'Sep 12 \\xb7 23:59', 'Points\\xa0',
        '50\\xa0pts'

    'Sep 05' and 'Due\\xa0' are adjacent with no whitespace anywhere between
    them. The ONLY thing keeping them apart is that flex blockifies its
    children, so each fact is its own block box rather than one more run in a
    single inline flow. Change `.doc-facts` to inline flow and the line runs
    together for a screen reader, silently: `make check-access` cannot see it,
    because neither pa11y engine has a rule for adjacent text with no
    separating whitespace, and the PDF stays correct throughout because the
    separators DO reach the text layer.

    So this asserts the USED display of the children, not the declared display
    of the container. `display: block` on `.doc-facts` would satisfy a
    stylesheet grep and leave its `<span>` children inline, which is exactly
    the regression worth catching.
    """
    boxes = facts_probe["boxes"]
    assert boxes, "the facts line rendered no children"

    inline = [b for b in boxes if b["display"] not in BLOCK_LEVEL]
    assert not inline, (
        "these .doc-facts children are not block boxes, so the accessibility "
        "tree has nothing separating one fact from the next: "
        + ", ".join(f"{b['cls']} is {b['display']}" for b in inline)
    )


def test_the_facts_line_reaches_the_pdf_as_words(to_pdf):
    """The other half of the facts line, and the half that IS a character.

    The separators' non-breaking spaces sit inside a text run that also carries
    the middot, which is why they survived into the text layer when the
    header's bare gaps did not. Asserted here against a document carrying only
    the facts, so a failure names the facts rather than the whole header.
    """
    result, pdf = to_pdf("doc", "facts.md", text=FACTS)
    assert result.returncode == 0, result.stderr

    # Runs of ordinary and non-breaking space collapse; newlines deliberately
    # do not. pypdf synthesizes a space from a wide horizontal jump and inserts
    # line breaks from its layout heuristics, so collapsing newlines here would
    # manufacture the very separation this is meant to prove is present.
    text = re.sub(r"[ \t\u00a0]+", " ", text_of(pdf))

    assert "Issued Sep 05 · Due Sep 12 · 23:59 · Points 50 pts" in text, text[:400]
