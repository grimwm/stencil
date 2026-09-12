"""A broken config must fail the build instead of silently dropping a package.

stn-k73: `show_download_default` (and friends) raise ValueError on a bad
config value, and two call sites -- the `validate_config` loop that builds
`available`, and the `get_generated_files` loop that feeds `install` and
`clean` -- used to catch that ValueError and `continue`. So a package with a
quoted `show_download: "no"`, an invalid `package_type`, an unsafe path in
`docs`, or a dozen other mistakes just vanished: dropped from the managed
`.gitignore` section, from what `clean` removes, and from a `gen --all` that
still exited 0. Nothing said so.

This file is the unit tier for the fix: one aggregating, fail-closed
pre-flight that replaces both swallows. It iterates every package, collects
every problem instead of stopping at the first, and raises ONE ValueError
naming all of them -- or returns a context per package when there are none.
Brand validation (a missing `brand-alt`, a brand file that does not exist)
joins the same pre-flight rather than surfacing later as a raw traceback
half way through generation.

The aggregating pre-flight may not land under the exact name below --
`package_contexts` is this task's best guess, `brand_problem` is fixed by the
ticket. Imported in one place so a rename on the implementation side is a
one-line fix here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stencil.generate import brand_problem, get_generated_files, package_contexts

from test_cli import run_cli, write_config


def package(**overrides):
    """A minimal valid package, so every test overrides only what it breaks."""
    base = {"name": "Demo", "package_type": "none", "docs": ["README.md"]}
    base.update(overrides)
    return base


# --- aggregation and basics -------------------------------------------------


def test_a_broken_package_raises_and_names_it():
    """The ticket's whole complaint: a package_type mistake used to drop a
    package with no error at all. It must now raise, and the message has to
    say which package -- a config with a dozen of them otherwise sends the
    author hunting through all of them."""
    config = {"packages": {"broken": package(package_type=None)}}
    with pytest.raises(ValueError) as exc:
        package_contexts(config)
    assert "broken" in str(exc.value)


def test_two_broken_packages_are_both_named_in_one_message():
    """The aggregation guarantee the whole ticket sells, and the one thing
    here a reviewer cannot check by reading. A regression to first-error-wins
    reports whichever package happens to be iterated first and hides the
    other -- which is the swallow's own failure mode wearing a different
    coat, so this test has to name both or it is not testing the guarantee."""
    config = {
        "packages": {
            "first": package(package_type=None),
            "second": package(package_type="bogus"),
            "fine": package(),
        }
    }
    with pytest.raises(ValueError) as exc:
        package_contexts(config)
    message = str(exc.value)
    assert "first" in message, f"first broken package missing from: {message!r}"
    assert "second" in message, f"second broken package missing from: {message!r}"


def test_a_valid_config_returns_one_context_per_package_and_raises_nothing():
    config = {"packages": {"one": package(), "two": package()}}
    contexts = package_contexts(config)
    assert set(contexts) == {"one", "two"}
    assert contexts["one"]["package_id"] == "one"
    assert contexts["two"]["package_id"] == "two"


def test_a_config_level_typo_produces_one_line_not_one_per_package():
    """show_download's config-wide fallback is checked once per package (each
    one asks 'do I set my own value? no -- does the config?'), so a single
    top-level typo is hit once per package that shares it. Three packages
    here so a collector that appends one line per package, rather than
    deduplicating, is actually caught rather than passing by accident at a
    two-line boundary."""
    config = {
        "show_download": "no",
        "packages": {"one": package(), "two": package(), "three": package()},
    }
    with pytest.raises(ValueError) as exc:
        package_contexts(config)
    message = str(exc.value)
    assert message.count("show_download must be true or false") == 1, (
        f"the config-level complaint should appear once, not once per "
        f"package sharing it: {message!r}"
    )


def test_control_characters_in_a_package_id_do_not_reach_the_message_raw():
    """A package id is config text, and it has to reach the aggregated
    message because that message must NAME the broken package. Nothing needs
    to interpret an escape sequence embedded in one -- a package id carrying
    a literal ESC or CR has no legitimate reading, and must not be able to
    steer what the terminal does when the error prints."""
    config = {"packages": {"\x1b[1Ademo\r": "not-a-mapping"}}
    with pytest.raises(ValueError) as exc:
        package_contexts(config)
    message = str(exc.value)
    assert "\x1b" not in message, f"a raw ESC reached the message: {message!r}"
    assert "\r" not in message, f"a raw CR reached the message: {message!r}"


# --- every previously hidden error class surfaces ---------------------------
#
# None of these raises are new -- get_template_context has always thrown them.
# What is new is that they REACH the caller instead of being swallowed by
# `except ValueError: continue`. Each case below is a single-package config
# broken in one specific, previously-invisible way.

HIDDEN_ERROR_CASES = [
    pytest.param(
        {"show_download": "no"},
        "show_download must be true or false",
        id="quoted-show-download",
    ),
    pytest.param(
        {"package_type": None},
        "missing required 'package_type'",
        id="missing-package-type",
    ),
    pytest.param(
        {"package_type": "bogus"},
        "invalid package_type",
        id="invalid-package-type",
    ),
    pytest.param(
        {"package_folder": "htdocs"},
        "package_folder",
        id="package-folder-renamed",
    ),
    pytest.param(
        {"pre_build": [{"run": "make figs"}]},
        "needs 'outputs'",
        id="pre-build-missing-outputs",
    ),
    pytest.param(
        {
            "pre_build": [
                {"run": "a", "outputs": ["a.svg"], "name": "dup"},
                {"run": "b", "outputs": ["b.svg"], "name": "dup"},
            ]
        },
        "are named 'dup'",
        id="pre-build-duplicate-name",
    ),
    pytest.param(
        {"template_env": {"docs": ["x"]}},
        "derived context key: docs",
        id="template-env-clashes-with-a-derived-key",
    ),
    pytest.param(
        {"docs": ["a b.md"]},
        "contains whitespace",
        id="check-config-path-whitespace",
    ),
    pytest.param(
        {"docs": ["a;b.md"]},
        "shell or Make metacharacter",
        id="check-config-path-shell-metacharacter",
    ),
    pytest.param(
        {"docs": ["/etc/passwd"]},
        "is absolute",
        id="check-config-path-absolute",
    ),
    pytest.param(
        {"docs": ["../escape.md"]},
        "escapes the package directory",
        id="check-config-path-dotdot",
    ),
    pytest.param(
        {"docs": ["~/escape.md"]},
        "expands to a path outside the package",
        id="check-config-path-tilde",
    ),
]


@pytest.mark.parametrize("overrides, expected", HIDDEN_ERROR_CASES)
def test_each_hidden_error_class_now_surfaces(overrides, expected):
    config = {"packages": {"demo": package(**overrides)}}
    with pytest.raises(ValueError) as exc:
        package_contexts(config)
    message = str(exc.value)
    assert expected in message, f"expected {expected!r} in: {message!r}"
    assert "demo" in message, f"the broken package is not named: {message!r}"


# --- type-broken configs: AttributeError/TypeError today, not ValueError ----
#
# A ValueError-only collector lets these traceback straight past the
# aggregation -- the exact hole a bare `except ValueError` leaves open. The
# pre-flight has to catch these classes too and convert them.


def test_docs_as_an_integer_raises_valueerror_not_typeerror():
    """docs: 7 hits `for d in package.get('docs', [])`, which today raises
    TypeError -- 'int' object is not iterable -- with no ValueError guard
    anywhere near it."""
    config = {"packages": {"demo": package(docs=7)}}
    with pytest.raises(ValueError) as exc:
        package_contexts(config)
    assert "demo" in str(exc.value)


def test_a_non_string_package_name_raises_valueerror_not_attributeerror():
    """package_name: 42 on a doc package with package_sources set reaches
    `package_name.endswith('.pdf')`, which today raises AttributeError --
    'int' object has no attribute 'endswith'."""
    config = {
        "packages": {
            "demo": package(
                package_type="doc",
                package_sources=["a.md"],
                package_name=42,
            )
        }
    }
    with pytest.raises(ValueError) as exc:
        package_contexts(config)
    assert "demo" in str(exc.value)


def test_a_non_string_package_name_names_the_key_too():
    """The test above only asserts that 'demo' -- the package -- appears in
    the message. This pins that the KEY, package_name, is named too: a
    config with a dozen keys otherwise sends the author looking through all
    of them for which one was the problem.

    package_type "none" never CONSUMES package_name at all (unlike "doc"
    above, which reaches it via `.endswith('.pdf')`) -- used deliberately so
    this also pins that a non-string package_name is refused whenever the
    key is PRESENT, not only for the doc/zip types that happen to read it.
    """
    config = {"packages": {"demo": package(package_type="none", package_name=42)}}
    with pytest.raises(ValueError) as exc:
        package_contexts(config)
    message = str(exc.value)
    assert "demo" in message, f"the broken package is not named: {message!r}"
    assert "package_name" in message, f"the key is not named: {message!r}"


def test_a_non_string_output_dir_raises_valueerror_not_typeerror():
    """output_dir: 5 hits `Path(raw_output)` before the try/except around
    os.path.relpath even starts, so today's TypeError -- 'argument should be
    a str or os.PathLike object...' -- comes from a codepath with no
    ValueError guard at all."""
    config = {"packages": {"demo": package(output_dir=5)}}
    with pytest.raises(ValueError) as exc:
        package_contexts(config)
    assert "demo" in str(exc.value)


# --- stn-40a/stn-pe3: the CONFIG-level output_dir is unchecked too ----------
#
# The test above is about the PACKAGE-level key (`packages.demo.output_dir`).
# This is its config-level twin (the top-level `output_dir` that `_main`
# joins onto `config_dir` to compute `output_base`) -- named distinctly so
# neither shadows the other.


@pytest.mark.parametrize("value", [7, 0, False, True, [], {}, 1.5])
def test_a_non_string_top_level_output_dir_raises_valueerror_not_typeerror(value):
    """Before 0.39.0, `_main` computed
    `output_base = (config_dir / output_dir_raw).resolve()` with no shape
    check at all, and `package_contexts` raised nothing here -- the value
    passed straight through. 0, False and [] are FALSY non-strings, which a
    naive `if not value: return None` swallows into "output base is the
    config directory", the same silent mis-read stn-40a exists to close --
    which is why `check_output_dir` tests the type BEFORE falsiness.

    `True` is in the matrix for the opposite reason to `False`: it is the
    TRUTHY bool, the one a "falsiness first" reader assumes the falsiness
    branch never sees, so it is the value most likely to be let through by
    a later "simplification". `{}` covers the falsy non-list container and
    `1.5` the numeric that is not an int. Only `None` and `""` fall
    through to "the output base is the config directory", which is what a
    blank key has always meant.
    """
    config = {"output_dir": value, "packages": {"demo": package()}}
    with pytest.raises(ValueError, match="output_dir"):
        package_contexts(config)


@pytest.mark.parametrize(
    "args",
    [("gen", "--all"), ("clean", "--all"), ("install",)],
    ids=["gen", "clean", "install"],
)
def test_cli_reports_a_non_string_top_level_output_dir_without_a_traceback(
    tmp_path, args
):
    """stn-40a's other half. `_main` computes `output_base` before
    `package_contexts`'s pre-flight ever runs, so before 0.39.0 `gen` and
    `clean` tracebacked with a bare `TypeError` -- 'unsupported operand
    type(s) for /: PosixPath and int' -- while `install`, which returns
    above that line entirely, reported nothing at all and exited 0. All
    three are now an ordinary, non-zero, traceback-free error.
    """
    write_config(
        tmp_path,
        {
            "output_dir": 7,
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": package()},
        },
    )
    result = run_cli(*args, cwd=tmp_path)
    assert result.returncode != 0
    assert "Traceback" not in result.stderr


def test_a_list_valued_packages_key_raises_valueerror():
    """packages: as a YAML list instead of a mapping. Iterating it yields
    the package MAPPINGS rather than their ids, and get_template_context's
    `config.get("packages", {}).get(package_id)` then raises a raw
    AttributeError -- 'list' object has no attribute 'get'. There is no
    package id to name here; the point is that the pre-flight has to reject
    the shape rather than let this traceback."""
    config = {"packages": [{"name": "Demo"}]}
    with pytest.raises(ValueError) as exc:
        package_contexts(config)
    assert "packages" in str(exc.value)


def test_a_string_valued_package_raises_valueerror_naming_it():
    """A package whose entry is a bare string instead of a mapping hits
    `package.get(...)` immediately and raises AttributeError -- 'str' object
    has no attribute 'get'."""
    config = {"packages": {"demo": "not-a-mapping"}}
    with pytest.raises(ValueError) as exc:
        package_contexts(config)
    assert "demo" in str(exc.value)


# --- get_generated_files -----------------------------------------------------


def test_get_generated_files_raises_rather_than_returning_a_short_list():
    """Before this, a broken package among good ones silently vanished from
    the list get_generated_files returns -- the list that feeds both
    `install`'s .gitignore section and `clean`'s removal list. The dropped
    package's generated files were then neither ignored nor cleaned, and
    nothing said so. get_generated_files's own signature does not change --
    config_dir is not threaded through it."""
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"broken": package(package_type=None), "fine": package()},
    }
    with pytest.raises(ValueError) as exc:
        get_generated_files(config)
    assert "broken" in str(exc.value)


# --- brand -------------------------------------------------------------------


def test_brand_problem_flags_a_missing_alt_for_an_image_brand():
    problem = brand_problem({"brand": "logo.svg"}, {}, None)
    assert problem is not None
    assert "brand-alt" in problem


@pytest.mark.parametrize(
    "brand", ["Some University", "https://example.com/l.png"],
    ids=["plain-name", "remote-url"],
)
def test_brand_problem_is_none_for_a_name_or_a_remote_url(brand):
    """brand_image_path returns None for both -- there is no local file to
    check and no picture that needs alt text."""
    assert brand_problem({"brand": brand}, {}, None) is None


def test_brand_problem_skips_the_file_check_with_no_config_dir():
    """The existence check needs a filesystem root and is gen-only (decision
    d-96da97d8) -- this is what keeps `clean` usable as a recovery operation
    while a logo is missing. Same config, config_dir omitted: passes even
    though logo.svg exists nowhere."""
    assert brand_problem(
        {"brand": "logo.svg", "brand-alt": "SIU"}, {}, None
    ) is None


def test_brand_problem_reports_a_missing_file_only_when_config_dir_is_given(
    tmp_path,
):
    package_cfg = {"brand": "logo.svg", "brand-alt": "SIU"}
    problem = brand_problem(package_cfg, {}, tmp_path)
    assert problem is not None
    assert "not a file" in problem


def test_brand_problem_passes_with_both_alt_and_an_existing_file(tmp_path):
    (tmp_path / "logo.svg").write_text("<svg/>")
    problem = brand_problem(
        {"brand": "logo.svg", "brand-alt": "SIU"}, {}, tmp_path
    )
    assert problem is None


# --- brand, through the aggregating pre-flight ------------------------------


def test_package_contexts_reports_a_missing_brand_alt():
    config = {"packages": {"demo": package(brand="logo.svg")}}
    with pytest.raises(ValueError) as exc:
        package_contexts(config)
    message = str(exc.value)
    assert "brand-alt" in message
    assert "demo" in message


def test_package_contexts_brand_file_check_only_runs_with_config_dir(tmp_path):
    """Assert both halves: this is what keeps `clean` usable while a logo is
    missing, and it is only true if the SAME config passes with no
    config_dir and fails with one pointing somewhere the file is absent."""
    config = {
        "packages": {"demo": package(brand="logo.svg", **{"brand-alt": "SIU"})}
    }
    # No config_dir: passes, even though logo.svg exists nowhere.
    contexts = package_contexts(config)
    assert "demo" in contexts

    # config_dir supplied, and the file genuinely is not there: reported.
    with pytest.raises(ValueError) as exc:
        package_contexts(config, tmp_path)
    assert "not a file" in str(exc.value)


def test_package_contexts_passes_when_the_brand_file_exists(tmp_path):
    (tmp_path / "logo.svg").write_text("<svg/>")
    config = {
        "packages": {"demo": package(brand="logo.svg", **{"brand-alt": "SIU"})}
    }
    contexts = package_contexts(config, tmp_path)
    assert "demo" in contexts


def test_a_pageless_package_is_not_checked_against_a_configured_brand():
    """A page-less package (package_type: none, no docs, no slides) never
    renders anything a brand could appear on -- copy_brand_image itself is
    only ever called when has_pages is true. A config-level image brand with
    no brand-alt must not newly fail generation for such a package, or this
    change breaks configs that generate fine today. Regression guard for the
    has_pages gate."""
    config = {
        "brand": "logo.svg",
        "packages": {"demo": package(package_type="none", docs=[], slides=[])},
    }
    contexts = package_contexts(config)
    assert "demo" in contexts


@pytest.mark.parametrize(
    "brand", ["Some University", "https://example.com/l.png"],
    ids=["plain-name", "remote-url"],
)
def test_a_name_or_remote_brand_passes_the_pre_flight_with_no_alt(brand):
    config = {"brand": brand, "packages": {"demo": package()}}
    contexts = package_contexts(config)
    assert "demo" in contexts


def test_the_message_names_a_way_out_of_the_clean_deadlock():
    """`clean` becomes unavailable exactly when it is most wanted: the config
    has drifted and generated files are sitting on disk. Refusing is still
    right -- deleting from a config stencil cannot read is worse -- but the
    person on the other end needs to be told the way out in the same breath,
    or the first one to hit it reaches for a wider `rm -rf` than they meant.

    Asserted loosely on purpose: the wording is the implementer's to choose,
    the presence of an escape hatch is not."""
    config = {"packages": {"broken": package(package_type=None)}}
    with pytest.raises(ValueError) as exc:
        package_contexts(config)
    message = str(exc.value).lower()
    assert "by hand" in message or "manually" in message or "remove" in message, (
        "the message refuses to clean but never says how to recover: "
        f"{str(exc.value)!r}"
    )
    # stn-2x4.8: package_contexts(config) with no explicit `trailer` is the
    # call gen and install make, and must keep saying `clean` refuses too --
    # `clean` itself now asks for a DIFFERENT trailer (CLEAN_DEGRADED_TRAILER)
    # on its own degraded path, precisely because that sentence stops being
    # true there. Pinned here so a future edit to the default trailer cannot
    # silently change gen/install's wording without a test noticing.
    assert "refuses for the same reason this did" in message, (
        "the gen/install (default) trailer changed -- package_contexts(config) "
        f"with no `trailer` argument must keep today's wording: {str(exc.value)!r}"
    )


def test_a_malformed_package_is_not_blamed_on_its_innocent_siblings():
    """get_template_context does NOT read only the package it is asked about:
    declared_template_env_keys walks `packages` in full and calls .get() on
    every value. So ONE package whose value is a string makes the context
    build raise for EVERY package -- and a pre-flight that interleaved the
    shape check with the context build reported that AttributeError against
    whichever innocent package happened to be building when it hit.

    A report naming the wrong package is worse than the silence this whole
    change replaced: the silence at least did not send someone to edit a file
    that was already correct. Found by running the thing rather than by a
    test, which is why there is now a test."""
    config = {
        "packages": {
            "innocent": package(),
            "malformed": "just a string",
        }
    }
    with pytest.raises(ValueError) as exc:
        package_contexts(config)
    message = str(exc.value)

    assert "malformed" in message, f"the real culprit is not named: {message!r}"
    assert "innocent" not in message, (
        f"a valid package was blamed for its sibling's shape: {message!r}"
    )
    assert "AttributeError" not in message, (
        "the raw exception class leaked instead of the shape being caught "
        f"up front: {message!r}"
    )


# --- findings from the adversarial review of the implementation -------------


def test_a_config_level_brand_problem_is_named_once_against_the_config():
    """brand_of falls back to the config, so ONE config-level brand mistake is
    inherited by every package with pages. Prefixing each with its own package
    id produced N distinct strings the dedup could not collapse: N bullets for
    one typo, none of them naming the place the typo actually is."""
    config = {
        "brand": "logo.svg",  # an image, and no brand-alt anywhere
        "packages": {
            "one": package(),
            "two": package(),
            "three": package(),
        },
    }
    with pytest.raises(ValueError) as exc:
        package_contexts(config)
    message = str(exc.value)

    bullets = [line for line in message.splitlines() if line.startswith("- ")]
    assert len(bullets) == 1, (
        f"one config-level brand mistake produced {len(bullets)} bullets, "
        f"one per inheriting package: {message!r}"
    )
    assert bullets[0].startswith("- config:"), (
        f"the message blames a package for a config-level key: {bullets[0]!r}"
    )
    assert "brand-alt" in bullets[0]


def test_a_non_string_brand_is_reported_as_a_config_error_not_an_attributeerror():
    """`brand: 2024` unquoted reaches brand_image_path's .startswith and used
    to raise AttributeError from inside the per-package loop -- reported
    against every package inheriting it, three innocent files named and the
    word `brand` nowhere in the message. Worse than a traceback, which at
    least had brand_image_path in its top frame."""
    config = {
        "brand": 2024,
        "brand-alt": "Some Institution",
        "packages": {"one": package(), "two": package(), "three": package()},
    }
    with pytest.raises(ValueError) as exc:
        package_contexts(config)
    message = str(exc.value)

    assert "brand must be a string" in message, message
    assert "AttributeError" not in message, (
        f"the raw exception leaked instead of being typed at the source: {message!r}"
    )
    assert message.count("brand must be a string") == 1, (
        f"one config-level typo produced a bullet per package: {message!r}"
    )


def test_a_newline_in_a_config_value_cannot_forge_a_bullet():
    """The guarantee _safe defends is a BULLET LIST, and a newline is the one
    character that can add lines to it. `package_type` is interpolated bare
    into its error, so an ordinary YAML config -- no exotic key needed -- can
    put a convincing extra finding into the report, or a reassuring one."""
    config = {
        "packages": {
            "genuine": package(show_download="no"),
            "sneaky": package(
                package_type="none\n\n- everything is fine, ignore the above"
            ),
        }
    }
    with pytest.raises(ValueError) as exc:
        package_contexts(config)
    message = str(exc.value)

    bullets = [line for line in message.splitlines() if line.startswith("- ")]
    assert len(bullets) == 2, (
        f"expected exactly two bullets, one per real problem, got "
        f"{len(bullets)}: {message!r}"
    )
    assert "\n\n- everything is fine" not in message, (
        f"a config value forged a line break into the report: {message!r}"
    )


def test_a_missing_packages_key_is_itself_a_config_problem():
    """`package:` for `packages:` is a one-letter typo. It used to leave
    install writing an EMPTY managed section over a populated one, printing
    "Updated", and exiting 0 -- this ticket's exact harm, on this ticket's own
    command, because install returns before main's own `packages` guard.

    An explicitly empty `packages: {}` is a different statement -- "I have
    none yet" -- and is checked separately below."""
    with pytest.raises(ValueError) as exc:
        package_contexts({"templates": [{"src": "Makefile.j2"}]})
    assert "packages" in str(exc.value)


