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
