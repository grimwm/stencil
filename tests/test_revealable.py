"""::: revealable -- content folded behind a toggle, previewed on hover.

Presenter notes are the case it was written for. `::: {.hidden}` already
keeps them out of the student build, but a presenter who builds with
WITH=hidden then projects them, in full, on every slide that has them. The
notes need to be ON the page and OFF the screen until asked for.

The behavioural assertions run in a browser, because the questions are
about where boxes land and what a click does: the stylesheet says
`display: none` and a reader of it would agree the body is hidden, which is
the half nobody doubted. The first regression this file guards was found
only that way -- the deck's overlay opened on hover and covered its own
toggle, so the click that pins it could never land (the body intercepted
the pointer). A test that read the CSS would have passed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from stencil import pipeline

TEMPLATES = Path(__file__).parent.parent / "stencil" / "templates"


# --- The template, read as text: the cheap tier --------------------------


def _css(name: str) -> str:
    return re.sub(r"/\*.*?\*/", "", (TEMPLATES / name).read_text(), flags=re.S)


def _print_block(css: str) -> str:
    """The body of the `@media print` block, brace-matched."""
    start = css.index("@media print")
    depth = 0
    for i in range(css.index("{", start), len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[start:i]
    raise AssertionError("unterminated @media print block")


def test_the_raw_div_is_hidden_until_the_script_has_shaped_it():
    """Between parse and script the div holds the notes in full. A rule that
    only hid `.revealable-body` would show them for that window -- and for
    good on a page whose script never ran."""
    css = _css("_page-style.css.j2")
    assert re.search(
        r"\.revealable:not\(\.revealable--ready\)\s*\{\s*display:\s*none;", css
    )


def test_a_revealable_block_never_prints():
    """Inside the print block, so it holds for make pdf and for Ctrl+P alike."""
    printed = _print_block(_css("_page-style.css.j2"))
    assert re.search(r"\.revealable\s*\{\s*display:\s*none\s*!important;", printed)


def test_hover_and_pinned_states_are_distinct_rules():
    """Hover is a glance and the click pins; both must open the body, and
    neither may be the only way, or a keyboard loses one and a mouse the
    other."""
    css = _css("_page-style.css.j2")
    opener = re.search(
        r"((?:\.revealable[^{]*,\s*)*\.revealable[^{]*)\{\s*display:\s*block;", css
    )
    assert opener, "no rule opens .revealable-body"
    selectors = opener.group(1)
    assert ".revealable.is-open > .revealable-body" in selectors
    assert ".revealable:hover > .revealable-body" in selectors
    assert ":focus-visible" in selectors, "a keyboard gets no preview on Tab"


def test_the_script_builds_a_real_disclosure():
    """A <button> with aria-expanded and aria-controls, not a div with a
    click handler: that is what makes it a disclosure to a screen reader
    and what pa11y measures on the fixture deck."""
    script = (TEMPLATES / "_page-scripts.html.j2").read_text()
    block = script[script.index("::: revealable") :]
    assert "document.createElement('button')" in block
    assert "toggle.type = 'button'" in block
    assert "setAttribute('aria-expanded', 'false')" in block
    assert "setAttribute('aria-controls', body.id)" in block
    assert "getAttribute('data-label') || 'Notes'" in block


def test_the_slide_overlay_anchors_to_the_toggle_not_the_slide():
    """bottom: 100% of the block -- the overlay rises from the toggle's top
    edge. Anchored to the slide's bottom it covered the toggle."""
    css = _css("_slide-style.css.j2")
    rules = [
        m.group(1)
        for m in re.finditer(r"\.slide \.revealable-body\s*\{([^}]*)\}", css)
    ]
    assert rules, "no slide rule for .revealable-body"
    body = next((r for r in rules if re.search(r"position:\s*absolute", r)), None)
    assert body, "no slide rule positions .revealable-body"
    assert re.search(r"bottom:\s*100%", body)
    block = re.search(r"\.slide > \.revealable\s*\{([^}]*)\}", css)
    assert block and re.search(r"position:\s*relative", block.group(1)), (
        "the block must be the overlay's containing block"
    )