def test_an_explicitly_empty_packages_mapping_is_allowed():
    """Saying "none yet" out loud is not the same as forgetting the key."""
    assert package_contexts({"packages": {}}) == {}


# --- stn-hwo: a damaged install is not a broken config ----------------------


def test_a_corrupt_vendored_lockfile_is_not_collected_as_a_config_problem(
    tmp_path, monkeypatch
):
    """The wrong advice for the right failure.

    `pipeline.read_lockfile` raised ValueError when a vendored lockfile does
    not end in exactly one newline; `get_template_context` calls it, and the
    loop above collects every ValueError as a CONFIG problem. So a damaged
    stencil INSTALL was reported under a heading saying the config has a
    problem, with a trailer telling the reader to fix their config and delete
    a directory by hand -- for a fault whose real fix is
    `python3 scripts/vendor_npm_locks.py`, in a file a consumer may not even
    be able to edit.

    The collection site's own comment described this gap and named the fix:
    read_lockfile needs a type of its own. It has one now, and the type is
    NOT a ValueError -- which is the whole point, since ValueError is the
    config channel.
    """
    import shutil

    from stencil import pipeline

    monkeypatch.setattr(pipeline, "ASSETS_DIR", tmp_path)
    for name in (pipeline.BROWSER_LOCKFILE, pipeline.FORMAT_LOCKFILE):
        shutil.copy(
            Path(pipeline.__file__).parent / "assets" / name, tmp_path / name
        )
    corrupt = tmp_path / pipeline.BROWSER_LOCKFILE
    corrupt.write_text(corrupt.read_text() + "\n")

    cfg = {"packages": {"demo": package(docs=["README.md"])}}

    with pytest.raises(pipeline.VendoredAssetError, match="vendor_npm_locks"):
        package_contexts(cfg)


