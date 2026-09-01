# Authoring Guide

How to write the markdown. This guide is for the person writing a lecture, a set of notes, or an
assignment; [STENCIL.md](STENCIL.md) is for the person configuring the package that builds it.

Everything here goes through the same pipeline: prettier formats the markdown, pandoc converts it to
a standalone HTML file, and Lua filters handle hidden content and diagram captions. The only choice
that changes the output shape is whether the file is listed under `docs:` or `slides:`.

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
make slides       # builds only the decks
make format-md    # runs prettier over the markdown
make check-access # pa11y accessibility check over the built HTML
```

`make doc` depends on `make slides`, so the one command always produces everything. Both targets run
`format-md` first, which matters — see [Fenced divs and prettier](#fenced-divs-and-prettier).

## What both kinds share

### Front matter

```markdown
---
title: "Flow, Limits, and Specifications"
subtitle: "Kanban and its neighbors"
---
```

A document renders these as a page header; a deck renders them as a generated title slide, which
also picks up `author` and `date` if present. The package's `name` is passed in as `course` and
appears above the title on a deck's title slide.

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

The deck reads as a scrollable stack of cards. A toolbar in the corner shows your position and a
**Present** button.

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

Print from the browser. The page is set up for letter landscape, one slide per page, with the
toolbar suppressed — no extra flag or separate build.

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

`make doc` and `make slides` both run `format-md` first, and prettier does not know what a fenced div
is. With the fence glued to its content it folds the whole block into a single paragraph, and pandoc
then emits a literal `:::` into the page instead of a styled box. The blank lines cost nothing and
survive formatting.

## Before you hand it out

```bash
make check-access
```

This runs pa11y against every built HTML file at WCAG 2.1 AA. It is the cheap check that catches a
missing diagram caption, a contrast problem, or a heading level skipped on the way down.
