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

`write_manifest` and `ManifestError` are deliberately NOT imported here.
Nothing in this file calls either: the writer is exercised through
`generate_package`, and the error type belongs with the parser-rejection cases
in stn-2x4.5. Importing a name only to `assert` it exists proves nothing that
the tests below do not already prove, and leaves an unused import behind.

`read_manifest` takes the PATH TO A MANIFEST FILE and returns the parsed
document, rather than an (output_base, pkg_dir) pair returning just the
entries. That is the seam this file asserts against, and stn-2x4.4/.8 were
corrected to match it: a pure parser that knows nothing about `clean` is the
one the architecture review asked for, and these tests need the whole
document -- `manifest_version`, `stencil_version`, `package`, `dir` -- not
only the entries list.
"""

from __future__ import annotations

import json
import os

import pytest
import yaml
from jinja2 import UndefinedError

from stencil.generate import (
    MANIFEST_NAME,
    MANIFEST_VERSION,
    get_generated_files,
    package_contexts,
    package_entries,
    read_manifest,
)

from test_cli import run_cli

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
    excluded -- the no-drift guarantee package_entries exists to keep true."""
    pkg_dir = config["packages"][package_id].get("dir", package_id)
    prefix = f"{pkg_dir}/"
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
