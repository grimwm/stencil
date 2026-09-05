# Front matter additions and HTML theming

Date: 2026-09-05
Status: approved, awaiting implementation plans

Two releases, shipped as two pull requests:

- **0.11.0 — front matter.** A `points` badge, a `due` key, an `Issued` label on
  the existing `date`, and a regrid of the document header so its source order
  matches its visual order.
- **0.12.0 — theming.** A light/dark/system selector for HTML output, with PDF
  output light by construction.

They are separated because they collide in `_doc-body.html.j2` and
`_page-style.css.j2`, and because doing the front matter first means the new
badge and date colors get tokenized as part of the theming sweep rather than
being written by hand twice.

______________________________________________________________________

# Part A — 0.11.0, front matter

## A.1 The contract

| key         | shape                              | renders as                                                | placement                       |
| ----------- | ---------------------------------- | --------------------------------------------------------- | ------------------------------- |
| `points`    | number, or any string              | `50 pts`, `1 pt`, `1.5 pts`; a non-numeric value verbatim | badge beside the title          |
| `due`       | `yyyy-mm-dd` or `yyyy-mm-ddThh:mm` | `Due Sep 12 · 23:59`                                      | byline row, right, under Issued |
| `date`      | `yyyy-mm-dd` or `yyyy-mm-ddThh:mm` | `Issued Sep 01 · 21:45`                                   | byline row, right               |
| `show_date` | boolean-ish, unchanged             | fills `date` with the build date when `date` is absent    | —                               |

All three new/changed keys join `BLANKABLE` in `frontmatter-filter.lua`, so a key
written with nothing after it is indistinguishable from an absent one.

The filter computes seven keys for the templates to read, so each template asks
one question rather than reimplementing the logic.

Five are rendered: `points-label`, `date-label`, `date-iso`, `due-label`,
`due-iso`. The `-iso` pair carries the author's original string for the
`datetime` attribute; the `-label` keys carry the rendered text.

Two are internal, and exist only because a pandoc template cannot OR two keys
together: `has-dates` (`date-label` or `due-label`) opens the date wrapper in
`_doc-body.html.j2`, and `due-needs-separator` (`author` or `date-label`) tells
`_slide-body.html.j2` whether anything precedes Due on the deck byline. Both
also gate `has-byline`.

### Dates

One grammar for both date keys. The time renders **iff the author wrote one**:

```
date: 2026-09-01          ->  Issued Sep 01
date: 2026-09-01T21:45    ->  Issued Sep 01 · 21:45
due:  2026-09-12          ->  Due Sep 12
due:  2026-09-12T23:59    ->  Due Sep 12 · 23:59
show_date: true, no date  ->  Issued Sep 05          (date-only)
```

- Month abbreviations are the C-locale three-letter forms: `Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec`.
- The day is zero-padded (`Sep 05`).
- **No year.** A handout is read inside a term the reader already knows, and
  `term:` is usually in the header saying so. This reverses the earlier
  position in this section that the visible form must stay lossless for print:
  the year is unprinted rather than lost, and the `<time datetime>` attribute
  still carries it for anything reading the page rather than looking at it.
- Time is 24-hour, zero-padded.
- The separator between date and time is a middot with hair spaces, matching the
  author-list separator already used in `.doc-meta`.
- The visible text is wrapped in `<time datetime="...">` carrying the **exact
  original ISO string**, so the HTML stays machine-readable regardless of how
  the display is abbreviated. PDFs keep only the visible text, which is why the
  display itself remains lossless.
- The auto-filled build date is rendered date-only. The build stamp's clock time
  is an artifact of when `make` ran, not a fact about the document.

**Validation.** Three gates, in order. A value failing any of them fails the
build, in the manner `frontmatter-filter.lua` already uses for a missing
`brand-alt` — while the author is still looking at the document.

1. **Shape.** The string matches `yyyy-mm-dd` or `yyyy-mm-ddThh:mm` exactly.
   Anything else fails with a message naming both accepted shapes.
1. **Range.** Month `01`–`12`, hour `00`–`23`, minute `00`–`59`. The calendar
   gate below would catch a bad month on its own — `month = 13` normalizes into
   the following January and fails the comparison — but it would report it as a
   date the calendar does not contain, which is not what went wrong. Checking
   the range first buys a message that names the field.
1. **Calendar.** The day is checked against the real length of that month in
   that year.

The calendar check is a round-trip through Lua's `os` library, **not** a
hand-rolled days-in-month table and leap rule:

```lua
local t = os.time({ year = y, month = m, day = d, hour = 12 })
local back = os.date("*t", t)
local ok = back.year == y and back.month == m and back.day == d
```

`os.time` *normalizes* an out-of-range day rather than rejecting it — `2026-02-30`
comes back as March 2. That normalization is the detector: compare the fields
back against what was written, and any date the calendar does not contain fails
the comparison. The Gregorian rules, leap years included, come from the platform
`mktime` rather than from code in this repository.

`hour = 12` is load-bearing. A date pinned to midnight can cross a day boundary
in a zone observing a DST transition, which would fail a date the calendar does
contain. Noon is far enough from every transition that no shift reaches it.

Verified in the render image (`pandoc/core:3.10.0.0`), covering all four branches
of the Gregorian rule and both ends of the `time_t` range:

```
2026-02-30 -> 2026-03-02  reject      2024-02-29 -> match   accept
2026-02-29 -> 2026-03-01  reject      2000-02-29 -> match   accept   (400-year)
1900-02-29 -> 1900-03-01  reject      2038-12-31 -> match   accept   (64-bit)
2100-02-29 -> 2100-03-01  reject      1900-02-28 -> match   accept   (pre-1970)
```

The earlier draft of this spec ruled `os.time` out on the grounds that it
normalizes rather than rejects. That was wrong: normalizing is only a hazard if
the normalized value is what you use.

The error names the specific failure rather than restating the grammar, since
the grammar is not what was violated:

```
due: 2026-02-30 is not a real date -- February 2026 has 28 days.
```

**Back-compat.** Imposing strict ISO on `date` is a behavior change — the key
accepts any string today. The measured cost is zero: no handout in the consuming
course repository sets `date` at all (all five use `show_date: true`), and the
only `date` values anywhere in this repository are `tests/fixtures/document.md`
and `tests/fixtures/deck.md`, both already `2026-09-02`.

### Points

`points-label` is computed once in Lua so templates ask a single question,
`$if(points-label)$` — the pattern the filter already documents for `date`:

- parses as a number: `1` gives `1 pt`, everything else gives `N pts`
  (`0 pts`, `1.5 pts`, `100 pts`)
- does not parse: rendered verbatim, so `points: "extra credit"` is a usable
  escape hatch rather than a build failure

## A.2 The header regrid

Today `<header class="doc-title">` holds two block-level rows:

```
.doc-headrow   .doc-identity (title, subtitle)  |  .doc-context (brand, program·section, term)
.doc-byline    .doc-meta (author)               |  .doc-date
```

Visually the author already sits directly under title and subtitle. In **source
order** it does not — `.doc-context` sits between them, and that is the order a
screen reader announces and `pdftotext` extracts.

`.doc-headrow` is removed and `<header class="doc-title">` becomes the grid:

```css
header.doc-title {
  display: grid;
  grid-template-columns: minmax(18rem, 1fr) auto;
  align-items: baseline;
  column-gap: 1.5rem;
  row-gap: 0.35rem;
}
.doc-identity { grid-column: 1;      grid-row: 1; min-width: 0; }
.doc-context  { grid-column: 2;      grid-row: 1; text-align: right; }
.doc-byline   { grid-column: 1 / -1; grid-row: 2; margin-top: 0.6rem; }
```

Source order becomes `.doc-identity`, `.doc-byline`, `.doc-context` — so title →
badge → subtitle → author → dates → context. The rendered layout is unchanged.

The `18rem` basis is carried over from the flex rule it replaces, preserving the
existing wrap threshold. Under the existing `max-width: 40rem` query the grid
collapses to `grid-template-columns: 1fr` and every child resets to
`grid-column: 1; grid-row: auto`, so it stacks in source order — which is the
narrow-viewport improvement, not a regression: author now genuinely follows the
subtitle instead of trailing the context block. The explicit `grid-row` values
**must** be reset there, or the two row-1 items overlap.

`.doc-title` keeps its `border-bottom`, which sits under the whole header
including the byline — not between the rows.

## A.3 Markup

`.doc-identity` gains the badge, immediately after `$title$` and before the
subtitle:

```html
<div class="doc-identity">$title$
$if(points-label)$
  <span class="doc-points">$points-label$</span>
$endif$
$if(subtitle)$
  <div class="doc-subtitle">$subtitle$</div>
$endif$
</div>
```

`.doc-date` is kept as the **wrapper** for both dates rather than renamed, so the
existing `margin-left: auto; text-align: right` rule and its narrow-viewport
override keep applying without change:

```html
<div class="doc-date">
$if(date-label)$  <div class="doc-issued"><span class="doc-date-label">Issued</span>
                    <time datetime="$date-iso$">$date-label$</time></div>$endif$
$if(due-label)$   <div class="doc-due"><span class="doc-date-label">Due</span>
                    <time datetime="$due-iso$">$due-label$</time></div>$endif$
</div>
```

`has-byline` becomes `author or date or due`.

Deck title slides get the same badge inside `<h1 class="deck-title">`. Both dates
join `.deck-meta` alongside the author — **not** the context line above the title
— so the deck matches the document, where the dates live in the byline and the
context column stays institutional:

```
        CS 425 · Fall 2026
    Kanban Board Simulation ⟨50 pts⟩
          Pull, Don't Push
  William Grim · Issued Sep 05 · Due Sep 12 · 23:59
```

`_slide-body.html.j2` already branches on `$if(author)$` to decide whether the
date renders alone; that branch grows a third case for `due`.

## A.4 Styling

`.doc-points` / `.deck-points`: a pill — ~0.95rem (doc) / 1.1rem (deck), weight
600, muted accent background, rounded, `white-space: nowrap`, baseline-aligned.
It must never outrank the title; `_page-style.css.j2` already restates sizes for
`.doc-subtitle` because that element inherits `.doc-title`'s 2.2rem/700, and the
badge needs the same treatment for the same reason.

`.doc-date-label` is muted and slightly smaller than the value it labels.

The badge's colors are written as literal values in 0.11.0 and converted to
tokens by the Part B sweep. This is the sequencing rationale in concrete form:
shipping theming first would mean writing the badge's light and dark values
before the badge exists, and shipping them together would mean one pull request
doing two unrelated things to the same file.

No new print rules. The badge and dates ride the existing root rescale in the
print block; `tests/test_title_block.py` already holds the rule that nothing in
the header may print larger than the title, and the new test below extends it.

## A.5 Tests — `tests/test_points.py`, `tests/test_dates.py`

Points:

- badge renders; absent `points` emits no badge element; blank `points:` behaves
  as absent
- `1 pt` vs `0 pts` / `1.5 pts` / `100 pts`
- non-numeric renders verbatim
- badge appears on the deck title slide
- PDF: the badge prints smaller than the title

Dates:

- both accepted shapes, for both keys, with and without a time
- `<time datetime>` carries the original ISO string unmodified
- `show_date` auto-fill renders date-only
- an author-written `date` still beats `show_date`
- each rejected shape fails the build with a message naming both accepted shapes
- out-of-range fields fail: month `00`/`13`, hour `24`, minute `60`
- calendar validity, parametrized over all twelve months: the last real day of
  each is accepted and the day after it is rejected (`2026-01-31` yes,
  `2026-01-32` no; `2026-04-30` yes, `2026-04-31` no)
- leap years across all four branches of the Gregorian rule: `2024-02-29`
  accepted, `2026-02-29` rejected, `2000-02-29` accepted, `1900-02-29` rejected
- `1900-02-28` and `2038-12-31` are accepted, pinning both ends of the `time_t`
  range the round-trip depends on
- the calendar error names the month and its real length, not the grammar

The leap-year and `time_t` cases are not redundant with the probe recorded in
A.1. That probe confirmed the approach works in today's image; these assert it
keeps working when the image is bumped, which is the only time it could
plausibly break.

- blank `date:` / `due:` behave as absent
- `has-byline` opens the row for `due` alone

Ordering (extends `tests/test_byline.py`):

- `.doc-meta` precedes `.doc-context` in document order
- the narrow-viewport query resets `grid-row`, so nothing overlaps

`tests/test_byline.py` needs updating either way: its `_text(soup, ".doc-date")`
helper reads that element's text directly, and `.doc-date` becomes a wrapper
holding `.doc-issued` and `.doc-due`. The existing assertions should move to
`.doc-issued` rather than accept a concatenation of both dates.

## A.6 Also

`AUTHORING.md` front matter table and per-key sections; `CHANGELOG.md` 0.11.0
entry; `stencil/__init__.py` to `0.11.0`.

Downstream, in the course repository: drop `subtitle: "Points: 100"` from
`final-presentation-1.md`, convert `subtitle: "Points: 50"` to `points: 50` in
`sprint-report.md`, sweep the remaining handouts for the same subtitle hack, then
`make reinstall REF=<pr>` and `make gen`. Every generated file there is
gitignored, so nothing is hand-edited.

