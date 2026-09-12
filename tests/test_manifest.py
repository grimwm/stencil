"""gen writes a per-package manifest from one derivation shared with
`get_generated_files`; `clean` reading it is a separate ticket (stn-2x4's
other children) and is out of scope here.

stn-p9a: `clean_generated` derives what to remove from `.config.yaml`, so the
one command someone reaches for BECAUSE their config broke is the one that
cannot answer, and a config edited after `gen` ran makes `clean` guess at a
file list that no longer matches what is on disk. The fix is a manifest --
`<pkg_dir>/.stencil-manifest.json`, written at the end of a successful
`generate_package` -- that `clean` will read in preference to the config.
This file is the gen side only: it proves the manifest gets written, names
everything `gen` actually produced, and matches `get_generated_files` exactly,
because the two are meant to come from one extracted derivation
(`package_entries`) rather than a second walk of the config. The repository
has already paid for that kind of drift twice -- `injected_sources` and
stn-8wt -- and a manifest built by a parallel walk would be a third instance
of it.

The manifest names below are guesses at what the implementation will call
them: `MANIFEST_NAME`, `MANIFEST_VERSION`, `package_entries`, `read_manifest`.
Imported in one place, at the top, so a rename on the implementation side is a
one-line fix here rather than a search-and-replace across every test (see
test_config_fail_closed.py's module docstring for the same convention).

`write_manifest` is deliberately NOT imported here. Nothing in this file
calls it: the writer is exercised through `generate_package`.

`ManifestError` WAS deliberately not imported, on the same reasoning, until
stn-jez: `read_manifest`'s required-field checks are a pure parser property
("does this document have the fields it must") independent of `clean`, and
test_config_fail_closed.py's convention for exactly that shape is a DIRECT
call to the raising function, not only a CLI-driven one. Imported below for
that reason; every OTHER `ManifestError` case in this file still goes
through the CLI only (see the parser-rejection section), because those are
about what `_clean_one_directory` does with the error, not about the parser.

`read_manifest` takes the PATH TO A MANIFEST FILE and returns the parsed
document, rather than an (output_base, pkg_dir) pair returning just the
entries. That is the seam this file asserts against, and stn-2x4.4/.8 were
corrected to match it: a pure parser that knows nothing about `clean` is the
one the architecture review asked for, and these tests need the whole
document -- `manifest_version`, `stencil_version`, `package`, `dir` -- not
only the entries list.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from jinja2 import UndefinedError

from stencil.generate import (
    MANIFEST_NAME,
    MANIFEST_VERSION,
    ManifestError,
    check_glob_vocabulary,
    get_generated_files,
    package_contexts,
    package_entries,
    read_manifest,
)

from test_cli import GOOD_AND_BROKEN_CONFIG, run_cli, write_config

MAKEFILE_TEMPLATES = [{"src": "Makefile.j2"}, {"src": "docker-compose.yml.j2"}]


def _on_disk(generated) -> set[str]:
    """Every file gen actually wrote, relative to the package dir, POSIX,
    excluding the manifest itself -- which does not list itself (it is added
    on top by get_generated_files, so the managed .gitignore section and a
    config-derived clean still cover it)."""
    return {
        path.relative_to(generated).as_posix()
        for path in generated.rglob("*")
        if path.is_file() and path.name != MANIFEST_NAME
    }


def _expected_slice(config: dict, package_id: str = "demo") -> set[str]:
    """get_generated_files' slice for one package, unprefixed, manifest
    excluded -- the no-drift guarantee package_entries exists to keep true.

    THE TOP-LEVEL output_dir IS PART OF THE PREFIX (stn-jl3), and getting
    that wrong here fails in the worst available direction rather than
    loudly: a prefix of `demo/` against entries now spelled `out/demo/...`
    matches nothing, `_expected_slice` returns an EMPTY SET, and
    `test_manifest_entries_equal_get_generated_files_slice_end_to_end`
    compares the manifest against nothing at all. The whole point of this
    helper is to catch package_entries and get_generated_files drifting
    apart; a vacuous version of it still passes forever. Never loosen the
    match to fix a failure here -- widen the prefix to whatever
    get_generated_files now prepends."""
    declared = config.get("output_dir")
    output_prefix = (
        "/".join(Path(declared).parts) + "/"
        if isinstance(declared, str) and Path(declared).parts
        else ""
    )
    pkg_dir = config["packages"][package_id].get("dir", package_id)
    prefix = f"{output_prefix}{pkg_dir}/"
    return {
        entry.removeprefix(prefix)
        for entry in get_generated_files(config)
        if entry.startswith(prefix) and entry != f"{prefix}{MANIFEST_NAME}"
    }


# --- the manifest says what gen actually put on disk ------------------------


def test_manifest_names_every_file_on_disk(generate_package):
    """If gen ever writes a file the manifest does not name -- the next
    injected template, the same failure mode test_package_sources.py guards
    for get_generated_files -- a manifest-driven `clean` would leave that
    file behind with nothing pointing at it."""
    config = {
        "output_dir": "out",
        "templates": MAKEFILE_TEMPLATES,
        "packages": {
            "demo": {
                "name": "Demo",
                "package_type": "none",
                "docs": ["Guide.md"],
                "slides": ["Deck.md"],
            }
        },
    }
    generated = generate_package(config)

    manifest = read_manifest(generated / MANIFEST_NAME)
    entries = set(manifest["entries"])

    assert _on_disk(generated) - entries == set(), (
        "gen wrote a file the manifest does not name, so a manifest-driven "
        "clean would leave it behind"
    )


def test_manifest_names_a_symlinked_brand_image_by_the_config_name(
    tmp_path, generate_package
):
    """The drift this manifest is uniquely exposed to: stn-8wt was
    `copy_brand_image` naming the copy from the RESOLVED symlink target while
    `get_generated_files` named it from the raw config string, so a
    symlinked logo landed under a name `clean` never looked for. That was
    fixed at those two call sites, but the manifest is a DERIVED third
    consumer of the same fact -- if `package_entries` ever names the brand
    image differently than the file `copy_brand_image` actually writes, this
    is the only test that catches it, because test_path_containment.py's
    equivalent case never generates a manifest at all.
    """
    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "siu-logo-2024.svg").write_text("<svg/>")
    os.symlink("img/siu-logo-2024.svg", tmp_path / "logo.svg")

    config = {
        "output_dir": "out",
        "templates": [{"src": "html-template.html.j2"}],
        "packages": {
            "demo": {
                "name": "Demo",
                "package_type": "none",
                "docs": ["a.md"],
                "brand": "file://logo.svg",
                "brand-alt": "SIU",
            }
        },
    }
    generated = generate_package(config)

    assert (generated / "logo.svg").is_file(), "setup: the logo was not copied in"

    manifest = read_manifest(generated / MANIFEST_NAME)
    entries = set(manifest["entries"])

    assert _on_disk(generated) - entries == set(), (
        "the copied brand image is on disk under a name the manifest does "
        "not list -- a manifest-driven clean would leave it behind and git "
        "would track it, the exact failure stn-8wt already shipped once"
    )
    assert "logo.svg" in entries


# --- the manifest and get_generated_files are one derivation, not two -------


def test_package_entries_matches_get_generated_files_slice():
    """The no-drift guarantee at the derivation itself, independent of
    anything written to disk. `package_entries` is meant to be the one body
    `get_generated_files`'s per-package loop also calls -- if it goes back
    to being a second walk of the config, this fails before a missing file on
    disk has to expose it."""
    config = {
        "templates": MAKEFILE_TEMPLATES,
        "packages": {
            "demo": {
                "name": "Demo",
                "package_type": "none",
                "docs": ["Guide.md"],
                "slides": ["Deck.md"],
            }
        },
    }
    contexts = package_contexts(config)
    entries = package_entries(
        "demo",
        config["packages"]["demo"],
        contexts["demo"],
        config.get("templates", []),
    )
    assert entries == _expected_slice(config)


def test_manifest_entries_equal_get_generated_files_slice_end_to_end(
    generate_package,
):
    """The same guarantee, but against what write_manifest actually put on
    disk rather than against the in-memory derivation -- so a bug in
    write_manifest's own serialization (a stale entries list, a missed
    package_id) is caught even if package_entries itself is correct."""
    config = {
        "output_dir": "out",
        "templates": MAKEFILE_TEMPLATES,
        "packages": {
            "demo": {
                "name": "Demo",
                "package_type": "none",
                "docs": ["Guide.md"],
                "slides": ["Deck.md"],
            }
        },
    }
    generated = generate_package(config)
    manifest = read_manifest(generated / MANIFEST_NAME)

    assert set(manifest["entries"]) == _expected_slice(config)


# --- the manifest's own shape ------------------------------------------------


def test_manifest_is_json_with_the_documented_shape(generate_package):
    """clean (a later ticket) refuses a manifest whose version it does not
    know and refuses one that is not the documented shape -- both of those
    checks are only meaningful if gen actually writes that shape. A silent
    change here (a float version, an unsorted or non-string entries list)
    would make that refusal logic untestable from the writing side."""
    config = {
        "output_dir": "out",
        "templates": MAKEFILE_TEMPLATES,
        "packages": {
            "demo": {
                "name": "Demo",
                "package_type": "none",
                "docs": ["Guide.md"],
            }
        },
    }
    generated = generate_package(config)

    raw = json.loads((generated / MANIFEST_NAME).read_text())

    assert MANIFEST_VERSION == 1
    assert raw["manifest_version"] == MANIFEST_VERSION
    assert isinstance(raw["manifest_version"], int)
    assert isinstance(raw["stencil_version"], str) and raw["stencil_version"]
    assert raw["package"] == "demo"
    assert raw["dir"] == "demo"
    assert isinstance(raw["entries"], list)
    assert all(isinstance(entry, str) for entry in raw["entries"])
    assert raw["entries"] == sorted(raw["entries"]), "entries must be sorted"


# --- --dry-run previews, it does not write ----------------------------------


def test_gen_dry_run_writes_no_manifest_and_says_it_would(tmp_path):
    """`generate_package` never writes under --dry-run for anything else it
    produces; a manifest that slipped past that rule would leave a
    .stencil-manifest.json behind from a preview nobody asked to keep, and
    clean would then have something real to read for a package that was
    never actually generated."""
    config = {
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    (tmp_path / ".config.yaml").write_text(yaml.safe_dump(config))

    result = run_cli("gen", "demo", "--dry-run", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "demo" / MANIFEST_NAME).exists(), (
        "--dry-run wrote a manifest for a package that was never generated"
    )
    assert MANIFEST_NAME in result.stdout, (
        "--dry-run should say it would write the manifest, the same way it "
        "announces every other file it would write"
    )


# --- the manifest is a generated file like any other ------------------------


def test_get_generated_files_names_the_manifest():
    """`clean` (config-derived path) and the managed .gitignore section are
    both driven by get_generated_files. If the manifest is missing from its
    output, a config-derived clean leaves it behind and git tracks a
    generated file -- the exact failure stn-k73 exists to prevent, for a new
    kind of generated file."""
    config = {"packages": {"demo": {"name": "Demo", "package_type": "none"}}}
    assert f"demo/{MANIFEST_NAME}" in get_generated_files(config)


def test_install_gitignore_lists_the_manifest(tmp_path):
    """The managed .gitignore section is built from get_generated_files, so
    this is the end-to-end guarantee that a fresh clone's `stencil install`
    actually ignores the manifest rather than leaving it for git to pick up."""
    config = {
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    (tmp_path / ".config.yaml").write_text(yaml.safe_dump(config))

    result = run_cli("install", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    gitignore = (tmp_path / ".gitignore").read_text()
    assert f"demo/{MANIFEST_NAME}" in gitignore


# --- stn-jl3: the managed section names what stencil ACTUALLY writes -------


def _git_init(root):
    """A hermetic repository to ask `git check-ignore` in.

    Skips rather than fails without git, the way the container tier does --
    but note that this tier is NOT marked integration, so on any machine
    with git (which is every machine that can clone this repository) these
    run in the ordinary unit pass. A guard that silently stops running is
    the failure AGENTS.md already records.
    """
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    for command in (
        ["git", "-c", "init.defaultBranch=main", "init"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(command, cwd=root, check=True, capture_output=True)


def _ignored(root, *relative) -> None:
    for rel in relative:
        assert (root / rel).is_file(), f"setup: {rel} was not generated"
        check = subprocess.run(["git", "check-ignore", "-q", rel], cwd=root)
        assert check.returncode == 0, (
            f"git does not ignore {rel} -- the managed .gitignore section "
            "does not name the path stencil actually writes to"
        )



def test_a_generated_file_is_actually_ignored_by_git(tmp_path):
    """The ticket's own reproduction (stn-jl3), run rather than read: with a
    top-level output_dir set, `stencil gen` writes to out/demo/Makefile but
    the managed .gitignore section named demo/Makefile -- no 'out/' prefix
    -- so every file stencil actually produced was offered to the author as
    untracked. Reading the rendered section's text would have missed this
    the same way it already shipped once: a substring check on 'Makefile'
    passes whether or not the prefix is there. Only git's own answer, via
    `git check-ignore`, proves the section covers the path stencil wrote to.
    """
    _git_init(tmp_path)

    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    write_config(tmp_path, config)

    gen_result = run_cli("gen", "demo", cwd=tmp_path)
    assert gen_result.returncode == 0, gen_result.stderr

    install_result = run_cli("install", cwd=tmp_path)
    assert install_result.returncode == 0, install_result.stderr

    _ignored(tmp_path, f"out/demo/{MANIFEST_NAME}", "out/demo/Makefile")


def test_the_gitignore_lands_beside_the_config_not_the_cwd(tmp_path):
    """install_gitignore's other half: it wrote to Path.cwd() / '.gitignore'
    rather than beside the config file it just read, and every entry it
    writes is config-relative. So `stencil --config sub/.config.yaml
    install` run from anywhere else produced a .gitignore in the wrong
    place, with entries that do not even apply there, while the config's
    own directory -- the one a fresh clone would actually open -- got
    nothing. Runs `install` from a directory that is not the config's own,
    and checks both where the file landed and that git still recognizes the
    generated file once it does.
    """
    _git_init(tmp_path)

    project = tmp_path / "project"
    project.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    config_path = write_config(project, config)

    gen_result = run_cli("gen", "demo", cwd=project)
    assert gen_result.returncode == 0, gen_result.stderr

    install_result = run_cli("--config", str(config_path), "install", cwd=elsewhere)
    assert install_result.returncode == 0, install_result.stderr

    assert (project / ".gitignore").is_file(), (
        "install_gitignore wrote nowhere near the config it read -- the "
        "config's own directory got no .gitignore at all"
    )
    assert not (elsewhere / ".gitignore").exists(), (
        "install_gitignore wrote into Path.cwd() instead of beside the "
        "config file it was told to read"
    )

    check = subprocess.run(
        ["git", "check-ignore", "-q", "out/demo/Makefile"], cwd=project
    )
    assert check.returncode == 0, (
        "the .gitignore that landed beside the config does not actually "
        "ignore the file stencil generated there"
    )


def test_the_managed_entries_stay_unprefixed_when_output_dir_is_unset():
    """stn-jl3's fix teaches get_generated_files to prefix every entry with
    the top-level output_dir. Most configs never set one -- STENCIL.md's own
    example spells the default as the literal '.' -- so the fix has to
    normalize a missing key and an explicit '.' to the same empty prefix, or
    every consumer without output_dir set would wake up to a spurious './'
    segment in their .gitignore the day this ships. Pinned against the exact
    entry set, not a substring, so the fix cannot satisfy this by getting
    one entry right while leaving another one prefixed.
    """
    packages = {"demo": {"name": "Demo", "package_type": "none"}}
    templates = [{"src": "Makefile.j2"}]
    expected = {
        f"demo/{MANIFEST_NAME}",
        "demo/Makefile",
        "demo/format-package-lock.json",
    }

    no_output_dir = {"templates": templates, "packages": copy.deepcopy(packages)}
    dot_output_dir = {
        "output_dir": ".",
        "templates": templates,
        "packages": copy.deepcopy(packages),
    }

    assert set(get_generated_files(no_output_dir)) == expected
    assert set(get_generated_files(dot_output_dir)) == expected


@pytest.mark.parametrize("declared", ["out", "./out", "out/"])
def test_the_output_dir_prefix_is_normalized_before_it_becomes_a_pattern(
    tmp_path, declared
):
    """A gitignore line is a pattern, and `./out/demo/Makefile` matches
    NOTHING -- measured with `git check-ignore`, which is the only reason
    this is asserted by running git rather than by reading the section.
    `check_config_path` accepts all three spellings, STENCIL.md's own
    example writes the default as a bare `.`, and a consumer who types
    `output_dir: ./out` would otherwise get a managed section that looks
    completely correct and ignores nothing at all."""
    _git_init(tmp_path)
    write_config(
        tmp_path,
        {
            "output_dir": declared,
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        },
    )

    assert run_cli("gen", "demo", cwd=tmp_path).returncode == 0
    assert run_cli("install", cwd=tmp_path).returncode == 0

    _ignored(tmp_path, "out/demo/Makefile")


@pytest.mark.parametrize("declared_dir", ["demo", "./demo", "demo/", "a//b"])
def test_the_package_dir_segment_is_normalized_too(tmp_path, declared_dir):
    """The same normalization, one segment down, and it was missed.

    The comment on the output_dir prefix states the rule exactly -- a
    leading `./` matches nothing at all in a gitignore pattern, which is a
    silent way to ignore nothing -- and the next statement interpolated
    `dir` raw. Measured with `git check-ignore`: `dir: "./demo"` produced
    the line `out/./demo/Makefile`, `dir: "demo/"` produced
    `out/demo//Makefile`, and git matched NEITHER, so `install` printed
    every line and ignored none of them. That is stn-jl3's own failure mode
    at the segment nobody normalized, reached by an ordinary typing habit."""
    _git_init(tmp_path)
    write_config(
        tmp_path,
        {
            "output_dir": "out",
            "templates": [{"src": "Makefile.j2"}],
            "packages": {
                "demo": {
                    "name": "Demo",
                    "package_type": "none",
                    "dir": declared_dir,
                }
            },
        },
    )

    assert run_cli("gen", "demo", cwd=tmp_path).returncode == 0
    assert run_cli("install", cwd=tmp_path).returncode == 0

    expected = "out/" + "/".join(Path(declared_dir).parts) + "/Makefile"
    _ignored(tmp_path, expected)


def test_install_does_not_traceback_on_a_gitignore_that_is_not_utf8(tmp_path):
    """The stale-section note runs AFTER the write, and its guard was
    `except OSError` -- but `UnicodeDecodeError` is a `ValueError`. So a
    `.gitignore` holding a latin-1 comment in the working directory ended a
    run that had already done its real work with a traceback and rc=1.

    The content here is only ever substring-matched and re-emitted around
    the managed section, so it is read with `errors="replace"` rather than
    refused: a byte stencil does not understand in a file it does not own is
    not a reason to fail."""
    project = tmp_path / "project"
    project.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / ".gitignore").write_bytes(b"# caf\xe9 build\n")

    config_path = write_config(
        project,
        {
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        },
    )

    result = run_cli("--config", str(config_path), "install", cwd=elsewhere)

    assert "Traceback" not in result.stderr, result.stderr
    assert result.returncode == 0, (
        f"a successful install reported failure: {result.stderr!r}"
    )
    assert (project / ".gitignore").is_file()


def test_install_cannot_un_ignore_a_file_the_author_already_ignores(tmp_path):
    """The managed section's lines are PATTERNS, and a leading `!` NEGATES
    one. `dir` and the top-level `output_dir` are the two config values that
    lead every line, and neither was checked for it.

    Measured, on a course-handout repository whose own .gitignore says
    `*.pdf`, with `dir: "!solutions"`:

        $ stencil install
        $ git check-ignore -v -- solutions/answers.pdf
        .gitignore:7:!solutions/answers*.pdf    solutions/answers.pdf
        $ git status --porcelain
        ?? solutions/

    `stencil install` -- whose entire purpose is to stop generated files
    being committed -- un-ignored the answer key and made it committable,
    silently. In this tool's problem domain that is the worst available
    outcome, and stn-jl3 moves the managed section INTO the file holding the
    author's own rules, which is where a negation gets something to negate.

    Asserted as a refusal, because a backslash-escaped ``!`` (the documented
    gitignore escape) would also work and a `dir` beginning with `!` is not a
    directory name anyone means."""
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_text("*.pdf\n")
    write_config(
        tmp_path,
        {
            "templates": [{"src": "Makefile.j2"}],
            "packages": {
                "hs1": {
                    "name": "HS1",
                    "package_type": "none",
                    "dir": "!solutions",
                    "docs": ["answers.md"],
                }
            },
        },
    )

    result = run_cli("install", cwd=tmp_path)

    assert result.returncode != 0, (
        f"install exited 0 with a negating dir: {result.stdout!r}"
    )
    assert "Traceback" not in result.stderr, result.stderr
    assert "!" in result.stderr and "dir" in result.stderr, result.stderr

    (tmp_path / "solutions").mkdir()
    (tmp_path / "solutions" / "answers.pdf").write_text("key\n")
    check = subprocess.run(
        ["git", "check-ignore", "-q", "solutions/answers.pdf"], cwd=tmp_path
    )
    assert check.returncode == 0, (
        "the author's own `*.pdf` rule stopped applying after `stencil "
        "install` -- the managed section un-ignored it"
    )


@pytest.mark.parametrize("value", ["!out", "#out", "o*t"])
def test_a_top_level_output_dir_that_is_a_pattern_is_refused(value):
    """The same hole from the other side, and the one stn-jl3 opens wider:
    prefixing every managed line with the top-level `output_dir` moves the
    leading position onto a key that was equally unchecked for these. One
    `output_dir: "!out"` flips the ENTIRE managed section to negations."""
    config = {
        "output_dir": value,
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    with pytest.raises(ValueError, match="output_dir"):
        get_generated_files(config)


# --- build-artifact glob patterns, recorded rather than rewalked ------------


def test_manifest_records_doc_and_slide_build_glob_patterns(generate_package):
    """`make pdf`/`make html` print files gen never writes itself; the
    manifest has to record them as the glob patterns they are (mirroring
    get_generated_files) or a manifest-driven clean would never remove a
    built .html/.pdf standing next to its source markdown."""
    config = {
        "output_dir": "out",
        "templates": MAKEFILE_TEMPLATES,
        "packages": {
            "demo": {
                "name": "Demo",
                "package_type": "none",
                "docs": ["Guide.md"],
                "slides": ["Deck.md"],
            }
        },
    }
    generated = generate_package(config)
    entries = set(read_manifest(generated / MANIFEST_NAME)["entries"])

    for pattern in ("Guide*.html", "Guide*.pdf", "Deck*.html", "Deck*.pdf"):
        assert pattern in entries, f"{pattern!r} missing from {sorted(entries)}"


def test_manifest_records_the_zip_archive_name(generate_package):
    """`pkg` zips into `package_name`; a manifest that omits it would let a
    manifest-driven clean leave a built archive behind for git to track."""
    config = {
        "output_dir": "out",
        "templates": MAKEFILE_TEMPLATES,
        "packages": {
            "demo": {
                "name": "Demo",
                "package_type": "zip",
                "package_name": "hs3.zip",
            }
        },
    }
    generated = generate_package(config)
    entries = set(read_manifest(generated / MANIFEST_NAME)["entries"])

    assert "hs3.zip" in entries


# --- a failed gen must not leave a stale manifest behind (D5) ---------------


def test_a_failed_regeneration_leaves_no_manifest(tmp_path, generate_package):
    """AGENTS.md's own _main documents 'generation is idempotent, fix the
    cause and run again' as the recovery path for a half-written package.
    A stale manifest surviving a failed re-gen breaks that promise silently:
    it would still name the OLD file list while whatever the broken template
    run half-wrote sits unnamed on disk, and because a manifest outranks the
    config for a manifest-driven clean, that clean would walk straight past
    the new files with no fallback. gen must therefore leave no manifest
    at all rather than a stale one when a re-gen fails partway.
    """
    ok_config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {
            "demo": {"name": "Demo", "package_type": "none", "docs": ["Guide.md"]}
        },
    }
    generated = generate_package(ok_config)
    manifest_path = generated / MANIFEST_NAME
    assert manifest_path.is_file(), "setup: the first, successful gen should manifest"

    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "broken.j2").write_text("{{ not_a_real_context_key }}\n")

    broken_config = {
        "output_dir": "out",
        "templates_dir": "templates",
        "templates": [{"src": "Makefile.j2"}, {"src": "broken.j2"}],
        "packages": {
            "demo": {"name": "Demo", "package_type": "none", "docs": ["Guide.md"]}
        },
    }
    with pytest.raises(UndefinedError):
        generate_package(broken_config)

    assert not manifest_path.exists(), (
        "a failed re-gen left the manifest from the PREVIOUS successful run "
        "in place, naming a file list that no longer matches what is on disk"
    )


# =============================================================================
# stn-2x4.3: clean reads the manifest, and the degraded path.
#
# Everything below drives `clean` through the CLI (run_cli, subprocess) in a
# tmp_path -- never by calling clean_generated directly -- because the exit
# status is part of what each test asserts, and that only exists at the CLI
# boundary. Setup (the initial `gen`) is free to use whichever is more direct
# for the case: the `generate_package` fixture (in-process) when only one
# package is involved, or `run_cli("gen", ...)` + `write_config` when a
# config needs to be rewritten between a `gen` and the `clean` under test.
# =============================================================================


# --- clean reads the manifest in preference to a since-edited config -------


def test_a_dropped_docs_entry_is_refused_as_a_widened_manifest_not_silently_honoured(
    generate_package, tmp_path
):
    """SUPERSEDES this file's old "PRECEDENCE" headline (stn-jez, operator
    ruling). The manifest still names a document's build-artifact glob
    patterns (Guide*.html, Guide*.pdf, ...) for every doc that existed AT
    GEN TIME. Editing `docs:` afterward to drop one used to still get that
    document's artifacts removed -- the manifest overriding a since-edited
    config, which was the whole point of stn-p9a. It no longer does: a
    manifest naming an entry the CURRENT config does not derive is now
    indistinguishable from a forged one that never went through `docs:` at
    all (see _clean_one_directory's stn-jez block, and its own docstring's
    "field presence was never the boundary"), so it is refused the same
    way, and NOTHING is removed for the package -- not even Guide's own
    artifacts.

    This is a deliberate, accepted trade, not a regression nobody noticed:
    the operator's ruling names this exact scenario and says the refusal
    message must tell the author what to do about it -- restore the dropped
    `docs:` entry, or delete the stale build artifacts (and the manifest)
    by hand.
    """
    config = {
        "output_dir": "out",
        "templates": MAKEFILE_TEMPLATES,
        "packages": {
            "demo": {
                "name": "Demo",
                "package_type": "none",
                "docs": ["Guide.md", "Extra.md"],
            }
        },
    }
    generated = generate_package(config)

    # Simulate a `make html`/`make pdf` run: gen itself only writes the
    # scaffolding, never these build artifacts.
    for name in ("Guide.html", "Guide.pdf", "Extra.html", "Extra.pdf"):
        (generated / name).write_text("built")

    edited = copy.deepcopy(config)
    edited["packages"]["demo"]["docs"] = ["Guide.md"]
    (tmp_path / ".config.yaml").write_text(yaml.safe_dump(edited))

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode != 0, (
        "a manifest naming a glob the edited config no longer derives must "
        "be refused, not silently honoured"
    )
    assert "Traceback" not in result.stderr, result.stderr
    combined = result.stdout + result.stderr
    assert "Extra" in combined, f"the widened entries should be named: {combined!r}"
    for name in ("Guide.html", "Guide.pdf", "Extra.html", "Extra.pdf"):
        assert (generated / name).exists(), (
            f"{name} must survive too -- nothing is removed for a package "
            "whose manifest widens what the config authorises"
        )
    assert (generated / MANIFEST_NAME).exists(), (
        "the manifest survives a refusal, same as every other whole-group "
        "refusal in this file"
    )


def test_clean_does_not_remove_a_file_gen_never_produced_from_an_added_docs_entry(
    generate_package, tmp_path
):
    """The converse of the precedence test above. Editing the config AFTER
    gen to ADD a docs entry must not make clean remove a file gen never
    produced -- the manifest, not the current config, is authoritative, so a
    file matching a pattern the manifest never recorded is never even
    considered, let alone removed.
    """
    config = {
        "output_dir": "out",
        "templates": MAKEFILE_TEMPLATES,
        "packages": {
            "demo": {
                "name": "Demo",
                "package_type": "none",
                "docs": ["Guide.md"],
            }
        },
    }
    generated = generate_package(config)
    (generated / "Guide.html").write_text("built")

    edited = copy.deepcopy(config)
    edited["packages"]["demo"]["docs"] = ["Guide.md", "New.md"]
    (tmp_path / ".config.yaml").write_text(yaml.safe_dump(edited))
    # A file that happens to match the NEWLY configured pattern, standing in
    # for whatever a later `make` run (against the edited config) might have
    # produced -- gen itself never ran again, so the manifest never heard
    # about it.
    (generated / "New.html").write_text("not gen's to remove")

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert not (generated / "Guide.html").exists()
    assert (generated / "New.html").exists(), (
        "clean removed a file the manifest never named, by re-deriving the "
        "removal list from the edited config instead of trusting the "
        "manifest"
    )


def test_clean_removes_the_manifest_last(generate_package, tmp_path):
    """Assert the ordering directly rather than assuming it. The manifest
    must be the LAST thing removed for its package, so a clean that fails
    partway through still has a manifest on disk to resume from.
    """
    config = {
        "output_dir": "out",
        "templates": MAKEFILE_TEMPLATES,
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generate_package(config)

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    removed = [
        line for line in result.stdout.splitlines() if line.startswith("Removed ")
    ]
    assert len(removed) >= 2, f"expected at least Makefile and the manifest: {removed}"
    assert removed[-1].endswith(MANIFEST_NAME), (
        f"the manifest was not the last thing removed: {removed}"
    )


def test_manifest_survives_a_partial_clean(generate_package, tmp_path):
    """Review finding A10. When ANY entry for a package was refused (a
    path-check failure) or failed to be removed, the manifest itself must
    NOT be removed -- otherwise the file that was refused has no record
    left naming it at all, and the next clean cannot even try again.

    SUPERSEDED IN PART by stn-jez: "../escape.txt" is not just a path-check
    failure any more, it is also an entry the config never derives for
    "demo" at all -- a widened manifest, refused as a whole group before
    `_remove_entries` ever runs, so the OTHER, legitimate entries no longer
    get removed either (see _clean_one_directory's stn-jez block). What
    A10 actually pins -- the manifest surviving so the refused entry is
    still named for next time -- still holds and is still the point of this
    test; only the "valid entries removed anyway" half changed.
    """
    config = {
        "output_dir": "out",
        "templates": MAKEFILE_TEMPLATES,
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)
    manifest_path = generated / MANIFEST_NAME

    # A file just outside the package directory that a malicious/corrupt
    # manifest entry could reach with "..". If it survives, containment held.
    outside = tmp_path / "out" / "escape.txt"
    outside.write_text("do not touch")

    manifest = json.loads(manifest_path.read_text())
    manifest["entries"].append("../escape.txt")
    manifest_path.write_text(json.dumps(manifest))

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode != 0, (
        "a refused manifest entry must fail the command, not pass silently"
    )
    assert (generated / "Makefile").exists(), (
        "stn-jez: '../escape.txt' is also an entry the config does not "
        "derive, so nothing is removed for the package, Makefile included"
    )
    assert outside.is_file() and outside.read_text() == "do not touch", (
        "the refused '../escape.txt' entry must not have been removed"
    )
    assert manifest_path.exists(), (
        "the manifest must survive a partial clean, so the refused entry is "
        "still named for next time"
    )


def test_clean_all_does_not_report_a_false_failure_when_a_shared_manifest_is_already_gone(
    tmp_path,
):
    """Review finding D4. Two packages configured with the same `dir` share
    one physical manifest on disk (today `get_generated_files` unions their
    entries so a config-derived `clean` already covers both). Cleaning the
    first package in scope removes that shared manifest; the second package
    must not be reported as a failure just because ITS manifest is already
    gone -- its own config is still perfectly readable, so it falls back to
    deriving from the config, exactly as a package that never had a manifest
    at all would, and finds nothing left to do.
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {
            "alpha": {"package_type": "none", "dir": "shared"},
            "beta": {"package_type": "none", "dir": "shared"},
        },
    }
    write_config(tmp_path, config)

    setup = run_cli("gen", "--all", cwd=tmp_path)
    assert setup.returncode == 0, setup.stderr

    result = run_cli("clean", "--all", cwd=tmp_path)

    assert result.returncode == 0, (
        f"a shared manifest already removed by the first package must not "
        f"fail the second: {result.stderr}"
    )
    assert "beta" not in result.stderr
    assert not (tmp_path / "out" / "shared" / "Makefile").exists()


