# Front matter: points, due, Issued Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `points` badge and a `due` key to the front matter, label the
existing `date` as `Issued`, and regrid the document header so its source order
matches its visual order.

**Architecture:** All parsing, validation and label-building happens once in
`frontmatter-filter.lua.j2`, which hands the pandoc templates pre-computed keys
so each template asks a single `$if(...)$` rather than reimplementing logic. The
document header changes from two flex rows to a two-column grid, which lets the
author precede the context block in source order without moving anything on
screen.

**Tech Stack:** Jinja2 templates rendering pandoc HTML templates and Lua filters;
pandoc 3.10.0.0 in a container; pytest with BeautifulSoup and pypdf.

**Spec:** `docs/superpowers/specs/2026-09-05-frontmatter-and-theming-design.md`
(Part A only — Part B ships separately as 0.12.0)

## Global Constraints

- Target version is **0.11.0**. Bump `stencil/__init__.py`; `pyproject.toml`
  reads it from there.
- Date grammar is exactly `yyyy-mm-dd` or `yyyy-mm-ddThh:mm`, for both `date`
  and `due`. Nothing else is accepted.
- Rendered date form is `MMM DD`, zero-padded day, no year, optionally followed by
  ` · HH:MM` in 24-hour time. The time renders **iff the author wrote one**.
- Calendar validation is an `os.time` round-trip with `hour = 12`, never a
  hand-rolled days-in-month table. `hour = 12` keeps the value clear of DST
  transitions.
- Every new front-matter key joins `BLANKABLE`, so a blank key equals an absent
  key.
- Badge and label colors are written as literal values in this release. The
  0.12.0 theming sweep converts them to tokens; do not invent tokens here.
- Container-backed tests carry `pytestmark = pytest.mark.integration`.
- Run the full suite with `python3 -m pytest` from the repo root.
- A pre-commit `mdformat` hook rewrites Markdown. When a commit fails with
  "files were modified by this hook", `git add -A` the reformatted files and
  commit again. **Never** use `git commit -C HEAD` as a fallback — it silently
  reuses the previous commit's message.

______________________________________________________________________

### Task 1: `points` — label and badge

**Files:**

- Modify: `stencil/templates/frontmatter-filter.lua.j2`
- Modify: `stencil/templates/_doc-body.html.j2`
- Modify: `stencil/templates/_slide-body.html.j2`
- Modify: `stencil/templates/_page-style.css.j2`
- Modify: `stencil/templates/_slide-style.css.j2`
- Test: `tests/test_points.py` (create)

**Interfaces:**

- Consumes: nothing from earlier tasks.

