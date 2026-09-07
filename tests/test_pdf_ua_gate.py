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
# stn-tm8 -- 7.18.1 t2 and 7.18.5 t2, three of six real handouts, and the one
# thing 0.21.0's fixture did not contain when it reported full conformance.
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
| Review | Grace | 2    |
"""


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


@integration
def test_a_generated_pdf_conforms_to_pdf_ua_1(conformant_pdf):
    """0.21.0's claim, asserted rather than recorded in a changelog."""
    result = pipeline.verapdf(workdir=conformant_pdf, timeout=300)

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
    result = pipeline.verapdf(workdir=conformant_pdf, timeout=300)
    assert "Checked 1 PDF(s)" in result.stdout, (
        f"the gate did not say what it checked:\n{result.stdout}"
    )


@integration
def test_an_empty_directory_fails_the_gate(tmp_path):
    """THE BREACH THIS FILE EXISTS FOR.

    veraPDF's own exit code is 0 here -- measured, with no arguments it
    succeeds silently. If this test ever passes because the guard was removed
    rather than because it fired, the whole check-pdf target becomes a green
    light that means nothing.
    """
    result = pipeline.verapdf(workdir=tmp_path, timeout=120)

    assert result.returncode != 0, (
        "an empty directory passed the gate. veraPDF exits 0 on an empty file "
        "list, so this is exactly the silent pass the guard exists to stop."
    )
    assert "no PDF to check" in result.stderr, (
        f"the gate failed without saying why:\n{result.stderr}"
    )


@integration
def test_a_non_conformant_pdf_fails_the_gate(tmp_path, pdf_workspace):
    """The other direction: the gate has to reject something.

    A one-page PDF written by hand with no structure tree at all. Proving the
    gate rejects a bad file matters as much as proving it accepts a good one,
    because a gate that answers PASS to everything is indistinguishable from
    one that works until the day it is needed.
    """
    untagged = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n"
    )
    (tmp_path / "untagged.pdf").write_bytes(untagged)

    result = pipeline.verapdf(workdir=tmp_path, timeout=120)

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
