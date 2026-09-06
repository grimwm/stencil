"""What the PDF's structure tree has to carry, which the DOM cannot tell you.

stn-cuo. PDF/UA (ISO 14289-1) requires every /Figure to have /Alt or
/ActualText. HTML has no alt attribute on <figure>, and Chromium tags the
<figure> ELEMENT as /Figure, so a perfectly captioned figure reaches the PDF
unnamed. WCAG has no equivalent rule, pa11y sees nothing wrong, and a five-way
CI matrix stayed green over it -- which is why every assertion here reads a
built PDF's structure tree rather than the markup that produced it.

Measured on a 0.12.0 deck before the fix: 21 /Figure, 8 with /Alt (the <img>
inside each wrapper), 13 without -- exactly the 13 <figure> elements.
"""

from __future__ import annotations

import pytest
from pypdf import PdfReader
from pypdf.generic import IndirectObject

pytestmark = pytest.mark.integration

FIGURE_DOC = """---
title: "Figures"
lang: en
---

## Figures

![A labelled flow diagram](images/flow.svg)

```{.mermaid caption="Diagram: the review loop"}
flowchart LR
  A[Draft] --> B[Review]
  B --> A
```

A block with no caption at all, and one whose caption is explicitly empty.
Both take the default name, and both are here so the check over *every*
/Figure below covers them rather than covering only the happy path.

```{.mermaid}
flowchart LR
  C --> D
```

```{.mermaid caption=""}
flowchart LR
  E --> F
```

## A table

| Stage  | Owner | Days |
|--------|-------|------|
| Draft  | Ada   | 3    |
| Review | Grace | 2    |
"""


def _deref(obj):
    return obj.get_object() if isinstance(obj, IndirectObject) else obj


def _scope_of(node) -> str | None:
    """/Scope lives in /A, which is a dict or a list of dicts depending on how
    many attribute owners the node has. Both shapes occur in these PDFs."""
    attrs = _deref(node.get("/A"))
    if isinstance(attrs, dict):
        return attrs.get("/Scope")
    if isinstance(attrs, list):
        for item in attrs:
            item = _deref(item)
            if isinstance(item, dict) and "/Scope" in item:
                return item["/Scope"]
    return None


def struct_nodes(path) -> list[dict]:
    """Every element of the structure tree, flattened.

    Deliberately not the DOM. The whole class of defect this file guards
    against is one where the HTML is correct and only the PDF is wrong.
    """
    root = PdfReader(path).trailer["/Root"]
    tree = root.get("/StructTreeRoot")
    assert tree is not None, "the PDF is untagged; it has no structure tree"

    found: list[dict] = []

    def walk(node):
        node = _deref(node)
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, dict):
            return
        kind = node.get("/S")
        if kind is not None:
            found.append(
                {
                    "type": str(kind),
                    "alt": node.get("/Alt"),
                    "actual": node.get("/ActualText"),
                    "scope": _scope_of(node),
                }
            )
        if "/K" in node:
            walk(node["/K"])

    walk(_deref(tree).get("/K"))
    return found


def of_type(nodes, kind: str) -> list[dict]:
    return [n for n in nodes if n["type"] == kind]


def names(nodes) -> set[str]:
    return {str(n["alt"]) for n in nodes if n["alt"]}


@pytest.fixture(scope="module")
def figure_pdf(to_pdf):
    """Built once; the tests below read its structure tree and its text."""
    result, path = to_pdf("doc", "figures.md", text=FIGURE_DOC, stem="figures")
    assert result.returncode == 0, result.stderr[-3000:]
    return path


@pytest.fixture(scope="module")
def figure_nodes(figure_pdf):
    return struct_nodes(figure_pdf)


@pytest.fixture(scope="module")
def figure_text(figure_pdf):
    return "\n".join(page.extract_text() or "" for page in PdfReader(figure_pdf).pages)


def test_every_figure_in_the_pdf_has_an_accessible_name(figure_nodes):
    """The acceptance criterion, stated once against the artifact.

    Written over every /Figure rather than a counted subset: the failure this
    replaces was 13 unnamed figures that nobody had counted either.
    """
    figures = of_type(figure_nodes, "/Figure")
    assert figures, "no /Figure in the PDF at all -- the document draws two"
    unnamed = [f for f in figures if not (f["alt"] or f["actual"])]
    assert not unnamed, (
        f"{len(unnamed)} of {len(figures)} /Figure carry neither /Alt nor "
        "/ActualText, which PDF/UA rejects"
    )


def test_an_image_figure_is_named_by_its_caption(figure_nodes):
    assert "A labelled flow diagram" in names(of_type(figure_nodes, "/Figure"))


def test_a_mermaid_figure_is_named_by_its_caption(figure_nodes):
    """The five mermaid figures were the half of the failure with no text
    anywhere near them: the diagram is drawn by JS after load, so the HTML file
    contains no <svg> at all and the caption was the only name available."""
    assert "Diagram: the review loop" in names(of_type(figure_nodes, "/Figure"))


