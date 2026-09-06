# HTML Theming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A light/dark/system theme selector for HTML output, with PDF light by
construction.

**Architecture:** `:root` carries the light palette as the only unconditional
definition; every dark declaration is sealed inside `@media screen`, which is
the entire print guarantee — print cannot match that block, so the light values
apply and nothing has to be re-declared. System resolution happens in JS, which
writes a resolved `light`/`dark` to `data-theme`, so the dark palette stays one
CSS block.

**Tech Stack:** Jinja2 templates rendering pandoc HTML templates and Lua
filters; pandoc 3.10.0.0 and headless Chromium in containers; pytest with
BeautifulSoup and pypdf.

**Spec:** `docs/superpowers/specs/2026-09-05-frontmatter-and-theming-design.md`
(Part B). Ticket: `stn-pci`.

## Global Constraints

- Target version is **0.12.0**. Bump `stencil/__init__.py`.
- **Every colour in both stylesheets is a token.** No raw hex, `rgb()`,
  `rgba()` or `hsl()` outside the token-definition blocks — including inside
  `@media print`, which gets its own `--print-*` tokens rather than a lint
  exemption.
- **Every dark declaration lives inside `@media screen`.** This is the print
  guarantee. Nothing dark may be written anywhere else, and no light value may
  be re-declared for print.
- `data-theme` holds the **resolved** value (`light`/`dark`); `data-theme-pref`
  holds the **preference** (`light`/`dark`/`system`). The control reads the
  preference; the CSS reads the resolved value.
- Bootstrap's `data-bs-theme` is **not** used. Map the `--bs-*` variables the
  pages actually surface from stencil's own tokens, inside the sealed block.
- Container-backed tests carry `pytestmark = pytest.mark.integration`.
- Run the suite with `.venv/bin/python -m pytest` from the repo root.
- **Run `coderabbit review --agent --committed --base main` before opening the
  PR.** In 0.11.0 this caught a dropped CSS rule and a 3.88:1 contrast failure
  that a green suite, a five-way CI matrix and the hosted review all missed.
- A pre-commit `mdformat` hook rewrites Markdown; a pre-push hook rejects a
  stale `.beads/issues.jsonl`. On failure, follow what the hook says and retry.
  Never `git commit -C HEAD` as a fallback — it silently reuses the previous
  message. Run `git push` as its own command; chaining it after a heredoc
  leaves the pre-push hook with empty stdin.

______________________________________________________________________

### Task 1: Token layer for the document stylesheet

**Files:**

- Modify: `stencil/templates/_page-style.css.j2`
- Test: `tests/test_theme.py` (create)

**Interfaces:**

- Produces: the `:root` token set every later task reads. Names below are the
  contract — Task 2 and Task 3 use them verbatim.

**Token set** (light values, taken from the colours already in the file):

```
--surface            #fff        page and card surfaces
--surface-sunken     #f6f8fa     code blocks
--surface-muted      #f5f7fa     tab panes
--surface-accent     #e8eef7     nav-tabs strip, points badge
--surface-accent-on  #d2def2     active tab
--text               #212529     body (Bootstrap's default, made explicit)
--text-muted         #5a6270     byline, context
--text-label         #687080     Issued/Due labels
--text-subtle        #555        subtitle
--text-strong        #3a424f     brand
--text-faint         #99a1b0     generated separators
--border             #dee2e6     rules, code borders
--border-muted       #c8c8c8     tab borders
--accent             #29417a     accent colour and badge text
--on-accent          #fff        text on an accent fill
--on-accent-muted    rgba(255,255,255,0.85)
--on-accent-fill     rgba(255,255,255,0.15)
--card-bg            #fffdf5     blockquote callout
--card-border        #c8b48c
--code-inline        #c7254e
--hl-builtin         #c85200
--hl-string          #0550ae
--shadow-card        rgba(0,0,0,0.08)
--print-th-bg        #e8e8e8     print-only, must stay light
--print-stripe-bg    #f5f5f5     print-only
--print-card-bg      #fffef8     print-only
```