______________________________________________________________________

# Part B — 0.12.0, HTML theming

## B.1 Mechanism

`:root` carries the light palette as the **only unconditional definition**. All
58 hardcoded colors across `_page-style.css.j2` (30) and `_slide-style.css.j2`
(28) become `var(--token)`. Dark values live in exactly one block:

```css
:root { --surface: #fff; --text-muted: #555; /* light, always */ }

@media screen {
  :root[data-theme="dark"] { --surface: #1b1f27; --text-muted: #a8b0bd; /* ... */ }
}
```

**The `@media screen` wrapper is the entire print guarantee.** Print does not
match it, so no dark declaration is visible to the print formatter and the light
`:root` values apply by default. Nothing is re-declared in a print block,
nothing can drift out of sync, and it holds identically for a browser's own
Cmd+P from a dark page and for Puppeteer.

**System resolution happens in JS, not CSS.** The script stores the *preference*
(`light` | `dark` | `system`) and writes the *resolved* value to
`data-theme`, re-resolving on `matchMedia` change. This keeps the dark palette to
one CSS block instead of duplicating it across a `prefers-color-scheme` variant.
Generated pages already require JS for Mermaid, tabs and highlighting, so
no-JS-means-light is an acceptable floor.

`data-theme-pref` carries the preference separately, so the control can show
which segment is pressed while `data-theme` stays a resolved `light`/`dark`.

### Bootstrap

`data-bs-theme` is deliberately **not** used, despite Bootstrap 5.3.3 shipping
dark support. Its `[data-bs-theme=dark]` block lives inside the vendored
`bootstrap.min.css` and is not wrapped in `@media screen`; setting that attribute
would put a second dark mechanism in the page that print cannot switch off,
returning us to re-asserting variables and depending on specificity.

Instead the Bootstrap variables these pages actually surface are mapped from
stencil's own tokens, inside the same sealed block: `--bs-body-bg`,
`--bs-body-color`, `--bs-border-color`, `--bs-secondary-bg`, `--bs-tertiary-bg`,
`--bs-emphasis-color`, `--bs-link-color`, `--bs-link-hover-color`, `--bs-code-color`,
and the `--bs-table-*` set. One mechanism.

### Palette

Starting values, to tune against a real build:

| role                | light                 | dark                            |
| ------------------- | --------------------- | ------------------------------- |
| page / surface      | `#fff`                | `#14171c` / `#1b1f27`           |
| sunken (code, tabs) | `#f6f8fa`             | `#22262f`                       |
| body text           | `#212529`             | `#e3e6ea`                       |
| muted / faint       | `#555` / `#99a1b0`    | `#a8b0bd` / `#7b8494`           |
| border              | `#dee2e6`             | `#333a46`                       |
| accent              | `#29417a`             | `#8fa9e0` text, `#2f4680` fills |
| callout card        | `#fffdf5` / `#c8b48c` | `#232016` / `#5c5031`           |
| focus ring          | `#ffbf47`             | unchanged — passes on both      |

## B.2 The control

A new `_theme-toggle.html.j2` partial owns markup, styles and behavior:
`role="radiogroup"`, `aria-label="Color theme"`, three `role="radio"` buttons
with `aria-checked`, arrow-key navigation, visible focus ring.

Two mount points, one implementation: documents mount it into a fixed top-right
container; decks inject it into the existing `.deck-toolbar` before the Present
button, since that toolbar is built by JS. `@media print { display: none }`.

Persistence is `localStorage` under one key, every access wrapped in `try`/`catch`
— private windows and blocked site data throw. On `file://` Chrome pools all
local pages into one storage origin, so the choice is effectively repo-wide;
that is intended.

`_slide-scripts.html.j2` records that the toolbar's *position* was deliberately
made non-persistent. That reasoning does not transfer: a parked toolbar explains
nothing on screen, whereas a theme's result is the page itself and the pressed
segment names the active choice.

## B.3 Print and PDF

`html-to-pdf.js` gains
`emulateMediaFeatures([{ name: 'prefers-color-scheme', value: 'light' }])`.

This is **defense in depth only** — B.1 already makes it structurally impossible
for a dark rule to reach print. The `__mermaidReady` handshake is untouched.

## B.4 Syntax highlighting

`highlight-github.min.css` is light-only. Vendor `highlight-github-dark.min.css`
alongside it (~1.3 KB), scoped inside the sealed dark block; add it to `_FILES`
in `stencil/assets.py` and to `scripts/vendor_page_assets.py`. Both are inlined;
the size cost is negligible against the 3.5 MB Mermaid bundle already carried.