- Produces: metadata key `points-label` (a `pandoc.Inlines`), read by both body
  templates as `$points-label$`. CSS classes `.doc-points` and `.deck-points`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_points.py`:

```python
"""The points badge.

`points` is a number in the front matter; the badge beside the title is the
only place it renders. The interesting cases are the plural boundary and the
non-numeric escape hatch -- "1 pts" is the bug this file exists to prevent.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def document(front_matter: str) -> str:
    return f"---\n{front_matter}---\n\n## Body\n\nSome prose.\n"


def badge(soup, selector: str = ".doc-points") -> str | None:
    found = soup.select_one(selector)
    return None if found is None else " ".join(found.get_text().split())


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1", "1 pt"),
        ("0", "0 pts"),
        ("50", "50 pts"),
        ("100", "100 pts"),
        ("1.5", "1.5 pts"),
    ],
)
def test_points_pluralize(render_soup, value, expected):
    soup = render_soup(
        "doc", "pts.md", text=document(f'title: "T"\npoints: {value}\n')
    )
    assert badge(soup) == expected


def test_a_non_numeric_value_renders_verbatim(render_soup):
    soup = render_soup(
        "doc", "pts.md", text=document('title: "T"\npoints: "extra credit"\n')
    )
    assert badge(soup) == "extra credit"


def test_absent_points_emits_no_badge(render_soup):
    soup = render_soup("doc", "pts.md", text=document('title: "T"\n'))
    assert badge(soup) is None


def test_blank_points_renders_as_if_absent(render):
    absent, absent_path = render(
        "doc", "a.md", text=document('title: "T"\n'), output="a.html"
    )
    blank, blank_path = render(
        "doc", "b.md", text=document('title: "T"\npoints:\n'), output="b.html"
    )
    assert absent.returncode == 0, absent.stderr
    assert blank.returncode == 0, blank.stderr
    assert blank_path.read_text() == absent_path.read_text()


def test_the_badge_reaches_the_deck_title_slide(render_soup):
    soup = render_soup(
        "slide", "deck.md", text=document('title: "T"\npoints: 50\n')
    )
    assert badge(soup, ".deck-points") == "50 pts"
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python3 -m pytest tests/test_points.py -v`
Expected: FAIL — every assertion returns `None`, because no template emits
`.doc-points` yet.

- [ ] **Step 3: Compute `points-label` in the filter**

In `stencil/templates/frontmatter-filter.lua.j2`, add `"points"` to the
`BLANKABLE` list, then add this inside `function Meta(meta)`, after the brand
block and before the `has_context` computation:

```lua
  -- Points is a number the reader cares about, so the badge says "1 pt" rather
  -- than "1 pts". Computed here because a pandoc template cannot compare a
  -- value -- the same reason `date` is filled in here rather than there.
  --
  -- A value that is not a number renders verbatim, so `points: "extra credit"`
  -- is a usable escape hatch rather than a build failure. There is nothing to
  -- validate about it: unlike a date, any string is a legible answer to "what
  -- is this worth".
  local points = text_of(meta.points)
  if points then
    local count = tonumber(points)
    local label
    if count == nil then
      label = points
    elseif count == 1 then
      label = points .. " pt"
    else
      label = points .. " pts"
    end
    meta["points-label"] = pandoc.Inlines({ pandoc.Str(label) })
  end
```

- [ ] **Step 4: Emit the badge in the document template**

In `stencil/templates/_doc-body.html.j2`, replace the `.doc-identity` block:

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

- [ ] **Step 5: Emit the badge on the deck title slide**

In `stencil/templates/_slide-body.html.j2`, replace the `<h1>` line:

```html
  <h1 class="deck-title">$title$$if(points-label)$ <span class="deck-points">$points-label$</span>$endif$</h1>
```

- [ ] **Step 6: Style both badges**

In `stencil/templates/_page-style.css.j2`, after the `.doc-subtitle` rule:

```css
    /* What the document is worth, beside the title. Sits inside .doc-title,
       so like .doc-subtitle it has to opt out of that block's 2.2rem/700
       rather than inherit it -- a badge that outranks the title is the same
       inversion .doc-subtitle was fixed for.

       Colors are literal here and become tokens in the theming release; there
       is no token layer to hang them on yet. */
    .doc-points {
      display: inline-block;
      margin-left: 0.5rem;
      padding: 0.15em 0.55em;
      border-radius: 999px;
      background: #e8eef7;
      color: #29417a;
      font-family: var(--font-sans);
      font-size: 0.95rem;
      font-weight: 600;
      line-height: 1.4;
      white-space: nowrap;
      vertical-align: middle;
    }
```

In `stencil/templates/_slide-style.css.j2`, after the `.slide--title .deck-subtitle` rule:

```css
    /* The title slide sits on the accent gradient, so the badge is a
       translucent white plate rather than the light one documents use. */
    .slide--title .deck-points {
      display: inline-block;
      margin-left: 0.6rem;
      padding: 0.12em 0.5em;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.18);
      color: #fff;
      font-size: 1.1rem;
      font-weight: 600;
      white-space: nowrap;
      vertical-align: middle;
    }
```

- [ ] **Step 7: Assert the badge cannot outrank the title**

The spec requires the badge to print subordinate to the title. Assert it against
the stylesheet rather than a PDF: `tests/test_title_block.py` already has the
`css` fixture and the `rule()` / `size_rem()` helpers for exactly this, and a
CSS assertion is deterministic where measuring glyphs in a PDF is not.

Append to `tests/test_title_block.py`:

```python
def test_the_points_badge_is_subordinate_to_the_title(css):
    """The badge sits inside .doc-title, so an omitted font-size would
    inherit that block's 2.2rem -- the same inversion .doc-subtitle exists
    to prevent, and the print block would carry it into the PDF.
    """
    title = size_rem(rule(css, ".doc-title"))
    badge = size_rem(rule(css, ".doc-points"))
    assert badge < title, f"badge {badge}rem is not below title {title}rem"
```

- [ ] **Step 8: Run the tests and make sure they pass**

Run: `python3 -m pytest tests/test_points.py tests/test_title_block.py -v`
Expected: PASS — 9 tests in `test_points.py`, plus the existing
`test_title_block.py` set with one addition.

- [ ] **Step 9: Commit**

```bash
git add stencil/templates/ tests/test_points.py tests/test_title_block.py
git commit -m "Add a points badge beside the document and deck title

points: 50 renders a badge reading 50 pts; points: 1 reads 1 pt. The
plural is decided in the lua filter rather than the template, which
cannot compare a value -- the same reason date is filled in there.

A non-numeric value renders verbatim, so points: \"extra credit\" works.
Unlike a date there is nothing to validate: any string is a legible
answer to what a document is worth."
```

______________________________________________________________________

### Task 2: date grammar — parse, validate, reject

**Files:**

- Modify: `stencil/templates/frontmatter-filter.lua.j2`
- Test: `tests/test_dates.py` (create)

**Interfaces:**

- Consumes: `text_of()` and the `BLANKABLE` loop from the existing filter.
- Produces: metadata keys `date-label`, `date-iso`, `due-label`, `due-iso`,
  `has-dates`, `due-needs-separator`. Task 3 reads all six. Also a local
  `parse_stamp(key, text)` returning `{year, month, day, hour, min}` with
  `hour`/`min` nil when no time was written, and `format_stamp(stamp)`
  returning the display string.

This task deliberately ships no markup. Its deliverable is the grammar and its
rejections, which are fully observable as build failures.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dates.py`:

```python
"""The date grammar shared by `date` and `due`.

