"""The gate that checks generated PDFs against PDF/UA-1, and its own breach.

stn-ips. `make check-access` runs pa11y over the HTML at WCAG 2.1 AA and never
opens a PDF. PDF/UA-1 (ISO 14289-1) is a different standard measured by a
different tool, and until this file nothing in the repository ran it: every
veraPDF number in the CHANGELOG was produced by hand.

The interesting assertion here is not that a conformant PDF passes. It is that
an EMPTY DIRECTORY FAILS. Measured against verapdf/cli:v1.30.2, veraPDF invoked
with no file arguments exits 0 and prints nothing, so the obvious gate --
`verapdf --flavour ua1 *.pdf` -- reports success having opened no file. Run
before `make pdf`, or after a clean, that gate is decoration.
"""

from __future__ import annotations

import pytest

from stencil import pipeline

# NOT a module-level mark. The two tests at the bottom read the generated
# compose file and need no container, so they belong in the fast tier that
# runs on every interpreter -- which is where a mis-escaped $ should be
# caught, not behind a Chromium build.
integration = pytest.mark.integration

# EVERY LINE OF THIS DOCUMENT IS A DEFECT THAT ONCE SHIPPED. The bold and the
# list were 7.1 t5 and 7.2 t20; the table was 7.1 t3. The hyperlink is
# stn-tm8 -- 7.18.1 t2 and 7.18.5 t2, three of six real handouts. The table's
# EMPTY CELL is stn-yly -- 7.2 t43, the other two handouts. Neither was in
# 0.21.0's fixture when it reported full conformance.
# A case that is absent from this document is a case veraPDF never sees.
GATE_DOC = """---
title: "Conformance"
lang: en
---

## Heading

Prose with **bold**, *italic* and [a hyperlink](https://example.com/guide).

- an item with **bold**
- an item with `code`

## A table

| Stage  | Owner | Days |
|--------|-------|------|
| Draft  | Ada   | 3    |
| Review | Grace |      |

------------------------------------------------------------------------

A paragraph after a horizontal rule. Bootstrap styles `hr` with an opacity
below 1, so Chromium emits a transparency group -- a Form XObject carrying
its own marked content. stn-48s is what happened when the artifact marking
wrapped the `Do` that paints it.
"""


# A DECK, not a document. stn-l68's acceptance asks for both, and until this
# every one of the twelve PDF/UA tests built a `doc` -- veraPDF had never seen
# a slide in CI.
#
# That is a whole template path unmeasured: slide-template.html rather than
# html-template.html, slide-sections.lua grouping blocks into cards, a
# GENERATED title slide nobody wrote, the toolbar, and the present-mode
# machinery. The classroom decks pass when measured by hand, so this is not
# expected to find a defect -- it is here so that the next change to any of
# those files cannot quietly stop being conformant.
#
# The same fixture-omission pattern cost four releases in a row: a missing
# hyperlink, a missing empty cell, a missing rule, a missing emoji. A missing
# WHOLE DOCUMENT KIND is the largest instance of it left.
GATE_DECK = """---
title: "Deck Conformance"
subtitle: "Every deck-only construct in one file"
author: Ada Lovelace
date: 2026-09-07
lang: en
---

## Columns and cards

::::: {.columns .cards data-cols=2}

:::: column
**Discover.** A card in a wrapped grid, which is 0.26.0's data-cols.
::::

:::: {.column .accent-2}
**Apply.** A second card carrying its own accent colour.
::::

:::::

## A lead-in and a takeaway

::: lead-in
The one thing to remember, set larger with an accent rule.
:::

- a list item with **bold**
- one with [a link](https://example.com/deck)

::: takeaway
Pinned to the bottom of the slide regardless of what is above it.
:::

## A table with a hole in it

| Column | Owner | Days |
|--------|-------|------|
| To Do  | Ada   | 3    |
| Done   |       |      |
"""


@pytest.fixture(scope="module")
def conformant_deck(to_pdf, pdf_workspace):
    """A deck built the way `make pdf` builds one, alone in a directory."""
    result, path = to_pdf(
        "slide", "gate-deck.md", text=GATE_DECK, stem="gate-deck", timeout=300
    )
    assert result.returncode == 0, f"the deck build failed\n{result.stderr[-3000:]}"

    checked = pdf_workspace / "gate-deck-check"
    checked.mkdir(exist_ok=True)
    for stale in checked.iterdir():
        stale.unlink()
    (checked / "gate-deck.pdf").write_bytes(path.read_bytes())
    return checked