## B.5 Mermaid

Mermaid bakes its colors into the SVG at draw time, so CSS cannot repaint a
rendered diagram and print cannot undo one. Left alone, a diagram would stay
light on a dark page.

**Diagrams are redrawn on theme change.** The current single-shot render in
`_page-scripts.html.j2` is factored into `renderMermaid(theme)`, called once at
load and again whenever the resolved theme changes:

- the source text is stashed in `dataset.mermaidSource` before the first run,
  since `mermaid.run()` replaces the element's contents with SVG
- a redraw restores the source, clears `data-processed`, re-initializes, re-runs,
  and re-applies the existing SVG id-prefixing pass (WCAG 4.1.1)
- `window.__mermaidReady` is set once after the **first** render and never
  cleared

Mermaid is driven with `theme: 'base'` and `themeVariables` mapped from the
token set, so diagrams match the page rather than approximating it.

**Why this is safe for PDF.** A PDF build loads the page fresh with empty
`localStorage` and `prefers-color-scheme` forced light, so the resolved theme
never changes and the diagram is drawn exactly once. The redraw path only fires
on a click, which cannot occur during a build. The earlier concern that a redraw
would race the `__mermaidReady` flag was overstated: the flag is set once, on the
first render, and a build never reaches a second one.

## B.6 Tests — `tests/test_theme.py`

- **Drift lint:** no raw hex or `rgb()`/`rgba()`/`hsl()` anywhere in either
  stylesheet outside the token-definition blocks. This is what stops the next
  edit from quietly reintroducing a hardcoded color, and is the single most
  valuable test here.
- **Containment:** every dark declaration sits inside `@media screen` — the print
  guarantee, asserted directly rather than inferred.
- **Parity:** the dark block defines exactly the token names `:root` defines,
  no more and no fewer.
- **Contrast:** computed WCAG ratios >= 4.5:1 for body, muted and on-accent
  pairs in both palettes.
- **Control:** renders on both document and deck, carries correct ARIA, and is
  hidden in print CSS.
- **Mermaid:** the source is stashed before the first render; `__mermaidReady` is
  set exactly once.
- Extend `make check-access` to run pa11y in both themes.

## B.7 Files

`_page-style.css.j2`, `_slide-style.css.j2`, `_page-head.html.j2` (inline
no-FOUC script, `color-scheme`), `_page-scripts.html.j2` (Mermaid refactor),
`_slide-scripts.html.j2` (toolbar mount), `html-template.html.j2`,
`slide-template.html.j2`, `html-to-pdf.js.j2`, new `_theme-toggle.html.j2`,
`stencil/assets.py`, `scripts/vendor_page_assets.py`, new
`stencil/assets/highlight-github-dark.min.css`, plus `AUTHORING.md`,
`CHANGELOG.md` and `stencil/__init__.py` to `0.12.0`.

______________________________________________________________________

# Rejected alternatives

- **`subtitle: "Points: N"`** — the status quo being replaced. Overloads a
  presentation field with a data field, so nothing can query or validate it.
- **Points as a context-column line, or in the byline.** Considered and passed
  over in favor of the badge, which puts what the document is worth where a
  student looks first.
- **`due` rendered verbatim.** Initially proposed to keep date formatting in the
  author's hands; overridden in favor of strict ISO in, compact out, so the
  format is consistent across every handout.
- **`issued:` as a new key, or as an alias for `date:`.** Rejected in favor of
  keeping `date:` and labeling it. Two spellings for one field is the kind of
  dead weight this repository has removed before.
- **`data-bs-theme`** — see B.1.
- **Re-declaring light values inside `@media print`** — works, but means keeping
  a second copy of 58 colors in sync forever. `@media screen` containment gets
  the same guarantee with no duplication.
- **Leaving Mermaid on `theme: 'neutral'` in all themes.** Proposed first as the
  build-safe option; rejected because it fails the stated bar of looking right in
  both themes, and because the safety argument did not survive scrutiny (B.5).
- **`filter: invert()` for dark mode** — destroys images, diagrams and the brand
  logo. Not considered further.

# Out of scope

- Theming the PDF. PDFs are light, always, by construction.
- A per-document or per-package default theme in `.config.yaml`. Nothing has
  asked for it; the viewer's choice is the only input.
- Any change to the pandoc filter order, which `AGENTS.md` documents as
  load-bearing.