Both keys accept yyyy-mm-dd or yyyy-mm-ddThh:mm and nothing else. The
calendar gate is an os.time round-trip rather than a days-in-month table --
os.time normalizes a day the month does not have, so comparing the fields
back is what detects it. These tests pin all four branches of the Gregorian
leap rule and both ends of the time_t range, because the round-trip's
correctness is inherited from the container's C library and the only thing
that can change it is bumping the image.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def document(front_matter: str) -> str:
    return f"---\n{front_matter}---\n\n## Body\n\nSome prose.\n"


def build(render, key: str, value: str):
    """Render one document carrying a single date key.

    The value is quoted on purpose. Unquoted, YAML would type some of these
    itself -- `2026-09-01 21:45` is a YAML timestamp, not a string -- and the
    test would then be measuring the YAML parser rather than this filter.
    Quoting is also how an author writes them.
    """
    return render(
        "doc", "d.md", text=document(f'title: "T"\n{key}: "{value}"\n'),
        output="d.html",
    )


ACCEPTED = [
    "2026-09-01",
    "2026-09-01T21:45",
    "2024-02-29",  # leap, % 4
    "2000-02-29",  # leap, % 400
    "1900-02-28",  # pre-1970, negative time_t
    "2038-12-31",  # past the 32-bit time_t cliff
    "2026-01-31",
    "2026-04-30",
]

REJECTED = [
    "2026-9-1",  # not zero-padded
    "2026/09/01",  # wrong separator
    "09-01-2026",  # wrong order
    "2026-09-01 21:45",  # space instead of T
    "2026-09-01T21:45:30",  # seconds
    "next Friday",
    "2026-13-01",  # month out of range
    "2026-00-01",
    "2026-09-01T24:00",  # hour out of range
    "2026-09-01T21:60",  # minute out of range
    "2026-02-30",  # not a real date
    "2026-02-29",  # not a leap year
    "1900-02-29",  # century, not a leap year
    "2100-02-29",  # century, not a leap year
    "2026-01-32",
    "2026-04-31",
]


@pytest.mark.parametrize("key", ["date", "due"])
@pytest.mark.parametrize("value", ACCEPTED)
def test_accepted_shapes_build(render, key, value):
    result, _ = build(render, key, value)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("key", ["date", "due"])
@pytest.mark.parametrize("value", REJECTED)
def test_rejected_shapes_fail_the_build(render, key, value):
    result, _ = build(render, key, value)
    assert result.returncode != 0, f"{key}: {value} was accepted"
    assert key in result.stderr


def test_a_bad_shape_names_both_accepted_forms(render):
    result, _ = build(render, "due", "next Friday")
    assert "yyyy-mm-dd" in result.stderr
    assert "yyyy-mm-ddThh:mm" in result.stderr


def test_a_bad_month_names_the_field_not_the_grammar(render):
    result, _ = build(render, "due", "2026-13-01")
    assert "month" in result.stderr


def test_an_impossible_day_names_the_month_and_its_length(render):
    result, _ = build(render, "due", "2026-02-30")
    assert "Feb" in result.stderr
    assert "28" in result.stderr


def test_blank_keys_render_as_if_absent(render):
    absent, absent_path = render(
        "doc", "a.md", text=document('title: "T"\n'), output="a.html"
    )
    blank, blank_path = render(
        "doc", "b.md", text=document('title: "T"\ndate:\ndue:\n'),
        output="b.html",
    )
    assert absent.returncode == 0, absent.stderr
    assert blank.returncode == 0, blank.stderr
    assert blank_path.read_text() == absent_path.read_text()
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python3 -m pytest tests/test_dates.py -v`
Expected: FAIL — the `REJECTED` cases all build successfully today, because
`date` accepts any string and `due` is ignored entirely.

- [ ] **Step 3: Add the month table and the parser**

In `stencil/templates/frontmatter-filter.lua.j2`, add `"due"` to `BLANKABLE`,
then add this **after the `truthy` function and before `function Meta(meta)`**.
The position matters: `stamp_keys` closes over `text_of`, and a Lua local is
only in scope for code that appears after its declaration — placed above
`text_of`, it would read a nil global instead, which is precisely the failure
the `CONFIG_BRAND` comment in this file already records.

```lua
local MONTHS = {
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
}

local SHAPES = "expected yyyy-mm-dd or yyyy-mm-ddThh:mm"