@pytest.fixture(scope="module")
def conformant_pdf(to_pdf, pdf_workspace):
    """One PDF, built the way `make pdf` builds one, alone in a directory.

    Alone matters: the session workspace accumulates PDFs from other modules,
    and a gate that checks every *.pdf in it would be reporting on files this
    test did not build.
    """
    result, path = to_pdf("doc", "gate.md", text=GATE_DOC, stem="gate", timeout=300)
    assert result.returncode == 0, f"the pdf build failed\n{result.stderr[-3000:]}"

    checked = pdf_workspace / "gate-check"
    checked.mkdir(exist_ok=True)
    (checked / "gate.pdf").write_bytes(path.read_bytes())
    return checked



def scratch(pdf_workspace, name):
    """A fresh directory for one gate test, under the SESSION workspace.

    Not tmp_path, and the reason is measured rather than reasoned. On CI these
    tests mounted a tmp_path and the container reported every file "not there";
    the same tests pass locally, because Docker Desktop's file sharing hands a
    macOS host directory to the container whatever its mode is. I could not
    reproduce the CI behaviour on this machine -- a 0700 tmp_path with the
    image's own non-root user is still fully visible here -- so the mechanism
    is NOT established.

    What is established is that pdf_workspace subdirectories work in CI:
    conformant_pdf writes one and passes there. So these use the mechanism
    that demonstrably works instead of the theory I could not confirm.

    Worth recording why it went unnoticed: every other guard here asserts a
    NON-ZERO exit, and "the file could not be read" is also a non-zero exit.
    On CI they were green without opening a single PDF. Only
    test_a_pdf_nobody_asked_about_is_not_opened caught it, because it asserts
    on the COUNT rather than on the exit code -- which is exactly why that
    assertion was written that way.
    """
    directory = pdf_workspace / name
    directory.mkdir(exist_ok=True)
    for stale in directory.iterdir():
        stale.unlink()
    return directory


@integration
def test_a_generated_pdf_conforms_to_pdf_ua_1(conformant_pdf):
    """0.21.0's claim, asserted rather than recorded in a changelog."""
    result = pipeline.verapdf(
        workdir=conformant_pdf, files=["gate.pdf"], timeout=300
    )

    assert "PASS" in result.stdout, (
        "veraPDF did not report PASS for a PDF this build produced:\n"
        f"{result.stdout}\n{result.stderr[-2000:]}"
    )
    assert "FAIL" not in result.stdout, (
        f"veraPDF reported a PDF/UA-1 failure:\n{result.stdout}"
    )
    assert result.returncode == 0, (
        f"the gate rejected a conformant PDF:\n{result.stdout}\n{result.stderr[-2000:]}"
    )


@integration
def test_the_gate_reports_how_many_files_it_opened(conformant_pdf):
    """A count is what turns "it passed" into evidence about something."""
    result = pipeline.verapdf(
        workdir=conformant_pdf, files=["gate.pdf"], timeout=300
    )
    assert "Checked 1 of 1 PDF(s)" in result.stdout, (
        f"the gate did not say what it checked:\n{result.stdout}"
    )


@integration
def test_an_empty_file_list_fails_the_gate(pdf_workspace):
    """THE BREACH THIS FILE EXISTS FOR.

    veraPDF's own exit code is 0 here -- measured, with no arguments it
    succeeds silently. If this test ever passes because the guard was removed
    rather than because it fired, the whole check-pdf target becomes a green
    light that means nothing.
    """
    result = pipeline.verapdf(
        workdir=scratch(pdf_workspace, "gate-empty"), files=[], timeout=120
    )

    assert result.returncode != 0, (
        "an empty file list passed the gate. veraPDF exits 0 when it is given "
        "no files, so this is exactly the silent pass the guard exists to stop."
    )
    assert "given no file to check" in result.stderr, (
        f"the gate failed without saying why:\n{result.stderr}"
    )


@integration
def test_a_file_that_was_never_written_is_named(pdf_workspace):
    """Naming the files instead of globbing them buys this.

    A glob cannot tell "you gave me nothing" from "one of the six you promised
    is missing" -- it just checks five and reports success. The list is what
    `make pdf` just wrote, so a gap in it is a build failure worth a name.
    """
    here = scratch(pdf_workspace, "gate-missing")
    (here / "there.pdf").write_bytes(
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n"
    )

    result = pipeline.verapdf(
        workdir=here,
        files=["there.pdf", "missing.pdf"],
        timeout=120,
    )

    assert result.returncode != 0
    assert "missing.pdf" in result.stderr, (
        f"the gate did not name the file it could not find:\n{result.stderr}"
    )


