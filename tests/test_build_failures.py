"""What must fail the build, and what must not.

stn-rxn.5. --fail-if-warnings draws a line: a mistyped citation key is a
mistake in the source and stops the build, while a missing figure is reported
and shipped as a broken image. Both halves matter -- making the second one
fatal would be just as wrong as letting the first one through.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

CITED = """---
title: "A Document"
bibliography: refs.bib
---

## Body

Little's Law relates the three [@{key}].

::: {{#refs}}
:::
"""


def test_a_mistyped_citation_key_fails_the_build(render):
    """Otherwise '(**littel1961?**)' ships into a handout for someone to notice.

    The whole reason --fail-if-warnings is on. Pandoc's default is a console
    warning and a question-marked placeholder in the page, which survives right
    through to the reader.
    """
    result, output = render(
        "doc", "typo.md", text=CITED.format(key="littel1961"), output="typo.html"
    )

    assert result.returncode != 0, (
        "a citation key with no bibliography entry built successfully; "
        "--fail-if-warnings is not in effect"
    )
    assert not output.exists(), "a failed build should leave no output behind"


def test_a_correct_citation_key_builds(render):
    """The control. Without it the test above passes on any build failure."""
    result, _ = render(
        "doc", "ok.md", text=CITED.format(key="little1961"), output="ok.html"
    )
    assert result.returncode == 0, result.stderr


def test_a_missing_figure_does_not_fail_the_build(render):
    """embed-images.lua reports this on stderr by design -- it must stay non-fatal.

    A missing image is usually a path the author will notice immediately in the
    page. Promoting it to a build failure would make --fail-if-warnings
    unusable, because it does not go through pandoc's warning system at all and
    could not be suppressed per-file.
    """
    source = """---
title: "A Document"
---

## Body

![A figure that is not there](images/absent.svg)
"""
    result, output = render(
        "doc", "missing.md", text=source, output="missing.html"
    )

    assert result.returncode == 0, (
        "a missing figure became fatal; embed-images.lua's notice is supposed "
        f"to be advisory\n{result.stderr}"
    )
    assert output.exists()
    assert "absent.svg" in result.stderr, (
        "the missing figure should at least be named on stderr"
    )