def test_a_missing_vendored_lockfile_uses_the_same_type(tmp_path, monkeypatch):
    """Both halves of read_lockfile's contract are install faults, so both
    raise the install type. The missing case already escaped the config
    channel -- as a FileNotFoundError traceback -- which was right about the
    classification and wrong about the presentation."""
    from stencil import pipeline

    monkeypatch.setattr(pipeline, "ASSETS_DIR", tmp_path)
    with pytest.raises(pipeline.VendoredAssetError, match="vendor_npm_locks"):
        pipeline.read_lockfile(pipeline.BROWSER_LOCKFILE)


# --- stn-oty: bounded, as well as escaped -----------------------------------


def test_a_huge_config_scalar_is_truncated_in_the_report():
    """The half of stn-oty that was still live.

    `_safe` already neutralises the dangerous half -- measured: an ANSI CSI
    sequence in a package id comes out as literal `\\x1b`, and an embedded
    newline cannot forge an extra bullet in the aggregated list. What it did
    not do is bound the length, and yaml.safe_load will hand back a scalar of
    any size, so one config value could bury the report that names it.

    The cap is per problem line, and generous: a real message runs to a
    couple of hundred characters, so nothing ordinary is touched.
    """
    huge = "X" * 5000
    with pytest.raises(ValueError) as caught:
        package_contexts({"packages": {"demo": package(package_type=huge)}})

    message = str(caught.value)
    assert len(message) < 1000, (
        f"a 5000-character config value produced a {len(message)}-character "
        f"report"
    )
    import re

    reported = re.search(r"truncated, (\d+) characters", message)
    assert reported, (
        f"the report should say how much it dropped:\n{message}"
    )
    assert int(reported.group(1)) >= 5000, (
        "the reported length should cover the value that caused it"
    )


