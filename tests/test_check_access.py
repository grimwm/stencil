"""`make check-access`, run for the first time by something other than a person.

stn-s5b. pa11y is a compose service, and every test in this repository reads
markup or a built PDF, so **nothing here had ever executed pa11y**. Three
consequences, all of them live until this file:

- pa11y 10.0.0 -- a major -- floated into the browser image ten days after
  release and no check noticed;
- the `actions` block in ``Dockerfile.browser.j2`` that clicks the theme
  control, and the argument for forcing both themes rather than resolving
  `system`, were defended by a comment and nothing else;
- the WCAG 2.1 AA result the project claims for its output was measured only
  by whoever last ran the target by hand.

This is the counterpart to ``tests/test_pdf_ua.py``. That file checks the PDF
against PDF/UA-1; this one checks the HTML against WCAG 2.1 AA, with the same
engine, the same configs and the same two themes the generated service uses --
so a template change that quietly breaks contrast or removes a label fails here
rather than in front of a reader.

WHY BOTH THEMES ARE SEPARATE CASES. Checking one leaves the other's contrast
unmeasured, and those are the ratios nobody looks at: 0.11.0 shipped a label at
3.88:1 that looked entirely unremarkable. A single combined assertion would also
report "the page has issues" without saying which theme, which is the first
thing you need to know.

WHY THE DECK IS HERE TOO. It renders through a different template, a different
Lua filter and a different stylesheet, and 0.28.2 is the release that found a
whole document kind had never been put in front of veraPDF. The same omission
was sitting here.

WHAT STOPS THIS PASSING VACUOUSLY. The configs reach their theme by clicking the
real control, and pa11y rejects a `click element` action whose selector matches
nothing rather than carrying on -- so renaming the control in
``_theme-toggle.html.j2`` fails the fixture with the action error rather than
quietly measuring the default theme twice and reporting green.
"""

from __future__ import annotations

import json

import pytest

from stencil import pipeline

pytestmark = pytest.mark.integration

# (source, kind, rendered) -- the two document kinds a package can produce.
PAGES = [
    ("document.md", "doc", "document.html"),
    ("deck.md", "slide", "deck.html"),
]
THEMES = ["light", "dark"]

# Runs pa11y exactly as the generated check-access service does: the config the
# Dockerfile wrote, which carries both the sandbox flags and the click on the
# theme control, with WCAG2AA on top. Reporting the issues rather than a count
# so a failure names the rule and the selector instead of a number.
PROBE = """
const pa11y = require("pa11y");

const pages = %(pages)s;
const themes = %(themes)s;

(async () => {
  const out = {};
  for (const page of pages) {
    for (const theme of themes) {
      const config = require(`/opt/pa11y-${theme}.json`);
      const results = await pa11y(`file:///workspace/${page}`, {
        ...config,
        standard: "WCAG2AA",
      });
      out[`${page}:${theme}`] = results.issues.map(
        (issue) => `${issue.type} ${issue.code} at ${issue.selector}`
      );
    }
  }
  console.log("<<<A11Y>>>" + JSON.stringify(out));
})().catch((error) => {
  console.log("<<<A11Y>>>" + JSON.stringify({ error: String(error) }));
  process.exit(1);
});
"""

MARKER = "<<<A11Y>>>"


@pytest.fixture(scope="session")
def accessibility(pdf_workspace):
    """Every page in every theme, from one run inside the built image.

    pa11y launches a browser per page per theme, so this is the expensive
    fixture in the suite after the image build itself. Once per session, and
    the four cases below read its result rather than each paying for a
    container start of their own.
    """
    for source, kind, rendered in PAGES:
        built = pipeline.render(kind, source, rendered, workdir=pdf_workspace)
        assert built.returncode == 0, f"pandoc failed on {source}\n{built.stderr}"

    script = PROBE % {
        "pages": json.dumps([rendered for _, _, rendered in PAGES]),
        "themes": json.dumps(THEMES),
    }
    result = pipeline.run_in_browser(script, workdir=pdf_workspace, timeout=900)

    line = next(
        (line for line in result.stdout.splitlines() if line.startswith(MARKER)), None
    )
    assert line is not None, (
        f"pa11y printed no result (exit {result.returncode})\n"
        f"stdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
    )
    report = json.loads(line[len(MARKER) :])
    assert "error" not in report, f"pa11y failed inside the image: {report['error']}"
    return report


@pytest.mark.parametrize("rendered", [rendered for _, _, rendered in PAGES])
@pytest.mark.parametrize("theme", THEMES)
def test_a_generated_page_passes_wcag_2_1_aa(accessibility, rendered, theme):
    """What `make check-access` asserts, asserted where CI can see it."""
    key = f"{rendered}:{theme}"
    assert key in accessibility, f"pa11y never ran for {key}"
    assert accessibility[key] == [], (
        f"{rendered} fails WCAG 2.1 AA in the {theme} theme:\n  "
        + "\n  ".join(accessibility[key])
    )