@integration
def test_a_pdf_nobody_asked_about_is_not_opened(pdf_workspace):
    """The defect this ticket is actually about.

    cs425/classroom carries an 11 MB third-party book. `for f in *.pdf` failed
    the build on it -- a true report, and one nobody can act on, which is how
    a gate gets switched off. Asserting on the COUNT rather than only the exit
    code, because an implementation that checked it anyway and happened to
    pass would look identical from the outside.
    """
    here = scratch(pdf_workspace, "gate-foreign")
    conformant = here / "ours.pdf"
    conformant.write_bytes(
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n"
    )
    (here / "somebody-elses-book.pdf").write_bytes(conformant.read_bytes())

    result = pipeline.verapdf(
        workdir=here, files=["ours.pdf"], timeout=120
    )

    assert "Checked 1 of 1" in result.stdout, (
        f"the gate opened more than the one file it was given:\n{result.stdout}"
    )
    assert "somebody-elses-book" not in result.stdout, (
        f"the gate opened a file nobody asked about:\n{result.stdout}"
    )


@integration
def test_a_non_conformant_pdf_fails_the_gate(pdf_workspace):
    """The other direction: the gate has to reject something.

    A one-page PDF written by hand with no structure tree at all. Proving the
    gate rejects a bad file matters as much as proving it accepts a good one,
    because a gate that answers PASS to everything is indistinguishable from
    one that works until the day it is needed.
    """
    here = scratch(pdf_workspace, "gate-bad")
    untagged = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n"
    )
    (here / "untagged.pdf").write_bytes(untagged)

    result = pipeline.verapdf(
        workdir=here, files=["untagged.pdf"], timeout=120
    )

    assert result.returncode != 0, (
        f"an untagged PDF passed the gate:\n{result.stdout}\n{result.stderr[-2000:]}"
    )


def test_the_generated_compose_file_runs_the_same_script(doc_package):
    """One source of truth for the script, checked rather than trusted.

    The compose file and pipeline.verapdf() have to run the same text or this
    module measures something the build does not. Compose substitutes $VAR
    inside a service definition, so what the file carries is the doubled form.
    """
    compose = (doc_package / "docker-compose.yml").read_text()

    assert "check-pdf:" in compose, "the generated compose file has no check-pdf service"
    assert pipeline.VERAPDF_IMAGE in compose, (
        "the check-pdf service does not use the pinned veraPDF image"
    )

    expected = pipeline.VERAPDF_SCRIPT.replace("$", "$$")
    for line in expected.strip().splitlines():
        assert line.strip() in compose, (
            f"the compose file is missing a line of the gate script: {line.strip()!r}"
        )


def test_every_dollar_in_the_rendered_script_is_escaped(doc_package):
    """The failure this catches is silent, which is why it has its own test.

    An unescaped $found is substituted by compose before the shell sees it and
    arrives as the empty string. `[ "" -eq 0 ]` does not fail safely -- the
    guard stops guarding and nothing says so.
    """
    compose = (doc_package / "docker-compose.yml").read_text()
    start = compose.index("check-pdf:")
    end = compose.index("restart:", start)
    service = compose[start:end]

    singles = [
        line
        for line in service.splitlines()
        if "$" in line and "$$" not in line
    ]
    assert not singles, (
        "the check-pdf script carries an unescaped $, which compose will "
        f"substitute away before sh runs it: {singles}"
    )


def test_check_pdf_checks_the_same_files_the_pdf_target_wrote(doc_package):
    """stn-5ea listed this guard and 0.27.0 shipped without it.

    `make check-pdf with=hidden` must check the -hidden PDFs, not the plain
    ones. Both targets spell the filename with $(OUTPUT_SUFFIX), so they agree
    by construction -- but "by construction" is exactly the kind of claim that
    stops being true when someone edits one of the two lines, and checking the
    wrong six files would PASS.

    So this asserts the two lists are equal rather than asserting either one's
    shape: whatever `pdf` writes is what `check-pdf` opens.
    """
    makefile = (doc_package / "Makefile").read_text()

    written = set()
    checked = set()
    for line in makefile.splitlines():
        stripped = line.strip()
        if stripped.startswith("$(DC) run --rm pdf "):
            # `... pdf <stem>.html <stem>.pdf`
            written.add(stripped.split()[-1])
        elif stripped.startswith("$(DC) run --rm check-pdf"):
            checked.update(stripped.split()[4:])

    assert written, "the pdf target writes nothing; the fixture has no documents"
    assert checked == written, (
        "check-pdf and pdf disagree about which files exist.\n"
        f"  pdf writes:    {sorted(written)}\n"
        f"  check-pdf opens: {sorted(checked)}\n"
        "A mismatch here checks the wrong files and passes."
    )