def test_an_ordinary_message_is_not_truncated():
    """The cap must not start eating real explanations."""
    with pytest.raises(ValueError) as caught:
        package_contexts({"packages": {"demo": package(package_type="bogus")}})
    message = str(caught.value)
    assert "has invalid package_type: bogus" in message
    assert "truncated" not in message


# --- stn-dl3r: a malformed `templates:` entry is one named refusal, not a
# traceback, across gen, install and clean -----------------------------------
#
# package_contexts' own `templates` loop used to `continue` past a
# non-mapping entry, and skip the shape check entirely for a non-list value,
# so `gen` (validate_config's `when:` loop) and `install` (when_holds, via
# get_generated_files -> package_entries) each tracebacked with a bare
# AttributeError/KeyError -- while `clean` alone named it, via
# `_config_template_defs`. Both now share ONE shape check
# (`_checked_template_defs`), so all three commands read the same message.

MALFORMED_TEMPLATES_CASES = [
    pytest.param(["Makefile.j2"], id="entries-not-mappings"),
    pytest.param([{"dest": "out.txt"}], id="mapping-missing-src"),
    pytest.param([{"src": 2024}], id="mapping-non-string-src"),
    pytest.param({}, id="templates-not-a-list"),
]


@pytest.mark.parametrize("templates", MALFORMED_TEMPLATES_CASES)
def test_a_malformed_templates_entry_is_named_through_package_contexts(templates):
    config = {"templates": templates, "packages": {"demo": package()}}
    with pytest.raises(ValueError) as exc:
        package_contexts(config)
    message = str(exc.value)
    assert "templates" in message, f"the key is not named: {message!r}"
    assert "demo" in message, f"the affected package is not named: {message!r}"
    for class_name in ("AttributeError", "KeyError", "TypeError"):
        assert class_name not in message, (
            f"a Python class name leaked into a config message: {message!r}"
        )


