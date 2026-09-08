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
import shutil
from pathlib import Path

import pytest

from stencil import pipeline

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent / "fixtures"

# (source, kind, rendered) -- the two document kinds a package can produce.
PAGES = [
    ("document.md", "doc", "document.html"),
    ("deck.md", "slide", "deck.html"),
]

# show_download now defaults to true, so the two pages above already carry
# the button and only ever measure WCAG with it PRESENT. These pair each one
# with an otherwise-identical page that sets `show_download: false`, so the
# matrix also measures the button's ABSENCE -- both mount points, both
# themes -- rather than leaving that half of the toggle unmeasured. The
# fixture below writes their markdown into pdf_workspace before rendering,
# rather than adding fixture files, so each stays an obvious variant of the
# page next to it in PAGES.
NO_DOWNLOAD_PAGES = [
    ("document-no-download.md", "doc", "document-no-download.html"),
    ("deck-no-download.md", "slide", "deck-no-download.html"),
]

ALL_PAGES = PAGES + NO_DOWNLOAD_PAGES

THEMES = ["light", "dark"]


def _with_show_download_false(text: str) -> str:
    """document.md/deck.md's frontmatter plus an explicit `show_download:
    false`, so the no-download pages differ from the ones in PAGES only in
    the control's presence -- same content, same images, same citations."""
    return text.replace("---\n", "---\nshow_download: false\n", 1)


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
    the eight cases below read its result rather than each paying for a
    container start of their own.
    """
    for source, kind, rendered in PAGES:
        built = pipeline.render(kind, source, rendered, workdir=pdf_workspace)
        assert built.returncode == 0, f"pandoc failed on {source}\n{built.stderr}"

    # The show_download-false variants: write the fixture markdown they are
    # paired with into pdf_workspace with the key added, then render exactly
    # as above.
    for source, kind, rendered in NO_DOWNLOAD_PAGES:
        paired_source = "document.md" if kind == "doc" else "deck.md"
        text = (FIXTURES / paired_source).read_text()
        (pdf_workspace / source).write_text(_with_show_download_false(text))
        built = pipeline.render(kind, source, rendered, workdir=pdf_workspace)
        assert built.returncode == 0, f"pandoc failed on {source}\n{built.stderr}"

    script = PROBE % {
        "pages": json.dumps([rendered for _, _, rendered in ALL_PAGES]),
        "themes": json.dumps(THEMES),
    }
    # pa11y launches a browser per page per theme, and the matrix doubled
    # from 4 combinations to 8 with the show_download-false pages above --
    # double the work, double the budget.
    result = pipeline.run_in_browser(script, workdir=pdf_workspace, timeout=1800)

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


@pytest.mark.parametrize("rendered", [rendered for _, _, rendered in ALL_PAGES])
@pytest.mark.parametrize("theme", THEMES)
def test_a_generated_page_passes_wcag_2_1_aa(accessibility, rendered, theme):
    """What `make check-access` asserts, asserted where CI can see it."""
    key = f"{rendered}:{theme}"
    assert key in accessibility, f"pa11y never ran for {key}"
    assert accessibility[key] == [], (
        f"{rendered} fails WCAG 2.1 AA in the {theme} theme:\n  "
        + "\n  ".join(accessibility[key])
    )


# ---------------------------------------------------------------------------
# the service's own script, run rather than read
#
# The cases above drive pa11y the way check-access does. That is not the same
# claim as "check-access works": a broken loop, glob, skip-list or URL lives in
# the script, and every test in this repository read that script's TEXT.
#
# It was broken. Inlined in the compose file, the loop searched /out while the
# URL was built from /workspace, so for a package with an output_dir every page
# came back ERR_FILE_NOT_FOUND and check-access could not pass at all. It
# shipped in 0.30.0. Reading the compose file could not have found it -- both
# halves were individually plausible, and only running them together shows they
# disagree.


@pytest.fixture(scope="session")
def one_page(pdf_workspace, tmp_path_factory):
    """A directory holding exactly one rendered page.

    A directory of its own rather than the shared workspace, which by the time
    this runs holds every page the rest of the container tier rendered. The
    script checks EVERY html file it finds, so pointing it at the shared
    workspace would make these cases pass or fail on whatever some other test
    happened to render -- and the generated pages are self-contained, so one
    copied file is a complete page.

    Renders document.md, which now carries the download button by default.
    No edit needed here: this fixture and the two script tests below only
    check that check-access's own script finds and passes over HTML it is
    pointed at -- they are not the WCAG matrix above, and a button on the one
    page they render does not change what they are proving.
    """
    built = pipeline.render(
        "doc", "document.md", "document.html", workdir=pdf_workspace
    )
    assert built.returncode == 0, f"pandoc failed\n{built.stderr}"

    directory = tmp_path_factory.mktemp("one-page")
    shutil.copy2(pdf_workspace / "document.html", directory / "document.html")
    return directory


def test_the_script_passes_over_products_beside_their_sources(one_page):
    """The default layout: no output_dir, products land in the package."""
    result = pipeline.check_access(
        workdir=one_page, directory="/workspace", timeout=900
    )
    assert result.returncode == 0, (
        f"check-access failed\nstdout: {result.stdout[-3000:]}\n"
        f"stderr: {result.stderr[-3000:]}"
    )
    assert "at WCAG 2.1 AA, light and dark" in result.stdout


def test_the_script_passes_over_an_output_directory(one_page, tmp_path_factory):
    """THE ONE THAT WAS BROKEN, and the reason this file runs the script rather
    than reading it.

    A package with an output_dir gets its products on a second mount at /out,
    because a sibling directory is `..` away and `..` escapes a bind mount.
    Inlined in the compose file, the loop searched /out while the URL was built
    from /workspace, so every page came back

        Error: net::ERR_FILE_NOT_FOUND at file:///workspace//out/document.html

    and check-access could not pass at all for such a package. It shipped in
    0.30.0 behind two individually plausible lines that only disagree when run.
    """
    result = pipeline.check_access(
        workdir=tmp_path_factory.mktemp("sources"),
        directory="/out",
        out_dir=one_page,
        timeout=900,
    )
    assert "ERR_FILE_NOT_FOUND" not in result.stdout + result.stderr, (
        "the script found HTML and then asked the browser for a path that does "
        "not exist -- the loop and the URL disagree about where the page is"
    )
    assert result.returncode == 0, (
        f"check-access failed over /out\nstdout: {result.stdout[-3000:]}\n"
        f"stderr: {result.stderr[-3000:]}"
    )
    assert "at WCAG 2.1 AA, light and dark" in result.stdout


def test_the_script_fails_when_it_finds_nothing(tmp_path_factory):
    """The silent one, and the reason the count exists. A glob that matches
    nothing leaves the loop body unrun and $failed at 0, which exits 0 and reads
    exactly like success -- so moving the output somewhere the loop does not
    look would report a clean bill of health having opened no file."""
    result = pipeline.check_access(
        workdir=tmp_path_factory.mktemp("empty"), directory="/workspace", timeout=300
    )
    assert result.returncode != 0, (
        "check-access reported success having checked nothing:\n" + result.stdout
    )
    assert "found no HTML to check" in result.stdout + result.stderr
