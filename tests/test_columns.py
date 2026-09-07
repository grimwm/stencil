"""Deck columns: how many across, and whether they are cards.

stn-v9u. `.columns` was `grid-template-columns: 1fr 1fr` -- two columns
whatever you put in it. Three children left an empty cell and four stacked
2x2, both of which shipped in a deck in use and both of which read as mistakes
rather than choices.

The layout assertions run in a browser because a grid's used track count is not
in the stylesheet. Reading the CSS would confirm what was written, which is the
half that was never in doubt.
"""

from __future__ import annotations

import json

import pytest

from stencil import pipeline

pytestmark = pytest.mark.integration


def deck(wrapper: str, count: int, extra: str = "") -> str:
    items = "\n".join(
        f":::: column\n**Item {i}**\n\nSome prose.\n::::\n" for i in range(count)
    )
    return (
        f'---\ntitle: "Columns"\nlang: en\n---\n\n## Slide\n\n'
        f"::::: {wrapper}\n\n{items}\n:::::\n{extra}"
    )


PROBE = r"""
const puppeteer = require("puppeteer");
(async () => {
  const browser = await puppeteer.launch({args: ["--no-sandbox"]});
  const page = await browser.newPage();
  await page.setViewport({width: 1280, height: 720});
  await page.goto("file:///workspace/cols.html", {waitUntil: "networkidle0"});
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  const out = await page.evaluate(() => {
    const el = document.querySelector(".columns");
    const kids = Array.from(el.children);
    const first = getComputedStyle(kids[0]);
    return {
      children: kids.length,
      // Distinct top offsets IS the row count. Counting grid tracks would
      // report what was declared; this reports where the boxes landed.
      rows: new Set(kids.map((k) => Math.round(k.getBoundingClientRect().top))).size,
      // Distinct LEFT offsets is the used column count, for the same reason:
      // with grid-auto-flow: row the declared template and the boxes that
      // actually land can disagree, and the boxes are what a reader sees.
      cols: new Set(kids.map((k) => Math.round(k.getBoundingClientRect().left))).size,
      widths: kids.map((k) => Math.round(k.getBoundingClientRect().width)),
      background: first.backgroundColor,
      borderLeftWidth: first.borderLeftWidth,
      paddingTop: first.paddingTop,
      accents: kids.map((k) => getComputedStyle(k).borderLeftColor),
    };
  });
  console.log(JSON.stringify(out));
  await browser.close();
})();
"""


@pytest.fixture(scope="module")
def layout(pdf_workspace):
    def _layout(wrapper: str, count: int) -> dict:
        (pdf_workspace / "cols.md").write_text(deck(wrapper, count))
        built = pipeline.render(
            "slide", "cols.md", "cols.html", workdir=pdf_workspace
        )
        assert built.returncode == 0, built.stderr
        result = pipeline.run_in_browser(PROBE, workdir=pdf_workspace, timeout=180)
        assert result.returncode == 0, result.stderr[-2000:]
        return json.loads(result.stdout.strip().splitlines()[-1])

    return _layout


@pytest.mark.parametrize("count", [2, 3, 4])
def test_columns_sit_in_one_row_whatever_their_number(layout, count):
    """The bug, stated as the thing that was visible on a slide.

    Three was the loud case -- a 2x2 grid with a hole where a fourth card
    would have been -- but four was wrong too, just symmetrically enough to
    look deliberate.
    """
    got = layout("columns", count)
    assert got["children"] == count
    assert got["rows"] == 1, (
        f"{count} columns landed on {got['rows']} rows; they are one row now"
    )


def test_plain_columns_are_not_cards(layout):
    """Every existing deck uses bare `::: columns`, and none of them asked for
    a card. The treatment is opt-in precisely so this stays true."""
    got = layout("columns", 3)
    assert got["background"] in ("rgba(0, 0, 0, 0)", "transparent")
    assert got["borderLeftWidth"] == "0px"
    assert got["paddingTop"] == "0px"


def test_cards_get_a_surface_a_border_and_padding(layout):
    got = layout("{.columns .cards}", 3)
    assert got["background"] not in ("rgba(0, 0, 0, 0)", "transparent")
    assert got["borderLeftWidth"] == "4px"
    assert got["paddingTop"] != "0px"