@pytest.mark.parametrize("templates", MALFORMED_TEMPLATES_CASES)
@pytest.mark.parametrize(
    "args", [("gen", "--all"), ("install",)], ids=["gen", "install"]
)
def test_a_malformed_templates_entry_fails_closed_through_the_cli(
    tmp_path, templates, args
):
    write_config(
        tmp_path,
        {
            "templates": templates,
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        },
    )
    result = run_cli(*args, cwd=tmp_path)

    assert result.returncode != 0, (
        f"{' '.join(args)} exited 0 on a malformed templates:"
    )
    assert "Traceback" not in result.stderr, result.stderr
    assert "templates" in result.stderr, result.stderr
    for class_name in ("AttributeError", "KeyError", "TypeError"):
        assert class_name not in result.stderr, (
            f"a Python class name reached the terminal: {result.stderr!r}"
        )
    assert not (tmp_path / "demo").exists(), (
        "nothing should have been written for a config this broken"
    )
    if args[0] == "install":
        assert not (tmp_path / ".gitignore").exists(), (
            "install must not touch the managed .gitignore section on a "
            "config it refused"
        )


def test_gen_and_install_report_the_same_message_for_a_non_list_templates(tmp_path):
    """stn-dl3r's thesis, pinned directly. Before this change, gen's
    emptiness check ran ABOVE validate_config, and `templates: {}` is
    falsy -- so gen printed 'No templates defined in config' while install
    (which has no such check) went straight to package_contexts, which did
    not check the shape at all and let a non-list `templates:` straight
    through. Moving gen's emptiness check below validate_config, and giving
    package_contexts the same shape check `clean` already had, makes gen and
    install agree on the same config."""
    write_config(
        tmp_path,
        {
            "templates": {},
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        },
    )
    gen_result = run_cli("gen", "--all", cwd=tmp_path)
    install_result = run_cli("install", cwd=tmp_path)

    assert gen_result.returncode != 0
    assert install_result.returncode != 0
    assert gen_result.stderr == install_result.stderr, (
        "gen and install disagree about the same malformed config:\n"
        f"gen:     {gen_result.stderr!r}\n"
        f"install: {install_result.stderr!r}"
    )