def test_dry_run_on_the_degraded_path_previews_without_removing_and_matches_real_exit_status(
    tmp_path,
):
    """Review finding T2. `--dry-run` must preview the manifest path the
    same way it previews everything else: 'Would remove' for every entry
    INCLUDING the manifest, nothing actually removed, and -- the point of
    stn-w4v -- the SAME exit status the real run would go on to produce. A
    preview that exits 0 where the real run exits 1 (or the reverse) is
    exactly that bug's shape, applied here to the degraded path where the
    two exit statuses are least likely to have been kept in sync by hand.
    """
    initially_fine = copy.deepcopy(GOOD_AND_BROKEN_CONFIG)
    del initially_fine["packages"]["broken"]["show_download"]
    write_config(tmp_path, initially_fine)
    setup = run_cli("gen", "--all", cwd=tmp_path)
    assert setup.returncode == 0, setup.stderr

    write_config(tmp_path, GOOD_AND_BROKEN_CONFIG)

    good_manifest = tmp_path / "good" / MANIFEST_NAME
    assert good_manifest.exists(), "setup: good should still have its manifest"

    dry = run_cli("clean", "--all", "--dry-run", cwd=tmp_path)

    assert dry.returncode == 0, dry.stderr
    assert "Would remove" in dry.stdout
    assert good_manifest.exists(), "--dry-run must not remove anything"
    assert (tmp_path / "good" / "Makefile").exists()

    real = run_cli("clean", "--all", cwd=tmp_path)

    assert real.returncode == dry.returncode == 0, (
        "the real run's exit status must match what --dry-run already "
        "reported for the same command"
    )
    assert not good_manifest.exists()