--- The last day of month `m` in year `y`.
---
--- Day zero of the following month, which mktime resolves backwards. Used only
--- to say something useful in the error; the acceptance decision is the
--- round-trip in parse_stamp.
local function last_day_of(y, m)
  return os.date("*t", os.time({ year = y, month = m + 1, day = 0, hour = 12 })).day
end

--- One ISO stamp as a table, or an error naming what was wrong with it.
---
--- Three gates, narrowest first, so the message describes the actual failure
--- rather than restating the grammar every time.
---
--- The calendar gate is an os.time round-trip rather than a days-in-month
--- table. os.time *normalizes* a day the month does not have -- 2026-02-30
--- comes back as March 2 -- and that normalization is exactly the detector:
--- compare the fields back, and any date the calendar does not contain fails
--- the comparison. The Gregorian rules, leap years included, then come from
--- the platform mktime rather than from code here, where a wrong century rule
--- would not surface until 2100.
---
--- hour = 12 is load-bearing. A date pinned to midnight can cross a day
--- boundary in a zone observing a DST transition, which would reject a date
--- the calendar does contain. Noon clears every transition.
local function parse_stamp(key, text)
  local y, m, d = text:match("^(%d%d%d%d)%-(%d%d)%-(%d%d)$")
  local hh, mm
  if not y then
    y, m, d, hh, mm = text:match("^(%d%d%d%d)%-(%d%d)%-(%d%d)T(%d%d):(%d%d)$")
  end
  if not y then
    error(key .. ": " .. text .. " is not a date stencil can read -- " .. SHAPES .. ".")
  end

  y, m, d = tonumber(y), tonumber(m), tonumber(d)
  hh, mm = tonumber(hh), tonumber(mm)

  if m < 1 or m > 12 then
    error(key .. ": " .. text .. " has month " .. m .. "; months run 01-12.")
  end
  if hh and hh > 23 then
    error(key .. ": " .. text .. " has hour " .. hh .. "; hours run 00-23.")
  end
  if mm and mm > 59 then
    error(key .. ": " .. text .. " has minute " .. mm .. "; minutes run 00-59.")
  end

  local back = os.date("*t", os.time({ year = y, month = m, day = d, hour = 12 }))
  if back.year ~= y or back.month ~= m or back.day ~= d then
    error(
      key .. ": " .. text .. " is not a real date -- "
      .. MONTHS[m] .. " " .. y .. " has " .. last_day_of(y, m) .. " days."
    )
  end

  return { year = y, month = m, day = d, hour = hh, min = mm }
end

--- The display form: MMM DD, and the time only when one was written.
local function format_stamp(stamp)
  local text = string.format("%s %02d, %04d", MONTHS[stamp.month], stamp.day, stamp.year)
  if stamp.hour then
    text = text .. " " .. utf8.char(0x00B7) .. " "
        .. string.format("%02d:%02d", stamp.hour, stamp.min)
  end
  return text
end

--- Validate `key`, and write back its rendered label and its original ISO text.
---
--- The ISO string is kept verbatim for the <time datetime> attribute, so the
--- HTML stays machine-readable however short the visible form gets. A PDF
--- keeps only the visible text, which is why that form stays lossless too.
local function stamp_keys(meta, key)
  local text = text_of(meta[key])
  if text == nil then
    return false
  end
  meta[key .. "-label"] = pandoc.Inlines({ pandoc.Str(format_stamp(parse_stamp(key, text))) })
  meta[key .. "-iso"] = pandoc.Inlines({ pandoc.Str(text) })
  return true
end
```

- [ ] **Step 4: Call it from `Meta`**

In `function Meta(meta)`, replace the `show_date` / `date` block with:

```lua
  local show = truthy(meta.show_date)
  meta.show_date = show or nil

  if show and text_of(meta.date) == nil then
    -- The Makefile passes the build host's date; os.date is the fallback for
    -- a hand-run pandoc, and inside the container that is UTC. Date-only on
    -- purpose: the build stamp's clock time says when make ran, which is not
    -- a fact about the document.
    meta.date = text_of(meta["build-date"]) or os.date("%Y-%m-%d")
  end

  local has_date = stamp_keys(meta, "date")
  local has_due = stamp_keys(meta, "due")
  meta["has-dates"] = (has_date or has_due) or nil
  -- Two keys OR'd together, which a pandoc template cannot do for itself.
  meta["due-needs-separator"] = (meta.author ~= nil or has_date) or nil
```

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `python3 -m pytest tests/test_dates.py -v`
Expected: PASS. Every case in ACCEPTED and REJECTED, for both keys.

If any `ACCEPTED` case fails, check `last_day_of` first: confirm `day = 0`
resolves backwards in this image with
`docker run --rm --entrypoint pandoc docker.io/pandoc/core:3.10.0.0 lua -e 'print(os.date("*t", os.time({year=2026,month=3,day=0,hour=12})).day)'`
Expected output: `28`.

- [ ] **Step 6: Commit**

```bash
git add stencil/templates/frontmatter-filter.lua.j2 tests/test_dates.py
git commit -m "Accept only ISO dates in date:, and add due:

Both keys take yyyy-mm-dd or yyyy-mm-ddThh:mm and nothing else, gated
on shape, then field ranges, then the calendar.

The calendar gate is an os.time round-trip, not a days-in-month table.
os.time normalizes a day the month does not have -- 2026-02-30 comes
back as March 2 -- so comparing the fields back is what detects it, and
the leap rules come from the platform mktime rather than from a century
rule here that nothing would exercise until 2100. hour = 12 keeps the
value clear of DST transitions, which could otherwise shift the day.

Imposing a grammar on date: is a behavior change; it accepted any
string before. Nothing in tree relies on that -- the only date: values
anywhere are the two fixtures, both already ISO."
```

______________________________________________________________________

### Task 3: the Issued/Due byline

**Files:**

- Modify: `stencil/templates/_doc-body.html.j2`
- Modify: `stencil/templates/_slide-body.html.j2`
- Modify: `stencil/templates/_page-style.css.j2`
- Modify: `stencil/templates/frontmatter-filter.lua.j2` (`has-byline` only)
- Modify: `tests/test_byline.py`
- Modify: `tests/test_dates.py`

**Interfaces:**

- Consumes: `date-label`, `date-iso`, `due-label`, `due-iso`, `has-dates`,
  `due-needs-separator` from Task 2.

- Produces: `.doc-date` as a **wrapper** containing `.doc-issued` and
  `.doc-due`; class `.doc-date-label`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dates.py`:

```python
def test_issued_and_due_render_labelled(render_soup):
    soup = render_soup(
        "doc", "d.md",
        text=document('title: "T"\ndate: 2026-09-01\ndue: 2026-09-12T23:59\n'),
    )
    issued = soup.select_one(".doc-issued")
    due = soup.select_one(".doc-due")
    assert " ".join(issued.get_text().split()) == "Issued Sep 01"
    assert " ".join(due.get_text().split()) == "Due Sep 12 · 23:59"


def test_a_written_time_renders_and_a_missing_one_does_not(render_soup):
    soup = render_soup(
        "doc", "d.md",
        text=document('title: "T"\ndate: 2026-09-01T21:45\ndue: 2026-09-12\n'),
    )
    assert "21:45" in soup.select_one(".doc-issued").get_text()
    assert ":" not in soup.select_one(".doc-due").get_text()


def test_the_time_element_carries_the_original_iso(render_soup):
    soup = render_soup(
        "doc", "d.md", text=document('title: "T"\ndue: 2026-09-12T23:59\n')
    )
    assert soup.select_one(".doc-due time")["datetime"] == "2026-09-12T23:59"


def test_show_date_stamps_date_only(render_soup):
    soup = render_soup(
        "doc", "d.md", text=document('title: "T"\nshow_date: true\n')
    )
    issued = " ".join(soup.select_one(".doc-issued").get_text().split())
    assert issued.startswith("Issued ")
    assert ":" not in issued


def test_a_written_date_still_beats_show_date(render_soup):
    soup = render_soup(
        "doc", "d.md",
        text=document('title: "T"\ndate: 2026-09-01\nshow_date: true\n'),
    )
    assert "Sep 01" in soup.select_one(".doc-issued").get_text()


def test_due_alone_opens_the_byline(render_soup):
    soup = render_soup(
        "doc", "d.md", text=document('title: "T"\ndue: 2026-09-12\n')
    )
    assert soup.select_one(".doc-byline") is not None
    assert soup.select_one(".doc-issued") is None
    assert "Due Sep 12" in soup.select_one(".doc-due").get_text()


def test_the_deck_byline_never_strands_a_separator(render_soup):
    soup = render_soup(
        "slide", "deck.md", text=document('title: "T"\ndue: 2026-09-12\n')
    )
    meta = " ".join(soup.select_one(".deck-meta").get_text().split())
    assert meta == "Due Sep 12"


def test_the_deck_byline_joins_all_three(render_soup):
    soup = render_soup(
        "slide", "deck.md",
        text=document(
            'title: "T"\nauthor: Ada Lovelace\ndate: 2026-09-01\n'
            "due: 2026-09-12T23:59\n"
        ),
    )
    meta = " ".join(soup.select_one(".deck-meta").get_text().split())
    assert meta == (
        "Ada Lovelace · Issued Sep 01 "
        "· Due Sep 12 · 23:59"
    )
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python3 -m pytest tests/test_dates.py -v -k "issued or due or show_date or deck"`
Expected: FAIL — `.doc-issued` and `.doc-due` do not exist; the templates still
print a bare `$date$`.

- [ ] **Step 3: Widen `has-byline` in the filter**

In `frontmatter-filter.lua.j2`, replace the `has-byline` line:

```lua
  meta["has-byline"] = (meta.author ~= nil or has_date or has_due) or nil
```

- [ ] **Step 4: Rewrite the document byline**

