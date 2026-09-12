"""Generated files carry LF, whatever platform generated them (stn-at4).

`Path.write_text(data)` with no `newline=` opens in text mode with
`newline=None`, which translates every `\n` to `os.linesep` on write. On
Windows that is `\r\n`, so `stencil gen` there used to write CRLF into every
file it produced -- the lockfiles, the Lua filters, the Makefile, the compose
file, the pandoc templates.

THAT IS ALREADY FIXED, and this file exists because nothing was holding it.
stn-h5q (#124) moved every generated write onto `write_text_nofollow`, which
does `handle.write(text.encode("utf-8"))` onto a binary `os.fdopen(fd, "wb")`
-- bytes, not text mode, so no translation can happen on any platform. The fix
arrived as a side effect of a containment change, which is exactly the kind of
property that gets reverted by the next refactor with nobody noticing.

WHY IT MATTERS, since the symptom is invisible on POSIX: stn-qge's format-md
entrypoint and stn-egv's browser image both refuse a lockfile whose sha256 is
not the one stencil rendered, and an LF digest cannot match CRLF bytes. A
package generated on Windows would have `make format-md`, `make pdf` and
`make check-access` refuse their own untampered lockfiles, with a message
telling the reader to run `stencil gen` -- the step that produced them. That is
the worst shape a guard can fail in: it accuses the honest user and tells them
to repeat the cause.

THE MANAGED `.gitignore` IS DELIBERATELY NOT COVERED HERE. `install_gitignore`
still writes with `newline=None`, and that was considered and left alone rather
than overlooked. It is the AUTHOR's file, not one stencil generates -- the
comment above that write already protects it, and "a `.gitignore` that is a
symlink into a dotfiles repository is a thing people really do". On Windows it
round-trips CRLF correctly today, and `newline=""` would rewrite every line of
it. No measured harm attaches to a CRLF `.gitignore`: nothing hashes it, no
container reads it, and git does not care. stn-at4's actual harm is entirely
about files stencil GENERATES.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from stencil.generate import open_for_write_nofollow, write_text_nofollow

# Two documents and a deck, so the generated set includes the Lua filters, the
# lockfiles, the pandoc templates, the Makefile and the compose file rather
# than a minimal package that would prove less.
CONFIG = {
    "output_dir": "out",
    "templates": [{"src": "Makefile.j2"}, {"src": "docker-compose.yml.j2"}],
    "packages": {
        "demo": {
            "name": "Demo",
            "package_type": "none",
            "docs": ["Guide.md"],
            "slides": ["Deck.md"],
        }
    },
}


# --- the mechanism pin: this is the one with teeth -------------------------
#
# It fails on ubuntu CI, where `os.linesep` is already "\n" and no end-to-end
# byte check ever could. It is testing HOW the write happens, not what this
# platform happens to produce, which is the only way to hold a property whose
# violation is invisible on every machine the suite runs on today.


def test_the_generated_write_path_opens_in_binary_mode(tmp_path: Path):
    """`open_for_write_nofollow` hands back a BINARY stream.

    A text-mode stream is where newline translation lives. Asserting on the
    stream's mode -- rather than on the bytes it produced -- is what makes this
    fail on a platform that does not translate.
    """
    with open_for_write_nofollow(tmp_path / "written.txt") as handle:
        assert "b" in handle.mode, (
            f"the generated-file write path opened in mode {handle.mode!r}; "
            "a text-mode stream translates \\n to os.linesep on write, which "
            "is CRLF on Windows"
        )
        assert isinstance(handle, (io.RawIOBase, io.BufferedIOBase)), (
            "expected a binary stream, got a text wrapper"
        )
        with pytest.raises(TypeError):
            # A text stream would accept this; a binary one must not. The
            # cheapest possible proof that no encoder sits in the path.
            handle.write("str, not bytes")


def test_write_text_nofollow_writes_the_exact_utf8_bytes(tmp_path: Path):
    """Content in, `content.encode("utf-8")` out -- byte for byte.

    Two newlines and a non-ASCII character, so the assertion covers both the
    translation this ticket is about and the encoding decision
    `write_text_nofollow`'s docstring settles (UTF-8 on every platform, rather
    than the locale's preferred encoding).
    """
    content = "first\nsecond\nthird — dashed\n"
    target = tmp_path / "written.txt"

    write_text_nofollow(target, content)

    assert target.read_bytes() == content.encode("utf-8")
    assert b"\r" not in target.read_bytes()


# --- the end-to-end pin: belt and braces, and it cannot fail here ----------


def test_no_generated_file_contains_a_carriage_return(generate_package):
    """Every file `gen` produces is free of `\r`.

    THIS TEST CANNOT FAIL ON POSIX and nobody should count it as coverage on
    its own: `os.linesep` is already "\n" here, so a text-mode write would
    produce identical bytes and this would stay green through the exact
    regression it is named after. The mechanism pins above are what actually
    hold the property; this one is here for a future Windows job, and to state
    the end-to-end claim in the form a reader looks for.

    Read with `read_bytes()` rather than `open(newline="")`: the point is the
    bytes on disk, and no decoding step should stand between the file and the
    assertion.
    """
    package = generate_package(CONFIG)

    written = sorted(p for p in package.rglob("*") if p.is_file())
    assert written, "setup: the package generated no files"

    offenders = [p.name for p in written if b"\r" in p.read_bytes()]
    assert not offenders, (
        f"{len(offenders)} generated file(s) contain a carriage return: "
        f"{', '.join(offenders)}"
    )