def test_manifest_with_no_entries_still_gets_removed_and_counts_as_cleaned(
    generate_package, tmp_path
):
    """Review finding T3. `clean_generated`'s existing 'No generated paths
    for package X' early return is the exact lie stn-p9a's ticket text
    complains about: files can still be on disk -- the manifest itself, and
    whatever it deliberately does not list -- even when a manifest's own
    `entries` is empty. The manifest file must still be removed and the
    package must still count as successfully cleaned, exit 0.
    """
    config = {
        "output_dir": "out",
        "templates": MAKEFILE_TEMPLATES,
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)
    manifest_path = generated / MANIFEST_NAME

    manifest = json.loads(manifest_path.read_text())
    manifest["entries"] = []
    manifest_path.write_text(json.dumps(manifest))

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "No generated paths" not in result.stdout + result.stderr
    assert not manifest_path.exists(), (
        "an empty entries list must not stop the manifest itself from being "
        "removed"
    )
    assert (generated / "Makefile").exists(), (
        "the manifest is authoritative even when empty -- clean must not "
        "fall back to deriving from the config just because entries is "
        "empty"
    )


# --- the degraded path: clean works on a config it cannot fully read -------


def test_broken_config_with_manifests_on_both_packages_cleans_warns_and_exits_zero(
    tmp_path,
):
    """DEGRADED, the headline. Both packages were generated -- each has its
    own manifest -- before the config broke the way GOOD_AND_BROKEN_CONFIG is
    broken (show_download: "no"). `clean --all` does not need either
    package's config at all: it reads their manifests, removes every file
    for both, prints the config problem as a warning, and exits 0 -- because
    every package IN SCOPE was cleaned. Refusing here would take back the
    whole point of the ticket: `clean` staying usable when the config is the
    reason someone reached for it.
    """
    initially_fine = copy.deepcopy(GOOD_AND_BROKEN_CONFIG)
    del initially_fine["packages"]["broken"]["show_download"]
    write_config(tmp_path, initially_fine)
    setup = run_cli("gen", "--all", cwd=tmp_path)
    assert setup.returncode == 0, setup.stderr

    write_config(tmp_path, GOOD_AND_BROKEN_CONFIG)

    result = run_cli("clean", "--all", cwd=tmp_path)

    assert result.returncode == 0, (
        f"every package in scope had a manifest; clean should still have "
        f"succeeded: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "broken" in combined, "the config problem must still be reported"
    assert not (tmp_path / "good" / MANIFEST_NAME).exists()
    assert not (tmp_path / "broken" / MANIFEST_NAME).exists()


def test_broken_config_warning_does_not_claim_nothing_was_removed_or_that_clean_refuses(
    tmp_path,
):
    """Review finding D2. _raise_config_problems' trailer ('Nothing was
    generated, removed or written ... rather than reaching for `clean`,
    which refuses for the same reason this did') is written for the path
    where NOTHING happened. On the degraded path that same sentence would be
    printed immediately BEFORE clean removes files and exits 0 -- a direct
    contradiction. The warning printed on the degraded path must not repeat
    either claim.
    """
    initially_fine = copy.deepcopy(GOOD_AND_BROKEN_CONFIG)
    del initially_fine["packages"]["broken"]["show_download"]
    write_config(tmp_path, initially_fine)
    setup = run_cli("gen", "--all", cwd=tmp_path)
    assert setup.returncode == 0, setup.stderr

    write_config(tmp_path, GOOD_AND_BROKEN_CONFIG)

    result = run_cli("clean", "--all", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "Nothing was generated, removed or written" not in combined, (
        "the warning claims nothing was removed on a run that just removed "
        "files"
    )
    assert "which refuses for the same reason this did" not in combined, (
        "the warning tells the reader clean refuses, on the run where it "
        "just succeeded"
    )


def test_broken_config_with_no_manifest_anywhere_still_fails_closed(tmp_path):
    """Pins the stn-445 guarantee at this file too, so a manifest-aware
    rewrite of clean_generated cannot regress it: a manifest is a NEW way to
    succeed, never a new way to fail differently. Nothing was ever generated
    here, so neither package has a manifest, and a broken config must still
    refuse outright -- non-zero, no traceback -- exactly as it did before
    this ticket.
    """
    write_config(tmp_path, GOOD_AND_BROKEN_CONFIG)

    result = run_cli("clean", "--all", cwd=tmp_path)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "broken" in result.stderr


def test_clean_all_cleans_the_manifest_package_and_names_the_unmanifested_broken_one(
    tmp_path,
):
    """`good` was generated alone, before `broken` existed in the config, so
    only `good` has a manifest. `clean --all` puts both packages in scope:
    `good` cleans from its manifest regardless of the broken sibling,
    `broken` has neither a manifest nor a readable config for itself, so it
    is named and the command exits non-zero overall -- 'every package in
    scope was cleaned' is false here, unlike the headline case above.
    """
    initial = {
        "templates": MAKEFILE_TEMPLATES,
        "packages": {"good": {"package_type": "none"}},
    }
    write_config(tmp_path, initial)
    setup = run_cli("gen", "good", cwd=tmp_path)
    assert setup.returncode == 0, setup.stderr

    write_config(tmp_path, GOOD_AND_BROKEN_CONFIG)

    result = run_cli("clean", "--all", cwd=tmp_path)

    assert result.returncode != 0, (
        "broken has neither a manifest nor a readable config; the command "
        "must still fail overall"
    )
    assert "broken" in result.stderr
    assert not (tmp_path / "good" / MANIFEST_NAME).exists(), (
        "good should still have been cleaned even though the overall exit "
        "is non-zero"
    )
    assert not (tmp_path / "good" / "Makefile").exists()


def test_clean_of_a_single_manifest_backed_package_succeeds_despite_a_broken_sibling(
    tmp_path,
):
    """Deliberately NARROWS decision d-adf7c52b (stn-p9a) -- see the updated
    docstring on tests/test_cli.py's
    test_a_single_good_package_still_fails_when_a_sibling_is_broken. `broken`
    is not even named on this command line, so it is out of scope entirely:
    a manifest-backed `clean good` no longer pays for a sibling it was never
    asked about.
    """
    initial = {
        "templates": MAKEFILE_TEMPLATES,
        "packages": {"good": {"package_type": "none"}},
    }
    write_config(tmp_path, initial)
    setup = run_cli("gen", "good", cwd=tmp_path)
    assert setup.returncode == 0, setup.stderr

    write_config(tmp_path, GOOD_AND_BROKEN_CONFIG)

    result = run_cli("clean", "good", cwd=tmp_path)

    assert result.returncode == 0, (
        f"good has a manifest and was not asked to consider broken: "
        f"{result.stderr}"
    )
    assert not (tmp_path / "good" / MANIFEST_NAME).exists()
    assert not (tmp_path / "good" / "Makefile").exists()


def test_a_control_character_in_a_package_id_does_not_reach_the_terminal_raw(
    tmp_path,
):
    """clean's degraded path prints TWO reports about the same package -- the
    config warning, and the "could not be cleaned" list underneath it -- and
    they must not disagree about whether an id is safe to print.

    Found by probing stn-2x4.8 rather than by a test: the warning ran through
    _safe and rendered the id as `demo\\x1b[2Jx`, while the error list printed
    the escape raw and repainted the terminal. The unescaped half sat directly
    below a sentence promising the escape "cannot repaint this line".

    YAML refuses a raw control BYTE in the file, which is why this looked
    unreachable; a double-quoted scalar honours \\x escapes, so the id carries
    one without the file containing one. test_cli.py pins the same rule for
    the paths that existed before this ticket.
    """
    esc = chr(27)
    (tmp_path / ".config.yaml").write_text(
        "templates:\n"
        "  - src: Makefile.j2\n"
        "packages:\n"
        '  "demo\\x1b[2Jx":\n'
        "    name: Demo\n"
        "    package_type: none\n"
        "  broken:\n"
        "    name: B\n"
        "    package_type: none\n"
        '    show_download: "no"\n'
    )

    result = run_cli("clean", "--all", cwd=tmp_path)

    assert esc not in result.stdout + result.stderr, (
        "a control character in a package id reached the terminal raw, so a "
        "config can repaint the report that is describing it"
    )


# =============================================================================
# stn-2x4.5: every path clean unlinks, from either source, stays under the
# package's own output directory (stn-7t9, a filed P1), and a manifest that
# nothing has validated is treated as data, not as commands or as trustworthy
# shell input.
#
# Everything below drives `clean` through the CLI (run_cli, subprocess) in a
# tmp_path, same convention as the stn-2x4.3 section above. Each test's
# docstring says whether it is RED (waiting on stn-2x4.6's per-entry
# containment work) or GREEN (already true, because stn-2x4.8's package-
# directory containment check already covers it -- kept here as a regression
# guard rather than removed, since this is the file stn-7t9's own reproduction
# belongs in).
# =============================================================================


# --- THE CENTRAL CASE: stn-7t9's filed reproduction, both sources ----------


def test_stn_7t9_reproduction_config_derived_path_refuses_and_survives(tmp_path):
    """stn-7t9's filed reproduction, verbatim, on the CONFIG-DERIVED path:

        mkdir -p out outside; ln -s $PWD/outside out/demo; echo victim > outside/Makefile
        stencil clean --all   ->  'Removed .../outside/Makefile'

    GREEN already -- measured against this branch before writing this
    docstring. stn-2x4.8's `_validated_package_dirs` resolves 'demo' and
    checks THAT against the resolved output base before `_clean_one_directory`
    is ever reached, so the escape is refused at the package-directory level
    regardless of what is inside it. This test is now a regression guard for
    that fix rather than a red reproduction of the open bug.
    """
    (tmp_path / "out").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Makefile").write_text("victim")
    os.symlink(outside, tmp_path / "out" / "demo")

    write_config(
        tmp_path,
        {
            "output_dir": "out",
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        },
    )

    result = run_cli("clean", "--all", cwd=tmp_path)

    assert result.returncode != 0, "the escape must fail the command"
    assert "Traceback" not in result.stderr, result.stderr
    assert (outside / "Makefile").read_text() == "victim", (
        "clean must never remove a file that resolves outside output_base"
    )


def test_stn_7t9_reproduction_with_a_manifest_present_also_refuses(tmp_path):
    """Same arrangement, but a manifest is present at the (symlinked)
    package location, so the manifest path gets its own case rather than
    inheriting the config-derived one's coverage above.

    GREEN already, for the same reason: `_validated_package_dirs` refuses
    the package directory itself before `_clean_one_directory` ever looks
    for a manifest there, so a manifest sitting in the escaped location
    changes nothing.
    """
    (tmp_path / "out").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Makefile").write_text("victim")
    (outside / MANIFEST_NAME).write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "stencil_version": "0.38.0",
                "package": "demo",
                "dir": "demo",
                "entries": ["Makefile"],
            }
        )
    )
    os.symlink(outside, tmp_path / "out" / "demo")

    write_config(
        tmp_path,
        {
            "output_dir": "out",
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        },
    )

    result = run_cli("clean", "--all", cwd=tmp_path)

    assert result.returncode != 0, "the escape must fail the command"
    assert "Traceback" not in result.stderr, result.stderr
    assert (outside / "Makefile").read_text() == "victim"
    assert (outside / MANIFEST_NAME).exists(), (
        "the manifest itself must survive the refusal too"
    )


