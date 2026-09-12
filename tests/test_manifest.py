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

import copy
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


def test_clean_removes_a_dropped_docs_entrys_build_artifacts_from_the_manifest(
    generate_package, tmp_path
):
    """PRECEDENCE, the headline of the gen/clean split. The manifest names a
    document's build-artifact glob patterns (Guide*.html, Guide*.pdf, ...)
    for every doc that existed AT GEN TIME. Editing `docs:` afterward to
    drop one must not make clean forget that document's artifacts -- a
    config-derived removal list would no longer name them at all, and they
    would be left on disk forever. A manifest-driven clean still removes
    them, because the manifest records what gen actually did, not what the
    config says today.
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

    assert result.returncode == 0, result.stderr
    for name in ("Guide.html", "Guide.pdf", "Extra.html", "Extra.pdf"):
        assert not (generated / name).exists(), (
            f"{name} survived clean -- a config-derived removal list would "
            "have dropped Extra's artifacts the moment `Extra.md` left docs:"
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
    assert not (generated / "Makefile").exists(), (
        "the valid entries should still be removed despite the one refusal"
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
