"""`brand`: what the document belongs to, above the program it is part of.

Either a name or a picture, and the difference is decided by the value rather
than by a second key -- `file://img/logo.svg` and `img/logo.svg` are pictures,
`Southern Illinois University` is a name.

Two things here are less obvious than they look.

`file://` is rewritten to a plain relative path before the Image is built.
embed-images.lua classes anything matching `^%a[%w+.-]*://` as remote and
leaves it alone, so a file:// URI would be the single spelling that never got
inlined: the page would carry a path rather than the picture, and
html-to-pdf.js fails the build on an asset it cannot fetch. Stripping the
scheme hands it over as something that filter knows how to bundle.

And a logo with no `brand-alt` fails the build rather than defaulting to
`alt=""`. A logo is often the only thing naming the institution on the page,
so a silent empty alt drops that entirely for a screen reader -- and
`make check-access` runs pa11y at WCAG2AA.
"""

from __future__ import annotations

import re

import pytest

LOGO = "logo.svg"
LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40">'
    '<rect width="120" height="40" fill="#29417a"/></svg>\n'
)


def document(front_matter: str) -> str:
    return f"---\n{front_matter}---\n\n## Body\n\nSome prose.\n"


def deck(front_matter: str) -> str:
    return f"---\n{front_matter}---\n\n## A slide\n\nSome prose.\n"


@pytest.fixture
def with_logo(doc_package):
    """A generated package carrying a logo next to the markdown."""
    (doc_package / LOGO).write_text(LOGO_SVG)
    return doc_package


def brand_of(soup):
    return soup.select_one("header.doc-title .doc-brand")


# --- a name ----------------------------------------------------------------


@pytest.mark.integration
def test_a_name_renders_as_text(render_soup):
    soup = render_soup(
        "doc",
        "b.md",
        text=document('title: "T"\nbrand: "Southern Illinois University"\n'),
    )
    brand = brand_of(soup)
    assert brand is not None, "no brand rendered"
    assert " ".join(brand.get_text().split()) == "Southern Illinois University"
    assert brand.select_one("img") is None
    assert "doc-brand--image" not in brand.get("class", [])


@pytest.mark.integration
def test_a_name_needs_no_alt(render):
    """brand-alt is required of a picture, not of a name."""
    result, _ = render(
        "doc", "b.md", text=document('title: "T"\nbrand: "SIU"\n')
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.integration
def test_the_brand_comes_before_the_program(render_soup):
    """It is the parent of the program, so it sits above it and pushes the
    rest of the context column down."""
    soup = render_soup(
        "doc",
        "b.md",
        text=document('title: "T"\nbrand: "SIU"\nprogram: "CS 425"\nterm: "Fall 2026"\n'),
    )
    context = soup.select_one(".doc-context")
    classes = [
        c
        for child in context.find_all(recursive=False)
        for c in child.get("class", [])
    ]
    assert classes[0] == "doc-brand", f"brand is not first: {classes}"
    assert "doc-program" in classes


@pytest.mark.integration
def test_a_brand_alone_opens_the_context_column(render_soup):
    """has-context ORs brand in. Without that, a document branded but with no
    program rendered no column at all."""
    soup = render_soup("doc", "b.md", text=document('title: "T"\nbrand: "SIU"\n'))
    assert soup.select_one(".doc-context") is not None


@pytest.mark.integration
def test_a_blank_brand_renders_nothing(render_soup):
    soup = render_soup("doc", "b.md", text=document('title: "T"\nbrand:\n'))
    assert brand_of(soup) is None
    assert soup.select_one(".doc-context") is None


# --- a picture -------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "value", [f"file://{LOGO}", LOGO], ids=["file-uri", "relative-path"]
)
@pytest.mark.integration
def test_a_logo_renders_as_an_inlined_image(render_soup, with_logo, value):
    """And is inlined. A src still pointing at the file would mean the page is
    not self-contained and `make pdf` cannot fetch it."""
    soup = render_soup(
        "doc",
        "b.md",
        text=document(f'title: "T"\nbrand: "{value}"\nbrand-alt: "SIU"\n'),
    )
    img = brand_of(soup).select_one("img")
    assert img is not None, "the logo did not render as an image"
    assert img["src"].startswith("data:"), (
        f"the logo was not inlined: {img['src'][:60]!r}"
    )
    assert img["alt"] == "SIU"


@pytest.mark.integration
def test_a_logo_marks_its_box_as_an_image(render_soup, with_logo):
    """The class the sizing and the descender-gap rules hang off."""
    soup = render_soup(
        "doc",
        "b.md",
        text=document(f'title: "T"\nbrand: "{LOGO}"\nbrand-alt: "SIU"\n'),
    )
    assert "doc-brand--image" in brand_of(soup).get("class", [])