def test_the_drawn_diagram_itself_is_named_not_just_its_wrapper(figure_nodes):
    """role="img" on the mermaid container, which is a second /Figure.

    Without it the container is not tagged at all and the diagram is absent
    from the structure tree rather than unnamed -- measured. That would still
    pass the check above, because the <figure> wrapper around it is named, so
    this asserts the count rather than the presence of the name.
    """
    named = [
        f
        for f in of_type(figure_nodes, "/Figure")
        if str(f["alt"]) == "Diagram: the review loop"
    ]
    assert len(named) == 2, (
        "expected both the <figure> wrapper and the drawn diagram to carry the "
        f"caption as their name, found {len(named)}"
    )


def test_every_table_header_declares_its_scope(figure_nodes):
    """A regression guard, not a fix.

    The ticket recorded all 20 /TH on a real deck reaching the PDF with
    /Scope: None. Measured on that same PDF, every one of them carries
    /Scope /Column -- Chromium infers it from <thead>, and infers /Row for a
    row-header table too. So there was nothing to repair, and no Lua filter
    was added to repair it.

    What there was, was an undefended assumption: the inference depends on
    table shape, and a table pandoc emits without a <thead> would lose it
    silently. This pins the behaviour we actually depend on.
    """
    headers = of_type(figure_nodes, "/TH")
    assert headers, "no /TH in the PDF -- the document has a table with headers"
    unscoped = [h for h in headers if not h["scope"]]
    assert not unscoped, (
        f"{len(unscoped)} of {len(headers)} /TH carry no /Scope"
    )


def test_a_mermaid_caption_is_one_paragraph_not_one_per_word(render_soup):
    """pandoc.Caption takes BLOCKS first, and it was being handed inlines.

    pandoc coerced the list, so every Str and Space in the caption became a
    block of its own and one <figcaption> ended up holding four of them.

    Asserted on the markup, which is the exception in this file and worth
    saying why. The obvious assertion is on the PDF text layer, and it does not
    work: measured both ways, the extracted text is "Diagram: the review loop"
    whether the caption is one block or four, because the whitespace between
    two block boxes never reaches the text layer to be doubled in the first
    place -- the same rule that made 0.13.0's header run words together. So the
    only place the defect is visible is the DOM:

        fixed   <figcaption><span id=...>Diagram: the review loop</span>
        broken  <figcaption><div id=...>Diagram:\n\nthe\n\nreview\n\nloop</div>

    A Span also matters to the fix beside it: figure-name-filter wraps a
    single-paragraph caption in a Span and falls back to a Div otherwise, so a
    caption shaped wrongly here quietly changes the markup there too.
    """
    soup = render_soup(
        "doc",
        "caption.md",
        text=(
            '---\ntitle: "T"\n---\n\n## B\n\n'
            '```{.mermaid caption="Diagram: the review loop"}\n'
            "flowchart LR\n  A --> B\n```\n"
        ),
    )
    caption = soup.select_one("figcaption")
    assert caption is not None, "the mermaid figure lost its caption entirely"

    children = [c for c in caption.find_all(recursive=False)]
    assert len(children) == 1 and children[0].name == "span", (
        "expected the caption to be one inline run; a Div here means pandoc "
        f"split it into one block per word: {caption!r}"
    )
    assert " ".join(children[0].get_text().split()) == "Diagram: the review loop"


def test_a_mermaid_block_without_a_caption_still_names_its_diagram(render_soup):
    """The default name has to reach BOTH consumers, not just the figcaption.

    mermaid-figure-filter defaults a missing caption to "Diagram" and always
    did -- but it used the default without writing it back to the block, so
    data-caption, which is what the page script reads to name the drawn
    diagram, existed only when the author had written a caption. The result
    was a named <figure> wrapped around an anonymous diagram, which is half of
    the defect this release fixes and the half nothing would have reported.
    """
    soup = render_soup(
        "doc",
        "nocap.md",
        text='---\ntitle: "T"\n---\n\n## B\n\n```{.mermaid}\nflowchart LR\n  A --> B\n```\n',
    )
    pre = soup.select_one("pre.mermaid")
    assert pre is not None, "the mermaid block did not survive to the page"
    assert pre.get("data-caption") == "Diagram", (
        "the page script has no caption to name the diagram with: "
        f"{pre.attrs!r}"
    )


def test_an_empty_caption_is_treated_as_no_caption(render_soup):
    """caption="" is the one input that looks like it asks for no caption.

    In Lua the empty string is truthy, so the existing `or 'Diagram'` default
    did not fire for it: the figure reached the page with an empty
    <figcaption> and no accessible name -- precisely the PDF/UA failure this
    release exists to remove, reachable through the most natural way to ask
    for silence.
    """
    soup = render_soup(
        "doc",
        "emptycap.md",
        text=(
            '---\ntitle: "T"\n---\n\n## B\n\n'
            '```{.mermaid caption=""}\nflowchart LR\n  A --> B\n```\n'
        ),
    )
    figure = soup.select_one("figure")
    assert figure is not None
    assert figure.get("aria-labelledby"), (
        f"the figure has no accessible name: {figure.attrs!r}"
    )
    assert soup.select_one("figcaption").get_text(strip=True) == "Diagram"