- [ ] **Step 1: Write the failing drift test**

Create `tests/test_theme.py`:

```python
"""The theme token layer, and the containment that makes PDFs light.

The load-bearing assertion here is not that dark mode looks right -- it is
that no dark declaration exists outside `@media screen`. Print cannot match
that block, so the light `:root` values apply and nothing needs re-declaring.
Break the containment and every printed handout silently goes dark.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).parent.parent / "stencil" / "templates"
STYLESHEETS = ["_page-style.css.j2", "_slide-style.css.j2"]

COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")


def source(template: str) -> str:
    return (TEMPLATES / template).read_text()


def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def token_blocks(css: str) -> str:
    """Every `:root {...}` block, which is where colours are allowed to live."""
    return "\n".join(
        m.group(0) for m in re.finditer(r":root[^{]*\{[^}]*\}", css, re.S)
    )


@pytest.mark.parametrize("template", STYLESHEETS)
def test_no_raw_colour_outside_the_token_blocks(template):
    """The drift guard. Every colour is a token, so a hardcoded one cannot be
    reintroduced without this failing -- which is how a value escapes theming
    silently."""
    css = strip_comments(source(template))
    outside = css.replace(token_blocks(css), "")
    stray = sorted(set(COLOUR.findall(outside)))
    assert not stray, f"{template}: colours declared outside :root: {stray}"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_theme.py -v`
Expected: FAIL for both stylesheets, listing the raw colours.

- [ ] **Step 3: Define the tokens**

In `_page-style.css.j2`, replace the existing `:root` block with the full token
set above, keeping the three font tokens already there. Keep `--accent-color`
as an alias of `--accent` **only if** anything still references it; otherwise
rename its uses.

- [ ] **Step 4: Replace every colour with its token**

Work top to bottom through the file, substituting `var(--token)` for each of
the 34 colour declarations. The three inside `@media print` become
`var(--print-th-bg)`, `var(--print-stripe-bg)`, `var(--print-card-bg)` and keep
their `!important`.

- [ ] **Step 5: Run the drift test and the full suite**

Run: `.venv/bin/python -m pytest tests/test_theme.py::test_no_raw_colour_outside_the_token_blocks -v -k page`
Expected: PASS for `_page-style.css.j2`.

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. **Rendered output must be byte-identical** — this task changes
how colours are spelled, not what they are.

- [ ] **Step 6: Commit**

```bash
git add stencil/templates/_page-style.css.j2 tests/test_theme.py
git commit -m "Tokenize the document stylesheet's colours

Every colour becomes a var(), so there is one place to define a palette
and one place to override it. No rendered output changes: this is a
change of spelling, not of value.

The drift test is the point -- a hardcoded colour reintroduced later is
a value that silently escapes theming, and nothing else would notice."
```

______________________________________________________________________

### Task 2: Token layer for the deck stylesheet

**Files:**

- Modify: `stencil/templates/_slide-style.css.j2`

**Interfaces:**

- Consumes: the token names from Task 1. Deck-specific values that have no
  document equivalent get their own tokens, defined in `_page-style.css.j2`'s
  `:root` (decks include that file, so there is one `:root`, not two):

```
--deck-surface       #fff        slide card
--deck-border        #d8dee9
--deck-shadow        rgba(20,30,60,0.10)
--deck-shadow-lg     rgba(20,30,60,0.26)
--deck-counter       #8a93a5
--deck-accent-from   #29417a     title-slide gradient start
--deck-accent-to     #3c5da8     gradient end
--deck-accent-edge   #22335e
--deck-body-text     #2c3444
--deck-quote-bg      #eef2fb
--deck-quote-border  #c3cfe8
--deck-toolbar-bg    rgba(255,255,255,0.94)
--deck-toolbar-fg    #55607a
--deck-toolbar-hover #1d2f5c
--deck-presenting-bg #e9edf4
--focus-ring         #ffbf47     shared; passes on both palettes
--badge-plate        rgba(16,26,52,0.35)
```