# --- The page, in a browser: the container tier --------------------------

def deck(fence: str = "::: {.revealable label=\"Presenter notes\"}") -> str:
    return (
        '---\ntitle: "Revealable"\nlang: en\nslide-level: 2\n---\n\n'
        "## Slide\n\nBody text.\n\n"
        "::: takeaway\n\nThe conclusion.\n\n:::\n\n"
        f"{fence}\n\nAsk the class first.\n\n- one\n- two\n\n:::\n"
    )


def document() -> str:
    return (
        '---\ntitle: "Revealable"\nlang: en\n---\n\n'
        "# Problem\n\nWhat is $2+2$?\n\n"
        '::: {.revealable label="Answer"}\n\nFour.\n\n:::\n\n'
        "Trailing paragraph.\n"
    )


PROBE = r"""
const puppeteer = require("puppeteer");
(async () => {
  const browser = await puppeteer.launch({args: ["--no-sandbox"]});
  const page = await browser.newPage();
  await page.setViewport({width: 1280, height: 800});
  await page.goto("file:///workspace/%(file)s", {waitUntil: "networkidle0"});
  // The pointer does not scroll. Bring the toggle on screen once, so
  // elementFromPoint below asks about a point that is actually painted.
  await page.$eval(".revealable-toggle", (t) => t.scrollIntoView({block: "center", behavior: "instant"}));

  const read = () => page.evaluate(() => {
    const block = document.querySelector(".revealable");
    const toggle = block.querySelector(".revealable-toggle");
    const body = block.querySelector(".revealable-body");
    const r = toggle.getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return {
      ready: block.classList.contains("revealable--ready"),
      tag: toggle.tagName,
      type: toggle.type,
      label: toggle.textContent,
      expanded: toggle.getAttribute("aria-expanded"),
      controls: toggle.getAttribute("aria-controls") === body.id,
      bodyDisplay: getComputedStyle(body).display,
      bodyPosition: getComputedStyle(body).position,
      // The click has to be able to land: whatever is under the toggle's
      // centre must be the toggle, open or closed.
      toggleReachable: hit === toggle || toggle.contains(hit),
      rawChildren: Array.from(block.children).map((c) => c.className),
    };
  });

  const out = {};
  out.initial = await read();

  const box = await page.$eval(".revealable-toggle", (t) => {
    const r = t.getBoundingClientRect();
    return {x: r.left + r.width / 2, y: r.top + r.height / 2};
  });
  await page.mouse.move(box.x, box.y);
  out.hover = await read();

  await page.mouse.move(2, 2);
  out.unhover = await read();

  await page.mouse.click(box.x, box.y);
  await page.mouse.move(2, 2);
  out.clicked = await read();

  await page.mouse.click(box.x, box.y);
  await page.mouse.move(2, 2);
  out.clickedAgain = await read();

  // Keyboard: Tab onto the toggle previews it; Enter pins it. The toggle
  // is reached by tabbing from the top of the page, the way a keyboard
  // reader reaches it, so :focus-visible applies -- page.focus() is a
  // programmatic focus and does not count, and tabbing off and back
  // depends on which element happens to precede the toggle.
  await page.evaluate(() => { if (document.activeElement) document.activeElement.blur(); });
  for (let i = 0; i < 40; i++) {
    await page.keyboard.press("Tab");
    const onToggle = await page.evaluate(
      () => document.activeElement === document.querySelector(".revealable-toggle")
    );
    if (onToggle) break;
  }
  out.focused = await read();
  await page.keyboard.press("Enter");
  out.entered = await read();

  await page.emulateMediaType("print");
  out.print = await page.evaluate(
    () => getComputedStyle(document.querySelector(".revealable")).display
  );

  console.log(JSON.stringify(out));
  await browser.close();
})();
"""