@pytest.mark.integration
def test_a_logo_without_alt_fails_the_build(render, with_logo):
    """Refused rather than defaulted to alt="". The message has to say what to
    add, because this fires on an otherwise reasonable-looking document."""
    result, _ = render(
        "doc", "b.md", text=document(f'title: "T"\nbrand: "{LOGO}"\n')
    )
    assert result.returncode != 0, "a logo with no alt text built anyway"
    assert "brand-alt" in result.stderr, (
        f"the error does not name the missing key: {result.stderr[-400:]}"
    )


@pytest.mark.integration
def test_a_blank_alt_counts_as_missing(render, with_logo):
    result, _ = render(
        "doc", "b.md", text=document(f'title: "T"\nbrand: "{LOGO}"\nbrand-alt:\n')
    )
    assert result.returncode != 0, "an empty brand-alt built anyway"


@pytest.mark.integration
def test_a_remote_logo_is_left_as_a_reference(render_soup, with_logo):
    """embed-images.lua does not fetch remote images at build time, and this
    is not the place to overturn that. It stays a URL."""
    soup = render_soup(
        "doc",
        "b.md",
        text=document(
            'title: "T"\nbrand: "https://example.com/logo.png"\nbrand-alt: "X"\n'
        ),
    )
    img = brand_of(soup).select_one("img")
    assert img is not None
    assert img["src"] == "https://example.com/logo.png"


@pytest.mark.integration
def test_a_name_that_merely_contains_a_dot_is_still_a_name(render_soup):
    """The picture test is an image extension, not the presence of a dot."""
    soup = render_soup(
        "doc", "b.md", text=document('title: "T"\nbrand: "St. Louis U."\n')
    )
    assert brand_of(soup).select_one("img") is None


# --- decks -----------------------------------------------------------------


@pytest.mark.integration
def test_a_deck_carries_the_brand_above_its_context(render_soup, with_logo):
    soup = render_soup(
        "slide",
        "b.md",
        text=deck(f'title: "T"\nbrand: "{LOGO}"\nbrand-alt: "SIU"\nprogram: "CS 425"\n'),
    )
    brand = soup.select_one(".slide--title .deck-brand")
    assert brand is not None, "the title slide rendered no brand"
    assert brand.select_one("img")["src"].startswith("data:")


# --- the logo's proportions ------------------------------------------------


@pytest.fixture
def css(generate_package) -> str:
    """Both stylesheets, past the inlined Bootstrap in each.

    The document rules are in html-template.html and the deck rules in
    slide-template.html, so a fixture reading only the first reports "no rule"
    for every deck selector -- which is indistinguishable from the rule having
    been deleted.
    """
    package = generate_package(
        {
            "templates": [{"src": "html-template.html.j2"}],
            "packages": {
                "demo": {
                    "name": "D",
                    "package_type": "none",
                    "docs": ["a.md"],
                    "slides": ["d.md"],
                }
            },
        }
    )
    sheets = []
    for name, marker in (
        ("html-template.html", "/* Document title"),
        ("slide-template.html", "/* --- Deck layout ---"),
    ):
        text = (package / name).read_text()
        sheets.append(text[text.index(marker) :])
    return "\n".join(sheets)


def declarations_of(css: str, selector: str) -> dict[str, str]:
    """One rule's declarations as a property -> value map.

    Comments stripped first: a CSS comment holds no braces, so a
    selector-matching pattern otherwise reads the comment above a rule as part
    of its selector list. Same reason test_task_lists.py does it.
    """
    stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for match in re.finditer(r"([^{}]+)\{([^}]*)\}", stripped):
        if selector in [s.strip() for s in match.group(1).split(",")]:
            return {
                k.strip(): v.strip()
                for k, _, v in (d.partition(":") for d in match.group(2).split(";"))
                if k.strip()
            }
    return {}


@pytest.mark.parametrize(
    "selector", [".doc-brand img", ".slide--title .deck-brand img"]
)
def test_a_logo_is_capped_without_being_distorted(css, selector):
    """`max-width` and `max-height` scale a replaced element proportionally --
    but only while `width` and `height` are both auto. Pin either one and the
    cap becomes a stretch, silently, for whichever logo happens to be the
    wrong shape.

    Measured in a browser to confirm the pair does what the spec says: a
    900x60 wordmark renders 223.95x14.93 and a 50x600 crest renders 3.66x44 --
    each at its exact natural ratio, one limited by width and the other by
    height. This test guards the two declarations that make that true.
    """
    declarations = declarations_of(css, selector)
    assert declarations, f"no rule for {selector}"
    assert declarations.get("max-height"), f"{selector} has no height cap"
    assert declarations.get("max-width"), f"{selector} has no width cap"
    assert declarations.get("width") == "auto", (
        f"{selector} pins width, which distorts a logo capped by height: "
        f"{declarations!r}"
    )
    assert declarations.get("height") == "auto", (
        f"{selector} pins height, which distorts a logo capped by width: "
        f"{declarations!r}"
    )