def test_each_card_can_carry_its_own_accent(pdf_workspace):
    """One card per accent, and the colours must actually differ.

    Asserting that the class is present would pass with every card the same
    colour, which is the failure the slide this came from already had.
    """
    md = (
        '---\ntitle: "Accents"\nlang: en\n---\n\n## Slide\n\n'
        "::::: {.columns .cards}\n\n"
        ":::: column\n**S**\n\nOne.\n::::\n\n"
        ":::: {.column .accent-2}\n**T**\n\nTwo.\n::::\n\n"
        ":::: {.column .accent-3}\n**A**\n\nThree.\n::::\n\n"
        ":::: {.column .accent-4}\n**R**\n\nFour.\n::::\n\n"
        ":::::\n"
    )
    (pdf_workspace / "cols.md").write_text(md)
    built = pipeline.render("slide", "cols.md", "cols.html", workdir=pdf_workspace)
    assert built.returncode == 0, built.stderr
    result = pipeline.run_in_browser(PROBE, workdir=pdf_workspace, timeout=180)
    assert result.returncode == 0, result.stderr[-2000:]
    accents = json.loads(result.stdout.strip().splitlines()[-1])["accents"]
    assert len(set(accents)) == 4, (
        f"four cards were given four accents and rendered {len(set(accents))} "
        f"distinct colours: {accents}"
    )


# ---------------------------------------------------------------------------
# stn-o5s: say how many columns a row has.
#
# The other half of the 0.20.0 trade. Before that release .columns was
# `1fr 1fr`, so four children DID wrap to 2x2 -- and three left an empty cell.
# grid-auto-flow: column killed the empty cell and took the deliberate 2x2
# with it. Both shapes are legitimate; the defect was that either was implicit.
#
# Every assertion below reads the RENDERED boxes, not the stylesheet. A grid's
# used track count is not in the CSS, and with wrapping enabled the declared
# template and where the boxes land can disagree.


@pytest.mark.parametrize(
    "count,cols,rows",
    [
        (4, 2, 2),  # the 2x2 that 0.20.0 took away
        (6, 3, 2),
        (8, 4, 2),
    ],
)
def test_data_cols_wraps_into_rows_of_that_width(layout, count, cols, rows):
    got = layout(f"{{.columns data-cols={cols}}}", count)
    assert got["children"] == count
    assert got["cols"] == cols, f"asked for {cols} across, got {got['cols']}"
    assert got["rows"] == rows, f"expected {rows} rows, got {got['rows']}"


def test_a_short_last_row_is_left_short(layout):
    """Five at three across is 3 + 2, and the gap on the right stays.

    This is NOT the 0.20.0 empty-cell bug returning. There the count was
    imposed by the stylesheet and the hole was a surprise; here the author
    asked for three across and the remainder is arithmetic. Nothing stretches
    the orphan to hide it, and a test says so, because "fixing" this is the
    obvious wrong idea.
    """
    got = layout("{.columns data-cols=3}", 5)
    assert got["rows"] == 2
    assert got["cols"] == 3
    # The two cards in the last row keep their own width rather than growing
    # to fill it: every card in the grid is the same size.
    assert len(set(got["widths"])) == 1, got["widths"]


@pytest.mark.parametrize("count", [2, 3, 4])
def test_without_data_cols_nothing_changes(layout, count):
    """The guard every deck already written depends on.

    0.20.0 exists because a layout moved under slides nobody had touched. The
    same must not happen again on the way to fixing it.
    """
    got = layout("columns", count)
    assert got["rows"] == 1
    assert got["cols"] == count


def test_data_cols_beats_a_width_bias(layout):
    """Declared precedence, asserted rather than left to source order.

    `wide-left` biases two tracks; `data-cols` says how many tracks there are.
    Saying how many is the more fundamental statement, and `data-cols=3` with
    `wide-left` has no meaning at all -- so data-cols wins and the bias is
    ignored. Combining them is documented as useless rather than made to work,
    and this is what pins that decision.
    """
    got = layout("{.columns .wide-left data-cols=2}", 4)
    assert got["rows"] == 2
    assert got["cols"] == 2
    assert len(set(got["widths"])) == 1, (
        f"the width bias survived data-cols: {got['widths']}"
    )


def test_wrapped_columns_can_still_be_cards(layout):
    """The two features are orthogonal and the STAR slide wants both."""
    got = layout("{.columns .cards data-cols=2}", 4)
    assert got["rows"] == 2
    assert got["cols"] == 2
    assert got["borderLeftWidth"] == "4px"
    assert got["paddingTop"] != "0px"