- [ ] **Step 1: Replace every colour with its token**

Same treatment as Task 1, across the 31 colour declarations. The two inside
`@media print` (`#29417a`/`#fff` on the title slide, `#eef2fb`) become
`--print-deck-bg`, `--print-deck-fg`, `--print-deck-quote-bg`.

- [ ] **Step 2: Run the drift test and the full suite**

Run: `.venv/bin/python -m pytest tests/test_theme.py -v`
Expected: PASS for both stylesheets.

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, output unchanged.

- [ ] **Step 3: Commit**

```bash
git add stencil/templates/_slide-style.css.j2 stencil/templates/_page-style.css.j2
git commit -m "Tokenize the deck stylesheet's colours

One :root for both, since a deck includes the document stylesheet. Deck
values that have no document equivalent get their own names rather than
being forced onto a shared token that means something else."
```

______________________________________________________________________

### Task 3: The dark palette, sealed inside `@media screen`

**Files:**

- Modify: `stencil/templates/_page-style.css.j2`
- Modify: `tests/test_theme.py`

**Interfaces:**

- Produces: `:root[data-theme="dark"]` inside `@media screen`, defining exactly
  the token names `:root` defines, plus the `--bs-*` mapping.

- [ ] **Step 1: Write the failing containment, parity and contrast tests**

Append to `tests/test_theme.py`:

```python
def screen_blocks(css: str) -> list[str]:
    """Every `@media screen` block, brace-matched.

    Slicing to the first `}` would stop inside the first rule and hide the
    rest, which makes the containment test pass for no reason.
    """
    blocks = []
    for m in re.finditer(r"@media screen[^{]*\{", css):
        depth, i = 1, m.end()
        while depth and i < len(css):
            depth += (css[i] == "{") - (css[i] == "}")
            i += 1
        blocks.append(css[m.start():i])
    return blocks


def test_every_dark_declaration_is_sealed_inside_media_screen():
    """The print guarantee, asserted directly rather than inferred.

    Print never matches @media screen, so a dark block living there is
    invisible to the print formatter and the light :root values apply. A dark
    rule written anywhere else would reach the PDF.
    """
    css = strip_comments(source("_page-style.css.j2"))
    sealed = "\n".join(screen_blocks(css))
    outside = css.replace(sealed, "")
    assert 'data-theme="dark"' not in outside, (
        "a dark rule sits outside @media screen; print would see it"
    )


def test_the_dark_block_defines_exactly_the_light_token_names():
    css = strip_comments(source("_page-style.css.j2"))
    light = set(re.findall(r"(--[a-z0-9-]+)\s*:", token_blocks(css)))
    dark_block = re.search(
        r':root\[data-theme="dark"\][^{]*\{([^}]*)\}', css, re.S
    )
    assert dark_block, "no dark token block"
    dark = set(re.findall(r"(--[a-z0-9-]+)\s*:", dark_block.group(1)))
    dark = {t for t in dark if not t.startswith("--bs-")}
    light = {t for t in light if not t.startswith("--font-")}
    assert dark == light, (
        f"only in light: {sorted(light - dark)}; only in dark: {sorted(dark - light)}"
    )
```

Reuse the `relative_luminance` / `contrast` helpers already in
`tests/test_title_block.py` by importing them, and assert >= 4.5:1 for
`--text`, `--text-muted`, `--text-label` against `--surface` in **both**
palettes.

- [ ] **Step 2: Run to confirm failure**

Run: `.venv/bin/python -m pytest tests/test_theme.py -v`
Expected: FAIL — no dark block exists yet.

- [ ] **Step 3: Write the dark block**

At the end of `_page-style.css.j2`:

