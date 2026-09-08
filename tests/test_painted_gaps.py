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
and grid both discard it, which is precisely the stn-40n shape. Chromium emits
the two sides as two separate text objects with their own ``Tm`` origins and no
space glyph between them. What recovers the boundary is the advance, and every
extractor does that with a threshold.

THE THRESHOLD IS REAL AND IT IS RELATIVE. Measured on hand-built PDFs -- two
Helvetica runs on one baseline, no space glyph, only the second run's x varying,
so the gap is the sole variable -- both extractors jam at zero and both break
the word at a fraction of an em that does not depend on point size:

    gap        pypdf 6.x        poppler pdftotext 26.x
    0.10 em    LEFTENDRIGHT     split
    0.12 em    LEFTENDRIGHT     split
    0.15 em    split            split

So ~0.15 em for pypdf and ~0.12 em for poppler, holding at 9, 11, 14 and 24pt.
"Two text objects stay two text objects however close they are" would be wrong,
and an earlier draft of this file said it.

MARGINS, measured in the real print-media PDFs rather than in the browser.
This distinction matters: ``@media print`` rescales the root font to 9.78pt
(_page-style.css.j2), so a gap quoted in screen px describes a different
document than the one being extracted. Read out of the content stream, at the
tightest arrangement each site can reach with the columns on one baseline:

- ``header.doc-title``: 1.06 em  (~7x the pypdf threshold)
- ``.columns``:         2.8 em   (~18x)
- ``.side-by-side``:    3.5 em   (~23x)

All three are block boxes, and that is what keeps them safe rather than a
character: a block boundary is the only thing any extractor has between two
paragraphs, two table cells or two list items anywhere in any PDF. But it was
unasserted, and the whole lesson of stn-40n is that a page can be perfect in
the DOM and wrong in the PDF with nothing in the build to say so.

WHAT THESE TESTS CATCH, established by mutating the stylesheet and rerunning
rather than by reasoning about it:

- ``.columns { gap: 0 }`` does NOT fail them, and that is not evidence of
  insensitivity -- it is evidence the mutation failed. Setting the CSS gap to
  zero leaves the left column's line-breaking slack in place, so the
  glyph-to-glyph distance never goes near zero. A narrowing control has to move
  the last glyph to the track edge, which the fill sweeps do and a gap edit
  does not.
- Inline flow DOES fail them, through the non-vacuity check: every pair
  line-breaks and no variant lands on one extracted line.

The MECHANISM is asserted directly, and only where it is load-bearing:
``.doc-facts`` alone has inline children, so blockification is the only thing
separating them. The children of ``.side-by-side``, ``.columns`` and
``header.doc-title`` are tables and divs, block-level whatever their container
does, so the same assertion there would be a tautology rather than a guard.

TWO EXTRACTORS, DIFFERENT MODELS, and the comments elsewhere should not overstate
one. pypdf preserves content-stream order and breaks on any y change; poppler
does geometric column detection. On the deck columns poppler emits a paragraph
break where pypdf emits a space -- both correct, neither a jam. The header is
the one place the two genuinely disagree about layout, so the guarantee there is
the 1.06 em gap and not, as an earlier draft of the stylesheet comment implied,
the content-stream distance alone.

NOT SWEPT, because the block-box argument reaches them and none narrows a gap
below what is swept here: ``.columns.wide-left`` / ``.wide-right`` (two explicit
tracks, same gap), ``data-cols=2|3|4`` (more gaps, each the same width),
``.columns.cards`` (card padding widens the glyph-to-glyph distance, so strictly
safer), and RTL/CJK content. Listed so nobody re-derives that they were
considered.
"""

from __future__ import annotations

import json
import re

import pytest
from pypdf import PdfReader

from stencil import pipeline

pytestmark = pytest.mark.integration

# Fourteen steps walks the deck columns from a gap of ~12.5 em down past the
# wrap: measured in print, fills 0-12 land on one extracted line and 13 wraps,
# so the sweep straddles the tight arrangement rather than hoping to hit it.
FILLS = "ABCDEFGHIJKLMN"
BASE = "alpha bravo charlie delta echo foxtrot golf"

# `.side-by-side` sizes its tables to content, so its sweep is one more word per
# variant rather than one more character -- and BASE has seven of them. Running
# all fourteen would just render the same seven widths twice.
SBS_FILLS = FILLS[: len(BASE.split())]

# The header's arrangement is decided by front matter, so each variant is its
# own document and its own PDF -- eight rather than fourteen to keep the tier
# affordable. Measured in print: fills 0-6 put the title and the context on one
# extracted line, and 7 wraps the title.
HEADER_FILLS = FILLS[:8]


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
        "extractor does for free.\n"
        "Two things cause this, and they need different fixes: the layout "
        "stopped producing a same-baseline row (check the container is still a "
        "grid or flex row), or the fill sweep no longer reaches the tight "
        "arrangement because font metrics moved. pypdf's space and line-break "
        "insertion is a layout heuristic that has changed materially across "
        "majors -- pyproject.toml pins it below 7 for this reason -- so a pypdf "
        "upgrade is the third candidate."
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

    Measured at 3.5 em in print, ~23x the ~0.15 em at which pypdf stops
    recovering a word break. That figure is for TABLE children, where a cell's
    0.75rem padding and 1px border sit inside the gap on each side; a
    `.side-by-side` holding bare divs gets the 2rem alone, which is narrower
    but still an order of magnitude clear. Content wide enough to close the gap
    wraps to a stack instead, which is `flex-wrap: wrap` doing its job.
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
    granularity bottoms out several em short, because a line rarely breaks flush
    with its track; single characters walk the line end up to the edge, which is
    where the declared 1.75rem is all that is left.
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

    Measured at 2.8 em in print at the tightest fill that still shares a
    baseline, ~18x the pypdf threshold. Nothing separates the two columns in
    the content stream: they are two text objects sharing a `Tm` y with
    different x origins, and the space an extractor hands back is synthesized
    from the jump rather than read from a glyph.

    poppler's `pdftotext` emits a paragraph break here instead of a space,
    because it does geometric column detection where pypdf follows the content
    stream. Both are correct and neither is a jam.
    """
    result, pdf = to_pdf("slide", "gapcolumns.md", text=columns_source())
    assert result.returncode == 0, result.stderr

    assert_kept_apart(
        text_of(pdf), [(f"LEFTEND{tag}", f"RIGHTSTART{tag}") for tag in FILLS]
    )


