# Authoring Guide

How to write the markdown. This guide is for the person writing a lecture, a set of notes, or an
assignment; [STENCIL.md](STENCIL.md) is for the person configuring the package that builds it.

Everything here goes through the same pipeline: prettier formats the markdown, pandoc parses it as
[pandoc's `markdown`](#the-dialect) and writes a standalone HTML file, and Lua filters handle hidden
content, diagram captions, inlined images and — for a deck — the slide breaks. The only choice that
changes the output shape is whether the file is listed under `docs:` or `slides:`.

## Which one am I writing?

| Writing this                                        | Use      | You get                                     |
| --------------------------------------------------- | -------- | ------------------------------------------- |
| Syllabus, classroom notes, assignment, handout, lab | `docs`   | One flowing page, read by scrolling         |
| A lecture you stand up and present                  | `slides` | A stack of slide cards, plus a present mode |

A markdown file belongs to exactly one list. Listing it in both is a configuration error and
stencil refuses to generate the package.

```yaml
packages:
  week3:
    name: "Week 3"
    dir: week3
    package_type: doc
    docs: [lab-setup.md, assignment.md]
    slides: [lecture.md]
```

Then, in the package directory:

```bash
make doc          # builds everything the package declares
make slide        # builds only the decks
make pdf          # prints the built HTML to PDF
make format-md    # runs prettier over the markdown
make check-access # pa11y accessibility check over the built HTML
```

`make doc` depends on `make slide`, so the one command always produces everything. Both targets run
`format-md` first, which matters — see [Fenced divs and prettier](#fenced-divs-and-prettier). It is
the only formatter to point at these files; [mdformat breaks
them](#do-not-run-mdformat-over-this-markdown).
`make pdf` depends on `make doc`, so it always prints the build you just made.

## The dialect

Both kinds of file are written in **pandoc's `markdown`** — pandoc's own extended dialect, not
CommonMark and not GitHub-Flavored Markdown. Nothing in the pipeline passes `--from`, so pandoc
infers the format from the `.md` extension and turns on its full default extension set. The
authoritative reference is [pandoc's manual](https://pandoc.org/MANUAL.html#pandocs-markdown); these
are the extensions the rest of this guide actually leans on.

| Extension                | What it buys you                                                                                            |
| ------------------------ | ----------------------------------------------------------------------------------------------------------- |
| `fenced_divs`            | `::: {.hidden}`, `::: columns`, `::: takeaway` — every fence below                                          |
| `bracketed_spans`        | `[text]{.class}` when you need a class on an inline run                                                     |
| `header_attributes`      | `## Setup {#custom-id}` to pin a heading's id                                                               |
| `auto_identifiers`       | Every other heading gets an id derived from its text                                                        |
| `fenced_code_attributes` | ```` ```{.mermaid caption="…"} ````                                                                         |
| `pipe_tables`            | The ordinary `\| a \| b \|` table                                                                           |
| `tex_math_dollars`       | `$L = \lambda W$`                                                                                           |
| `raw_html`               | The `<nav>` block that becomes a tab bar                                                                    |
| `citations`              | `[@key]` and `@key` — see [Citations](#citations)                                                           |
| `footnotes`              | `[^1]` and its definition                                                                                   |
| `definition_lists`       | A term, then a `:`-prefixed definition                                                                      |
| `task_lists`             | `- [ ]` and `- [x]` — the checkbox replaces the bullet; `1. [ ]` keeps its number and puts the box after it |
| `implicit_figures`       | An image alone in a paragraph becomes a `<figure>`, alt text as its caption                                 |
| `smart`                  | Straight quotes and `--` become typographic quotes and dashes                                               |

**There is one dialect, not two.** A deck and a document parse identically — same parser, same
extensions. What differs is everything downstream of the parse: which pandoc HTML template wraps the
result, which Lua filters run, and therefore which constructs mean anything. That is what the rest of
this guide describes.

A few pandoc features are *off*, because the pipeline never enables them: a generated table of
contents (`--toc`) and section numbering (`--number-sections`).

## What works where

| Construct                                                  | Document                | Deck                                                      |
| ---------------------------------------------------------- | ----------------------- | --------------------------------------------------------- |
| Headings, lists, tables, code, footnotes, definition lists | yes                     | yes — a slide-level heading also breaks                   |
| `$math$` and `$$math$$`, rendered as MathML                | yes                     | yes                                                       |
| Mermaid blocks with `caption=`                             | yes                     | yes, with figure height capped                            |
| Local images inlined as base64                             | yes                     | yes                                                       |
| Blockquotes as callout cards                               | yes                     | yes                                                       |
| Citations and a generated reference list                   | yes                     | yes, but give `#refs` its own slide                       |
| `::: {.hidden}` with `WITH=hidden`                         | yes                     | yes — see [Presenter-only slides](#presenter-only-slides) |
| `::: {.side-by-side}`                                      | yes                     | yes                                                       |
| `<nav class="nav-tabs">` tabbed sections                   | yes                     | no — a deck already paginates                             |
| `---` horizontal rule                                      | renders as a rule       | starts a new slide                                        |
| `::: columns` (with `.wide-left` / `.wide-right`)          | no — plain unstyled div | yes                                                       |
| `::: lead-in`, `::: takeaway`, `::: center`                | no — plain unstyled div | yes                                                       |
| `slide-level:` front matter                                | ignored                 | sets the breaking level                                   |
| `author:` / `date:` front matter                           | byline in the header    | byline on the title slide                                 |
| `program:` / `section:` / `term:` front matter             | top right of the header | above the title on the title slide                        |
| Present mode, one-slide-per-page printing                  | no                      | yes                                                       |

The "no — plain unstyled div" rows are the trap worth remembering: the fences still *parse* in a
document, they just come out as a bare `<div>` with no styling attached, so the text lands on the page
looking like an ordinary paragraph rather than failing loudly.

## What both kinds share

### Front matter

```markdown
---
title: "Flow, Limits, and Specifications"
subtitle: "Kanban and its neighbors"
author: Ada Lovelace
date: 2026-09-02
---
```

A document renders these as a page header; a deck renders them as a generated title slide. Every key
the pipeline reads, and what each one does on each side:

| Key           | In a document                                   | In a deck                                       |
| ------------- | ----------------------------------------------- | ----------------------------------------------- |
| `title`       | Page header, and the browser tab                | Title slide, and the browser tab                |
| `subtitle`    | Under the title                                 | Under the title on the title slide              |
| `brand`       | Top right, above `program`                      | Above the title on the title slide              |
| `brand-alt`   | Alt text; **required** when `brand` is an image | Same                                            |
| `program`     | Top right, opposite the title                   | Above the title, and prefixes the browser tab   |
| `section`     | Top right, after `program`                      | Above the title, after `program`                |
| `term`        | Top right, its own line                         | Above the title, after `section`                |
| `points`      | Badge beside the title                          | Badge beside the title on the title slide       |
| `author`      | Second row, under the title                     | Byline on the title slide                       |
| `date`        | Second row, labelled `Issued`                   | Byline, labelled `Issued`, after the author     |
| `due`         | Second row, under `Issued`                      | Byline, after `Issued`                          |
| `show_date`   | Stamps the build date as `date`                 | Same                                            |
| `lang`        | `<html lang>` (default `en`)                    | Same                                            |
| `dir`         | `<html dir>`, only when set                     | Same                                            |
| `slide-level` | Ignored                                         | Heading level that starts a slide (default `2`) |

`author` takes one name or a list of them:

```markdown
---
title: "Flow, Limits, and Specifications"
author:
  - Ada Lovelace
  - Grace Hopper
date: 2026-09-02
---
```

Names are joined with `·`. The byline is part of the title header, so a file with an `author` but no
`title` gets no header at all — same rule a deck follows for its title slide. Leaving a key blank is
the same as leaving it out: `program:` with nothing after it renders nothing, not an empty line.

#### Where a document puts them

A document's header is two rows, each with identity on the left and context on the right, so a file
that sets everything costs two lines rather than five:

```text
Sprint Report                          CS 425/499 · 001
Points: 50                                 Spring 2026
Ada Lovelace · Grace Hopper                 2026-09-05
────────────────────────────────────────────────────
```

Below roughly 640px the two columns collapse into one left-aligned stack. A deck has the width to
keep `program · section · term` on a single line above the title.

#### `brand`

What the document belongs to, above the program it is part of. Either a name or a picture — the value
decides which, not a second key:

```markdown
---
title: "Sprint Report"
brand: "Southern Illinois University"   # a name, rendered as text
program: "CS 425"
---
```

```markdown
---
title: "Sprint Report"
brand: "file://img/logo.svg"            # a picture
brand-alt: "Southern Illinois University"
program: "CS 425"
---
```

A value is a picture when it starts with `file://` or ends in an image extension (`.png`, `.jpg`,
`.jpeg`, `.gif`, `.svg`, `.webp`, `.avif`). Anything else is a name, so `St. Louis U.` stays text.

The picture is **inlined** as a `data:` URI like any other image, so the page stays one
self-contained file. `file://img/logo.svg` and `img/logo.svg` behave identically — the scheme is
stripped before the image is resolved, because `embed-images.lua` treats any `scheme://` as remote
and skips it, which would leave the page carrying a path instead of the logo and fail `make pdf`.

A genuinely remote `https://` logo stays a reference and is **not** downloaded, which is the same
position `embed-images.lua` takes on remote figures — it will fail `make pdf`, which does not reach
the network.

A logo is capped on both axes and **never distorted**: whichever dimension hits its cap first, the
other scales with it, so the image keeps its proportions. A 900×60 wordmark renders 224×14.93 and a
50×600 crest renders 3.66×44 — each at its exact natural ratio. Supply whatever shape you have; a very
tall logo simply ends up narrow, because the height cap is what keeps the header from growing.

A whole project or one package can set a brand once, so every document need not repeat it; see
[STENCIL.md](STENCIL.md). A configured logo is **copied into each generated package**, so the folder
stands on its own when it is handed to someone as their own project. Front matter still wins.

`brand-alt` is **required** when `brand` is a picture, and the build fails without it. That is
deliberate rather than defaulting to `alt=""`: a logo is often the only thing naming the institution
on the page, so an empty alt drops it entirely for anyone using a screen reader, and
`make check-access` runs pa11y at WCAG 2.1 AA. A name needs no `brand-alt` — it is already text.

#### `program`, `section`, `term`

The document's institutional context: what it belongs to, which instance of that, and when. The names
are deliberately not `course`/`semester` — they read the same way to an instructor and to anyone
running a training program, and nothing in stencil assumes a university:

```markdown
---
title: "Sprint Report"
program: "CS 425/499"      # or "Engineering Onboarding"
section: "001"             # or "Q3 New Hires"
term: "Spring 2026"        # or "FY2026"
---
```

`program` also prefixes the browser tab: `CS 425/499: Sprint Report`.

#### `points`

What the document is worth, rendered as a badge beside the title:

```yaml
points: 50      # 50 pts
points: 1       # 1 pt
```

The plural follows the number, so a one-point exercise does not read `1 pts`.

A value that is not a number renders exactly as written, which is the escape hatch for anything a
number cannot say:

```yaml
points: "extra credit"
```

Unlike a date there is nothing to validate here — any string is a legible answer to what a document
is worth.

#### `date` and `due`

When the document was issued, and when it is owed. Both take an ISO date, and optionally a 24-hour
time after a `T`:

```yaml
date: 2026-09-01          # Issued Sep 01
date: 2026-09-01T21:45    # Issued Sep 01 · 21:45
due: 2026-09-12           # Due Sep 12
due: 2026-09-12T23:59     # Due Sep 12 · 23:59
```

The time appears only when you write one. `yyyy-mm-dd` and `yyyy-mm-ddThh:mm` are the only shapes
accepted; anything else fails the build rather than rendering something approximate, and so does a
date the calendar does not contain:

```
due: 2026-02-30 is not a real date -- Feb 2026 has 28 days.
```

Neither prints the year. A handout is read inside a term the reader already knows, and `term:` is
usually in the header saying so. The year is unprinted rather than lost: each date is wrapped in a
`<time datetime="2026-09-12T23:59">` carrying the full ISO string, so anything reading the page
rather than looking at it still has it.

Both render in the byline, `Issued` above `Due`. They are labelled because two bare dates in one
column cannot be told apart; the build stamp went unlabelled before `due` existed only because it
was the only date there.

#### `show_date`

Stamps the document with the day it was built, in `YYYY-MM-DD`:

```markdown
---
title: "Sprint Report"
show_date: yes
---
```

`yes`, `true`, `on` and `1` all mean yes; `no`, `false`, `off`, `0`, `none` and a blank value all mean
no, as does leaving the key out. Spelling matters less than the fact that it is settled before the
template sees it — pandoc reads YAML 1.2, where `no` is the *string* `"no"` rather than a boolean, so
without `frontmatter-filter.lua` normalizing it first, `show_date: no` would print a date.

Writing your own `date:` beats `show_date` — a date you wrote is the date you meant — so use one or
the other, not both. Under `make` the stamp is the build machine's day, passed in as `build-date`,
rather than the container's UTC one. Running pandoc directly passes nothing, and the filter falls
back to the date inside the container — UTC — which can be a day off from yours either side of
midnight. Either way it is date-only: the clock reading when the build ran says nothing about the
document.

#### `lang` and `dir`

The language a page claims to be written in, and its text direction:

```markdown
---
title: "Informe de Sprint"
lang: es
---
```

`lang` defaults to `en` and is never omitted — a page with no language is a WCAG 2.1 failure (3.1.1),
and `make check-access` will say so. A whole project or one package can change that default without
every file repeating it; see [STENCIL.md](STENCIL.md). Front matter wins over both.

`dir` is only emitted when you set it. Browsers infer `ltr`, so an attribute saying so adds nothing,
and one saying so wrongly reverses the page. Set it alongside a right-to-left `lang`:

```markdown
---
title: "تقرير"
lang: ar
dir: rtl
---
```

There is no config-level `dir`. A package's `dir:` is already its output subdirectory, and giving one
key two meanings is how the package name ended up being a document's course.

#### Keys that come from the build

`include-<feature>`, which `WITH=` sets — see [Optional content](#optional-content) — and
`build-date`, which the Makefile passes so `show_date` has the build host's day to use.

Citations add four more — `bibliography`, `csl`, `nocite` and `link-citations` — which behave the
same in a document as in a deck; see [Citations](#citations).

#### Keys stencil does not render

Pandoc parses these, and stencil's templates ignore them, so setting one is a silent no-op rather
than an error. Listed because "it parsed" reads a lot like "it worked":

| Key                                                  | What pandoc would normally do with it           |
| ---------------------------------------------------- | ----------------------------------------------- |
| `abstract`, `abstract-title`                         | An abstract block above the body                |
| `keywords`, `description`                            | `<meta>` tags in the head                       |
| `title-prefix`                                       | A prefix on the browser tab                     |
| `toc`, `toc-title`                                   | A table of contents (stencil passes no `--toc`) |
| `institute`, `thanks`                                | Title-block extras on pandoc's own templates    |
| `header-includes`, `include-before`, `include-after` | Raw HTML injected around the body               |
| `css`                                                | Extra stylesheet links                          |

Anything else you put in the front matter is carried along by pandoc and read by nothing.

### Light, dark and system

Every generated page carries a three-way theme control — light, dark, or follow the
operating system. It sits top right on a document and in the toolbar on a deck. Nothing in
the front matter turns it on or off; it is simply there.

The choice is remembered, though how widely depends on the browser. Opened over `http://`,
it applies to every page on that origin. Opened as a `file://` URL — which is how a
generated handout is usually read — browsers disagree: some treat all local files as one
storage origin, so the choice follows you from handout to handout; others partition per
file, or decline to store anything at all. If cross-page persistence matters, serve the
folder over HTTP rather than relying on it.

**PDFs are always light.** Not by a setting that could be got wrong — by construction. Every
dark declaration lives inside an `@media screen` block, and print does not match `@media screen`, so the light values are the only ones a printer or a PDF ever sees. That holds for
`make pdf` and for your browser's own Ctrl+P alike, whatever the page looked like on screen
when you pressed it.

Two consequences worth knowing:

- **Diagrams are redrawn when the theme changes**, because Mermaid bakes its colours into the
  SVG as it draws. Nothing is required of you; a diagram simply follows the page.
- **`make check-access` checks both themes**, clicking the control the way a reader would.
  A colour that only fails in dark is still a failure.

### Optional content

Wrap anything in a `hidden` fenced div and it is dropped from the normal build:

```markdown
::: {.hidden}

Answer: the WIP limit forces the queue to drain before new work enters.

:::
```

```bash
make doc                    # answer omitted        -> assignment.html
make doc WITH=hidden        # answer included       -> assignment-hidden.html
make doc WITH=hidden,draft  # multiple features     -> assignment-hidden-draft.html
```

The feature name lands in the output filename, so the student build and the answer-key build sit
side by side without overwriting each other. `with=hidden` in lowercase works too.

This is the mechanism for answer keys, grading rubrics, solutions, and presenter notes. It applies
only to fenced divs — you cannot hide a single word or list item this way.

### Math

Rendered as MathML, so it needs no JavaScript and prints correctly.

```markdown
Inline $L = \lambda W$, and display:

$$W = \frac{L}{\lambda}$$
```

Prefer this over pasting Unicode symbols: write `$A \subseteq B$`, not `A ⊆ B`.

### Citations

Point `bibliography` at a file next to the markdown and cite by key. Pandoc formats the citation and
builds the reference list for you.

```markdown
---
title: "Flow, Limits, and Specifications"
bibliography: refs.bib
---

Little's Law relates the three [@little1961], and @reinertsen2009 works through the queueing case.

## References

::: {#refs}
:::
```

`[@key]` gives a parenthetical citation, `@key` names the author in the sentence, and `[-@key]`
suppresses the author when you have already said the name. BibTeX, BibLaTeX, CSL-JSON and CSL-YAML
files all work.

| Key              | Does                                                                     |
| ---------------- | ------------------------------------------------------------------------ |
| `bibliography`   | The source file, or a list of them                                       |
| `csl`            | A CSL style file; without one you get Chicago author-date                |
| `nocite`         | Force entries into the list without citing them — `nocite: '@*'` for all |
| `link-citations` | Link each citation to its entry in the reference list                    |

**Put an empty `::: {#refs}` div where you want the list.** Without one, pandoc appends it to the end
of the document — which in a deck means it is swept into whatever slide happens to be last. Give it a
slide of its own:

```markdown
---

## References

::: {#refs}
:::
```

**A key with no entry fails the build.** Pandoc runs with `--fail-if-warnings`, so a mistyped key
stops the build rather than shipping `(**little1961?**)` into the page for someone to notice later.
The same flag catches an unclosed fenced div — see
[Fenced divs and prettier](#fenced-divs-and-prettier). A missing *figure* still does not fail: that
one is reported by `embed-images.lua` and stays visible as a broken image in the page.

**Escape a stray `@` in prose.** Citations are parsed before anything knows what you meant, so a bare
`@word` in running text becomes a broken citation. `` `@media` `` in backticks is safe, and so is
anything in a fenced block or an email address like `you@example.com`, where the `@` follows a
non-space character. Only a free-standing `@word` is at risk; write `\@word` if you need one.

### Code

Fenced code blocks are highlighted in the browser. Bash, JavaScript, Python, and SQL are bundled;
other languages still render as code, just without highlighting.

### Diagrams

Mermaid blocks are rendered client-side and automatically wrapped in a `<figure>`. Always give a
caption — it becomes the `<figcaption>` a screen reader announces, and without one the figure is
labelled just "Diagram".

````markdown
```{.mermaid caption="Requests flow from the client through the API to the database"}
flowchart LR
  client --> api --> db
```
````

### Images and tables

Standard markdown. Images are looked up relative to the markdown file, so keep them in an `images/`
subdirectory next to it.

**Local images are inlined into the HTML as base64.** Stylesheets, scripts and webfonts are inlined
the same way at `stencil gen` time. The generated page is one self-contained file: mail it, upload
it to the LMS, or drop it on a flash drive and every figure, font and diagram renderer travels with
it. Nothing breaks when the `images/` directory is left behind, and `make pdf` does not reach the
network. Readers keep the normal image context menu — right-click still offers Save Image As and
Copy Image — because each figure is a real `<img>` element, not inlined `<svg>` markup. The one
thing a `data:` URI cannot carry is a filename, so the save dialog proposes a generic one rather
than the original basename.

The cost is file size: base64 adds about a third on top of the raw bytes of every figure, the
vendored libraries add a few megabytes (Mermaid is most of that), and every figure is paid for
whether or not the reader scrolls to it. A deck with 400 KB of SVG becomes a ~600 KB page of
images alone, and a few MB with the libraries. That is the right trade for something you hand out,
and the wrong one for a page with fifty photographs, so keep an eye on the total.

Images referenced by URL are left alone. Fetching them at build time would make the build depend on
the network and bake in whatever that URL served that day.

**A figure can have a dark variant.** Put `images/foo-dark.svg` beside `images/foo.svg` and both are
embedded — one shown in light, one in dark. You write the figure exactly as you always did:

```markdown
![Cumulative flow: arrivals outrun departures until the WIP limit lands](images/cfd.svg)
```

There is no new syntax and nothing to set in the front matter. The `-dark` file is found by name or
it is not; a figure with no dark sibling is emitted unchanged and costs nothing. A figure that
*does* have one embeds twice, so a deck full of dark variants roughly doubles that part of its
weight.

This matters because mermaid diagrams already follow the theme — they are drawn in the browser, so
they are redrawn when the palette changes. A static SVG cannot do that, so a deck mixing the two
goes half-themed in dark mode: recoloured diagrams beside white-plate figures. The dark sibling is
how a static figure keeps up.

**The PDF always prints the light one**, whatever the screen was showing. Same guarantee as the rest
of the theme: the rule that *hides* the dark variant lives outside `@media screen`, so print never
sees the dark figure at all.

A deck caps figure height so a diagram cannot push a slide off the page, on screen and in print. A
document does not, so an oversized image will run past the text column — scale it yourself, or use
SVG, which stays sharp at whatever width it lands on.

## Writing a document

Documents are the default and need almost nothing beyond ordinary markdown. Headings nest as deep as
you like, and the page is one continuous scroll.

**Blockquotes render as callout cards** — a bordered, tinted box rather than the usual indented
italics. Use them for the "think about it" prompt, the warning, the aside. Bold text inside a
callout picks up the accent color.

```markdown
> **Before you start:** back up your database. The migration is not reversible.
```

**Side-by-side blocks** put two or three narrow things on one row instead of stacking them — most
often small tables that are meant to be compared. The fence is a flex row that wraps, so it collapses
back to a stack on a narrow screen:

```markdown
::: {.side-by-side}

| id | name |
| -- | ---- |
| 1  | ada  |

| id | role  |
| -- | ----- |
| 1  | admin |

:::
```

**Tabbed sections** are available for documents whose parts are alternatives rather than a sequence
— per-problem solutions, or the same setup written for three operating systems. Write a raw HTML
`nav` whose links point at heading ids, and the page turns it into a tab bar at load:

```markdown
<nav class="nav-tabs">
  <a href="#problem-1">Problem 1</a>
  <a href="#problem-2">Problem 2</a>
</nav>

## Problem 1

...

## Problem 2

...
```

Each linked heading and everything under it moves into its own tab pane. When printing, every pane is
shown and the tab bar is hidden, so a printed copy is complete.

## Writing a slide deck

### If you know pandoc's slide shows, read this first

Stencil does not use them. There is no `-t revealjs`, `-t beamer`, `-t s5` or `-t dzslides` anywhere
in the pipeline. Pandoc writes ordinary HTML; `slide-sections.lua` then groups the blocks into
`<div class="slide">` cards and CSS does the rest. So everything in
[pandoc's "Slide shows" chapter](https://pandoc.org/MANUAL.html#slide-shows) is inapplicable here.
Following it produces markdown that quietly does nothing — or, in the case of speaker notes, prints
the thing you meant to keep to yourself.

| If pandoc's slide docs tell you to…                           | Here                                                                                                         |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| pass `--slide-level=N`                                        | Not passed. Put `slide-level:` in the front matter instead.                                                  |
| write `::: notes` for speaker notes                           | Worse than nothing: the note renders visibly on the slide. Use `::: {.hidden}` and build with `WITH=hidden`. |
| write `. . .` to pause                                        | Nothing — it renders as literal text. A deck has no incremental reveal.                                      |
| write `::: incremental`                                       | Nothing.                                                                                                     |
| nest `::: {.column width="40%"}` inside `::: {.columns}`      | Don't nest. `::: columns` splits its own children; bias it with `.wide-left` / `.wide-right`, not `width=`.  |
| expect a content-free slide-level heading to be a title slide | It becomes an ordinary slide. The title slide is generated from the front matter instead.                    |
| expect `---` to break slides only at slide level 0            | `---` always starts a new slide, whatever `slide-level` is.                                                  |

What you get in exchange is that a deck is one self-contained HTML file — no reveal.js to download,
no separate embedding step — with the same math, diagrams, code highlighting and callouts a document
gets.

### Where slides break

A new slide starts at:

- every heading at the slide level, `##` by default; and
- every `---` horizontal rule.

Nothing else splits a slide. A `###` under a `##` stays on the same slide as a subheading.

Change the level in the front matter if your deck is organized differently:

```markdown
---
title: "..."
slide-level: 3
---
```

Use `---` when one heading's worth of material needs two slides. It starts a slide with no heading of
its own, which is also the natural way to give a full-bleed diagram its own page. Leave a blank line
before it, or markdown reads it as a setext heading for the paragraph above instead of a rule.

### Slide layouts

Four fenced divs shape a slide:

| Fence          | Effect                                                                  |
| -------------- | ----------------------------------------------------------------------- |
| `::: columns`  | Side-by-side, one column per child block                                |
| `::: lead-in`  | A larger pull statement with an accent rule — the one thing to remember |
| `::: takeaway` | A boxed conclusion, pinned to the bottom of the slide                   |
| `::: center`   | Centers the text inside                                                 |

**A columns block is as wide as its contents.** Two children give two columns, three give three,
four give four. You do not declare the count anywhere; the number of top-level blocks inside the
fence is the number of columns. (Before 0.20.0 it was always two, so three children left an empty
cell and four stacked 2×2. If an old deck looks different, that is why, and the new shape is the
one you meant.)

Add `wide-left` or `wide-right` to bias a **two**-column split about 60/40 — useful when prose sits
beside a diagram. These name two explicit tracks, so they say two columns and mean it:

```markdown
::: {.columns .wide-right}

Kanban limits work in progress, so the queue has to drain before anything new is pulled.

![Cumulative flow](images/cfd.svg)

:::
```

`takeaway` is positioned at the bottom of its slide regardless of how much content is above it, so a
deck built around one boxed conclusion per slide stays visually consistent.

#### Columns as cards

Add `cards` and each column gets a surface, a border, a radius and padding, built from the same
tokens `takeaway` uses so a card matches the boxed conclusion under it rather than introducing a
second kind of box. It is **opt-in**: a card costs vertical space on a medium that has none spare,
so a plain `::: columns` stays plain.

```markdown
::::: {.columns .cards}

:::: column
**Discover.** Job boards, referrals, the career fair. Wide and cheap.
::::

:::: column
**Apply.** A tailored résumé per posting. Narrower and expensive.
::::

:::: column
**Interview.** The part everyone prepares for, and the smallest step of the three.
::::

:::::
```

Note the colon counts. A fence closes at the same width it opened, so the children need fewer
colons than the block that holds them — `:::::` outside, `::::` inside. Bare paragraphs work as
children too, one paragraph per column; a `column` div is what lets a card hold more than one block,
and what an accent class attaches to.

Three things a card does that plain columns do not:

| Class         | On             | Effect                                                                    |
| ------------- | -------------- | ------------------------------------------------------------------------- |
| `cards`       | the fence      | Surface, border, radius, padding, and an accent stripe down the left edge |
| `accent-2..4` | a single child | Overrides that one card's accent colour                                   |
| `lead-letter` | the fence      | Enlarges the first character of each card's lead run                      |

The accent reaches both the left stripe and the **lead run** — the bold text that opens the card —
so `**Discover**` above is coloured to match its own stripe. Cards default to the deck accent;
`accent-2`, `accent-3` and `accent-4` step through three more so a four-card row reads as four
things rather than one thing four times. Put them on the child, not the fence:

```markdown
::::: {.columns .cards .lead-letter}

:::: column
**S — Situation.** Where you were and what was at stake.
::::

:::: {.column .accent-2}
**T — Task.** What you specifically owned.
::::

:::: {.column .accent-3}
**A — Action.** What you did, in the first person singular.
::::

:::: {.column .accent-4}
**R — Result.** The number, if you have one.
::::

:::::
```

`lead-letter` is what makes the `S`, `T`, `A`, `R` above read as letters rather than as bold words:
it enlarges the first character of each card's opening bold run and leaves the rest alone. It does
nothing on a card whose first block is not bold, so it is safe to put on the fence and forget.

### Presenter-only slides

A hidden fenced div works in a deck exactly as it does in a document, with one wrinkle worth knowing:
**a heading inside a fenced div does not start a new slide.** The splitter only sees top-level
blocks, so a `::: {.hidden}` block containing a `##` merges into the slide before it when you build
with `WITH=hidden`.

Put a `---` in front of it and it becomes its own slide:

```markdown
## The Hockey Stick

![Utilization vs. wait time](images/utilization.svg)

---

::: {.hidden}

## Presenter Notes

Ask the class where they think the knee is before revealing it.

:::
```

Built normally, the rule leaves no empty slide behind — an emptied slide is dropped rather than
rendered blank. Built with `WITH=hidden`, the notes appear as a slide of their own.

### Presenting

The deck reads as a scrollable stack of cards. Each one carries its number in the bottom corner —
except the title slide, which is the cover and shows none, so the first slide you wrote is numbered
2\. A toolbar in the corner shows your position and a **Present** button.

| Key                            | Does                        |
| ------------------------------ | --------------------------- |
| `p`                            | Enter or leave present mode |
| `Escape`                       | Leave present mode          |
| `→` `↓` `PageDown` `Space` `j` | Next slide                  |
| `←` `↑` `PageUp` `k`           | Previous slide              |
| `Home` / `End`                 | First / last slide          |

Present mode shows one slide at a time and asks the browser for fullscreen; leaving fullscreen exits
present mode. Arrow keys only navigate while presenting, so they still scroll the page normally when
you are reading the deck. Keystrokes inside a text field are left alone.

**While presenting, the arrows belong to the deck** — no matter what has focus. That is a deliberate
carve-out rather than the browser's default. The theme control is a radiogroup, and a radiogroup
answers arrow keys by changing its selection, so before 0.18.0 a presenter who had touched the
control once found every later slide change also repainting the deck. Presenting suspends the
control's arrow handling; Tab walks its three options and Enter picks one, so it stays reachable
from the keyboard. Space is handled the same way: on a focused button it activates the button and
does not also advance the slide.

### Printing a deck

Run `make pdf`. The page is set up for letter landscape, one slide per page, with the toolbar
suppressed. There is no separate deck build and no extra flag: the deck you present is the deck that
prints.

Your browser's own Ctrl+P lays the pages out identically and is fine for a quick look, but **it does
not produce the same file**. `make pdf` drives headless Chromium and then repairs the result to
PDF/UA-1 — it adds the role map, the XMP metadata and the list-body tags Chromium omits, and marks
the decoration Chromium paints untagged. A browser print skips all of that and hands you an untagged
PDF that no accessibility checker will pass. Use `make pdf` for anything you hand out or submit.

## Fenced divs and prettier

**Put a blank line after an opening `:::` and before the closing one.**

```markdown
::: takeaway

Batch size is the real variable.

:::
```

Not:

```markdown
::: takeaway
Batch size is the real variable.
:::
```

`make doc` and `make slide` both run `format-md` first, and prettier does not know what a fenced div
is. With the fence glued to its content it folds the whole block into a single paragraph, and pandoc
then emits a literal `:::` into the page instead of a styled box. The blank lines cost nothing and
survive formatting.

When the damage leaves a fence unclosed, the build now stops on it rather than rendering the mess —
pandoc runs with `--fail-if-warnings`.

Prettier runs with `--prose-wrap always --print-width 100`, so it rewraps every paragraph to 100
columns. Line breaks you put in for your own reading comfort will not survive, and neither will a
one-sentence-per-line habit. Where a break has to be real, use an explicit markdown line break — two
trailing spaces, or a backslash at end of line — or a list item.

## Do not run mdformat over this markdown

Prettier is the formatter the pipeline runs, and the fenced-div rule above is the only concession
it asks for. **[mdformat](https://github.com/hukkin/mdformat) is a different matter: pointed at a
document or a deck, it rewrites the file into something that either fails to build or builds into
the wrong document.** If your project runs mdformat as a pre-commit hook, exclude wherever the
course markdown lives:

```yaml
- repo: https://github.com/hukkin/mdformat
  rev: 0.7.21
  hooks:
    - id: mdformat
      exclude: ^week\d+/ # wherever the course content lives
```

Three things it does, all of them found by letting it run:

**It eats the YAML front matter.** mdformat has no front-matter support unless a plugin adds it, so
the opening `---` becomes a thematic break and the metadata below it becomes body text — in a
document, a single very long heading. The title, subtitle, author, date and `bibliography:` are all
gone from the page. That last one is the one you notice, because the build stops on

```
[WARNING] Citeproc: citation little1961 not found
```

which reads as a typo in a citation key rather than as a formatter having removed the bibliography.
Installing `mdformat-frontmatter` fixes this one, and nothing else below.

**It escapes the backslashes in LaTeX.** `$L = \lambda W$` becomes `$L = \\lambda W$`, and
`\frac{L}{\lambda}` becomes `\\frac{L}{\\lambda}`. In a diff this is close to invisible. The build
does stop — `--fail-if-warnings` promotes pandoc's `Could not convert TeX math` to a failure — but
the message points at an "unexpected control sequence" in your math, which is not where the problem
came from.

**It rewrites the `---` slide separator as `______`.** This one is cosmetic: both parse as a
thematic break, so a deck still breaks into exactly the same slides. It is worth knowing only
because it makes a separator indistinguishable from what used to be a metadata fence in a file the
other two changes have already broken.

## Before you hand it out

```bash
make doc            # or make slide
make check-access   # the HTML, at WCAG 2.1 AA
make check-pdf      # the PDF, at PDF/UA-1  (builds it first)
```

**Two checks, because there are two standards.** `make check-access` runs pa11y against every built
HTML file at WCAG 2.1 AA, in **both** themes — it clicks the theme control the way a reader would,
because a colour that only fails in dark is still a failure. It catches a missing diagram caption, a
contrast problem, or a heading level skipped on the way down.

`make check-pdf` runs veraPDF against the PDFs at PDF/UA-1 (ISO 14289-1), and it depends on
`make pdf`, so it builds what it is about to check. The two standards disagree about figures: an
HTML page can be perfectly accessible and still produce a PDF whose `<figure>` wrappers carry no
accessible name, because HTML has no `alt` on `<figure>` and WCAG has no rule requiring one. So
`check-access` passing tells you nothing about the PDF, and this is the target that does.

If `check-pdf` says **"found no PDF to check"**, that is the check refusing to lie to you rather
than a bug. veraPDF exits successfully when handed no files, so the target counts what it opened
and treats zero as an error — otherwise a run in a cleaned directory would report a pass having
looked at nothing.

**Your link text becomes the PDF's description of the link.** PDF/UA requires every link
annotation to carry an alternate description, and Chromium writes none, so `make pdf` adds one
after printing — taken from the anchor's `aria-label`, then its text, then the accessible name of a
picture inside it, then its `title`, and only as a last resort the URL itself. So
`[the authoring guide](https://…)` describes itself and `[click here](https://…)` describes
nothing, exactly as on the web; the difference is that in the PDF the fallback is the raw URL read
out character by character. Nothing to do here beyond writing the link text you would want read
aloud — but a bare `<https://example.com/a/b/c.html>` autolink is its own description, and that is
what a reader gets.