```css
    /* Dark, and nothing else in this file may be. @media screen is the whole
       print guarantee: print does not match it, so these never reach the
       print formatter and the light :root values above apply unchanged. That
       is why no light value is re-declared for print anywhere -- there is
       nothing to undo. */
    @media screen {
      :root[data-theme="dark"] {
        --surface: #1b1f27;
        /* ...every token from :root, dark value... */

        /* Bootstrap, mapped from our own tokens rather than via
           data-bs-theme. Bootstrap's dark block lives in the vendored
           bootstrap.min.css and is NOT wrapped in @media screen, so setting
           that attribute would put a second dark mechanism in the page that
           print cannot switch off. */
        --bs-body-bg: var(--surface);
        --bs-body-color: var(--text);
        --bs-border-color: var(--border);
        --bs-secondary-bg: var(--surface-sunken);
        --bs-tertiary-bg: var(--surface-muted);
        --bs-emphasis-color: var(--text-strong);
        --bs-link-color: var(--accent);
        --bs-link-hover-color: var(--accent);
        --bs-code-color: var(--code-inline);
        --bs-table-color: var(--text);
        --bs-table-bg: transparent;
        --bs-table-border-color: var(--border);
      }
    }
```

Use the palette in the spec's B.1 table as the starting values, and adjust
until the contrast test passes.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_theme.py -v`
Expected: PASS — containment, parity and contrast.

- [ ] **Step 5: Verify the containment guard actually catches a breach**

Temporarily move one dark declaration outside the `@media screen` wrapper, run
the containment test, confirm it FAILS, then restore. A guard that has never
failed has not been tested.

- [ ] **Step 6: Commit**

```bash
git add stencil/templates/_page-style.css.j2 tests/test_theme.py
git commit -m "Add the dark palette, sealed inside @media screen

The wrapper is the entire print guarantee. Print does not match @media
screen, so no dark declaration reaches the print formatter and the light
:root values apply -- no second copy of 65 colours to keep in sync, and
it holds for a browser's own Cmd+P as well as for Puppeteer.

Bootstrap is mapped from our tokens rather than via data-bs-theme: its
dark block is inside the vendored CSS and is not wrapped, so that
attribute would be a second mechanism print cannot switch off."
```

______________________________________________________________________

### Task 4: Theme resolution without a flash

**Files:**

- Modify: `stencil/templates/_page-head.html.j2`
- Modify: `tests/test_theme.py`

**Interfaces:**

- Produces: `window.__stencilTheme` with `get()`, `set(pref)`, and
  `subscribe(fn)`; `data-theme` (resolved) and `data-theme-pref` on
  `documentElement`; storage key `stencil-theme`.

- [ ] **Step 1: Add the blocking inline script**

In `_page-head.html.j2`, before the stylesheets:

```html
  <script>
  /* Synchronous and inline on purpose: this must run before first paint, or a
     dark reader gets a white flash on every page load.

     Resolution happens here rather than in CSS so the dark palette stays one
     block. A prefers-color-scheme variant would mean maintaining the same
     sixty-odd values twice. These pages already require JS for Mermaid, tabs
     and highlighting, so no-JS-means-light is an acceptable floor. */
  (function () {
    var KEY = 'stencil-theme';
    var root = document.documentElement;
    var query = window.matchMedia('(prefers-color-scheme: dark)');
    var listeners = [];

    function read() {
      try { return window.localStorage.getItem(KEY) || 'system'; }
      catch (e) { return 'system'; }   /* private windows throw on access */
    }
    function resolve(pref) {
      return pref === 'system' ? (query.matches ? 'dark' : 'light') : pref;
    }
    function apply(pref) {
      var theme = resolve(pref);
      root.dataset.theme = theme;
      root.dataset.themePref = pref;
      root.style.colorScheme = theme;
      listeners.forEach(function (fn) { fn(theme, pref); });
    }
    apply(read());
    query.addEventListener('change', function () {
      if (read() === 'system') apply('system');
    });

    window.__stencilTheme = {
      get: read,
      set: function (pref) {
        try { window.localStorage.setItem(KEY, pref); } catch (e) {}
        apply(pref);
      },
      subscribe: function (fn) { listeners.push(fn); },
    };
  })();
  </script>