# --- THE ANCHOR (adversarial CRITICAL 1) ------------------------------------


def test_the_naive_resolved_anchor_would_call_the_escape_contained(tmp_path):
    """Documents adversarial CRITICAL 1 directly, rather than only avoiding
    it by accident.

    Measured: an anchor of `(output_base / pkg_dir).resolve()` reports the
    outside file as CONTAINED, because `.resolve()` follows the very symlink
    the check exists to catch. A test written to assert containment against
    THAT anchor would pass while the bug is open -- so this test does not
    write its assertion that way. It demonstrates the trap explicitly (the
    naive computation really does say "contained"), and then asserts the
    actual property against real `clean` behaviour: the file survives.

    GREEN already, and for a specific reason worth stating: the real check
    (`_validated_package_dirs`) does not use this anchor to test a FILE's
    containment. It tests whether `pkg_path` ITSELF, once resolved, is still
    under the resolved output base -- before `pkg_path` is used as an anchor
    for anything else. That ordering is what avoids the trap; if a future
    change asked "is this file under `(output_base/pkg_dir).resolve()`"
    instead, it would reintroduce exactly this hole.
    """
    (tmp_path / "out").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Makefile").write_text("victim")
    os.symlink(outside, tmp_path / "out" / "demo")

    output_base = tmp_path / "out"
    naive_anchor = (output_base / "demo").resolve()
    victim = (outside / "Makefile").resolve()

    assert victim.is_relative_to(naive_anchor), (
        "setup: this is supposed to demonstrate the naive anchor's blind "
        "spot -- if this fails, the demonstration itself is broken"
    )

    write_config(
        tmp_path,
        {
            "output_dir": "out",
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        },
    )

    result = run_cli("clean", "--all", cwd=tmp_path)

    assert result.returncode != 0
    assert (outside / "Makefile").read_text() == "victim", (
        "the real property -- a file that resolves outside output_base is "
        "never unlinked -- must hold even though the naive anchor above "
        "says this file is contained"
    )


# --- GLOB VOCABULARY (adversarial CRITICAL 2) -------------------------------


@pytest.mark.parametrize(
    "pattern,decoy",
    [
        ("**", "AuthorNotes.md"),
        ("*", "AuthorNotes.md"),
        ("sub/*", "sub/AuthorNotes.md"),
    ],
    ids=["double-star", "bare-star", "dir-slash-star"],
)
def test_a_glob_vocabulary_entry_is_refused_by_name_not_expanded(
    tmp_path, generate_package, pattern, decoy
):
    """Measured on this interpreter (Python 3.14.7, so since-3.13 semantics
    apply): '**' PASSES `check_config_path` today, and `Path.glob('**')`
    yields FILES, not just directories -- so one manifest entry '**' expands
    to the entire package subtree, which is where an author's docs: markdown
    lives. A bare '*' component and 'dir/*' are the same shape at narrower
    scope. Every match is inside pkg_dir, so containment under ANY anchor
    passes it -- the only fix is refusing the pattern itself, by name.

    RED today: measured against this branch, all three patterns sweep up
    `decoy`, a file gen never listed.

    SUPERSEDED IN PART by stn-jez: none of these three patterns is
    something the config derives for "demo" either, so the widened-manifest
    check in `_clean_one_directory` now refuses the whole group before
    `_remove_entries` (and its own `check_glob_vocabulary` call) ever runs
    -- doubly guarded rather than differently guarded. `Makefile`, an
    ordinary legitimately manifested entry, therefore now survives
    alongside `decoy` rather than still being removed next to the refusal.
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)

    decoy_path = generated / decoy
    decoy_path.parent.mkdir(parents=True, exist_ok=True)
    decoy_path.write_text("author-only content, never in entries")

    manifest_path = generated / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"].append(pattern)
    manifest_path.write_text(json.dumps(manifest))

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode != 0, (
        f"{pattern!r} must be refused, not silently expanded, so the "
        f"command must fail"
    )
    assert pattern in (result.stdout + result.stderr), (
        f"{pattern!r} should be named in the refusal: {result.stderr!r}"
    )
    assert decoy_path.is_file(), (
        f"{pattern!r} swept up {decoy}, a file gen never listed -- exactly "
        f"the shape that would delete an author's own docs"
    )
    assert (generated / "Makefile").exists(), (
        "stn-jez: nothing is removed for a package whose manifest widens "
        "what the config authorises, Makefile included"
    )


def test_legitimate_build_artifact_glob_shapes_still_work(tmp_path, generate_package):
    """The converse of the vocabulary check above: the shapes
    `get_generated_files` actually emits -- `Guide*.html`, `Guide*.pdf`, a
    literal `package_name` archive -- must keep working once stn-2x4.6 adds
    the '**' / bare '*' / 'dir/*' refusal. GREEN already, and must stay
    green: these are not the vocabulary being refused, only their shape
    rhymes with it.
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {
            "demo": {
                "name": "Demo",
                "package_type": "zip",
                "package_name": "demo-bundle.zip",
                "docs": ["Guide.md"],
            }
        },
    }
    generated = generate_package(config)
    for name in ("Guide-v1.html", "Guide-v2.pdf"):
        (generated / name).write_text("built")
    (generated / "demo-bundle.zip").write_text("zip")

    manifest = json.loads((generated / MANIFEST_NAME).read_text())
    assert "Guide*.html" in manifest["entries"]
    assert "Guide*.pdf" in manifest["entries"]
    assert "demo-bundle.zip" in manifest["entries"]

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    for name in ("Guide-v1.html", "Guide-v2.pdf", "demo-bundle.zip"):
        assert not (generated / name).exists(), f"{name} should have been removed"