# stn-avj.3 -- header.doc-title ------------------------------------------------


def header_source(fill: int, tag: str) -> str:
    """A header whose identity and context CAN land on one baseline.

    Getting there needs three things at once, and an earlier version of this
    test had none of them, so it could not have failed:

    - No byline. Source order is identity, byline, context, so a byline puts
      the facts row between the two columns in the content stream and pypdf
      breaks on the y change before it ever compares x.
    - No subtitle, for the same reason one level down: the subtitle is another
      line of the identity column, emitted between the title and the context.
    - A single-line title tuned to nearly fill the identity track, so the last
      glyph of the title sits one `column-gap` from the first of the context.

    `align-items: baseline` then puts both first baselines on one line, and the
    two runs are adjacent in the stream with nothing between them. Measured,
    fills 0-6 land on one extracted line and 7 wraps the title.
    """
    title = "Title " + "m" * fill + f" TITLEEND{tag}"
    return (
        f'---\ntitle: "{title}"\n'
        f'brand: "CONTEXTSTART{tag} Institute"\n'
        'program: "CS 425"\nsection: "001"\nterm: "Fall 2026"\n'
        "---\n\n## Body\n\nSome prose.\n"
    )


@pytest.fixture(scope="module")
def header_sweep(to_pdf):
    """Each header variant is its own document, so this is one PDF per fill."""
    chunks = []
    for i, tag in enumerate(HEADER_FILLS):
        result, pdf = to_pdf(
            "doc", f"hdrgap{tag}.md", text=header_source(i, tag), stem=f"hdrgap{tag}"
        )
        assert result.returncode == 0, result.stderr
        chunks.append(text_of(pdf))
    return "\n".join(chunks)