```

- [ ] **Step 2: Test that a rendered page carries a resolved theme**

Add an integration test asserting the built HTML contains the script, that
`data-theme` is set by it, and that the storage access is wrapped in
`try`/`catch` (a private window must not throw an uncaught error, which
`html-to-pdf.js` treats as a build failure).

- [ ] **Step 3: Run the suite and commit**

Run: `.venv/bin/python -m pytest -q` — Expected: PASS.

```bash
git commit -m "Resolve the theme before first paint

Inline and synchronous, because anything deferred shows a dark reader a
white flash on every load. Resolution lives here rather than in CSS so
the dark palette stays one block instead of being maintained twice.

Every storage access is wrapped: private windows and blocked site data
throw, and an uncaught error is a failed PDF build."
```

______________________________________________________________________

### Task 5: The segmented control

**Files:**

- Create: `stencil/templates/_theme-toggle.html.j2`
- Modify: `stencil/templates/html-template.html.j2`
- Modify: `stencil/templates/slide-template.html.j2`
- Modify: `stencil/templates/_slide-scripts.html.j2`
- Modify: `stencil/templates/_page-style.css.j2`

**Interfaces:**

- Consumes: `window.__stencilTheme` from Task 4.

- Produces: `mountThemeToggle(container)`; markup with
  `role="radiogroup"`, three `role="radio"` buttons carrying `aria-checked`.

- [ ] **Step 1: Build the control**

Three buttons (Light / Dark / System), `aria-label="Color theme"`, arrow-key
navigation with roving `tabindex`, `aria-checked` tracking the **preference**
(not the resolved value). Subscribe to `__stencilTheme` so the pressed segment
follows an OS change while on System.

- [ ] **Step 2: Mount it in both templates**

Documents: a fixed top-right container in `html-template.html.j2`.
Decks: `_slide-scripts.html.j2` injects it into `.deck-toolbar` before the
Present button, since that toolbar is built by JS.

- [ ] **Step 3: Hide it in print**

```css
    @media print {
      .theme-toggle { display: none !important; }
    }
```

- [ ] **Step 4: Test**

Renders on both document and deck; correct ARIA; absent from the print
rendering; the three buttons carry the three preference values.

- [ ] **Step 5: Run the suite and commit**

______________________________________________________________________

### Task 6: Dark syntax highlighting

**Files:**

- Modify: `scripts/vendor_page_assets.py`

- Modify: `stencil/assets.py`

- Create: `stencil/assets/highlight-github-dark.min.css`

- Modify: `stencil/templates/_page-head.html.j2`

- [ ] **Step 1: Vendor the asset**

Add to `FILES` in `scripts/vendor_page_assets.py`, pinned to the same 11.9.0 as
the light theme:

```python
    "highlight-github-dark.min.css": (
        "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0"
        "/styles/github-dark.min.css"
    ),