# --- an escaping manifest entry is refused BY NAME, not silently skipped ---


def test_a_manifest_entry_that_is_absolute_is_refused_by_name(tmp_path, generate_package):
    """RED today: `pkg_path / "/abs/path"` is `/abs/path` -- pathlib's `/`
    operator discards the left side when the right side is absolute -- so an
    absolute manifest entry is followed and its target is unlinked, exactly
    like stn-7t9's arrangement but with no symlink needed at all.

    SUPERSEDED IN PART by stn-jez: an absolute path is also never something
    the config derives, so the widened-manifest check refuses the whole
    group before `_remove_entries` gets a chance to run its own check --
    Makefile now survives alongside the victim rather than being removed
    next to the refusal.
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)

    victim = tmp_path / "abs_victim.txt"
    victim.write_text("do not touch")
    bad_entry = str(victim)

    manifest_path = generated / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"].append(bad_entry)
    manifest_path.write_text(json.dumps(manifest))

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode != 0, "an absolute manifest entry must be refused"
    assert victim.read_text() == "do not touch", (
        "an absolute manifest entry must never be followed off the package"
    )
    assert bad_entry in (result.stdout + result.stderr), (
        f"the absolute entry should be named in the refusal: {result.stderr!r}"
    )
    assert (generated / "Makefile").exists(), (
        "stn-jez: nothing is removed for a package whose manifest widens "
        "what the config authorises, Makefile included"
    )


def test_a_manifest_entry_that_escapes_with_dotdot_is_refused_by_name(
    tmp_path, generate_package
):
    """RED today, distinct from test_manifest_survives_a_partial_clean: that
    test pins survival and the non-zero exit; this one additionally asserts
    the offending entry is named in the refusal, which is the "BY NAME" half
    of the requirement.

    SUPERSEDED IN PART by stn-jez: a '..'-escaping entry is also never
    something the config derives, so the widened-manifest check refuses the
    whole group before `_remove_entries` runs its own check -- Makefile now
    survives too.
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)

    sibling_victim = tmp_path / "out" / "sibling_victim.txt"
    sibling_victim.write_text("do not touch")
    bad_entry = "../sibling_victim.txt"

    manifest_path = generated / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"].append(bad_entry)
    manifest_path.write_text(json.dumps(manifest))

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode != 0, "a '..'-escaping manifest entry must be refused"
    assert sibling_victim.read_text() == "do not touch"
    assert bad_entry in (result.stdout + result.stderr), (
        f"the escaping entry should be named in the refusal: {result.stderr!r}"
    )
    assert (generated / "Makefile").exists(), (
        "stn-jez: nothing is removed for a package whose manifest widens "
        "what the config authorises, Makefile included"
    )


def test_a_manifest_entry_that_is_tilde_prefixed_is_refused_by_name(
    tmp_path, generate_package
):
    """RED today: nothing validates a manifest entry at all, so a '~'-
    prefixed entry is neither expanded (pathlib does not expand '~', unlike
    a shell) nor refused -- it is just silently skipped as a nonexistent
    file, and the command exits 0 as if nothing were wrong. `clean` must
    refuse it by name and fail, the same way `check_config_path` already
    refuses it for `docs`, `slides`, `dir`, `dest` and `brand`.

    SUPERSEDED IN PART by stn-jez: a '~'-prefixed entry is also never
    something the config derives, so the widened-manifest check refuses the
    whole group before `_remove_entries` runs its own check -- Makefile now
    survives too.
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)
    bad_entry = "~/nonexistent-target.txt"

    manifest_path = generated / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"].append(bad_entry)
    manifest_path.write_text(json.dumps(manifest))

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode != 0, "a '~'-prefixed manifest entry must be refused"
    assert bad_entry in (result.stdout + result.stderr), (
        f"the '~'-prefixed entry should be named in the refusal: {result.stderr!r}"
    )
    assert (generated / "Makefile").exists(), (
        "stn-jez: nothing is removed for a package whose manifest widens "
        "what the config authorises, Makefile included"
    )


def test_a_manifest_entry_with_a_control_character_is_refused_by_name(
    tmp_path, generate_package
):
    """RED today: no check runs on manifest entries at all, so a control
    character in one is neither refused nor is it what
    test_a_control_character_in_a_package_id_does_not_reach_the_terminal_raw
    already pins (that test is about a package ID, not a manifest entry).
    The raw escape must not reach the terminal either, mirroring that test's
    own assertion.

    SUPERSEDED IN PART by stn-jez: an entry with a control character is
    also never something the config derives, so the widened-manifest check
    refuses the whole group before `_remove_entries` runs its own check --
    Makefile now survives too.
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)
    esc = chr(27)
    bad_entry = f"read{esc}me.md"

    manifest_path = generated / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"].append(bad_entry)
    manifest_path.write_text(json.dumps(manifest))

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode != 0, "a control character in an entry must be refused"
    combined = result.stdout + result.stderr
    assert esc not in combined, (
        "a control character in a manifest entry reached the terminal raw"
    )
    assert (generated / "Makefile").exists(), (
        "stn-jez: nothing is removed for a package whose manifest widens "
        "what the config authorises, Makefile included"
    )