def test_an_absent_templates_key_is_not_a_config_problem():
    """An ABSENT `templates:` key is a different statement from a malformed
    one -- `config.get('templates')` is None either way, so the check has to
    be keyed on whether the key is PRESENT, not on the value alone."""
    config = {"packages": {"demo": package()}}
    contexts = package_contexts(config)
    assert "demo" in contexts


def test_an_explicit_null_templates_key_is_still_a_named_problem():
    """`templates:` with no value is a YAML null: present, but not a list.
    `_config_template_defs` already refused this shape, so this keeps the
    two consistent rather than letting a null slip through as though the
    key were absent."""
    config = {"templates": None, "packages": {"demo": package()}}
    with pytest.raises(ValueError) as exc:
        package_contexts(config)
    assert "templates" in str(exc.value)


def test_an_empty_templates_list_is_still_legal():
    """stn-jez's own reproduction config is `templates: []`; refusing an
    empty list would turn that config red for a reason that has nothing to
    do with what it is testing."""
    config = {"templates": [], "packages": {"demo": package()}}
    contexts = package_contexts(config)
    assert "demo" in contexts


def test_an_absent_templates_key_still_works_for_install_and_clean(tmp_path):
    """The regression the new non-list check could cause: `install` and
    `clean` have always treated a missing `templates:` key as 'no
    templates', via `config.get('templates', [])`, and must keep doing so."""
    write_config(
        tmp_path, {"packages": {"demo": {"name": "Demo", "package_type": "none"}}}
    )
    for args in [("install",), ("clean", "--all")]:
        result = run_cli(*args, cwd=tmp_path)
        assert result.returncode == 0, f"{' '.join(args)}: {result.stderr}"