In `_doc-body.html.j2`, replace the `$if(has-byline)$` block:

```html
$if(has-byline)$
  <div class="doc-byline">
$if(author)$
    <div class="doc-meta">$for(author)$$author$$sep$ &middot; $endfor$</div>
$endif$
$if(has-dates)$
    {# .doc-date stays the wrapper it always was, so the margin-left/text-align
       rule that pushes it right keeps applying to one element rather than two. #}
    <div class="doc-date">
$if(date-label)$
      <div class="doc-issued"><span class="doc-date-label">Issued</span> <time datetime="$date-iso$">$date-label$</time></div>
$endif$
$if(due-label)$
      <div class="doc-due"><span class="doc-date-label">Due</span> <time datetime="$due-iso$">$due-label$</time></div>
$endif$
    </div>
$endif$
  </div>
$endif$
```

- [ ] **Step 5: Rewrite the deck byline**

In `_slide-body.html.j2`, replace the `$if(author)$ ... $else$ ... $endif$`
byline block with a single line. `due-needs-separator` is computed in the filter
because a pandoc template cannot OR two keys together:

```html
$if(has-byline)$
  {# One line, for the reason _doc-body.html.j2 gives: a newline between these
     collapses to a word space landing on one side of a separator. #}
  <p class="deck-meta">$if(author)$$for(author)$$author$$sep$ &middot; $endfor$$endif$$if(date-label)$$if(author)$ &middot; $endif$Issued <time datetime="$date-iso$">$date-label$</time>$endif$$if(due-label)$$if(due-needs-separator)$ &middot; $endif$Due <time datetime="$due-iso$">$due-label$</time>$endif$</p>
$endif$
```

- [ ] **Step 6: Style the label**

In `_page-style.css.j2`, after the `.doc-byline` rule:

```css
    /* "Issued" and "Due" name which date is which. Two bare dates in one
       column cannot be told apart, and the build stamp has been unlabelled
       until now only because it was the only one. */
    .doc-date-label {
      font-weight: 600;
      color: #7a828f;
      margin-right: 0.3em;
    }

    .doc-due {
      margin-top: 0.15rem;
    }
```

- [ ] **Step 7: Update `tests/test_byline.py`**

`.doc-date` is now a wrapper, so `when()` would return both dates concatenated.
Point the existing assertions at `.doc-issued`, and update the two literal
dates to their rendered form. Replace the `when` helper and the three tests
that call it:

```python
def when(soup) -> str | None:
    """The Issued line, which is what `date:` renders as.

    Reads .doc-issued rather than .doc-date: the latter is now the wrapper
    holding both Issued and Due, and its text would be a concatenation of the
    two rather than either one.
    """
    return _text(soup, ".doc-issued")
```

Then in `test_author_and_date`, `test_an_author_list_is_joined_with_middots`
and `test_date_only_renders_on_its_own`, change every
`assert when(soup) == "2026-09-02"` to:

```python
    assert when(soup) == "Issued Sep 02"
```

- [ ] **Step 8: Run the full suite**

Run: `python3 -m pytest -v`
Expected: PASS. `test_byline.py` and `test_dates.py` both green.

- [ ] **Step 9: Commit**

```bash
git add stencil/templates/ tests/
git commit -m "Label the byline dates Issued and Due

Two bare dates in one column cannot be told apart, and the build stamp
went unlabelled until now only because it was the only one there.

.doc-date stays the wrapper rather than being renamed, so the rule that
pushes it right keeps applying to one element instead of two. The deck
byline joins author, Issued and Due with middots and strands none of
them when a piece is missing -- which needs due-needs-separator computed
in the filter, since a pandoc template cannot OR two keys together."
```

______________________________________________________________________

### Task 4: regrid the document header

**Files:**

- Modify: `stencil/templates/_doc-body.html.j2`
- Modify: `stencil/templates/_page-style.css.j2`
- Test: `tests/test_byline.py`

**Interfaces:**

- Consumes: the markup from Task 3.

- Produces: `.doc-headrow` is gone; `header.doc-title` is a two-column grid.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_byline.py`:

```python
def test_the_author_precedes_the_context_in_source_order(render_soup):
    """Visually the byline has always sat under the title. In source order it
    did not -- .doc-context came between them, which is the order a screen
    reader announces and pdftotext extracts.
    """
    soup = render_soup(
        "doc", "order.md",
        text=document(
            'title: "T"\nsubtitle: "S"\nauthor: Ada Lovelace\n'
            'program: "CS 425"\nterm: "Fall 2026"\n'
        ),
    )
    header = header_of(soup)
    classes = [
        el["class"][0]
        for el in header.find_all(True, recursive=False)
        if el.get("class")
    ]
    assert classes == ["doc-identity", "doc-byline", "doc-context"]