@pytest.fixture(scope="module")
def probe(pdf_workspace):
    def _probe(kind: str, text: str) -> dict:
        name = f"revealable-{kind}"
        (pdf_workspace / f"{name}.md").write_text(text)
        built = pipeline.render(
            kind, f"{name}.md", f"{name}.html", workdir=pdf_workspace
        )
        assert built.returncode == 0, built.stderr
        result = pipeline.run_in_browser(
            PROBE % {"file": f"{name}.html"}, workdir=pdf_workspace, timeout=180
        )
        assert result.returncode == 0, result.stderr[-2000:]
        return json.loads(result.stdout.strip().splitlines()[-1])

    return _probe


@pytest.mark.integration
@pytest.mark.parametrize("kind,text", [("slide", deck()), ("doc", document())])
def test_the_body_is_hidden_until_asked_for(probe, kind, text):
    out = probe(kind, text)
    first = out["initial"]
    assert first["ready"]
    assert first["bodyDisplay"] == "none"
    assert first["expanded"] == "false"
    assert first["rawChildren"] == ["revealable-toggle", "revealable-body"], (
        "the block's own children must all have moved into the body"
    )


@pytest.mark.integration
@pytest.mark.parametrize("kind,text", [("slide", deck()), ("doc", document())])
def test_the_toggle_is_a_disclosure_button(probe, kind, text):
    first = probe(kind, text)["initial"]
    assert first["tag"] == "BUTTON"
    assert first["type"] == "button", "a bare <button> submits a form"
    assert first["controls"], "aria-controls must name the body it opens"
    assert first["label"] in ("Presenter notes", "Answer"), (
        "label= on the fence is the toggle's text"
    )


@pytest.mark.integration
@pytest.mark.parametrize("kind,text", [("slide", deck()), ("doc", document())])
def test_hover_previews_and_leaving_closes(probe, kind, text):
    out = probe(kind, text)
    assert out["hover"]["bodyDisplay"] == "block"
    assert out["hover"]["expanded"] == "false", "a hover is not a click"
    assert out["unhover"]["bodyDisplay"] == "none"


@pytest.mark.integration
@pytest.mark.parametrize("kind,text", [("slide", deck()), ("doc", document())])
def test_a_click_pins_and_a_second_click_releases(probe, kind, text):
    out = probe(kind, text)
    assert out["clicked"]["bodyDisplay"] == "block"
    assert out["clicked"]["expanded"] == "true"
    assert out["clickedAgain"]["bodyDisplay"] == "none"
    assert out["clickedAgain"]["expanded"] == "false"


@pytest.mark.integration
@pytest.mark.parametrize("kind,text", [("slide", deck()), ("doc", document())])
def test_the_toggle_stays_under_the_pointer_while_open(probe, kind, text):
    """The regression: the deck's overlay opened on hover and covered its own
    toggle, so the click could never land."""
    out = probe(kind, text)
    assert out["hover"]["toggleReachable"], "the open body covers the toggle"
    assert out["clicked"]["toggleReachable"]


@pytest.mark.integration
@pytest.mark.parametrize("kind,text", [("slide", deck()), ("doc", document())])
def test_a_keyboard_gets_the_same_two_states(probe, kind, text):
    out = probe(kind, text)
    assert out["focused"]["bodyDisplay"] == "block", "Tab onto the toggle previews"
    assert out["entered"]["expanded"] == "true", "Enter pins"


@pytest.mark.integration
@pytest.mark.parametrize("kind,text", [("slide", deck()), ("doc", document())])
def test_nothing_of_it_prints(probe, kind, text):
    assert probe(kind, text)["print"] == "none"


@pytest.mark.integration
def test_on_a_slide_the_body_floats_rather_than_reflowing(probe):
    out = probe("slide", deck())
    assert out["hover"]["bodyPosition"] == "absolute"


@pytest.mark.integration
def test_in_a_document_the_body_opens_in_flow(probe):
    out = probe("doc", document())
    assert out["hover"]["bodyPosition"] == "static"