def test_the_header_columns_do_not_jam_in_the_text_layer(header_sweep):
    """The weakest of the four guards, now able to fail.

    test_pdf.py has asserted `"SimulationCS 425.001" not in text` since 0.13.0
    with a comment saying the identity and context columns stay apart because
    they are far apart in the CONTENT STREAM rather than because a character
    separates them -- and that nothing had checked whether an arrangement
    exists in which they are not. There is one, `header_source` builds it, and
    the columns still extract apart: 1.06 em at the tightest, ~7x the pypdf
    threshold. Narrower than the other two sites, which is why it is worth
    having a real guard rather than an assertion that cannot fire.

    Note that the content-stream argument is pypdf's behaviour specifically.
    poppler reorders the header geometrically and puts the context after the
    body text, so it never has these two adjacent at all. The gap width is what
    both models rely on.
    """
    assert_kept_apart(
        header_sweep,
        [(f"TITLEEND{tag}", f"CONTEXTSTART{tag}") for tag in HEADER_FILLS],
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
#
# The boxes are read off `.doc-fact` / `.doc-fact-sep` themselves rather than
# off `.doc-facts`'s direct children. Those are the elements whose blockification
# is load-bearing, and reading the container's children instead would pass for a
# refactor that groups facts in a wrapper div: the wrapper is block, while the
# spans inside it go back to one inline flow.
FACTS_PROBE = r"""
const puppeteer = require("puppeteer");
(async () => {
  const browser = await puppeteer.launch({args: ["--no-sandbox"]});
  const page = await browser.newPage();
  await page.setViewport({width: 1280, height: 720});
  await page.goto("file:///workspace/factsprobe.html", {waitUntil: "networkidle0"});
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  const snapshot = await page.accessibility.snapshot({interestingOnly: false});
  const names = [];
  (function walk(n) {
    if (n.role === "StaticText") names.push(n.name);
    (n.children || []).forEach(walk);
  })(snapshot);
  const facts = await page.evaluate(() => {
    const nodes = document.querySelectorAll(".doc-fact, .doc-fact-sep");
    if (!nodes.length) return null;
    return Array.from(nodes).map((k) => ({
      cls: k.className,
      display: getComputedStyle(k).display,
      ariaHidden: k.getAttribute("aria-hidden"),
      text: k.textContent,
    }));
  });
  console.log("---JSON---");
  console.log(JSON.stringify({names, facts}));
  await browser.close();
})();
"""

# Every display value that generates a block-level box. Under a flex parent,
# blockification can only produce these plus `contents` and `none`, which are
# excluded deliberately -- neither leaves a box to carry the boundary. The two
# values that would reintroduce the defect, `inline` and `inline-block`, are
# excluded for the same reason.
BLOCK_LEVEL = {"block", "flow-root", "flex", "grid", "list-item", "table"}


@pytest.fixture(scope="module")
def facts_probe(pdf_workspace):
    """Chromium's accessibility tree and computed display values for the facts.

    Rendered to its own filename rather than sharing `facts.md` with the PDF
    test below, so neither can be reading a file the other wrote.
    """
    (pdf_workspace / "factsprobe.md").write_text(FACTS)
    built = pipeline.render(
        "doc", "factsprobe.md", "factsprobe.html", workdir=pdf_workspace
    )
    assert built.returncode == 0, built.stderr

    result = pipeline.run_in_browser(FACTS_PROBE, workdir=pdf_workspace, timeout=300)
    assert result.returncode == 0, result.stderr[-2000:]
    payload = json.loads(result.stdout.split("---JSON---", 1)[1].strip())
    assert payload["facts"] is not None, "the page rendered no facts line at all"
    return payload


def test_the_facts_are_block_boxes(facts_probe):
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

    So this asserts the computed display of the facts THEMSELVES, not of
    `.doc-facts`'s direct children. `display: block` on the container would
    satisfy a stylesheet grep and leave its `<span>` children inline; grouping
    facts in a wrapper div would satisfy a check on the container's children.
    Both are caught here.
    """
    facts = facts_probe["facts"]
    assert facts, "the facts line rendered no facts"

    inline = [f for f in facts if f["display"] not in BLOCK_LEVEL]
    assert not inline, (
        "these facts are not block boxes, so the accessibility tree has nothing "
        "separating one from the next: "
        + ", ".join(f"{f['cls']} is {f['display']}" for f in inline)
    )


def test_the_fact_separators_stay_out_of_the_accessibility_tree(facts_probe):
    """The premise the blockification argument rests on.

    The separators carry the only whitespace on the facts line -- a non-breaking
    space either side of the middot -- and they are `aria-hidden`, so none of it
    reaches a screen reader. That is WHY the boundary has to come from the box
    structure. If a refactor dropped `aria-hidden`, the argument above would
    still be stated in the stylesheet but would no longer be the reason, and the
    middot would start being announced.

    Asserted from the accessibility tree rather than the DOM attribute, because
    what matters is that the subtree is actually gone from it.
    """
    hidden = [f for f in facts_probe["facts"] if "doc-fact-sep" in f["cls"]]
    assert hidden, "no fact separators rendered, so this asserts nothing"
    assert all(f["ariaHidden"] == "true" for f in hidden), (
        "a fact separator is no longer aria-hidden: "
        + ", ".join(f"{f['cls']}={f['ariaHidden']}" for f in hidden)
    )

    names = facts_probe["names"]
    assert names, "the accessibility tree carried no text at all"
    for f in hidden:
        assert f["text"] not in names, (
            f"the separator {f['text']!r} reached the accessibility tree as its "
            "own run, so it is no longer hidden from a screen reader"
        )


def test_the_facts_line_reaches_the_pdf_as_words(to_pdf):
    """The other half of the facts line, and the half that IS a character.

    The separators' non-breaking spaces sit inside a text run that also carries
    the middot, which is why they survived into the text layer when the
    header's bare gaps did not. Asserted against a document carrying only the
    facts, so a failure names the facts rather than the whole header.
    """
    result, pdf = to_pdf("doc", "facts.md", text=FACTS)
    assert result.returncode == 0, result.stderr

    # Runs of ordinary and non-breaking space collapse; newlines deliberately
    # do not. pypdf synthesizes a space from a wide horizontal jump and inserts
    # line breaks from its layout heuristics, so collapsing newlines here would
    # manufacture the very separation this is meant to prove is present.
    text = re.sub(r"[ \t\u00a0]+", " ", text_of(pdf))

    assert "Issued Sep 05 · Due Sep 12 · 23:59 · Points 50 pts" in text, text[:400]
