# Changelog

Notable changes to stencil, newest first.

This file starts at 0.3.0 and is not retroactive. The 170-odd commits before that
tag are the history of the tool arriving at this shape; git is the record of them,
and the closed epics in `.beads/issues.jsonl` are the readable index.

How the version gets bumped is written down in
[AGENTS.md](AGENTS.md#cutting-a-release), not here.

## 0.21.0

- **Generated PDFs are PDF/UA-1 conformant.** veraPDF 1.30.2 `--flavour ua1`,
  on a handout with headings, a list, a table and a figure:

  |               | at the start of this run | 0.19.0 | now   |
  | ------------- | ------------------------ | ------ | ----- |
  | rules failed  | 5                        | 1      | **0** |
  | checks failed | 47                       | 3      | **0** |

  106 rules and 1523 checks pass. Reaching zero was described in 0.19.0 as
  "not in this release"; this is that release.

- **Decoration Chromium paints untagged is now marked as an artifact.** What
  `7.1 t3` was reporting, measured on the content stream rather than reasoned
  about, was exactly three things: the white page background, a clip path, and
  the table header cells' shading. All three are decoration, which is what
  `/Artifact` exists to mark.

  **Marked, not removed.** Deleting them means turning `printBackground` off,
  which takes the table shading, the blockquote cards and the slide title bars
  with it.

  Only depth-0 runs are wrapped, and only runs that actually paint — a run of
  pure graphics state (colour, matrix, `gs`) is left alone, because marking it
  would claim content where there is none. Runs break on `q` and `Q`, so a
  wrapper can never straddle a save/restore pair it does not own.

- **Two things nearly shipped as successes**, and both are worth recording
  because each one passed every test it had.

  The first version read the stream with `getContentsString()`. That method
  exists and returns the stream's **compressed** bytes, so the transform found
  no operators, changed nothing, reported success, and the suite went green.
  The only thing that revealed it was the veraPDF failure count not moving.
  The working path is `decodePDFRawStream(...).decode()`, and the result must
  be written back as a **new** stream: a `PDFRawStream` owns its `/Length` and
  `/Filter`, so rewriting its bytes in place leaves a stream no reader can
  parse.

  A test asserted `count("re") > 5` and failed on a document with no table —
  it was measuring the fixture, not the code. It now checks that every
  `/Artifact` region contains a painting operator, which is the property that
  actually matters: the operators were bracketed rather than dropped.

  Three breaches were applied and measured, each failing only its own tests:
  no marking at all (2 tests), dropping the operators instead of bracketing
  them (1), and reverting to `getContentsString()` (2). That last row is the
  point — the silent no-op now fails loudly.

## 0.20.0

- **A columns block is as wide as its contents.** `.columns` was
  `grid-template-columns: 1fr 1fr` — two columns whatever you put in it. Three
  children left an **empty cell**; four stacked 2×2. Both shipped in a deck in
  use, and both read as mistakes rather than choices.

  `grid-auto-flow: column` takes the count from the content instead. Two stay
  two, three sit in a row of three, four in a row of four. `wide-left` and
  `wide-right` are unchanged — they name two explicit tracks, which is exactly
  what a biased two-column split is.

  Measured in a browser, not read off the stylesheet: a grid's *used* track
  count is not in the CSS, so the tests count distinct top offsets of the
  rendered boxes.

- **Columns can be cards.** `::: {.columns .cards}` gives each one a surface, a
  border, a radius and padding, built from the tokens `.takeaway` already uses
  so a card matches the boxed conclusion below it rather than introducing a
  second surface.

  **Opt-in, not default.** A card costs vertical space on a medium that has
  none spare, and every existing deck writes bare `::: columns`. Contrast was
  measured on the rendered card rather than assumed — body text is 13.76:1 in
  light and 11.6:1 in dark, the lead run 8.79:1 and 6.17:1.

- **A card can carry its own accent.** `--card-accent` defaults to the deck
  accent, and `::: {.column .accent-2}` overrides it for one card. The accent
  reaches both a left border — the same device `.lead-in` has always used — and
  the card's lead run, so `**S -- Situation**` is coloured the way the rest of
  the card is. `.lead-letter` on the block sets the first character larger,
  which is what a STAR-style slide wants.

  Page fit was checked rather than argued: a four-card slide and the same slide
  with plain columns both come out at the same page count, so the treatment
  adds no overflow of its own.

## 0.19.0

- **The document title is an `<h1>`.** It was bare text in a `<div>`, so a
  stencil document had **no h1 at all** and opened on whatever the author wrote
  first, normally an H2. That is wrong on its own terms — the title *is* the
  document's top heading, and the deck has always spelled it
  `<h1 class="deck-title">` — and PDF/UA rejects the skipped level. Styling is
  unchanged: `.doc-title` still sets the type and `.doc-name` only cancels the
  user-agent h1, so nothing moved.

- **Three things PDF/UA-1 needs that Chromium does not write** are now added to
  the finished PDF, after printing. None of them can be fixed from the HTML,
  because they are properties of the PDF rather than of the page.

  | added        | why                                                                                                                                                                                                   |
  | ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | `/RoleMap`   | Chromium tags `<strong>`/`<em>` as `/Strong`/`/Em`, which are not standard ISO 32000-1 structure types, and writes no map saying what they stand for. `<b>` tags identically, so no markup avoids it. |
  | XMP metadata | The catalog had no `/Metadata` stream at all. Now carries `pdfuaid:part=1` and the title.                                                                                                             |
  | `/LBody`     | Chromium tags a list item's content straight onto the `/LI`.                                                                                                                                          |

  The role map is **built from the document**, not hardcoded to Strong and Em.
  Chromium's tag vocabulary is not a published contract and the browser is
  unpinned, so a version that starts emitting one more non-standard type would
  otherwise reintroduce the same failure with nothing to catch it.

  The `/LBody` fix is the one worth recording, because the obvious explanation
  is wrong. It is **not** about tight versus loose markdown lists — measured,
  `<li>text</li>` and `<li><p>text</p></li>` both fail. A pandoc-side filter
  forcing loose lists was the first instinct and would have been wasted work.

- **Measured against veraPDF 1.30.2 `--flavour ua1`**, on a real handout:

  ```
  before   5 rules failed, 47 checks
  after    1 rule  failed,  3 checks
  ```

  What remains is `7.1 t3`, content Chromium paints without marking it an
  artifact: one per page plus roughly two per table. Bisected — dropping our
  own page background in print removes one of them, and the rest survive with
  every table background and border set to `transparent`, so they are the
  browser's own painting rather than anything this project declares. Reaching
  them means rewriting the page content stream, which is not in this release.

## 0.18.0

- **Changing slides no longer changes the theme.** Pick a theme while
  presenting and the next arrow key put it back; the palette then cycled
  light, dark, system as the talk went on. Reported by someone teaching from a
  deck, which was the only way it could be reported.

  Two keyboard handlers wanted the same key. The theme control is a
  radiogroup, and **selection follows focus** in a radiogroup -- its arrow
  handler does not merely move a highlight, it calls
  `__stencilTheme.set()`. The deck listens for the same arrows on `document`
  and guarded only `INPUT`, `TEXTAREA` and `contentEditable`. So once a reader
  had focused the theme control, one `ArrowRight` advanced the slide *and*
  stepped the theme, because the control's `preventDefault()` does not stop the
  event reaching the deck.

  That is why the behaviour looked so strange from outside: never touch the
  control and everything is fine, touch it once and every later slide change
  also repaints the deck.

  While `html.presenting` is set, the arrows now belong to the deck and the
  theme control ignores them. It is still clickable, and outside a
  presentation it keeps full keyboard control -- a document never sets that
  class, so nothing changes there.

  The obvious alternative fix is worse and is guarded against: silencing the
  *deck's* handler when focus sits in the toggle would make the theme stable
  and leave a presenter pressing a dead arrow key.

  Taking the arrows away needed a replacement, which a review caught before
  this shipped. Roving tabindex leaves only the *checked* option tabbable and
  the arrows are what normally reach the rest, so the guard on its own would
  have let a keyboard-only presenter focus the control and never change it --
  quieter than the bug being fixed, and worse. While presenting, every option
  is now its own tab stop: Tab walks them, Enter activates.

- **Space on a focused button no longer skips a slide as well.** The same
  double-action, one key over: Space activates a focused button and is also the
  deck's "next slide", so tabbing to the theme control and pressing Space
  changed the setting and lost a slide at once. Only the activation keys are
  surrendered -- the arrows stay with the deck wherever focus happens to be.

- **The deck's keyboard is now testable at all.** Every other test in this
  repository reads markup or a built PDF and none of them can press a key,
  which is why a collision between two handlers shipped. `pipeline.run_in_browser`
  runs a node script against the browser image the PDF build already uses, and
  `tests/test_present_mode.py` drives real key presses through it -- routed by
  focus, exactly as they would be for a person. Both failure modes above are
  covered, and both were confirmed to fail before the fix.

## 0.17.0

- **Bold is bold now.** `**like this**` in a handout rendered SemiBold rather
  than Bold, and in a serif at body size that is a difference you have to look
  for rather than one you see.

  The cause was not a CSS value, it was a missing file. Crimson Pro -- the body
  face -- was vendored at 400 and 600 only. Bootstrap's reboot says
  `b, strong { font-weight: bolder }`, which resolved to 700, no face at or
  above 700 existed, and the browser fell back to the nearest one it had: 600.
  A CSS rule naming a weight nobody ships does not fail. It renders as
  something close and looks almost right.

  Bold italic was worse. With no italic above 400, `***like this***` rendered
  as plain italic at regular weight -- no emphasis at all beyond the slant.

  The new weight is 800, and it was measured rather than picked. Advance width
  of "the quick brown fox" at 18px:

  | weight | width |                                                  |
  | ------ | ----- | ------------------------------------------------ |
  | 400    | 142.6 | regular                                          |
  | 600    | 149.1 | **what bold used to render as**                  |
  | 700    | 152.8 |                                                  |
  | 800    | 156.8 | what bold renders as now                         |
  | 900    | 156.8 | identical to 800; the family has nothing heavier |

  600 is the number to compare against, not 400, and against 600 a move to 700
  would have added 2.5% to a step that was already too small to read as
  emphasis. 800 roughly doubles it. 900 buys nothing at all.

  `strong, b` now names 800 explicitly rather than inheriting Bootstrap's
  relative `bolder`, which resolves against whatever the parent carries: inside
  `.doc-label` at 600 it asked for 700, and inside a heading at 700 it asked for
  900\. Emphasis should be one weight wherever it appears.

  Inter is untouched. It always shipped 700, which is why headings and the
  byline looked properly bold while the prose did not.

  `tests/test_fonts.py` now fails if the stylesheet and the vendor script ever
  disagree about which weights exist, in either direction. That is the check
  that was missing: nothing in the build, the suite, or a five-way CI matrix
  had anything to say about a rule asking for a face that was never fetched.

## 0.16.0

- **A figure can have a dark variant, and the PDF still prints the light one.**
  Since 0.12.0 mermaid diagrams redraw to follow the theme while static figures
  did not, so a deck carrying both went half-themed in dark mode: recoloured
  diagrams beside white-plate SVGs. That was true of a deck in active use.

  Put `images/foo-dark.svg` beside `images/foo.svg` and both are embedded, one
  classed `doc-img--light` and one `doc-img--dark`, with CSS showing one. The
  author writes `![alt](images/foo.svg)` exactly as before: no new syntax, no
  front matter, and a figure with no dark sibling is emitted unchanged.

  `<picture>` with `media="(prefers-color-scheme: dark)"` is the standard HTML
  answer and it is the wrong one here. This page resolves its theme in JS to
  `data-theme` so the dark palette stays one CSS block; `prefers-color-scheme`
  follows the operating system instead, so a `<picture>` would desync from the
  rest of the page the moment a reader picks a theme explicitly -- which is the
  case the theme control exists for.

  The containment has a new half. Every dark rule so far had to stay *inside*
  `@media screen`; the rule that **hides** the dark variant has to stay
  *outside* it, because print is what must match it. Tidying that rule in with
  the others would print both figures stacked on every handout. Both directions
  are now asserted.

  Verified against the artifact rather than the stylesheet: the fixtures are
  pure red and pure green, and the test reads the fill operator out of the
  built PDF's content stream. Chromium draws an SVG from an `<img>` as vector
  operators, so there is no image XObject to inspect -- measured, the PDF has
  none at all.

  Both variants embed, so a page using dark figures roughly doubles that part
  of its weight; a document with no dark siblings pays nothing.

## 0.15.0

- **Every figure in a generated PDF now carries an accessible name.** A PDF/UA
  checker reported 13 text-alternative failures on a 0.12.0 deck. All 13 were
  the `<figure>` elements: Chromium tags `<figure>` itself as `/Figure`, HTML
  has no `alt` attribute on `<figure>` for it to carry, and PDF/UA
  (ISO 14289-1) requires `/Alt` or `/ActualText` on every one. The `<img>`
  inside each wrapper was named correctly, which is why the count was 21
  `/Figure`, 8 named, 13 not.

  Nothing in the HTML was wrong, and that is the point. WCAG has no equivalent
  rule, so `make check-access` passed, the test suite passed, and a five-way CI
  matrix passed. The two standards disagree here, and only one of them was
  being checked.

  Four candidate fixes were measured in a built PDF's tag tree rather than
  argued about, because three of them look equally correct written down:

  |                                 | result                                  |
  | ------------------------------- | --------------------------------------- |
  | `<figcaption>` alone, no ARIA   | outer `/Figure` still unnamed — the bug |
  | `aria-label` on `<figure>`      | `/Alt` = the label                      |
  | `aria-labelledby` → the caption | `/Alt` = the caption's text             |
  | `role="none"` on `<figure>`     | wrapper not tagged **— do not use**     |

  `role="none"` is the trap. It removes the unnamed `/Figure`, so it clears the
  failure — and a mermaid figure's `<svg>` is not tagged either, so the whole
  diagram leaves the structure tree rather than being named. It reads as a fix
  and is a deletion.

  A new `figure-name-filter.lua` sets `aria-labelledby` on every figure,
  pointing at its own caption, so the name is derived from text already on the
  page rather than copied into an attribute that can drift from the caption
  beside it. It runs after `mermaid-figure-filter.lua`, which is what turns a
  mermaid block into a figure in the first place.

- **Mermaid diagrams are named in HTML too, not just in the PDF.** The drawn
  diagram is an `<svg>` full of shapes and loose label text with no name of its
  own; a screen reader walked into it and read the node labels as stray words.
  The container now takes `role="img"` and the caption as its `aria-label`.
  In the PDF this promotes the diagram to a named `/Figure` of its own, so the
  diagram is in the structure tree instead of absent from it.

- **A mermaid caption is one paragraph again, not one per word.**
  `pandoc.Caption` takes blocks first and was being handed inlines, so pandoc
  coerced the list and every `Str` and `Space` became a block: one
  `<figcaption>` holding four of them. Found while wiring the fix above, which
  reads the caption's structure.

- **A mermaid block with no caption, or an empty one, is now named too.** The
  default caption ("Diagram") was used but never written back to the block, so
  `data-caption` -- which the page script reads to name the drawn diagram --
  existed only when the author had written a caption. A captionless block got a
  named `<figure>` around an anonymous diagram.

  `caption=""` was worse: in Lua the empty string is truthy, so the existing
  `or 'Diagram'` default never fired for it, and the figure reached the page
  with an empty `<figcaption>` and no accessible name at all -- the exact
  PDF/UA failure this release removes, still reachable through the most natural
  way to ask for no caption. Both were found by review after the fix above was
  written, and both are now normalized in one place that every consumer reads.

- **No change for table headers, deliberately.** The ticket recorded all 20
  `/TH` on a real deck reaching the PDF with `/Scope: None`, and a Lua filter
  was planned to add `scope="col"`. Measured on that same PDF, every one of
  them already carries `/Scope /Column`: Chromium infers it from `<thead>`, and
  infers `/Row` for a row-header table too. So nothing was added. What was
  added is a test pinning it, because the inference depends on table shape and
  would be lost silently by a table with no `<thead>`.

## 0.14.0

- **A document that draws no diagram no longer carries the mermaid bundle.**
  Every generated page inlined `mermaid.min.js` unconditionally. It is 3.5 MB.
  A handout with no diagram in it weighed 5.19 MB, of which 3.57 MB was a
  library the page never called -- and none of the handouts in the course
  repository this tool was written for draw diagrams. They are all in that
  state. Dropping it takes such a page to 1.62 MB, a 69% cut.

  This is not a build-time fix and was not shipped as one. Measured both ways,
  alternating, on a real package:

  ```
  with the bundle     1776 ms / 1738 ms
  without             1756 ms / 1762 ms
  ```

  V8 does not charge for a script it never runs. What the weight cost was the
  point of the file: `embed-images.lua` and the asset inlining exist so a
  handout is one self-contained file somebody can email, and a 5 MB attachment
  that should be 1.6 MB is three times harder to send.

  `mermaid-figure-filter.lua` now sets a `has-mermaid` metadata flag and the
  template gates the bundle on it. The flag covers every route to a `.mermaid`
  element, not just the fenced code block the filter already rewrote -- the
  page script matches the class on any element, and four other things produce
  one. `<div class="mermaid">` and a fenced `::: {.mermaid}` both arrive as a
  `Div`; `<span class="mermaid">` arrives as a `Span`, because pandoc's
  `native_spans` and `native_divs` extensions parse those into the AST rather
  than leaving them raw. Only the tags pandoc has no native element for --
  `<pre class="mermaid">`, a custom element -- stay raw, as a `RawBlock` or a
  `RawInline`. Those two are matched by a plain substring search that
  deliberately over-matches: a false positive inlines a bundle the page does
  not need, which is what every page did before this release, while a false
  negative renders a diagram blank.

  The `Span` case is worth calling out because the markdown and the rendered
  HTML both say `<span`, so it reads like raw HTML from either end of the
  pipeline; only the AST in between disagrees. It was found by a review comment
  pointing at the right gap with the wrong mechanism, and settled by
  instrumenting the filter rather than by reasoning about it.

  The driver script is **not** gated, only the bundle. It no-ops on a page with
  no `.mermaid` element, and it is what sets `window.__mermaidReady` -- which
  `html-to-pdf.js` blocks on. Gating it too would not fail a PDF build, it
  would hang one for the full two-minute timeout first. `renderMermaid()` also
  now bails out with a console error rather than throwing if it somehow finds a
  diagram with no bundle loaded, for the same reason: an uncaught throw escapes
  the async function and `__mermaidReady` is never set.

  No action needed in a consuming repository. Re-run `stencil gen` and rebuild;
  pages with diagrams are byte-for-byte unchanged.

## 0.13.0

- **The header's PDF text layer no longer jams words together.** Extracted
  text read `IssuedSep 05`, `DueSep 12`, `Lovelace·Grace Hopper` and
  `001·Fall 2026` on a 0.12.0 build. Anyone copying from a PDF, or reading one
  with a screen reader, got run-on words in the header; the rendered page
  looked perfect, and so did the HTML, which has had real spaces there since
  0.11.0.

  The measured rule, which is narrower than it looks: **a whitespace-only text
  node sitting between two inline boxes never reaches the PDF text layer.**
  Chromium paints the advance and emits no glyph. Substituting a non-breaking
  space changes nothing -- a lone `&#160;` between two boxes is dropped exactly
  the same way. What survives is a space inside a text run that also carries a
  printing character, which is why `.doc-fact-sep`'s `&#160;&middot;&#160;`
  already came through and why `Points&#160;50 pts` did.

  So every gap moved inside its neighbour: `<span class="doc-label">Issued&#160;</span>`,
  and each author carries its own trailing space with the middot separator
  taking the other side. A `<wbr>` after each separator gives back the line
  break the non-breaking pair removes -- six names still wrap between names,
  on screen and in print, rather than overflowing.

- **The space between two authors is now in the accessibility tree as well.**
  The separator is `aria-hidden`, which removes its whole subtree, so a space
  parked inside it would reach the PDF and not a screen reader -- the same
  defect wearing the other coat. Chromium's accessibility tree for the 0.12.0
  markup read `Author`, `Ada Lovelace`, `Grace Hopper` as adjacent runs with
  no whitespace anywhere between them; it now reads `Author&nbsp;`,
  `Ada Lovelace&nbsp;`, `Grace Hopper&nbsp;`.

- **Something in this repository finally looks at a PDF.** `make check-access`
  is pa11y, an HTML checker, so until now "the PDFs pass WCAG" was unverified
  rather than verified-good. `tests/test_pdf.py` now asserts on a real
  generated PDF: that both headers extract as words, and that the document is
  tagged (`/StructTreeRoot`, `/MarkInfo /Marked true`), declares its language,
  and sets `/ViewerPreferences /DisplayDocTitle` so a reader announces the
  title rather than the file name. Those four held before this release only
  because they are Puppeteer's defaults; `html-to-pdf.js` now pins
  `tagged: true` explicitly, and turning it off fails the test.

- **What changed on the page.** The context separator's gap is held where it
  was -- 0.4rem of margin a side became 0.12em plus the non-breaking space now
  in the content, measured glyph-to-glyph at 14.17px before and 14.22px after
  at letter print width. The label gap is the one real change: it tightens by
  0.39-0.47px, because a non-breaking space rendered in the label's semibold
  face is slightly narrower than the regular-weight word space it replaced.
  That is 5% of the gap, and correcting it per font would over-fit to this
  one. Nothing else moves.

- **Copy-paste note.** The header's gaps are U+00A0, so text pasted out of a
  handout carries non-breaking spaces where it used to carry ordinary ones.
  Most tools treat them as spaces; some -- shells, diff tools, form
  validators -- do not. This was already true of `50&nbsp;pts` since 0.11.0
  and is now true of the labels and the author separators.

- **Knowingly left alone.** The rule above applies to any painted gap, and
  only the header was fixed. `.side-by-side` and `.columns` put two blocks of
  text on one baseline behind a `gap` with no character behind it, and the
  facts line's accessibility boundary comes from `.doc-facts` being
  `display: flex` -- its children blockify -- rather than from a character.
  Both are recorded rather than repaired here.

- `pypdf` is now bounded above. The suite asserts on extracted text, and
  pypdf's space and line-break insertion is a layout heuristic that has
  changed across majors.

## 0.12.0

- **A light/dark/system theme control on every HTML page**, top right on a
  document and in the toolbar on a deck. Three segments in a `radiogroup`, so
  all three states are visible and the pressed one says which is active. The
  choice persists per storage origin; over `file://` browsers disagree about
  what that means, so it may or may not follow you between handouts -- serve
  the folder over HTTP if that matters.

  The theme resolves in a synchronous inline script ahead of every stylesheet.
  Anything deferred paints light first, which is a white flash for a dark
  reader on every load. `data-theme` carries the resolved value the stylesheet
  reads and `data-theme-pref` the preference the control shows; they differ
  whenever the preference is "system".

- **PDFs are light by construction, not by configuration.** Every colour in
  both stylesheets is a token, and every dark value lives in one `@media screen` block. Print does not match `@media screen`, so the light `:root`
  values are the only ones a printer sees — no second copy of the palette to
  keep in sync, and it holds for a browser's own Ctrl+P as well as for
  Puppeteer. Measured with `data-theme="dark"` still set: screen paints
  `rgb(27,31,39)`, print paints `rgb(255,255,255)`.

  Bootstrap's `data-bs-theme` is deliberately unused. Its dark block sits
  inside the vendored CSS and is *not* wrapped, so that attribute would put a
  second dark mechanism in the page that print cannot switch off. The
  variables these pages surface are mapped from stencil's own tokens instead.

- **Diagrams follow the theme.** Mermaid bakes its colours into the SVG at
  draw time, so a theme change redraws them, with `themeVariables` read off
  the live tokens rather than a second palette that would drift.
  `__mermaidReady` is still set once and never cleared — `html-to-pdf.js`
  blocks on it, and a PDF build never changes theme.

- **`make check-access` runs pa11y in both themes**, clicking the real control
  rather than injecting state. It found three failures the first time it ran:
  `--accent` was serving as both ink and fill, so dark turned the deck's
  Present button white-on-pale at 2.35:1; `--text-faint` had been 2.60:1 for
  as long as it was `::before` content the checker could not see; and links
  stayed at Bootstrap's light-mode blue because 5.3 resolves them through
  `--bs-link-color-rgb`, not `--bs-link-color`.

- **The header reads as a header.** The points badge is gone from beside the
  title — it read as UI chrome on a document that has none — and Points joins
  Issued and Due on one line spread across the width. The byline is two
  stacked lines, authors above facts, so a team's `author:` list has room.
  Each name is its own element, so a list wraps between names and never
  mid-name. The subtitle is italic and sits tight to the title. `program`,
  `section` and `term` share one line, the section attaching to its program
  with a full stop — `CS 425.001 · Fall 2026`.

  The gaps in that header are real characters. A flex `gap` paints space and
  leaves nothing behind, so the header copied out of the page read
  "AuthorAda Lovelace". The same is not yet true of the PDF text layer
  (stn-40n).

## 0.11.0

- **`points` renders as a badge beside the title.** `points: 50` reads `50 pts`
  and `points: 1` reads `1 pt`; the plural is decided in the lua filter, which
  can compare a value where a pandoc template cannot. A non-numeric value
  renders verbatim, so `points: "extra credit"` works.

  This replaces a `subtitle: "Points: 50"` convention, which overloaded a
  presentation field with a data field so that nothing could query or validate
  it.

- **`due` is a new key, and `date` now renders as `Issued`.** Both take
  `yyyy-mm-dd` or `yyyy-mm-ddThh:mm` and render as `Due Sep 12 · 23:59`, with
  the time shown only when one was written. Two bare dates in one column cannot
  be told apart, which is why the build stamp grew a label it never needed while
  it was the only date there.

  Neither prints the year — a handout is read inside a term the reader already
  knows. Unprinted rather than lost: each date is wrapped in a `<time datetime>`
  carrying the full ISO string.

  `date` accepted any string before this and now accepts only the grammar above,
  a behaviour change with nothing in tree relying on the old latitude.
  Validation gates on shape, then field ranges, then the calendar, so the
  message names what actually broke rather than restating the grammar every
  time. The calendar gate is an `os.time` round-trip rather than a
  days-in-month table: `os.time` normalizes a day the month does not have, so
  comparing the fields back is what detects it, and the leap rules come from
  the platform `mktime` instead of a century rule here that nothing would
  exercise until 2100.

- **The document header is a grid, and its source order is its reading order.**
  The byline has always sat under the title on screen; in source order
  `.doc-context` came between the subtitle and the author, which is the order a
  screen reader announces and `pdftotext` extracts. Nothing moves visually. On a
  narrow viewport the header now stacks identity, byline, context.

## 0.10.3

- **Stopped naming a private consumer.** AGENTS.md, four comments in
  `generate.py` and three test files described what one course repository asks
  of stencil, by name: which of its configs set which custom keys, that its
  shared `Makefile.j2` composes stencil's partials, what its
  `front_controller` defaults to.

  A tool has no business carrying knowledge of a private consumer, and the
  copy it carried had already drifted -- it said three configs pointing at one
  templates directory, and there are four. That is what documentation in the
  wrong repository does: nobody working in either place is looking at it when
  the fact changes.

  Every reason survives, stated generally: a project *may* point several
  configs at one templates directory, a consumer's composition *may* include
  stencil's partials, a key defaulted to `False` *does* put the literal
  "False" where a filename belonged. The consuming repository now records its
  own arrangement in its own AGENTS.md.

## 0.10.2

- **Removed `copy_files` from STENCIL.md.** It described a per-package field
  for copying static files into a package, and no code has ever implemented
  it -- `grep -rn copy stencil/*.py` returns nothing. Setting it produced no
  files and no error, which is the exact failure the config validation exists
  to prevent for `template_env` keys and `when:` names: a key that reads and
  writes nothing, silently.

  Deleted rather than implemented. Nothing asked for it, and the one thing
  that wanted to copy a file -- a config-level `brand` logo, in 0.10.0 --
  needed to copy implicitly and register the copy in `.gitignore`, which a
  general opt-in list would not have given it. If a second caller ever turns
  up, that is the point to build the general thing, with two real uses to
  shape it (stn-4iv).

## 0.10.1

- **The two config-brand fallbacks were emitted on one line.** The Jinja
  environment sets `trim_blocks`, which eats the newline after a block tag, so
  the `{% endif %}` ending the first declaration pulled the second up beside
  it. With neither configured that produced `= nillocal CONFIG_BRAND_ALT`,
  where `nillocal` lexes as a single identifier -- so `CONFIG_BRAND` read an
  undefined global and `CONFIG_BRAND_ALT` became a global rather than a local.

  Both still evaluated to nil and every page still rendered, which is why the
  whole test suite passed over it. Inline conditionals now, which no block tag
  can trim, and a test asserts the two are separate `local` statements.

## 0.10.0

`brand` can be set once for a project instead of in every document.

- **`brand` and `brand-alt` in `.config.yaml`**, config-wide or per package,
  resolved the way `lang` is: package first, then config-wide, and front matter
  over both. The default only fills the key in when a document left it out,
  after which it is treated exactly as though the document had written it --
  which is what keeps a configured brand and a written one from drifting into
  two behaviours.

  A package that names its own brand does **not** inherit the config's
  `brand-alt`. "This logo, no alt yet" is what that means, and inheriting there
  would label one logo with another's name.

- **A configured logo is copied into every package that renders markdown**,
  without being asked to, and the copies are added to the managed `.gitignore`.
  The folders stencil generates are routinely handed to someone as a project of
  their own, separate from the repository that produced them -- so a logo
  living only next to `.config.yaml` would leave the recipient with a document
  referring to a file they were never given. A copy per package is the cost of
  each folder standing alone, and the filter names the copy rather than the
  source path so nothing looks outside the folder.

  Paths are resolved relative to the config file's directory. A config-level
  logo with no `brand-alt`, or one that is not a file, fails at `stencil gen`
  rather than at render -- a config mistake should reach whoever ran the
  generator, not whoever builds a document three packages away.

## 0.9.0

A document can carry the mark of what it belongs to.

- **`brand` front matter, a name or a logo.** It renders at the top of the
  header's right-hand column, above `program`, pushing the rest of the context
  and the byline down. On a deck it sits above the title slide's context line.

  The value decides which it is, rather than a second key: a `file://` prefix
  or an image extension makes it a picture, anything else is a name. So
  `St. Louis U.` stays text.

  A picture is inlined as a `data:` URI like any other image. `file://img/logo.svg`
  is rewritten to `img/logo.svg` before it is resolved, because
  `embed-images.lua` classes anything matching `scheme://` as remote and leaves
  it alone — a `file://` URI would have been the single spelling that never got
  bundled, leaving the page carrying a path and `make pdf` failing on an asset
  it could not fetch.

  A logo is capped on both axes and never distorted -- whichever cap binds
  first, the other dimension scales with it. Verified by measurement rather
  than by reading the spec: a 900x60 wordmark renders 223.95x14.93 and a
  50x600 crest renders 3.66x44, each at its exact natural ratio. A test guards
  the `width: auto` / `height: auto` pair that makes that true, because pinning
  either one turns the cap into a stretch silently, and only for whichever
  logo happens to be the wrong shape.

- **`brand-alt` is required when `brand` is a picture**, and the build fails
  naming the key when it is missing. Deliberately not defaulted to `alt=""`: a
  logo is frequently the only thing naming the institution on the page, so an
  empty alt drops that for a screen reader, and `make check-access` runs pa11y
  at WCAG 2.1 AA. A name needs none — it is already text.

## 0.8.0

Ordered task lists get the styling bullet ones have had.

- **`1. [ ]` no longer renders with the box jammed against the text.** Pandoc
  puts `class="task-list"` on a `<ul>` only; an ordered task list arrives as a
  bare `<ol type="1">` carrying the identical
  `<label><input type=checkbox>` items. Every rule from 0.4.0's task-list work
  was spelled `ul.task-list`, so an ordered list matched none of them — no gap
  beside the control, no accent tint, and a checked box printed empty because
  the print opt-out was scoped the same way.

  The two are still styled differently, on purpose. A bullet list's disc is
  decoration, so the checkbox replaces it and the text hangs off it. An ordered
  list's number is content the author asked for, so it stays and the box takes
  room beside it: `1. [x] done`, not `[x] done`.

  The gap is on both sides of the control. With only the reported side fixed,
  the box lands against the ordinal instead and the same complaint reads
  `1.[ ] text`.

## 0.7.0

A document says what it belongs to, and `show_date: no` means no.

- **`program`, `section` and `term` front matter.** The institutional context a
  handout carries: what it belongs to, which instance of that, and when. A
  document renders them top right, opposite the title; a deck puts them on one
  line above it. The names avoid `course`/`semester` deliberately — they read
  the same to an instructor and to anyone running a training program.

- **The document header is two columns.** Title and subtitle on the left of the
  first row with the context opposite them, author on the second with the date
  opposite. A file setting everything costs two lines instead of five. Below
  roughly 640px it collapses back to one left-aligned stack.

  The date moved out of `.doc-meta` and into `.doc-date`, so the middot that
  joined it to the author list is gone.

- **`show_date: yes` stamps the build date.** And `show_date: no` withholds it,
  which is worth stating because it did not used to. Pandoc reads YAML 1.2,
  where `true` and `false` are the only booleans, so `no` arrived at the
  template as the *string* `"no"` — as truthy as `"yes"`. The new
  `frontmatter-filter.lua` settles it before any template asks. An explicit
  `date:` still wins; the stamp is the build host's day, not the container's.

- **The package `name` is no longer injected as a document's `course`.** It was
  reaching pandoc as `--metadata course=`, which overrides front matter, so an
  author who set `course:` had it silently discarded — and a package name was
  never a course in the first place. `name` is a `stencil --list` label again.
  This is the one behavior change to look at when upgrading: a deck that showed
  its package name above the title now shows nothing there until it sets
  `program:`.

- **A deck's title no longer prints at slide-heading size** (stn-tum). The title
  slide's `.deck-title` is an `h1`, and `.slide > h1:first-child` outscores
  `.slide--title .deck-title` on specificity — so the title was sized as a
  section heading exactly when no context line preceded it. Adding a `course:`
  pushed a `<p>` in front and gave the title its size back, which is why it
  looked like a print-only bug. Both media now exclude the title slide.

- **`lang` and `dir` front matter, with a config-wide default.** Both templates
  hardcoded `<html lang="en">`, so a page written in Spanish asserted it was
  English — a screen reader pronounced it with English phonetics and sounded
  confidently wrong rather than obviously broken. Resolved narrowest first: a
  document's `lang:`, then a package's or the config's, then `en`. The
  attribute is never omitted, so nothing that renders today changes.

  `dir` is front matter only and emitted only when set. There is no
  config-level spelling for it because a package's `dir:` is already its output
  subdirectory, and giving one key two meanings is the mistake this release
  undoes elsewhere.

- **AUTHORING.md lists the keys stencil does *not* render.** `abstract`,
  `keywords`, `lang`, `toc` and the rest parse fine and do nothing, which reads
  a lot like working.

## 0.6.0

One type scale instead of two, and a way to ask which stencil you are running.

- **Print rescales the root rather than restating every size.** Sizes are in
  `rem` throughout, and a `rem` is the root font size, so `@media print` now
  sets `html { font-size: 9.78pt }` once and the whole scale follows. 9.78pt is
  what puts body text, at `1.125rem`, on the 11pt it has always printed at.

  This removes the defect class 0.5.0 fixed three instances of. There is no
  longer a second list of sizes that can fall out of step with the first, and a
  test now fails if print restates a size the shared scale already gives.

  Two visible consequences. Print inherits the screen proportions, so a
  document's title prints at 21.5pt rather than 18pt. And the root carries
  Bootstrap's `rem`-based padding and margins with it, so spacing tightens by
  about 18% and documents get shorter — a 12-page handout measured 11 pages.
  Everything else lands within ~2% of 0.5.0.

- **`stencil version`.** Prints the installed version, and answers before
  reading a config, because the question is usually asked when an install is
  suspect. The version now lives in `stencil/__init__.py` with `pyproject.toml`
  reading it, so the module and the installed distribution cannot disagree
  except by a stale install — which is the thing the command exists to reveal.
  Four course venvs were found running three different stencils while a
  template fix appeared not to work.

## 0.5.0

Typography fixes in the document stylesheet. Every generated document and deck
renders differently, on screen and on paper, so this is a minor bump.

Three instances of one mistake: a size stated in `pt` inside `@media print` for
some selectors, with the neighbouring ones left on `rem`. A `rem` is measured
against the root font size, not against the element beside it, so the two units
do not compare and the hierarchy inverts once the page is printed.

- **A document's subtitle printed larger than its title.** `.doc-title` scaled
  to 18pt while `.doc-subtitle` kept its screen `1.8rem` — 21.6pt. The student's
  name printed 20% larger than the assignment. Only the PDF was wrong, which is
  why it lasted (`stn-09g`).
- **The printed heading scale inverted at `h4`.** `h1`, `h2` and `h3` had `pt`
  sizes; `h4`, `h5` and `h6` did not, so they kept Bootstrap's `rem`. Measured
  off a real PDF: `h1` 21.3, `h2` 18.7, `h3` 16.0, then `h4` 22.3 — larger than
  `h1` and just under the title.
- **On screen a section heading outranked the document title.** stencil set no
  size for `h1`–`h6` at all, so they inherited Bootstrap's viewport-relative
  scale, `calc(1.375rem + 1.5vw)`. `h1` measured 39.4px against the title's
  35.2px, and resized with the window while the title stayed put.

The subtitle is also no longer bold: at `1.8rem`/700 under a `2.2rem`/700 title
it read as a second title rather than a subordinate line, and being the longer
of the two it took the eye. It is now `1.4rem`/400.

Headings now state their own sizes on screen and in print, stepping down
monotonically under the title in both, with the screen steps tracking the print
block's ratios against body text.

Known and not fixed: a deck's title prints at the same size as a slide heading,
and whether it does depends on an unrelated frontmatter key (`stn-tum`).

## 0.4.0

An undefined name in a template is now an error rather than the empty string,
which is a behaviour change for anyone whose config or templates were relying
on the old silence. No generated output changes: every package of six consumer
configs was generated before and after, 530 files, byte-identical.

- **A consumer's composition template is a checked interface.** Overriding
  `Makefile.j2` or `docker-compose.yml.j2` and including stencil's partials is
  the intended way to extend a build, and the keys those partials read are now
  recorded in `tests/test_template_contract.py`. Stencil's own suite fails when
  that set changes, and `StrictUndefined` makes a stale composition fail the
  consumer's `stencil gen` instead of emitting a Makefile with a recipe
  missing.
- **Custom keys can be declared.** A config-level `template_env` declares a key
  and supplies the value every package gets unless its own `template_env`
  overrides it. This is what lets one `templates_dir` serve several configs
  that each set a different subset of the flags their shared templates read.
- **Configuration mistakes are rejected instead of ignored.** A `when:` naming
  a key that is neither derived nor declared anywhere, and a `template_env` key
  whose name appears in no template, both now fail with the key named. Each was
  previously silent: the guarded template was skipped for every package, or the
  key sat there doing nothing.

Upgrading: if `stencil gen` now reports an undefined key, declare it in the
config-level `template_env` rather than setting it to `false` on every package
— a key some package sets stays undefined for the ones that do not, so
`{{ key | default('x') }}` keeps working, and a concrete `false` would break it.

Removed: `TEMPLATE-PACKS.md`, a design for packaging scaffolding as a
dependency. It recommended against building the thing it described, and the
reasoning is preserved in `stn-zza` and in git history.

## 0.3.0

The first tagged release. Not a set of changes so much as a name for what the
tool already does, so "the stencil that built this handout" is answerable.

What is in it:

- **Scaffolding generator.** Jinja2 templates rendered into per-package output
  directories from a YAML config, with a templates search path — every
  configured `templates_dir`, then the bundled set, first match wins — so a
  consuming project overrides one template without vendoring all of them.
- **Documents and decks.** Two pandoc HTML templates over shared partials.
  Decks are grouped by `slide-sections.lua` and CSS rather than by pandoc's
  slide-show support, which does not apply here.
- **A PDF stage.** `make pdf` prints the generated HTML through headless
  Chromium, with the print stylesheet as the page geometry rather than as
  decoration.
- **Self-contained output.** Bootstrap, highlight.js, Mermaid and the webfonts
  are vendored and inlined at `stencil gen` time, so a handout makes no network
  request and a build does not depend on a CDN being up.
- **Hidden variants.** `WITH=hidden` builds an answer key from the same source,
  with the filter order arranged so citeproc cannot leak a hidden citation's
  source into the visible build's reference list.
- **A pinned toolchain.** `docker.io/pandoc/core:3.10.0.0`, asserted by a test
  rather than trusted, so a pandoc release cannot change rendered output or
  break CI on a commit that changed nothing.
- **An authoring contract.** `AUTHORING.md` for the person writing markdown,
  `STENCIL.md` for the person configuring a package.
- **A test suite and CI.** Unit assertions on the rendered scaffolding, plus
  container-backed integration tests behind an `integration` marker that skip
  rather than fail when there is no runtime.

Fixed just before the tag:

- A doc package built only from `package_sources` generated five shared page
  files that `clean` and the managed `.gitignore` section could not see, and a
  `pkg` target naming a pandoc template that was never written (`stn-633`).