def test_every_checked_filename_carries_the_output_suffix(doc_package):
    """The specific way the pair can drift.

    `make check-pdf with=hidden` builds Guide-hidden.pdf and must not then
    check Guide.pdf -- which would be a stale file from an earlier build, or
    absent entirely. A literal filename in the check-pdf line is the mistake
    this catches.
    """
    makefile = (doc_package / "Makefile").read_text()
    line = next(
        stripped
        for stripped in (line.strip() for line in makefile.splitlines())
        if stripped.startswith("$(DC) run --rm check-pdf")
    )

    names = line.split()[4:]
    assert names, "check-pdf is passed no filenames at all"
    bare = [n for n in names if "$(OUTPUT_SUFFIX)" not in n]
    assert not bare, (
        f"{len(bare)} filename(s) without $(OUTPUT_SUFFIX): {bare}. "
        "With WITH=hidden these name files the build did not write."
    )


@integration
def test_a_generated_deck_conforms_to_pdf_ua_1(conformant_deck):
    """stn-l68's other half. A deck is a separate template path end to end.

    Not a redundant copy of the document test: slide-template.html,
    slide-sections.lua, the generated title slide and the toolbar are all
    unique to this path, and none of them had ever been put in front of
    veraPDF.
    """
    result = pipeline.verapdf(
        workdir=conformant_deck, files=["gate-deck.pdf"], timeout=300
    )

    assert "PASS" in result.stdout, (
        "veraPDF did not report PASS for a deck this build produced:\n"
        f"{result.stdout}\n{result.stderr[-3000:]}"
    )
    assert "FAIL" not in result.stdout, (
        f"veraPDF reported a PDF/UA-1 failure on a deck:\n{result.stdout}"
    )
    assert result.returncode == 0, (
        f"the gate rejected a conformant deck:\n{result.stdout}\n"
        f"{result.stderr[-2000:]}"
    )


@integration
def test_the_deck_fixture_is_actually_a_deck(conformant_deck):
    """Vacuous-test guard.

    If `slide` ever renders through the document template the test above still
    passes and stops meaning anything. A deck's pages are landscape; a
    document's are portrait, so page geometry tells them apart without
    depending on any styling detail.
    """
    from pypdf import PdfReader

    page = PdfReader(conformant_deck / "gate-deck.pdf").pages[0]
    box = page.mediabox
    assert float(box.width) > float(box.height), (
        f"the fixture is not landscape ({box.width} x {box.height}); "
        "it may have been rendered as a document rather than a deck"
    )


def test_with_hidden_actually_expands_to_the_hidden_pdfs(doc_package):
    """0.28.1 asserted on Makefile TEXT and never expanded the variable.

    A regression setting OUTPUT_SUFFIX empty leaves both target lines equal and
    both of that release's assertions passing -- which is precisely the
    criticism that release made of itself ("the rule being present and the rule
    working are different claims") and then did not apply to its own test.

    `make -n` expands variables and prints the recipe without running it, so
    this is bounded: no container, no build, and it measures the expansion
    rather than the template.
    """
    import shutil
    import subprocess

    if shutil.which("make") is None:
        pytest.skip("make is not installed")

    def recipe(*args):
        result = subprocess.run(
            ["make", "--no-print-directory", "-n", "check-pdf", *args],
            cwd=doc_package,
            capture_output=True,
            text=True,
        )
        # A make that FAILS can still have printed matching recipe lines before
        # it died, and parsing only stdout would let a broken Makefile pass.
        assert result.returncode == 0, (
            f"make -n {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
        )
        return [
            line
            for line in result.stdout.splitlines()
            if "run --rm check-pdf" in line
        ]

    plain = recipe()
    hidden = recipe("with=hidden")
    assert plain and hidden, (plain, hidden)

    assert plain != hidden, (
        "with=hidden expanded to the same command as a plain build; "
        f"OUTPUT_SUFFIX is not reaching check-pdf.\n{plain}\n{hidden}"
    )
    assert all("-hidden.pdf" in line for line in hidden), (
        f"with=hidden did not name the -hidden PDFs:\n{hidden}"
    )
    assert not any("-hidden.pdf" in line for line in plain), (
        f"a plain build named the -hidden PDFs:\n{plain}"
    )