def test_a_widen_check_that_cannot_derive_the_authorised_set_fails_closed(
    tmp_path,
):
    """_clean_one_directory's own defense in depth (stn-dl3r). When
    `_config_derived_entries` cannot compute what a directory's packages
    authorise, trusting the manifest anyway used to be the fallback -- the
    one option that is never right on its own, since the widen check exists
    precisely because the manifest is an unvalidated file on disk.

    Reached directly here, bypassing `clean_generated`'s own
    `package_contexts` probe (which now catches a malformed top-level
    `templates:` before this branch is ever reached that way -- see the
    tests above): the group handed to `_clean_one_directory` includes a
    package id the config does not have at all, so
    `_config_derived_entries` cannot derive what that directory's packages
    authorise, for a reason that has nothing to do with `templates:`.
    """
    from stencil.generate import MANIFEST_NAME, _clean_one_directory, write_manifest

    pkg_dir = tmp_path / "demo"
    pkg_dir.mkdir()
    (pkg_dir / "Makefile").write_text("all:\n\techo hi\n")
    write_manifest(pkg_dir, "demo", "demo", ["Makefile"])

    config = {"packages": {"demo": package()}}
    problems: list[str] = []
    _clean_one_directory(
        tmp_path,
        pkg_dir,
        ["demo"],
        ["demo", "phantom"],
        config,
        True,
        False,
        problems,
    )

    assert (pkg_dir / "Makefile").exists(), "nothing should have been removed"
    assert (pkg_dir / MANIFEST_NAME).exists(), "the manifest should survive too"
    assert problems, "the failure to derive the authorised set must be reported"
    message = "\n".join(problems)
    assert "phantom" in message, f"the undeliverable package is not named: {message!r}"
    assert "nothing was removed" in message.lower(), message
