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

| Extension                | What it buys you                                                            |
| ------------------------ | --------------------------------------------------------------------------- |
| `fenced_divs`            | `::: {.hidden}`, `::: columns`, `::: takeaway` — every fence below          |
| `bracketed_spans`        | `[text]{.class}` when you need a class on an inline run                     |
| `header_attributes`      | `## Setup {#custom-id}` to pin a heading's id                               |
| `auto_identifiers`       | Every other heading gets an id derived from its text                        |
| `fenced_code_attributes` | ```` ```{.mermaid caption="…"} ````                                         |
| `pipe_tables`            | The ordinary `\| a \| b \|` table                                           |
| `tex_math_dollars`       | `$L = \lambda W$`                                                           |
| `raw_html`               | The `<nav>` block that becomes a tab bar                                    |
| `citations`              | `[@key]` and `@key` — see [Citations](#citations)                           |
| `footnotes`              | `[^1]` and its definition                                                   |
| `definition_lists`       | A term, then a `:`-prefixed definition                                      |
| `task_lists`             | `- [ ]` and `- [x]` — the checkbox replaces the bullet                      |
| `implicit_figures`       | An image alone in a paragraph becomes a `<figure>`, alt text as its caption |
| `smart`                  | Straight quotes and `--` become typographic quotes and dashes               |

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

| Key           | In a document                    | In a deck                                       |
| ------------- | -------------------------------- | ----------------------------------------------- |
| `title`       | Page header, and the browser tab | Title slide, and the browser tab                |
| `subtitle`    | Under the title                  | Under the title on the title slide              |
| `author`      | Byline under the subtitle        | Byline on the title slide                       |
| `date`        | Byline, after the author         | Byline, after the author                        |
| `slide-level` | Ignored                          | Heading level that starts a slide (default `2`) |

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

Names are joined with `·`, and the date follows after another `·`. A date with no author renders on
its own. The byline is part of the title header, so a file with an `author` but no `title` gets no
header at all — same rule a deck follows for its title slide.

Citations add four more keys — `bibliography`, `csl`, `nocite` and `link-citations` — which behave
the same in a document as in a deck; see [Citations](#citations).

Two more keys arrive from the build rather than from you: `course`, which the Makefile sets from the
package's `name` (it prefixes the browser tab on both, and prints above the title on a deck's title
slide), and `include-<feature>`, which `WITH=` sets — see [Optional content](#optional-content).
Anything else you put in the front matter is carried along by pandoc but nothing reads it.

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
| `::: columns`  | Two side-by-side halves, one per child block                            |
| `::: lead-in`  | A larger pull statement with an accent rule — the one thing to remember |
| `::: takeaway` | A boxed conclusion, pinned to the bottom of the slide                   |
| `::: center`   | Centers the text inside                                                 |

`columns` splits evenly. Add `wide-left` or `wide-right` to bias it about 60/40 — useful when prose
sits beside a diagram:

```markdown
::: {.columns .wide-right}

Kanban limits work in progress, so the queue has to drain before anything new is pulled.

![Cumulative flow](images/cfd.svg)

:::
```

`takeaway` is positioned at the bottom of its slide regardless of how much content is above it, so a
deck built around one boxed conclusion per slide stays visually consistent.

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

### Printing a deck

Run `make pdf`, or print from the browser — both give you the same file. The page is set up for
letter landscape, one slide per page, with the toolbar suppressed. There is no separate deck build
and no extra flag: the deck you present is the deck that prints.

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
make check-access
```

This runs pa11y against every built HTML file at WCAG 2.1 AA. It is the cheap check that catches a
missing diagram caption, a contrast problem, or a heading level skipped on the way down.