```

Run `python3 scripts/vendor_page_assets.py` and commit the fetched file.

- [ ] **Step 2: Load it, scoped inside the sealed block**

Add `"highlight_css_dark": "highlight-github-dark.min.css"` to `_FILES` in
`stencil/assets.py`. In `_page-head.html.j2`, emit it inside
`@media screen { :root[data-theme="dark"] ... }` scoping so print never sees
it — the same containment rule as every other dark declaration.

- [ ] **Step 3: Test that the dark highlight CSS is inside the sealed block**

- [ ] **Step 4: Run the suite and commit**

______________________________________________________________________

### Task 7: Mermaid redraws on theme change

**Files:**

- Modify: `stencil/templates/_page-scripts.html.j2`

**Interfaces:**

- Consumes: `window.__stencilTheme.subscribe` from Task 4.

- Produces: `renderMermaid(theme)`; `window.__mermaidReady` set **once**.

- [ ] **Step 1: Stash the source before the first render**

`mermaid.run()` replaces each element's contents with SVG, so the source has to
be kept: `div.dataset.mermaidSource = text` before the first run.

- [ ] **Step 2: Factor the render into `renderMermaid(theme)`**

Drive Mermaid with `theme: 'base'` and `themeVariables` read from the computed
token values, so diagrams match the page rather than approximating it. A redraw
restores the stashed source, clears `data-processed`, re-initializes, re-runs,
and re-applies the existing SVG id-prefixing pass (WCAG 4.1.1).

- [ ] **Step 3: Set `__mermaidReady` exactly once**

```javascript
    /* Set after the FIRST render and never cleared. html-to-pdf.js blocks on
       this flag; a redraw that reset it would hang the build. Safe because a
       PDF build loads the page fresh with empty storage and
       prefers-color-scheme forced light, so the theme never changes and the
       diagram is drawn exactly once -- the redraw path only fires on a click,
       which cannot happen during a build. */
```

- [ ] **Step 4: Subscribe to theme changes**

- [ ] **Step 5: Test**

The source is stashed before the first render; `__mermaidReady` is assigned
exactly once in the file; a deck and a document both still build to PDF.

- [ ] **Step 6: Run the suite and commit**

______________________________________________________________________

### Task 8: PDF belt-and-braces, access checks, docs, release

**Files:**

- Modify: `stencil/templates/html-to-pdf.js.j2`

- Modify: `stencil/templates/Makefile-doc.j2`

- Modify: `AUTHORING.md`, `CHANGELOG.md`, `stencil/__init__.py`

- [ ] **Step 1: Force light in the PDF renderer**

In `html-to-pdf.js.j2`, after `const page = await browser.newPage();`:

```javascript
    // Defence in depth only. The stylesheet already makes it impossible for a
    // dark rule to reach print -- every one is sealed inside @media screen,
    // which print does not match. This pins the emulated preference so a
    // future Chromium default cannot make that argument moot.
    await page.emulateMediaFeatures([
      { name: "prefers-color-scheme", value: "light" },
    ]);
```

- [ ] **Step 2: Run pa11y in both themes**

Extend the `check-access` target so it checks the page with `data-theme` forced
to each value, not just whatever the default resolves to.

- [ ] **Step 3: Prove the print guarantee end to end**

Render a document with `data-theme="dark"` forced, print it to PDF, and assert
the page background is white — the one test that exercises the whole mechanism
rather than its parts.

- [ ] **Step 4: Document it**

`AUTHORING.md`: a short section on the selector — what the three states mean,
that the choice persists across handouts, and that PDFs are always light.
`CHANGELOG.md`: the 0.12.0 entry. `stencil/__init__.py`: `0.12.0`.

- [ ] **Step 5: Full suite, local review, then PR**

```bash
.venv/bin/python -m pytest -q
coderabbit review --agent --committed --base main
```

Fix what holds, skip what does not **with a technical reason**, then push and
open the PR.

- [ ] **Step 6: Tag**

After the PR merges, per `AGENTS.md` and the practice restored in 0.11.0:

```bash
git switch main && git pull
git tag -a v0.12.0 -m "stencil 0.12.0" && git push origin v0.12.0
```

______________________________________________________________________

## Verification before the PR

- [ ] `.venv/bin/python -m pytest` passes in full.
- [ ] No raw colour outside `:root` in either stylesheet.
- [ ] No `data-theme="dark"` declaration outside `@media screen`.
- [ ] The containment guard was proven to fail on a deliberate breach.
- [ ] A dark-forced page still prints white.
- [ ] `make check-access` green in both themes.
- [ ] A real handout and a real deck opened in both themes and eyeballed —
  the suite reads DOM and stylesheet text, and is blind to a rule the
  parser discarded or a contrast ratio nobody computed. Both of those
  shipped in 0.11.0 past a green suite.
