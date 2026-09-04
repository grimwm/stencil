"""The generated Makefile's pdf wiring.

stn-rxn.8. The Docker-free counterpart to the argv tests: assert on the rendered
Makefile text rather than on a build.

The contract that matters is $(OUTPUT_SUFFIX) appearing on *both* sides of each
conversion. Drop it from one side and `make pdf with=hidden` prints the plain
HTML into the -hidden PDF, or the -hidden HTML into the plain one. Nothing
errors, the file looks fine, and it is the wrong document -- which for an
answer-key build is the worst failure available.
"""

from __future__ import annotations

import re

import pytest

MAKEFILE_TEMPLATES = [{"src": "Makefile.j2"}, {"src": "docker-compose.yml.j2"}]


def recipe(makefile: str, target: str) -> list[str]:
    """The recipe lines of a target, without its dependency line."""
    lines = makefile.splitlines()
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(target)}\s*:", line):
            body = []
            for following in lines[i + 1 :]:
                if following.startswith("\t"):
                    body.append(following.strip())
                elif following.strip() == "" or following.startswith("#"):
                    continue
                else:
                    break
            return body
    raise AssertionError(f"no {target} target in the generated Makefile")


def dependencies(makefile: str, target: str) -> list[str]:
    """The prerequisites of a target, ignoring its trailing ## help text.

    The lookahead keeps this from matching a `NAME := value` assignment; the
    help text has to be stripped after the fact rather than excluded by the
    pattern, because it contains an `=` itself ("use with=hidden").
    """
    match = re.search(rf"^{re.escape(target)}\s*:(?!=)(.*)$", makefile, re.MULTILINE)
    assert match, f"no {target} target in the generated Makefile"
    return match.group(1).split("##")[0].split()


@pytest.fixture
def makefile(generate_package):
    def _makefile(**package):
        package.setdefault("name", "Demo")
        package.setdefault("package_type", "none")
        pkg = generate_package(
            {"templates": MAKEFILE_TEMPLATES, "packages": {"demo": package}}
        )
        return (pkg / "Makefile").read_text()

    return _makefile


@pytest.fixture
def pages_makefile(makefile):
    return makefile(docs=["Guide.md"], slides=["Deck.md"])


def test_the_suffix_appears_on_both_sides_of_every_conversion(pages_makefile):
    """The one that turns a build into the wrong document, silently."""
    conversions = [
        line for line in recipe(pages_makefile, "pdf") if "run --rm pdf" in line
    ]
    assert conversions, "the pdf target converts nothing"

    for line in conversions:
        _, html, pdf = line.rsplit(maxsplit=2)
        assert html.endswith("$(OUTPUT_SUFFIX).html"), (
            f"the input of a pdf conversion is missing $(OUTPUT_SUFFIX): {line}"
        )
        assert pdf.endswith("$(OUTPUT_SUFFIX).pdf"), (
            f"the output of a pdf conversion is missing $(OUTPUT_SUFFIX): {line}"
        )
        assert html.removesuffix("$(OUTPUT_SUFFIX).html") == pdf.removesuffix(
            "$(OUTPUT_SUFFIX).pdf"
        ), f"a pdf conversion pairs two different documents: {line}"


def test_every_page_gets_a_conversion(pages_makefile):
    """Both the document and the deck, not just whichever the loop reached."""
    conversions = "\n".join(recipe(pages_makefile, "pdf"))
    assert "Guide$(OUTPUT_SUFFIX).pdf" in conversions
    assert "Deck$(OUTPUT_SUFFIX).pdf" in conversions


def test_pdf_depends_on_doc(pages_makefile):
    """Otherwise a PDF can be printed from HTML left over from a previous run."""
    assert "doc" in dependencies(pages_makefile, "pdf")


def test_doc_depends_on_slide_so_pdf_covers_the_deck(pages_makefile):
    """pdf converts decks too, and reaches them only through doc."""
    assert "slide" in dependencies(pages_makefile, "doc")


def test_clean_removes_the_pdf_globs(pages_makefile):
    """Both the plain and the WITH= variants, alongside the html ones."""
    removed = " ".join(recipe(pages_makefile, "clean-pkg"))
    for pattern in (
        "Guide.pdf",
        "Guide-*.pdf",
        "Deck.pdf",
        "Deck-*.pdf",
        "Guide.html",
        "Guide-*.html",
    ):
        assert pattern in removed, f"clean-pkg leaves {pattern} behind"


def test_pdf_is_declared_phony(pages_makefile):
    """It produces files named after the target's dependencies, not itself."""
    phony = re.search(r"^\.PHONY:(.*)$", pages_makefile, re.MULTILINE)
    assert phony and "pdf" in phony.group(1).split()


def test_a_slides_only_package_does_not_claim_to_generate_docs(makefile):
    """stn-4t0. `doc` exists for a slides-only package only to reach `slide`.

    The closing echo used to sit outside the has_docs guard, so building a
    deck-only package printed "Generated docs" after generating no documents.
    """
    text = makefile(slides=["Deck.md"])

    assert "Generated docs" not in text, (
        "a package with no docs still reports that it generated some"
    )
    assert "Generated slides" in text, "the deck report should survive"


def test_a_slides_only_package_describes_itself_as_decks(makefile):
    """The same mistake in the help text, which `make help` prints verbatim."""
    summary = re.search(r"^doc\s*:.*?##(.*)$", makefile(slides=["Deck.md"]), re.M)
    assert summary and "documents" not in summary.group(1)


def test_a_package_with_both_still_reports_both(makefile):
    """The guard must not silence the report for a package that does have docs."""
    text = makefile(docs=["Guide.md"], slides=["Deck.md"])

    assert "Generated docs" in text
    assert "Generated slides" in text


@pytest.mark.parametrize(
    ("package", "expected"),
    [
        ({"docs": ["Guide.md"], "slides": ["Deck.md"]}, True),
        ({"docs": ["Guide.md"]}, True),
        ({"slides": ["Deck.md"]}, True),
        ({}, False),
    ],
    ids=["docs and slides", "docs only", "slides only", "neither"],
)
def test_pdf_is_gated_on_having_pages(makefile, package, expected):
    """It is gated on has_pages with no config key, so the gate needs a test."""
    text = makefile(**package)
    assert bool(re.search(r"^pdf\s*:", text, re.MULTILINE)) is expected