def test_a_manifest_entry_with_a_shell_metacharacter_is_refused_by_name(
    tmp_path, generate_package
):
    """RED today: `;` is a legal POSIX filename character, so the entry is
    followed as a literal name -- meaning a file that happens to exist under
    that literal name is removed with no refusal at all, exactly the
    "filenames, not commands" hole `check_config_path` closes everywhere
    else a configured path reaches a Make recipe.

    SUPERSEDED IN PART by stn-jez: a shell-metacharacter entry is also
    never something the config derives, so the widened-manifest check
    refuses the whole group before `_remove_entries` runs its own check --
    Makefile now survives too.
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)
    bad_entry = "evil;rm.txt"
    victim = generated / bad_entry
    victim.write_text("do not touch")

    manifest_path = generated / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"].append(bad_entry)
    manifest_path.write_text(json.dumps(manifest))

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode != 0, "a shell-metacharacter entry must be refused"
    assert victim.is_file() and victim.read_text() == "do not touch", (
        "a shell-metacharacter entry must not be treated as an ordinary "
        "filename to unlink"
    )
    assert bad_entry in (result.stdout + result.stderr), (
        f"the entry should be named in the refusal: {result.stderr!r}"
    )
    assert (generated / "Makefile").exists(), (
        "stn-jez: nothing is removed for a package whose manifest widens "
        "what the config authorises, Makefile included"
    )


# --- a glob whose expansion lands outside the package is refused -----------


def test_a_glob_entry_whose_expansion_resolves_outside_the_package_is_refused(
    tmp_path, generate_package
):
    """RED today: `(pkg_path / entry).resolve()` follows a symlinked
    subdirectory INSIDE the package before the glob ever runs, so
    'linked/*.txt' globs against whatever `linked` actually points to. The
    entry never needed to itself be absolute or '..'-escaping -- only one
    path SEGMENT of it needs to be a symlink leaving the package.

    SUPERSEDED IN PART by stn-jez: 'linked/*.txt' is also never something
    the config derives, so the widened-manifest check refuses the whole
    group before `_remove_entries` runs its own check -- Makefile now
    survives too.
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)

    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("do not touch")
    os.symlink(outside, generated / "linked")

    manifest_path = generated / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"].append("linked/*.txt")
    manifest_path.write_text(json.dumps(manifest))

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode != 0, "the escaping glob entry must be refused"
    assert secret.read_text() == "do not touch", (
        "a glob entry must never be allowed to expand outside the package, "
        "even via a symlinked subdirectory inside it"
    )
    assert (generated / "Makefile").exists(), (
        "stn-jez: nothing is removed for a package whose manifest widens "
        "what the config authorises, Makefile included"
    )


# --- RESOLVED vs UNRESOLVED (architecture T1) -------------------------------


def test_removing_a_generated_symlink_removes_the_link_not_its_target(
    tmp_path, generate_package
):
    """A generated package containing an ordinary symlink to a file outside
    it -- out/demo/Makefile -> some file elsewhere -- must have the LINK
    removed, not its target. Pinned because the opposite choice makes such a
    package permanently un-cleanable: if the containment check refuses to
    remove the link because its TARGET resolves outside the package, the
    link (which legitimately lives inside the package) can never be cleaned.

    RED today: `_remove_entries` computes `(pkg_path / entry).resolve()`,
    which follows the symlink to its target BEFORE checking existence or
    unlinking -- so it deletes the target and leaves the dangling symlink in
    place, exactly backwards.
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)

    outside_target = tmp_path / "outside_target.txt"
    outside_target.write_text("do not touch")

    makefile_path = generated / "Makefile"
    makefile_path.unlink()
    os.symlink(outside_target, makefile_path)

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert outside_target.is_file() and outside_target.read_text() == "do not touch", (
        "removing a generated entry that happens to be a symlink must not "
        "follow it to the target"
    )
    assert not os.path.lexists(makefile_path), (
        "the symlink itself, which legitimately lives inside the package, "
        "should have been removed"
    )


# --- DEGRADED-PATH VALIDATION (adversarial HIGH 3 / architecture D1) -------


def test_degraded_path_dir_escape_alongside_a_broken_sibling_is_refused(tmp_path):
    """A config with `dir: ../../../victim` AND a broken sibling, so `clean`
    goes degraded (the config does not parse as a whole). GREEN already:
    `check_config_path` refuses the '..' on the raw string before any
    resolution happens, inside `_validated_package_dirs`, which runs
    regardless of whether the config as a whole was readable.
    """
    write_config(
        tmp_path,
        {
            "templates": [{"src": "Makefile.j2"}],
            "packages": {
                "good": {"package_type": "none", "dir": "../../../victim"},
                "broken": {"package_type": "none", "show_download": "no"},
            },
        },
    )

    result = run_cli("clean", "--all", cwd=tmp_path)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "good" in result.stderr
    assert not (tmp_path / "victim").exists()


# --- DEGRADED-PATH SHAPES (architecture D3) ---------------------------------


@pytest.mark.parametrize(
    "config,args",
    [
        pytest.param(
            {"templates": [{"src": "Makefile.j2"}], "packages": [{"name": "oops"}]},
            ("clean", "--all"),
            id="packages-as-list/--all",
        ),
        pytest.param(
            {"templates": [{"src": "Makefile.j2"}], "packages": [{"name": "oops"}]},
            ("clean", "demo"),
            id="packages-as-list/demo",
        ),
        pytest.param(
            {"templates": [{"src": "Makefile.j2"}], "packages": {"demo": "oops"}},
            ("clean", "--all"),
            id="string-valued-package/--all",
        ),
        pytest.param(
            {"templates": [{"src": "Makefile.j2"}], "packages": {"demo": "oops"}},
            ("clean", "demo"),
            id="string-valued-package/demo",
        ),
        pytest.param(
            {
                "templates": [{"src": "Makefile.j2"}],
                "packages": {"demo": {"package_type": "none", "dir": 7}},
            },
            ("clean", "--all"),
            id="dir-is-int/--all",
        ),
        pytest.param(
            {
                "templates": [{"src": "Makefile.j2"}],
                "packages": {"demo": {"package_type": "none", "dir": 7}},
            },
            ("clean", "demo"),
            id="dir-is-int/demo",
        ),
    ],
)
def test_degraded_path_malformed_shapes_fail_closed_without_a_traceback(
    tmp_path, config, args
):
    """GREEN already: `_clean_scope` and `_validated_package_dirs` (stn-2x4.8)
    shape-guard `packages` and each package's `dir` before either is used, on
    both the `--all` and the single-package CLI paths.
    """
    write_config(tmp_path, config)

    result = run_cli(*args, cwd=tmp_path)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr


# --- NESTED dir (architecture T4) -------------------------------------------


def test_nested_dir_never_makes_the_package_root_an_rmdir_candidate(tmp_path):
    """`dir: a/b`. GREEN already: `_remove_empty_parent_dirs` guards with
    `len(d.relative_to(pkg_path).parts) >= 1`, anchored on the resolved
    `pkg_path` itself -- so the package's own root (whose relative path to
    itself has zero parts) can never be a candidate, nested or not.
    """
    write_config(
        tmp_path,
        {
            "output_dir": "out",
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": {"package_type": "none", "dir": "a/b"}},
        },
    )
    setup = run_cli("gen", "demo", cwd=tmp_path)
    assert setup.returncode == 0, setup.stderr

    pkg_path = tmp_path / "out" / "a" / "b"
    assert pkg_path.is_dir(), "setup: the nested package directory should exist"

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert pkg_path.is_dir(), (
        "the package's own root directory must never be an rmdir candidate"
    )
    assert not any(pkg_path.iterdir()), "the package root should now be empty"


# --- THE SWEEP MUST NOT GET MORE PERMISSIVE (adversarial MEDIUM 11) --------


def test_sweep_never_rmdirs_a_directory_outside_the_package_tree(
    tmp_path, generate_package
):
    """The existing `d.relative_to(pkg_path)` guard in
    `_remove_empty_parent_dirs` is the only reason `clean` does not also
    rmdir outside the tree today -- a directory outside `pkg_path` raises
    `ValueError` on that call and is skipped via `except ValueError: pass`.
    GREEN already: asserted directly here so a future rewrite of the sweep
    cannot quietly trade this accident for a looser rule.

    Deliberately independent of whether the FILE inside the escaped
    directory gets removed -- today it wrongly does (that is
    test_a_manifest_entry_that_escapes_with_dotdot_is_refused_by_name's
    concern, RED, stn-2x4.6), and the command exits 0 because nothing
    currently flags the escape as a problem at all. Neither of those is
    asserted here; the ONLY property under test is that emptying (or
    leaving non-empty) that outside directory never makes it an rmdir
    candidate, whatever the entry-level containment fix eventually does.
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)

    escape_dir = tmp_path / "out" / "escapedir"
    escape_dir.mkdir()
    (escape_dir / "escape.txt").write_text("do not touch the directory")

    manifest_path = generated / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"].append("../escapedir/escape.txt")
    manifest_path.write_text(json.dumps(manifest))

    run_cli("clean", "demo", cwd=tmp_path)

    assert escape_dir.is_dir(), (
        "a directory outside the package tree must never be rmdir'd by the "
        "empty-parent-directory sweep, regardless of what happened to a "
        "file inside it"
    )


# =============================================================================
# Parser rejection cases (stn-2x4.4), driven through `clean` at the CLI
# boundary rather than by calling `read_manifest` directly: a damaged
# manifest must be refused BY NAME, with NOTHING removed for that package,
# non-zero, no traceback -- and a v1 document with an unknown extra key must
# be ACCEPTED (forward compatibility).
# =============================================================================


@pytest.mark.parametrize(
    "raw_content,match",
    [
        pytest.param("{not valid json", "could not be parsed", id="damaged-json"),
        pytest.param(json.dumps(["a", "b"]), "not a JSON object", id="not-an-object"),
        pytest.param(
            json.dumps(
                {
                    "manifest_version": 1,
                    "stencil_version": "0.1.0",
                    "package": "demo",
                    "dir": "demo",
                    "entries": [1, 2, 3],
                }
            ),
            "entries",
            id="entries-not-list-of-strings",
        ),
        pytest.param(
            json.dumps(
                {
                    "manifest_version": 999,
                    "stencil_version": "0.1.0",
                    "package": "demo",
                    "dir": "demo",
                    "entries": ["Makefile"],
                }
            ),
            "manifest_version",
            id="unknown-manifest-version",
        ),
        pytest.param(
            '{"manifest_version": 1, "stencil_version": "0.1.0", '
            '"package": "demo", "dir": "demo", '
            '"entries": ["safe"], "entries": ["Makefile"]}',
            "duplicate",
            id="duplicate-entries-key",
        ),
        # --- stn-jez: a manifest missing (or lying about the type of) a
        # field write_manifest has always emitted, checked LAST inside
        # read_manifest -- below manifest_version and entries, which is why
        # the two fixtures just above still have to carry stencil_version.
        pytest.param(
            json.dumps(
                {
                    "manifest_version": 1,
                    "stencil_version": "0.1.0",
                    "dir": "demo",
                    "entries": ["Makefile"],
                }
            ),
            '"package"',
            id="stn-jez-package-missing",
        ),
        pytest.param(
            json.dumps(
                {
                    "manifest_version": 1,
                    "stencil_version": "0.1.0",
                    "package": "demo",
                    "entries": ["Makefile"],
                }
            ),
            '"dir"',
            id="stn-jez-dir-missing",
        ),
        pytest.param(
            json.dumps(
                {
                    "manifest_version": 1,
                    "package": "demo",
                    "dir": "demo",
                    "entries": ["Makefile"],
                }
            ),
            '"stencil_version"',
            id="stn-jez-stencil_version-missing",
        ),
        pytest.param(
            json.dumps(
                {
                    "manifest_version": 1,
                    "stencil_version": "0.1.0",
                    "package": 42,
                    "dir": "demo",
                    "entries": ["Makefile"],
                }
            ),
            '"package"',
            id="stn-jez-package-not-a-string",
        ),
        pytest.param(
            json.dumps(
                {
                    "manifest_version": 1,
                    "stencil_version": "0.1.0",
                    "package": "demo",
                    "dir": 42,
                    "entries": ["Makefile"],
                }
            ),
            '"dir"',
            id="stn-jez-dir-not-a-string",
        ),
        pytest.param(
            json.dumps(
                {
                    "manifest_version": 1,
                    "stencil_version": 42,
                    "package": "demo",
                    "dir": "demo",
                    "entries": ["Makefile"],
                }
            ),
            '"stencil_version"',
            id="stn-jez-stencil_version-not-a-string",
        ),
    ],
)
def test_a_malformed_manifest_is_refused_by_name_nothing_removed(
    tmp_path, generate_package, raw_content, match
):
    """The first four cases are GREEN already: stn-2x4.4's `read_manifest`
    already raises `ManifestError` for each of them, and `_clean_one_directory`
    already treats that as a whole-package refusal rather than falling back
    to the config. The six `stn-jez-*` cases are RED until `read_manifest`
    also requires `package`, `dir` and `stencil_version` -- the three fields
    `write_manifest` has always emitted but that were previously read
    permissively, which is what let a manifest missing `package` entirely
    slip past `_clean_one_directory`'s ownership guard (see that function's
    docstring).
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)
    manifest_path = generated / MANIFEST_NAME
    manifest_path.write_text(raw_content)

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert match in (result.stdout + result.stderr), result.stderr
    assert (generated / "Makefile").exists(), "nothing should have been removed"
    assert manifest_path.exists(), "the damaged manifest itself must survive too"


def _complete_manifest_document(**overrides) -> dict:
    """A manifest write_manifest could plausibly have written, so each
    direct `read_manifest` test below breaks exactly one field."""
    document = {
        "manifest_version": MANIFEST_VERSION,
        "stencil_version": "0.1.0",
        "package": "demo",
        "dir": "demo",
        "entries": ["Makefile"],
    }
    document.update(overrides)
    return document


@pytest.mark.parametrize(
    "key",
    ["package", "dir", "stencil_version"],
)
def test_read_manifest_refuses_a_missing_writer_field_directly(tmp_path, key):
    """DIRECT call, test_config_fail_closed.py's convention for a function
    that raises on its own: `read_manifest` requires `package`, `dir` and
    `stencil_version` (write_manifest has emitted all three since manifest
    v1 -- verified: `git show 1e25490`), and a manifest missing one entirely
    must raise `ManifestError` naming that field, not silently read as if
    the field were merely absent from the diagnostic context."""
    document = _complete_manifest_document()
    del document[key]
    manifest_path = tmp_path / MANIFEST_NAME
    manifest_path.write_text(json.dumps(document))

    with pytest.raises(ManifestError, match=f'"{key}"'):
        read_manifest(manifest_path)


@pytest.mark.parametrize(
    "key",
    ["package", "dir", "stencil_version"],
)
def test_read_manifest_refuses_a_non_string_writer_field_directly(tmp_path, key):
    """Same field, the other malformed shape: present but not a string."""
    document = _complete_manifest_document(**{key: 42})
    manifest_path = tmp_path / MANIFEST_NAME
    manifest_path.write_text(json.dumps(document))

    with pytest.raises(ManifestError, match=f'"{key}"'):
        read_manifest(manifest_path)


def test_read_manifest_still_reads_a_complete_manifest_directly(tmp_path):
    """The control: a manifest carrying every field write_manifest has
    always emitted, correctly typed, still reads -- the required-field
    checks refuse an absence, never a presence."""
    document = _complete_manifest_document()
    manifest_path = tmp_path / MANIFEST_NAME
    manifest_path.write_text(json.dumps(document))

    assert read_manifest(manifest_path) == document


def test_an_oversized_manifest_is_refused_by_name_nothing_removed(
    tmp_path, generate_package
):
    """GREEN already: `read_manifest` stats the file and refuses anything
    over `_MANIFEST_MAX_BYTES` before parsing it at all."""
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)
    manifest_path = generated / MANIFEST_NAME
    huge_entries = [f"file{i}.txt" for i in range(70000)]
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "stencil_version": "0.1.0",
                "package": "demo",
                "dir": "demo",
                "entries": huge_entries,
            }
        )
    )
    assert manifest_path.stat().st_size > 1024 * 1024, "setup: must exceed the cap"

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "byte" in (result.stdout + result.stderr)
    assert (generated / "Makefile").exists()
    assert manifest_path.exists()


def test_a_directory_at_the_manifest_path_is_refused_not_treated_as_absent(
    tmp_path, generate_package
):
    """RED today: `_clean_one_directory` gates on `manifest_path.is_file()`,
    which is False for a directory, so it silently falls to the "no
    manifest" branch and derives from the config instead of refusing -- a
    directory occupying the manifest's name is a DAMAGED manifest, not the
    same statement as no manifest being present, and clean must not guess.
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)
    manifest_path = generated / MANIFEST_NAME
    manifest_path.unlink()
    manifest_path.mkdir()

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode != 0, (
        "a directory at the manifest's path must be refused, not silently "
        "treated as no manifest at all"
    )
    assert "Traceback" not in result.stderr
    assert (generated / "Makefile").exists(), "nothing should have been removed"
    assert manifest_path.is_dir(), "the directory itself must survive too"


def test_a_fifo_at_the_manifest_path_is_refused_and_never_blocks(
    tmp_path, generate_package
):
    """Same shape as the directory case above, with an added hazard: a FIFO
    with no writer blocks forever on open() for reading. Measured directly
    against `read_manifest` on this branch: it DOES block until interrupted,
    which is why this test drives the CLI as a subprocess with an explicit
    timeout, so a future regression that opens the FIFO unconditionally
    fails this test rather than wedging the suite.

    RED today (for the same reason as the directory case): `is_file()` is
    False for a FIFO too, so `clean` never even attempts to open it -- it
    falls back to the config silently instead of refusing.
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)
    manifest_path = generated / MANIFEST_NAME
    manifest_path.unlink()
    os.mkfifo(manifest_path)

    try:
        result = run_cli("clean", "demo", cwd=tmp_path, timeout=10)
    except subprocess.TimeoutExpired:
        pytest.fail(
            "clean hung reading a FIFO at the manifest's path -- opening it "
            "at all is the bug this test exists to catch before it can "
            "wedge CI"
        )

    assert result.returncode != 0, (
        "a FIFO at the manifest's path must be refused, not silently "
        "treated as no manifest at all"
    )
    assert "Traceback" not in result.stderr
    assert (generated / "Makefile").exists(), "nothing should have been removed"
    assert manifest_path.exists(), "the FIFO itself must survive too"


def test_manifest_version_1_with_an_unknown_extra_key_is_accepted(
    tmp_path, generate_package
):
    """Forward compatibility (review finding D7): a v1 manifest written by a
    LATER stencil that has added some diagnostic field must still be
    readable by this one. GREEN already: `read_manifest` only enforces
    `manifest_version` and the shape of `entries`; every other key is read
    permissively.
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)
    manifest_path = generated / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["a_future_stencil_field"] = "something this version has never heard of"
    manifest_path.write_text(json.dumps(manifest))

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert not (generated / "Makefile").exists()
    assert not manifest_path.exists()


# --- ADVERSARIAL ROUND 2: the four defects found on this branch ------------
#
# Every test below was reproduced by hand against this branch before it was
# written. They are not speculative hardening; each one names a measured
# behaviour that the committed implementation got wrong.


# --- 1. the glob vocabulary reasoned about '*' and nothing else ------------


@pytest.mark.parametrize(
    "entry",
    ["?*", "[!z]*", "[a-z]*", "sub/?*", "Makefil?", "Guide[0-9]*.html", "sub/[a-z]/x"],
    ids=[
        "question-star",
        "negated-class-star",
        "range-class-star",
        "subdir-question-star",
        "trailing-question",
        "class-inside-a-legitimate-shape",
        "class-in-a-non-final-component",
    ],
)
def test_check_glob_vocabulary_refuses_every_glob_metacharacter(entry):
    """`check_glob_vocabulary` reasoned only about '*', but `Path.glob`
    honours '?' and '[...]' too, and none of the three is in
    `_UNSAFE_IN_PATH`. Measured on this branch: every entry above passed
    BOTH `check_config_path` and `check_glob_vocabulary`.

    '?*' is the one that matters most -- it is a bare '*' wearing a hat,
    and `Path.glob` (unlike `glob.glob`) matches dotfiles, so it also
    matches `.stencil-manifest.json` itself.
    """
    with pytest.raises(ValueError):
        check_glob_vocabulary("demo", "manifest entry", entry)


def test_a_question_mark_glob_entry_does_not_sweep_the_authors_own_files(
    tmp_path, generate_package
):
    """The end-to-end reproduction. A manifest carrying ['?*', 'sub/?*']
    removed every file in the package -- the author's own `thesis.md`,
    `research.bib` and `sub/keep.md` included -- and exited 0.

    SUPERSEDED IN PART by stn-jez: '?*' and 'sub/?*' are also never
    something the config derives, so the widened-manifest check now refuses
    the whole group before `_remove_entries` (and its own
    `check_glob_vocabulary` call) ever runs -- `Makefile` now survives
    alongside the author's own files, rather than still being removed next
    to the refusal.
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)

    (generated / "sub").mkdir()
    authored = {
        generated / "thesis.md": "the author's own source",
        generated / "research.bib": "the author's own bibliography",
        generated / "sub" / "keep.md": "the author's own chapter",
    }
    for path, text in authored.items():
        path.write_text(text)

    manifest_path = generated / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"] += ["?*", "sub/?*"]
    manifest_path.write_text(json.dumps(manifest))

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode != 0, (
        "'?*' must be refused, not expanded, so the command must fail"
    )
    assert "Traceback" not in result.stderr
    for path, text in authored.items():
        assert path.is_file() and path.read_text() == text, (
            f"{path.name} is the author's own file and was swept up by '?*'"
        )
    assert (generated / "Makefile").exists(), (
        "stn-jez: nothing is removed for a package whose manifest widens "
        "what the config authorises, Makefile included"
    )


@pytest.mark.parametrize("key", ["docs", "slides"])
def test_a_glob_metacharacter_in_docs_or_slides_is_refused_at_gen_time(tmp_path, key):
    """The other half of the same defect: a config value is where a
    metacharacter gets INTO a manifest entry. `docs: ["[!z].md"]` produced
    the entry `[!z]*.html`, which the vocabulary check above now refuses --
    leaving that package permanently un-cleanable, with the complaint
    pointing at a "manifest entry" the author never wrote.

    Refused where it was written instead. `package_sources` deliberately
    keeps its globs (`md/*.md` is documented and used), which is why
    `_UNSAFE_IN_PATH` itself is left alone.
    """
    write_config(
        tmp_path,
        {
            "output_dir": "out",
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": {"package_type": "none", key: ["[!z].md"]}},
        },
    )

    result = run_cli("gen", "demo", cwd=tmp_path)

    assert result.returncode != 0, f"a glob in {key} must be refused"
    assert "Traceback" not in result.stderr
    assert key in result.stderr


# --- 2. two packages sharing a `dir` -- clean --all lost files main removed -


def _shared_dir_config() -> dict:
    """Two packages on one `dir`, with DIFFERENT entry sets.

    The distinct `package_name` is the whole point: the existing shared-dir
    test uses two `package_type: none` packages with identical settings, so
    their entry sets are equal and a manifest naming only one of them is
    indistinguishable from a manifest naming both.
    """
    return {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {
            "alpha": {
                "name": "Alpha",
                "dir": "shareddir",
                "package_type": "zip",
                "package_name": "alpha.zip",
            },
            "beta": {
                "name": "Beta",
                "dir": "shareddir",
                "package_type": "zip",
                "package_name": "beta.zip",
            },
        },
    }


def test_clean_all_removes_both_shared_dir_packages_artifacts(tmp_path):
    """`generate_package` unlinks the shared manifest and writes only its
    own entries, so after `gen --all` the manifest names whichever package
    ran LAST. `_clean_one_directory` then drove the whole group from that
    manifest, and the other package's artifacts were never named.

    Measured on this branch: `alpha.zip` survived `clean --all`, which
    exited 0 and said nothing. `main` removed it. A manifest that makes
    `clean` LESS thorough than the config-derived predecessor is the
    opposite of what stn-p9a is for.
    """
    write_config(tmp_path, _shared_dir_config())
    setup = run_cli("gen", "--all", cwd=tmp_path)
    assert setup.returncode == 0, setup.stderr

    shared = tmp_path / "out" / "shareddir"
    (shared / "alpha.zip").write_text("alpha build output")
    (shared / "beta.zip").write_text("beta build output")

    result = run_cli("clean", "--all", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert not (shared / "beta.zip").exists()
    assert not (shared / "alpha.zip").exists(), (
        "alpha's archive was left behind because the shared manifest named "
        "only beta -- silently, and with exit 0"
    )
    assert not (shared / MANIFEST_NAME).exists()


def test_shared_dir_member_the_manifest_does_not_name_is_reported_when_the_config_is_broken(
    tmp_path,
):
    """The union above is only available while the config is readable. When
    it is not, the member the manifest does not name cannot be derived from
    anywhere -- so it is a NAMED problem and a non-zero exit, never a silent
    drop. The manifest survives, so the run can be resumed once the config
    is fixed.
    """
    config = _shared_dir_config()
    write_config(tmp_path, config)
    setup = run_cli("gen", "--all", cwd=tmp_path)
    assert setup.returncode == 0, setup.stderr

    shared = tmp_path / "out" / "shareddir"
    (shared / "alpha.zip").write_text("alpha build output")
    (shared / "beta.zip").write_text("beta build output")

    broken = copy.deepcopy(config)
    broken["packages"]["alpha"]["show_download"] = "no"
    write_config(tmp_path, broken)

    result = run_cli("clean", "--all", cwd=tmp_path)

    assert result.returncode != 0, (
        "alpha is neither named by the manifest nor derivable from a broken "
        "config; it cannot be reported as cleaned"
    )
    assert "Traceback" not in result.stderr
    assert "alpha" in result.stderr
    assert (shared / "alpha.zip").is_file(), "alpha's archive was not named"
    assert not (shared / "beta.zip").exists(), (
        "beta IS named by the manifest and should still have been cleaned"
    )
    assert (shared / MANIFEST_NAME).is_file(), (
        "a run with problems leaves the manifest behind to resume from"
    )


# --- 3. `clean <pkg>` was permanently impossible for a shared-dir package ---


def test_clean_of_one_package_sharing_a_dir_is_not_permanently_refused(tmp_path):
    """`clean_generated` built its groups from the CLI SCOPE, so
    `clean alpha` produced a member set of {alpha} and the shared manifest
    -- which names beta -- failed the membership test every single time.
    No user action cleared it: `clean --all` worked and `clean alpha` could
    not, ever.

    The membership test belongs against the directory's COMPLETE member set,
    read from `config['packages']`, with the selection intersected into it
    afterwards.
    """
    write_config(tmp_path, _shared_dir_config())
    setup = run_cli("gen", "--all", cwd=tmp_path)
    assert setup.returncode == 0, setup.stderr

    shared = tmp_path / "out" / "shareddir"
    (shared / "alpha.zip").write_text("alpha build output")
    (shared / "beta.zip").write_text("beta build output")

    result = run_cli("clean", "alpha", cwd=tmp_path)

    assert result.returncode == 0, (
        f"clean of a shared-dir package must be possible at all: "
        f"{result.stderr}"
    )
    assert "refusing to use it" not in result.stderr
    assert not (shared / "alpha.zip").exists()
    assert not (shared / MANIFEST_NAME).exists()
    assert not (shared / "beta.zip").exists(), (
        "one directory is one blast radius: the shared manifest is beta's, "
        "and removing it without removing what it names would strand beta"
    )


# --- 4. the sweep rmdir'd an UNRESOLVED path ------------------------------


def test_a_symlinked_intermediate_directory_does_not_traceback_after_deleting(
    tmp_path, generate_package
):
    """`_remove_entries` returned UNRESOLVED paths and the sweep called
    `rmdir` on `{p.parent}` of each. With an intermediate directory that is
    a symlink pointing INSIDE the package, that parent is the symlink, and
    `rmdir` raises NotADirectoryError -- a bare traceback from the command
    whose premise is working when everything else is broken, landing AFTER
    the unlink.

    It also means the sweep's `relative_to`/`is_relative_to` guards were
    lexical tests on an unresolved path rather than containment checks.

    The second template below (`dest: "sub/x.txt"`) is stn-jez's doing: the
    widened-manifest check refuses any entry the config does not derive, so
    "sub/x.txt" has to be a real, config-derived destination -- not merely
    appended to the manifest by hand as this test used to -- or the
    reproduction below would be refused before it ever reached the sweep
    this test is actually about.
    """
    config = {
        "output_dir": "out",
        "templates": [
            {"src": "Makefile.j2"},
            {"src": "Makefile.j2", "dest": "sub/x.txt"},
        ],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)

    # Swap the real "sub" gen just wrote for a symlink pointing at a fresh
    # "real" directory holding the same relative file, reproducing an
    # intermediate directory that is a symlink pointing INSIDE the package.
    # The manifest already names "sub/x.txt" -- write_manifest recorded it
    # from `package_entries` -- so nothing needs to be added by hand.
    (generated / "sub" / "x.txt").unlink()
    (generated / "sub").rmdir()
    (generated / "real").mkdir()
    (generated / "real" / "x.txt").write_text("generated")
    os.symlink("real", generated / "sub")

    manifest_path = generated / MANIFEST_NAME

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert "Traceback" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stderr
    assert not (generated / "real" / "x.txt").exists()
    assert not manifest_path.exists()


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores the directory permissions this test relies on",
)
def test_an_entry_that_cannot_be_unlinked_is_a_named_problem_not_a_traceback(
    tmp_path, generate_package
):
    """`path.unlink()` ran unguarded in the middle of the removal loop, so a
    permission error was a traceback halfway through deleting -- the exact
    shape `_main`'s clean branch says in a comment that it refuses to
    create.

    `dest: "locked/x.txt"` (stn-jez): the widened-manifest check refuses any
    entry the config does not derive, so the entry under test has to be a
    real, config-derived destination rather than spliced into the manifest
    by hand -- otherwise this test would still pass, but for stn-jez's
    refusal instead of the permission failure it exists to exercise.
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2", "dest": "locked/x.txt"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)

    locked = generated / "locked"
    manifest_path = generated / MANIFEST_NAME

    locked.chmod(0o555)
    try:
        result = run_cli("clean", "demo", cwd=tmp_path)
    finally:
        locked.chmod(0o755)

    assert "Traceback" not in result.stderr, result.stderr
    assert result.returncode != 0
    assert "locked/x.txt" in result.stderr
    assert (locked / "x.txt").is_file()
    assert manifest_path.is_file(), (
        "a refused entry leaves the manifest behind, so it is still named "
        "next time"
    )


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores the directory permissions this test relies on",
)
def test_a_directory_that_cannot_be_rmdired_is_a_named_problem_not_a_traceback(
    tmp_path, generate_package
):
    """The same guard on the sweep's own `rmdir`: an emptied directory whose
    PARENT is not writable cannot be removed, and that is a named failure,
    never a traceback after the files underneath it are already gone.

    `dest: "sub/x.txt"` (stn-jez): the widened-manifest check refuses any
    entry the config does not derive, so the entry under test has to be a
    real, config-derived destination -- otherwise this test would still
    pass, but for stn-jez's refusal instead of the rmdir failure it exists
    to exercise.
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2", "dest": "sub/x.txt"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)
    manifest_path = generated / MANIFEST_NAME

    generated.chmod(0o555)
    try:
        result = run_cli("clean", "demo", cwd=tmp_path)
    finally:
        generated.chmod(0o755)

    assert "Traceback" not in result.stderr, result.stderr
    assert result.returncode != 0
    assert "sub" in result.stderr
    assert not (generated / "sub" / "x.txt").exists(), (
        "the file itself was removable and should have been removed"
    )


# --- 5. a non-mapping `templates` crashed AFTER partial deletion -----------


def test_a_non_mapping_templates_entry_is_a_named_problem_not_a_traceback(tmp_path):
    """`package_contexts` only inspects `templates` when it is a list and
    skips non-dict members, so `templates: ["Makefile.j2"]` -- strings, not
    mappings -- passes validation and the config-derived fallback reaches
    `tdef.get(...)` with a `str`.

    On `main` the whole removal list was computed before the first unlink.
    On this branch the work is interleaved per group, so the first group is
    DELETED and the second tracebacks.
    """
    good = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {
            "aaa": {"name": "A", "package_type": "none"},
            "zzz": {"name": "Z", "package_type": "none"},
        },
    }
    write_config(tmp_path, good)
    setup = run_cli("gen", "--all", cwd=tmp_path)
    assert setup.returncode == 0, setup.stderr

    # aaa keeps its manifest and cleans from it; zzz must fall back to the
    # config, which is where the crash used to land -- after aaa's files
    # were already gone.
    (tmp_path / "out" / "zzz" / MANIFEST_NAME).unlink()

    broken = copy.deepcopy(good)
    broken["templates"] = ["Makefile.j2"]
    write_config(tmp_path, broken)

    result = run_cli("clean", "--all", cwd=tmp_path)

    assert "Traceback" not in result.stderr, result.stderr
    assert result.returncode != 0
    assert "templates" in result.stderr
    assert not (tmp_path / "out" / "aaa" / "Makefile").exists(), (
        "aaa had a manifest and should still have been cleaned"
    )


# =============================================================================
# stn-jez (operator ruling): A MANIFEST MAY NARROW WHAT THE CONFIG
# AUTHORISES, NEVER WIDEN IT.
#
# The field-presence fix earlier in this file (the "a manifest stencil did
# not write" section) stops a MALFORMED manifest, not a FORGED one: every
# field it requires is free to an attacker -- `stencil_version` is what
# `stencil version` prints, `package` is a package id read off the config
# being attacked, `dir` defaults to the package id. A manifest with every
# field correct and naming the right package can still list an entry the
# config never derives for it. `_clean_one_directory` now compares the
# manifest's own entries against `_config_derived_entries` for every package
# configured with this directory, on the config_readable=True path only, and
# refuses BY NAME -- removing nothing for the package -- when an entry is
# not in that authorised set, or when the manifest's own "dir" does not
# match the package's configured one.
# =============================================================================


def test_a_manifest_entry_the_config_does_not_derive_is_refused_and_nothing_removed(
    tmp_path, generate_package
):
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)
    manifest_path = generated / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"].append(".env")
    manifest_path.write_text(json.dumps(manifest))
    (generated / ".env").write_text("SECRET=1\n")

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode != 0, (
        "an entry the config does not derive must be refused, not honoured"
    )
    assert "Traceback" not in result.stderr, result.stderr
    combined = result.stdout + result.stderr
    assert ".env" in combined, f"the widened entry should be named: {combined!r}"
    assert (generated / ".env").exists(), "a widened entry must not be removed"
    assert (generated / "Makefile").exists(), (
        "nothing should be removed for a package whose manifest widens"
    )
    assert manifest_path.exists(), "the manifest survives a refusal"


def test_a_manifest_whose_dir_does_not_match_the_configured_one_is_refused(
    tmp_path, generate_package
):
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)
    manifest_path = generated / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["dir"] = "somewhere-else"
    manifest_path.write_text(json.dumps(manifest))

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode != 0, (
        "a manifest whose 'dir' does not match the package's configured "
        "one must be refused"
    )
    assert "Traceback" not in result.stderr, result.stderr
    combined = result.stdout + result.stderr
    assert '"dir"' in combined, f"the dir mismatch should be named: {combined!r}"
    assert (generated / "Makefile").exists(), (
        "nothing should be removed for a package whose manifest's dir "
        "does not match"
    )
    assert manifest_path.exists(), "the manifest survives a refusal"


def test_a_manifest_naming_a_strict_subset_of_the_configs_entries_still_cleans(
    tmp_path, generate_package
):
    """The narrowing half of the invariant: fewer entries than the config
    would derive is the NORMAL case (a doc dropped mid-package, say) and
    must keep working exactly as it always has -- only WIDENING is new."""
    config = {
        "output_dir": "out",
        "templates": MAKEFILE_TEMPLATES,
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)
    manifest_path = generated / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"] = ["Makefile"]
    manifest_path.write_text(json.dumps(manifest))

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert not (generated / "Makefile").exists()
    assert (generated / "docker-compose.yml").exists(), (
        "narrowing removes only what the manifest names, not a "
        "config-derived superset"
    )


def test_a_sibling_packages_config_error_does_not_disable_the_widen_check(tmp_path):
    """A fault in a package this command never touches must NOT switch the
    authority rule off for the package it does touch.

    THIS TEST REPLACES ONE THAT ASSERTED THE OPPOSITE, and the one it
    replaces was a pin of a live vulnerability rather than of a decision.
    The rule used to be gated on `config_readable`, a WHOLE-CONFIG boolean
    that `_main` clears when `package_contexts` raises for ANY package. So a
    single quoted `show_download: "no"` on an unrelated sibling restored the
    original stn-jez attack in full: measured, a planted manifest deleted a
    hand-written file at exit 0, with `clean`'s reassuring degraded warning
    printed immediately above the deletion.

    The gate is now the narrow question -- can the authorised set for THIS
    directory be derived? -- so a sibling's fault is irrelevant to it.
    """
    initially_fine = copy.deepcopy(GOOD_AND_BROKEN_CONFIG)
    del initially_fine["packages"]["broken"]["show_download"]
    write_config(tmp_path, initially_fine)
    setup = run_cli("gen", "--all", cwd=tmp_path)
    assert setup.returncode == 0, setup.stderr

    (tmp_path / "good" / ".env").write_text("SECRET=1\n")
    manifest_path = tmp_path / "good" / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"].append(".env")
    manifest_path.write_text(json.dumps(manifest))

    # Break ONLY the sibling. `good` itself is untouched and derivable.
    write_config(tmp_path, GOOD_AND_BROKEN_CONFIG)

    result = run_cli("clean", "good", cwd=tmp_path)

    assert (tmp_path / "good" / ".env").exists(), (
        "a sibling package's config error must not disable the widen check "
        f"for this one: {result.stdout + result.stderr!r}"
    )
    assert result.returncode != 0
    assert "Traceback" not in result.stderr, result.stderr
    assert ".env" in (result.stdout + result.stderr)


def test_a_forged_manifest_is_honoured_when_this_directory_cannot_be_derived(
    tmp_path,
):
    """DOCUMENTED LIMIT, not a defect (operator: "a limit with no fix is
    documentation").

    When the authorised set for THIS directory cannot be derived, the
    manifest is the only thing that can name what is here -- not the config,
    which is what failed, and not a sibling's manifest, which names other
    files. So it is trusted, and a forgery is honoured. That is the trade
    stn-p9a exists to make: the one command someone reaches for BECAUSE
    their config broke must not be the command that cannot answer.

    Note how much narrower this limit is than it used to be. It needs the
    fault to be in a package configured with THIS directory; a fault
    anywhere else in the config no longer reaches it. See the test above
    for the case that used to land here and no longer does.
    """
    broken = copy.deepcopy(GOOD_AND_BROKEN_CONFIG)
    initially_fine = copy.deepcopy(broken)
    del initially_fine["packages"]["broken"]["show_download"]
    write_config(tmp_path, initially_fine)
    setup = run_cli("gen", "--all", cwd=tmp_path)
    assert setup.returncode == 0, setup.stderr

    # The forgery goes on the package whose OWN config entry is about to
    # become underivable.
    (tmp_path / "broken" / ".env").write_text("SECRET=1\n")
    manifest_path = tmp_path / "broken" / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"].append(".env")
    manifest_path.write_text(json.dumps(manifest))

    write_config(tmp_path, broken)

    result = run_cli("clean", "broken", cwd=tmp_path)

    assert result.returncode == 0, (
        "broken has its own manifest and its config cannot be derived, so "
        f"the documented degraded trade applies: {result.stderr!r}"
    )
    assert not (tmp_path / "broken" / ".env").exists(), (
        "documented limit: with this directory underivable, the manifest is "
        "the only authority and a forged entry is honoured"
    )



# --- stn-jez, the exemption that only helped a forgery ----------------------


def test_a_manifest_that_lists_itself_is_refused_not_exempted(
    tmp_path, generate_package
):
    """A manifest naming `.stencil-manifest.json` among its own entries is a
    WIDENED manifest and gets the named refusal.

    An earlier version of the widen check exempted `MANIFEST_NAME` on the
    grounds that `package_entries` never lists it, so it "is not the caller's
    entry to authorise". That reasoning is backwards, and the adversarial pass
    over the implementation proved it: because no honest manifest ever
    contains it, the exemption could only ever admit a dishonest one -- and
    the exemption's own comment claimed `_remove_entries` never receives the
    name, which was false. `entries` is the manifest's list, unfiltered.

    The harm was not hypothetical. With the exemption in place a self-listing
    manifest had the manifest unlinked in the MIDDLE of the entry loop, and
    with one further entry refused the run ended with the refused file still
    on disk and the manifest gone -- destroying the resume guarantee
    `test_manifest_survives_a_partial_clean` exists to hold.
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    generated = generate_package(config)

    manifest_path = generated / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"].append(MANIFEST_NAME)
    manifest_path.write_text(json.dumps(manifest))

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode != 0, "a self-listing manifest must be refused"
    assert "Traceback" not in result.stderr, result.stderr
    assert MANIFEST_NAME in (result.stdout + result.stderr), (
        "the offending entry must be named"
    )
    assert manifest_path.exists(), "nothing is removed for a refused package"
    assert (generated / "Makefile").exists(), (
        "the refusal is whole-package: no entry is removed"
    )


def test_the_degraded_path_warns_that_the_manifest_is_taken_on_trust(tmp_path):
    """The one path where an unauthenticated file is trusted must say so.

    Found by the local CodeRabbit pass: the branch recorded exactly why it
    could not derive the authorised set and then discarded it, so from
    outside, "the manifest passed the check" and "the check never ran" looked
    identical -- the absence of a refusal was the only signal either way.

    A WARNING, not a problem: `problems` decides the exit status and this run
    is a success. stn-p9a's promise is that the command you reach for because
    your config broke still cleans from the manifest, so it exits 0 and says
    what it could not verify while doing it.
    """
    broken = copy.deepcopy(GOOD_AND_BROKEN_CONFIG)
    initially_fine = copy.deepcopy(broken)
    del initially_fine["packages"]["broken"]["show_download"]
    write_config(tmp_path, initially_fine)
    setup = run_cli("gen", "--all", cwd=tmp_path)
    assert setup.returncode == 0, setup.stderr

    write_config(tmp_path, broken)
    result = run_cli("clean", "broken", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "taken on trust" in combined, (
        f"the degraded path must say the manifest was not checked: {combined!r}"
    )
    assert "show_download" in combined, (
        "and it must name the underlying reason it could not be checked"
    )