def test_the_header_row_wrapper_is_gone(render_soup):
    soup = render_soup(
        "doc", "order.md",
        text=document('title: "T"\nprogram: "CS 425"\nauthor: Ada Lovelace\n'),
    )
    assert header_of(soup).select_one(".doc-headrow") is None
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python3 -m pytest tests/test_byline.py -v -k "source_order or wrapper"`
Expected: FAIL — the header's only direct child is `.doc-headrow`.

- [ ] **Step 3: Drop the wrapper from the markup**

In `_doc-body.html.j2`, delete the `<div class="doc-headrow">` opening tag and
its matching `</div>`, so `.doc-identity` and `.doc-context` become direct
children of `<header>`. Then move the whole `.doc-context` block so it sits
**after** the `$if(has-byline)$` block. Order inside `<header>` becomes:
`.doc-identity`, then the byline, then `.doc-context`.

Update the comment at the top of the file, which describes the old structure:

```
{# Document header: a two-column grid. Identity sits left, context right, and
   the byline spans both beneath them, so the width earns its keep instead of
   stacking five short lines down the page.

   Source order is identity, byline, context -- deliberately not the visual
   order. Grid placement puts context back on the top row, which lets the
   author follow the subtitle for a screen reader and for pdftotext without
   moving anything on screen. Both collapse to one left-aligned column on a
   narrow viewport -- see _page-style.css.j2.

   `has-context`, `has-byline` and `has-dates` come from frontmatter-filter.lua,
   which is also what guarantees a blank key reads as an absent one here. #}
```

- [ ] **Step 4: Replace the flex rows with the grid**

In `_page-style.css.j2`, replace the `.doc-headrow, .doc-byline` rule and the
`.doc-identity` rule:

```css
    /* The header's two rows, as a grid rather than two flex rows.

       Grid because source order and visual order have to differ: the byline is
       written second so the author follows the subtitle, and placement puts
       the context back up on row one. align-items: baseline so the program
       sits on the title's baseline rather than floating at the top of a
       two-line identity block. */
    header.doc-title {
      display: grid;
      grid-template-columns: minmax(18rem, 1fr) auto;
      align-items: baseline;
      column-gap: 1.5rem;
      row-gap: 0.35rem;
    }

    /* The 18rem minimum is the wrap threshold the flex-basis used to be: once
       the title has less than that, the context drops to its own line rather
       than being squeezed to one word per line. */
    .doc-identity {
      grid-column: 1;
      grid-row: 1;
      min-width: 0;
    }

    .doc-context {
      grid-column: 2;
      grid-row: 1;
    }

    .doc-byline {
      grid-column: 1 / -1;
      grid-row: 2;
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 0.35rem 1.5rem;
    }
```

Delete the now-duplicated `.doc-byline { margin-top: 0.6rem; }` rule further
down and fold `margin-top: 0.6rem` into the block above.

- [ ] **Step 5: Reset placement on narrow viewports**

In the same file, extend the existing `@media (max-width: 40rem)` block:

```css
    /* Narrow viewports: nothing is wide enough for two columns, so the header
       becomes the plain left-aligned stack it used to be -- now in source
       order, which puts the byline under the subtitle where it belongs.

       grid-row must be reset along with grid-column. Left at 1, identity and
       context would land in the same cell and overlap. */
    @media (max-width: 40rem) {
      header.doc-title {
        grid-template-columns: 1fr;
      }
      .doc-identity,
      .doc-context,
      .doc-byline {
        grid-column: 1;
        grid-row: auto;
      }
      .doc-context,
      .doc-date {
        margin-left: 0;
        text-align: left;
      }
    }
```

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest -v`
Expected: PASS.

- [ ] **Step 7: Check the rendered header by eye**

Run:

```bash
python3 -m pytest tests/test_byline.py -v -k source_order
```

Then build one real handout and open it, confirming the header looks unchanged:
title and subtitle left, program and term right, author and dates on the row
beneath, accent rule under all of it.

- [ ] **Step 8: Commit**

```bash
git add stencil/templates/ tests/test_byline.py
git commit -m "Regrid the document header so source order matches reading order

The byline has always sat under the title visually. In source order it
did not: .doc-context came between the subtitle and the author, which is
the order a screen reader announces and pdftotext extracts.

Two flex rows become one two-column grid, so the byline can be written
second and placed third. Nothing moves on screen. On a narrow viewport
it now stacks identity, byline, context -- the author finally follows
the subtitle there too. grid-row has to be reset alongside grid-column
in that query or the two row-one items land in the same cell."
```

______________________________________________________________________

### Task 5: documentation and the version bump

**Files:**

- Modify: `AUTHORING.md`
- Modify: `CHANGELOG.md`
- Modify: `stencil/__init__.py`

**Interfaces:**

- Consumes: everything above. Produces the 0.11.0 release.

- [ ] **Step 1: Document the keys in `AUTHORING.md`**

In the front-matter table (around line 124), add rows for `points` and `due`,
and change the `date` row's description to say it renders as `Issued`:

```markdown
| `points`      | Badge beside the title                          | Badge beside the title on the title slide       |
| `due`         | Byline, under `Issued`                          | Byline, after `Issued`                          |
| `date`        | Byline, labelled `Issued`                       | Byline, labelled `Issued`                       |
```

Add these two sections, placed before the existing `#### show_date`:

````markdown
#### `points`

What the document is worth, rendered as a badge beside the title:

```yaml
points: 50      # 50 pts
points: 1       # 1 pt
```

The plural follows the number, so a one-point exercise does not read `1 pts`.

A value that is not a number renders exactly as written, which is the escape
hatch for anything a number cannot say:

```yaml
points: "extra credit"
```

Unlike a date there is nothing to validate here — any string is a legible
answer to what a document is worth.

#### `date` and `due`

When the document was issued, and when it is owed. Both take an ISO date, and
optionally a 24-hour time after a `T`:

```yaml
date: 2026-09-01          # Issued Sep 01
date: 2026-09-01T21:45    # Issued Sep 01 · 21:45
due: 2026-09-12           # Due Sep 12
due: 2026-09-12T23:59     # Due Sep 12 · 23:59
```

The time appears only when you write one. `yyyy-mm-dd` and `yyyy-mm-ddThh:mm`
are the only shapes accepted; anything else fails the build rather than
rendering something approximate, and so does a date the calendar does not
contain:

```
due: 2026-02-30 is not a real date -- Feb 2026 has 28 days.
```

Both render in the byline, `Issued` above `Due`. They are labelled because two
bare dates in one column cannot be told apart; the build stamp went unlabelled
before `due` existed only because it was the only date there.
````

Update the existing note at line 249 to read in terms of the grammar while
keeping its point:

```markdown
Writing your own `date:` beats `show_date` — a date you wrote is the date you
meant — so use one or the other. `show_date` stamps the build date alone, with
no time: the clock reading when `make` ran says nothing about the document.
```

- [ ] **Step 2: Write the changelog entry**

At the top of `CHANGELOG.md`, under `# Changelog`'s preamble:

```markdown
## 0.11.0

- **`points` renders as a badge beside the title.** `points: 50` reads `50 pts`
  and `points: 1` reads `1 pt`; the plural is decided in the lua filter, which
  can compare a value where a pandoc template cannot. A non-numeric value
  renders verbatim, so `points: "extra credit"` works. This replaces the
  `subtitle: "Points: 50"` workaround, which overloaded a presentation field
  with a data field so that nothing could query or validate it.

- **`due` is a new key, and `date` now renders as `Issued`.** Both take
  `yyyy-mm-dd` or `yyyy-mm-ddThh:mm` and render as `Sep 12 · 23:59`, with
  the time shown only when one was written. Two bare dates in one column cannot
  be told apart, which is why the build stamp grew a label it never needed while
  it was alone.

  `date` accepted any string before this and now accepts only the grammar
  above — a behavior change with nothing in tree relying on the old latitude.
  Validation gates on shape, then field ranges, then the calendar, so the
  message names what actually broke. The calendar gate is an `os.time`
  round-trip rather than a days-in-month table: `os.time` normalizes a day the
  month does not have, so comparing the fields back detects it, and the leap
  rules come from the platform `mktime` instead of a century rule here that
  nothing would exercise until 2100.

- **The document header is a grid, and its source order is its reading order.**
  The byline has always sat under the title on screen; in source order
  `.doc-context` came between the subtitle and the author, which is the order a
  screen reader announces and `pdftotext` extracts. Nothing moves visually. On
  a narrow viewport the header now stacks identity, byline, context.
```

- [ ] **Step 3: Bump the version**

In `stencil/__init__.py`:

```python
__version__ = "0.11.0"
```

- [ ] **Step 4: Run the full suite one last time**

Run: `python3 -m pytest -v`
Expected: PASS, no skips beyond any the container runtime forces.

- [ ] **Step 5: Commit**

```bash
git add AUTHORING.md CHANGELOG.md stencil/__init__.py
git commit -m "Document points and due, and cut 0.11.0

Minor rather than patch: this changes what a generated package renders,
which is the line AGENTS.md draws."
```

______________________________________________________________________

## Verification before the PR

- [ ] `python3 -m pytest` passes in full, from the repo root.
- [ ] `git diff main --stat` touches only: the five templates, the three docs,
  `stencil/__init__.py`, the four test files, and this plan plus the
  spec it implements.
- [ ] A real handout builds and its header looks unchanged apart from the badge
  and the two labelled dates.

## Out of scope for this plan

The consuming course repository's own changes — dropping
`subtitle: "Points: 100"` from `final-presentation-1.md`, converting
`subtitle: "Points: 50"` to `points: 50` in `sprint-report.md`, sweeping the
remaining handouts, then `make reinstall REF=<pr>` and `make gen`. That is a
separate repository and a separate commit, done after this release is tagged.
It is recorded in the spec's A.6, not dropped.
