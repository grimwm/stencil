"""The deck's keyboard, driven by a real browser pressing real keys.

stn-avj's neighbour. Every other test in this repository reads markup or a
built PDF; none of them can press a key, which is why a collision between two
keyboard handlers shipped and was found by someone teaching from the deck.

The collision: the theme control is a radiogroup, and selection follows focus
in a radiogroup -- its arrow handler calls __stencilTheme.set(), not merely
"move the highlight". The deck listens for the same arrows on document and
guards only INPUT, TEXTAREA and contentEditable. So once a reader had focused
the theme control, one ArrowRight advanced the slide AND cycled the theme one
step: light, dark, system. Touch the control once mid-talk and every subsequent
slide change also changed the palette.
"""

from __future__ import annotations

import json

import pytest

from stencil import pipeline

pytestmark = pytest.mark.integration

DECK = """---
title: "Deck"
lang: en
---

## One

First.

## Two

Second.

## Three

Third.
"""

# Presses keys through the CDP input domain rather than dispatching synthetic
# events, so the browser routes them by focus exactly as it would for a person.
PROBE = r"""
const puppeteer = require("puppeteer");

(async () => {
  const browser = await puppeteer.launch({args: ["--no-sandbox"]});
  const page = await browser.newPage();
  await page.goto("file:///workspace/deck.html", {waitUntil: "networkidle0"});
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  await page.evaluate(() => window.__mermaidReady === true || undefined);

  const state = () => page.evaluate(() => ({
    pref: document.documentElement.dataset.themePref,
    theme: document.documentElement.dataset.theme,
    presenting: document.documentElement.classList.contains("presenting"),
    current: document.querySelector(".slide.is-current")
      ? Array.from(document.querySelectorAll(".slide"))
          .indexOf(document.querySelector(".slide.is-current"))
      : null,
  }));

  // Enter present mode the way a presenter does: the p key.
  await page.keyboard.press("p");
  const entered = await state();

  // Choose Light by clicking it, which is also what leaves focus on it.
  await page.evaluate(() => {
    const light = Array.from(document.querySelectorAll('[role="radio"]'))
      .find((b) => /light/i.test(b.textContent));
    light.focus();
    light.click();
  });
  const chosen = await state();

  // The press that used to do two things at once.
  await page.keyboard.press("ArrowRight");
  const afterArrow = await state();

  await page.keyboard.press("ArrowRight");
  const afterSecond = await state();

  // A keyboard-only presenter's path to the control: every option is its own
  // tab stop while presenting, because the arrows are spoken for.
  const tabStops = await page.evaluate(() =>
    Array.from(document.querySelectorAll('[role="radio"]')).map((b) => b.tabIndex)
  );

  // Tab to the option after the checked one and activate it with Enter.
  await page.evaluate(() => {
    const radios = Array.from(document.querySelectorAll('[role="radio"]'));
    radios[radios.findIndex((b) => b.getAttribute("aria-checked") === "true")].focus();
  });
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  const afterEnter = await state();

  // Space activates a focused button; it must not also skip a slide.
  await page.keyboard.press("Space");
  const afterSpace = await state();

  console.log(JSON.stringify({
    entered, chosen, afterArrow, afterSecond, tabStops, afterEnter, afterSpace,
  }));
  await browser.close();
})();
"""


@pytest.fixture(scope="module")
def deck_keyboard(pdf_workspace):
    (pdf_workspace / "deck.md").write_text(DECK)
    built = pipeline.render("slide", "deck.md", "deck.html", workdir=pdf_workspace)
    assert built.returncode == 0, built.stderr

    result = pipeline.run_in_browser(PROBE, workdir=pdf_workspace, timeout=180)
    assert result.returncode == 0, (
        f"the browser probe failed:\n{result.stderr[-3000:]}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_deck_enters_present_mode_from_the_keyboard(deck_keyboard):
    """A precondition for everything below, asserted so a failure here is not
    misread as a theme bug."""
    assert deck_keyboard["entered"]["presenting"] is True


def test_choosing_a_theme_while_presenting_sticks(deck_keyboard):
    assert deck_keyboard["chosen"]["pref"] == "light"
    assert deck_keyboard["chosen"]["theme"] == "light"


def test_changing_slides_does_not_change_the_theme(deck_keyboard):
    """The bug, stated as the thing a presenter noticed.

    Both arrows are checked, not one. The control cycles light -> dark ->
    system, so a single press moved the preference to "dark" -- which on a
    light machine still LOOKS like a change but on a dark machine looks like
    nothing happened until the second press reached "system". Asserting one
    press would have passed on the wrong machine.
    """
    assert deck_keyboard["afterArrow"]["pref"] == "light", (
        "the first slide change also cycled the theme control"
    )
    assert deck_keyboard["afterSecond"]["pref"] == "light", (
        "the second slide change also cycled the theme control"
    )
    assert deck_keyboard["afterSecond"]["theme"] == "light"


def test_the_arrows_still_change_slides(deck_keyboard):
    """The fix must not buy theme stability by swallowing the arrow.

    Silencing the deck's handler while the toggle has focus would pass every
    assertion above and leave a presenter pressing a dead key, which is a worse
    bug than the one being fixed.
    """
    first = deck_keyboard["chosen"]["current"]
    assert deck_keyboard["afterArrow"]["current"] == first + 1
    assert deck_keyboard["afterSecond"]["current"] == first + 2


def test_every_theme_option_is_reachable_by_tab_while_presenting(deck_keyboard):
    """The other half of taking the arrows away.

    Roving tabindex leaves only the CHECKED option tabbable, and the arrows are
    what normally reach the rest. Disable them during a presentation without
    doing anything else and a keyboard-only presenter can focus the control and
    never change it -- a quieter bug than the one being fixed, and a worse one.
    """
    assert deck_keyboard["tabStops"] == [0, 0, 0], (
        "not every theme option is a tab stop while presenting, so the arrows "
        f"were removed without a replacement: tabIndex {deck_keyboard['tabStops']}"
    )


def test_enter_on_a_theme_option_changes_the_theme_not_the_slide(deck_keyboard):
    before, after = deck_keyboard["afterSecond"], deck_keyboard["afterEnter"]
    assert after["pref"] != before["pref"], (
        "Tab then Enter did not change the theme, so the keyboard path is dead"
    )
    assert after["current"] == before["current"], (
        "activating the theme control also moved the deck"
    )


def test_space_on_a_focused_button_does_not_also_skip_a_slide(deck_keyboard):
    """The same double-action, one key over.

    Space activates a focused button AND is the deck's "next slide". A
    presenter who has tabbed to the theme control and pressed Space would
    otherwise change the setting and lose a slide at once.
    """
    before, after = deck_keyboard["afterEnter"], deck_keyboard["afterSpace"]
    assert after["current"] == before["current"], (
        "Space on a focused button skipped a slide as well as activating it"
    )
