"""Every configured path is a filename, and stays inside the package.

`check_config_path` already encodes what a configured path may look like --
no shell metacharacter, no whitespace, no `~`, not absolute, no `..` -- and
stn-k73 put it in front of `docs`, `slides`, `package_sources` and
`pre_build`. Four other paths never went through it, and each one is a
different way out of the package:

- `dir` prefixes EVERY entry `get_generated_files` returns, which is every
  line of the managed `.gitignore` section and every path `clean` deletes
  (stn-vhm);
- a template's `dest` is joined onto the output directory verbatim, so it
  writes outside the package on `gen` and REMOVES that file on `clean`
  (stn-c25);
- `brand` is resolved and handed to `shutil.copyfile`, which follows a
  symlink -- so a `logo.png` pointing at any readable file copies that
  file's CONTENT into a folder AGENTS.md says is routinely handed to a
  student (stn-ttg); and
- the copied logo is named from the RESOLVED path while
  `get_generated_files` names it from the config string, so a symlinked
  brand lands under a name `clean` cannot see and git happily tracks
  (stn-8wt).

NOT A PRIVILEGE BOUNDARY, and worth repeating from check_config_path's own
comment: `pre_build.run` is arbitrary execution by design, so whoever writes
.config.yaml can already run anything. What these close is a filename quietly
doing something other than naming a file -- and, for `brand`, a read of a
file nobody asked to publish reaching a distributed artifact. `stencil gen
--dry-run` over a pull request's config executes nothing today, which is the
case that makes the brand one worth more than its severity suggests.
"""

from __future__ import annotations

import json

import pytest

from stencil import __version__ as stencil_version
from stencil.generate import (
    MANIFEST_NAME,
    MANIFEST_VERSION,
    check_config_path,
    get_generated_files,
    package_contexts,
)

from test_cli import run_cli, write_config


def package(**overrides):
    base = {"name": "Demo", "package_type": "none", "docs": ["README.md"]}
    base.update(overrides)
    return base


def config(**overrides):
    return {"packages": {"demo": package(**overrides)}}


# --- stn-isr: the character class itself -----------------------------------
#
# ESC is the one with a demonstrable effect -- it is how a filename colours or
# rewrites the terminal line an error message prints it on -- but the class is
# the point rather than any one member of it.

# Not \n or \t: those already had messages that say more than "control
# character" does -- \n is refused as a Make metacharacter and \t as
# whitespace that Make would split a recipe word on -- and this check runs
# after both so they keep them. \r is whitespace by the same rule.
C0_CONTROLS = ["\x00", "\x07", "\x1b", "\x08", "\x7f"]


@pytest.mark.parametrize("control", C0_CONTROLS)
def test_a_control_character_is_not_a_filename(control):
    """`_UNSAFE_IN_PATH` listed `\\n` and stopped there.

    See the list above for why `\\n`, `\\t` and `\\r` are not in it.

    The others are the same class: harmless to /bin/sh, and none of them is
    anything a person meant to type into a path. ESC additionally lets a
    filename repaint the stderr line that reports it.
    """
    with pytest.raises(ValueError, match="control character"):
        check_config_path("demo", "docs", f"read{control}me.md")


def test_a_plain_filename_still_passes():
    """The guard above must not start rejecting ordinary names."""
    assert check_config_path("demo", "docs", "README.md") == "README.md"


# --- stn-vhm: dir prefixes everything clean deletes -------------------------


@pytest.mark.parametrize(
    "value", ["../..", "../escape", "/tmp/escape", "~/escape", "a b"]
)
def test_a_package_dir_that_escapes_is_refused(value):
    """`dir` is not decoration: `get_generated_files` prefixes every entry
    with it, and `clean_generated` resolves each of those against the output
    base and deletes it. `dir: ../..` deletes outside the output base."""
    with pytest.raises(ValueError, match="dir"):
        package_contexts(config(dir=value))


def test_an_ordinary_package_dir_still_works():
    contexts = package_contexts(config(dir="handout-01"))
    assert contexts["demo"]["package_dir"] == "handout-01"


# --- stn-c25: a template dest is joined on verbatim -------------------------


@pytest.mark.parametrize(
    "dest", ["../../shared/Makefile", "/etc/passwd", "a/../../b"]
)
def test_a_template_dest_that_escapes_is_refused(dest):
    """`output_dir / dest` writes outside the package on gen, and
    `get_generated_files` names the same path so `clean` REMOVES it. The
    escape is symmetric, which is what makes it worth refusing rather than
    containing on one side only."""
    cfg = {
        "templates": [{"src": "Makefile.j2", "dest": dest}],
        "packages": {"demo": package()},
    }
    with pytest.raises(ValueError, match="dest"):
        package_contexts(cfg)


def test_a_nested_dest_is_still_allowed():
    """`.vscode/settings.json` is in the config's own documentation."""
    cfg = {
        "templates": [{"src": "Makefile.j2", "dest": ".vscode/settings.json"}],
        "packages": {"demo": package()},
    }
    package_contexts(cfg)
    assert "demo/.vscode/settings.json" in get_generated_files(cfg)


# --- stn-ttg: brand is resolved and copied ----------------------------------


@pytest.mark.parametrize(
    "brand",
    [
        "../../../../etc/passwd.png",
        "/var/root/secret.png",
        "file:///etc/hosts.svg",
        "file://../../secret.png",
    ],
)
def test_a_brand_path_that_escapes_is_refused(brand):
    """Measured in the ticket: `(config_dir / relative).resolve()` with an
    absolute `relative` yields the absolute path, and `file://` is stripped
    before any of this, so the scheme is not a defence."""
    with pytest.raises(ValueError, match="brand"):
        package_contexts(config(brand=brand, **{"brand-alt": "Logo"}))


def test_an_ordinary_brand_image_still_passes():
    """`config_brand` is the BASENAME: the image is copied next to the page,
    so what the template references is the copied file's name, not the path
    the config pointed at. That is also the divergence stn-8wt is about."""
    cfg = config(brand="img/logo.svg", **{"brand-alt": "Logo"})
    contexts = package_contexts(cfg)
    assert contexts["demo"]["config_brand"] == "logo.svg"


def test_a_brand_that_is_a_name_is_not_a_path():
    """`Southern Illinois University` has spaces and is not a filename;
    check_config_path would reject it, and it is never opened."""
    contexts = package_contexts(config(brand="Southern Illinois University"))
    assert contexts["demo"]["config_brand"] == "Southern Illinois University"


# --- stn-8wt: one file, two spellings of its name ---------------------------


def test_a_symlinked_brand_is_copied_under_the_name_clean_looks_for(tmp_path):
    """Reproduced end to end before it was fixed.

    ``copy_brand_image`` named the destination from ``(config_dir /
    relative).resolve().name`` and ``.resolve()`` follows a symlink, while
    ``get_generated_files`` names the same file from the raw config string.
    Two spellings of one rule, and they diverged on exactly the arrangement a
    course repository uses -- a stable ``logo.svg`` pointing at a dated asset:

        ln -s img/siu-logo-2024.svg logo.svg
        stencil gen --all   ->  out/demo/siu-logo-2024.svg
        get_generated_files ->  ['demo/logo.svg']
        stencil clean --all ->  siu-logo-2024.svg SURVIVES

    So the copied logo was outside the managed .gitignore section and outside
    what clean removes: git tracked a generated file, which is the failure
    stn-k73 exists to prevent, arriving by a different route.

    The destination is now the name the CONFIG gives, which is the same string
    both other call sites already used.
    """
    import os

    from stencil.generate import copy_brand_image

    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "siu-logo-2024.svg").write_text("<svg/>")
    os.symlink("img/siu-logo-2024.svg", tmp_path / "logo.svg")

    cfg = config(brand="file://logo.svg", **{"brand-alt": "Logo"})
    output = tmp_path / "out" / "demo"
    output.mkdir(parents=True)

    copy_brand_image(
        cfg,
        cfg["packages"]["demo"],
        config_dir=tmp_path,
        output_dir=output,
    )

    written = {p.name for p in output.iterdir()}
    listed = {
        entry.removeprefix("demo/")
        for entry in get_generated_files(cfg)
        if entry.endswith(".svg")
    }
    assert written == listed, (
        f"gen wrote {written}, clean and .gitignore look for {listed}; a file "
        f"in the difference survives `clean` and is tracked by git"
    )
    assert (output / "logo.svg").read_text() == "<svg/>", (
        "the symlink's target content should still be copied -- only the "
        "NAME comes from the config"
    )


# --- stn-ttg, the half a string check cannot see: the resolved target ------


def test_a_permitted_brand_name_that_links_outside_the_config_dir_is_refused(
    tmp_path,
):
    """`logo.png` passes every lexical check and is still a symlink.

    check_config_path refuses `..` and an absolute value, so the only way the
    file stencil OPENS can be outside the config's directory is a link -- and
    a link is exactly what turned a permitted-looking name into a read of any
    file the user can open. The pre-flight contains the resolved path, so it
    is reported with every other config problem before anything is written,
    and copy_brand_image refuses the same way rather than trusting that the
    pre-flight ran.
    """
    import os

    from stencil.generate import copy_brand_image

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.png").write_bytes(b"not a logo")
    config_dir = tmp_path / "course"
    config_dir.mkdir()
    os.symlink(outside / "secret.png", config_dir / "logo.png")

    cfg = config(brand="file://logo.png", **{"brand-alt": "Logo"})
    with pytest.raises(ValueError, match="outside"):
        package_contexts(cfg, config_dir)

    output = tmp_path / "out" / "demo"
    output.mkdir(parents=True)
    with pytest.raises(ValueError, match="outside"):
        copy_brand_image(
            cfg,
            cfg["packages"]["demo"],
            config_dir=config_dir,
            output_dir=output,
        )
    assert list(output.iterdir()) == [], "nothing may be copied on refusal"


def test_a_brand_link_that_stays_inside_the_config_dir_is_fine(tmp_path):
    """The stn-8wt arrangement -- a stable name pointing at a dated asset in
    the same tree -- is the case containment must keep working."""
    import os

    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "logo-2024.png").write_bytes(b"logo")
    os.symlink("img/logo-2024.png", tmp_path / "logo.png")
    cfg = config(brand="file://logo.png", **{"brand-alt": "Logo"})
    assert package_contexts(cfg, tmp_path)["demo"]["config_brand"] == "logo.png"


# --- stn-c25, the shape check before the path check --------------------------


def test_a_non_string_dest_is_a_config_problem_not_a_typeerror():
    """check_config_path str()s its argument, so `dest: 2024` passed it and
    reached `output_dir / 2024` in render_templates as a TypeError, after
    the templates before it had already been written."""
    cfg = {
        "templates": [{"src": "Makefile.j2", "dest": 2024}],
        "packages": {"demo": package()},
    }
    with pytest.raises(ValueError, match="dest 2024 is int, not a string"):
        package_contexts(cfg)


# --- stn-pe3: output_dir is the third member of the set dir (stn-vhm) and ---
# --- dest (stn-c25) belong to, and the only one still exempt from every  ---
# --- path check -------------------------------------------------------------
#
# `_main` computes `output_base = (config_dir / output_dir_raw).resolve()`
# and nothing requires the result to stay under `config_dir`. Every path
# `clean` unlinks, and every line of the managed `.gitignore` section, is
# relative to it -- so an escaping top-level `output_dir` is a way OUT of
# the config directory for every one of those, string-clean or not.
#
# NOTE: `config()` above builds `{"packages": {"demo": package(**overrides)}}`
# -- its overrides land on the PACKAGE, not the top level -- so these tests
# build the config dict directly rather than reusing it.


def test_a_top_level_output_dir_that_escapes_is_refused():
    """Reproduced verbatim against 0.38.0: `output_dir: ../victim-base` plus
    `clean --all` removes a file at `../victim-base/demo/Makefile`, rc=0.
    See the CLI test below for the end-to-end version of this same value."""
    cfg = {"output_dir": "../victim", "packages": {"demo": package()}}
    with pytest.raises(ValueError, match="output_dir"):
        package_contexts(cfg)


def test_an_absolute_top_level_output_dir_is_refused():
    cfg = {"output_dir": "/tmp/escape", "packages": {"demo": package()}}
    with pytest.raises(ValueError, match="output_dir"):
        package_contexts(cfg)


@pytest.mark.parametrize("value", ["out", ".", ""])
def test_a_valid_top_level_output_dir_still_passes(value):
    """The regression guard on refusing too much: `out` is ordinary, `.` is
    the value STENCIL.md documents (`output_dir: . # Where to generate
    packages`), and `""` is what an empty-but-present key means today. None
    of these may start failing gen, clean or install."""
    cfg = {"output_dir": value, "packages": {"demo": package()}}
    contexts = package_contexts(cfg)
    assert "demo" in contexts


def test_a_config_with_no_top_level_output_dir_still_passes():
    """Most configs never set this key at all (checked across every
    consumer config on this machine before the epic's plan was written)."""
    cfg = {"packages": {"demo": package()}}
    contexts = package_contexts(cfg)
    assert "demo" in contexts


def test_a_symlinked_top_level_output_dir_is_refused(tmp_path):
    """The half a string check cannot see: `output_dir: out` is a perfectly
    ordinary value, and `out` is itself a symlink pointing outside the
    config directory.

    Driven through the CLI rather than through `checked_output_base`:
    that function does not exist until stn-sl2.3, so importing it here
    would raise ImportError instead of exercising today's actual (wrong)
    behaviour.

    Reproduced verbatim per the ticket: `ln -s <outside> cfg/out`,
    `output_dir: out`, a victim file at `<outside>/demo/Makefile`, then
    `clean --all`. TODAY: rc=0 and the victim file is gone.
    """
    import os

    outside = tmp_path / "outside"
    (outside / "demo").mkdir(parents=True)
    victim = outside / "demo" / "Makefile"
    victim.write_text("precious")

    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    os.symlink(outside, config_dir / "out")

    write_config(
        config_dir,
        {
            "output_dir": "out",
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": {"package_type": "none"}},
        },
    )

    result = run_cli("clean", "--all", cwd=config_dir)

    assert victim.exists(), (
        "a file outside the config directory was removed via a symlinked "
        f"top-level output_dir; rc={result.returncode}, "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    assert result.returncode != 0
    assert "Traceback" not in result.stderr


def test_cli_clean_all_leaves_a_file_outside_the_config_directory_alone(
    tmp_path,
):
    """The reproduction stn-pe3 was filed with, run through the CLI rather
    than through `package_contexts` -- which never sees `output_base` at
    all, only the config. TODAY: rc=0 and
    'Removed .../victim-base/demo/Makefile'. That last assertion, the file
    surviving, is the ticket -- not the exit code.
    """
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    victim_base = tmp_path / "victim-base"
    (victim_base / "demo").mkdir(parents=True)
    victim = victim_base / "demo" / "Makefile"
    victim.write_text("precious")

    write_config(
        config_dir,
        {
            "output_dir": "../victim-base",
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": package()},
        },
    )

    result = run_cli("clean", "--all", cwd=config_dir)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert victim.exists(), (
        "a file outside the config directory was removed by `clean --all`: "
        f"rc={result.returncode}, stdout={result.stdout!r}"
    )


def test_output_base_containment_runs_even_when_the_config_is_degraded(
    tmp_path,
):
    """THE ORDERING TEST (M7) -- the one the whole fix rests on.

    `clean`'s degraded path lets a package with its OWN manifest clean
    anyway, precisely so a broken config does not strand files nobody can
    remove (see CLEAN_DEGRADED_TRAILER's docstring). That is right for a
    package's own manifest, and wrong for `output_base` itself: an escaping
    top-level `output_dir` must be refused before ANY package -- manifest
    or not -- gets a chance to clean, degraded or not.

    This pins that `output_base` is computed (and refused, once the fix
    lands) ABOVE `clean`'s degraded pre-flight. Move that computation BELOW
    the pre-flight later -- stn-40a's OTHER suggested fix, quoted in this
    epic's source -- and this is the only test that would notice: the
    escaping output_dir would be re-enabled every time the rest of the
    config also happens to be broken.

    Setup: a config whose `demo` package is fine and already has its own
    on-disk manifest at the escaped location, and whose `broken` package
    fails `package_contexts` (whitespace in `docs`) -- so the CLI's
    pre-flight is degraded, `config_readable` is False, and yet `demo`'s
    directory carries a manifest naming it. TODAY, that manifest is enough
    to authorize a delete outside the config directory anyway.
    """
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    victim_base = tmp_path / "victim-base"
    demo_dir = victim_base / "demo"
    demo_dir.mkdir(parents=True)
    victim = demo_dir / "Makefile"
    victim.write_text("precious")

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "stencil_version": stencil_version,
        "package": "demo",
        "dir": "demo",
        "entries": ["Makefile"],
    }
    (demo_dir / MANIFEST_NAME).write_text(json.dumps(manifest))

    write_config(
        config_dir,
        {
            "output_dir": "../victim-base",
            "templates": [{"src": "Makefile.j2"}],
            "packages": {
                "demo": package(),
                "broken": package(docs=["a b.md"]),  # whitespace: unparseable
            },
        },
    )

    result = run_cli("clean", "--all", cwd=config_dir)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert victim.exists(), (
        "a package's OWN manifest let clean delete a file outside the "
        "config directory, even though the rest of the config was broken "
        f"and a degraded warning was printed: rc={result.returncode}, "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    assert "Removed" not in result.stdout, (
        "clean printed a removal despite the degraded warning: "
        f"{result.stdout!r}"
    )


# --- stn-vhr: gen writes through a symlinked package directory -------------
#
# stn-7t9 (via _validated_package_dirs) and stn-pe3/checked_output_base above
# both contain paths CLEAN unlinks or the top-level output_dir. Neither
# touches `generate_package`, which computes `output_dir = output_base /
# context["package_dir"]` and writes straight into it with NO containment
# check at all. If `package_dir` is a symlink pointing out of the tree,
# every rendered template lands at the target -- gen's own twin of stn-7t9,
# and NOT closed by fixing clean.
#
# Reproduced verbatim per the ticket:
#
#     mkdir -p out outside; ln -s $PWD/outside out/demo
#     .config.yaml: output_dir: out / templates: [{src: Makefile.j2}] /
#     packages: {demo: {name: Demo, package_type: none}}
#     $ stencil gen demo  ->  rc=0
#     Makefile and format-package-lock.json land in ./outside
#     and the success message names ./out/demo -- the path INSIDE the tree


def test_cli_gen_through_a_symlinked_package_dir_refuses_and_writes_nothing(
    tmp_path,
):
    """'Nothing was written' is the assertion that matters -- the exit code
    alone would pass a version that writes every template and THEN fails."""
    import os

    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "out").mkdir()
    outside = config_dir / "outside"
    outside.mkdir()
    os.symlink(outside, config_dir / "out" / "demo")

    write_config(
        config_dir,
        {
            "output_dir": "out",
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        },
    )

    result = run_cli("gen", "demo", cwd=config_dir)

    assert result.returncode != 0, (
        "gen through a symlinked package directory must refuse: "
        f"rc={result.returncode}, stdout={result.stdout!r}"
    )
    assert list(outside.iterdir()) == [], (
        "the outside directory must still be EMPTY afterwards -- found "
        f"{[p.name for p in outside.iterdir()]}"
    )
    assert "Traceback" not in result.stderr, result.stderr


def test_cli_gen_refusal_names_both_the_declared_and_resolved_paths(tmp_path):
    """Today's success message names only the path INSIDE the tree
    (``out/demo``), which is the specific harm the ticket records. The
    refusal must name the RESOLVED target too, so the report says where the
    bytes would actually have gone.

    Asserted with ``str(outside.resolve())``, not ``str(outside)``: the
    message carries resolved paths, and on macOS ``tmp_path`` is
    ``/var/...`` while its resolution is ``/private/var/...`` -- an
    assertion built from the unresolved spelling is a flake.

    The package declares ``dir: pkgdir`` rather than defaulting to its id,
    so "the declared directory was named" is a claim this test can actually
    make. With the default, the declared string IS ``demo`` -- and so is
    the package id, which every one of these messages already carries, so
    an assertion on it would pass without the declared path being named at
    all.
    """
    import os

    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "out").mkdir()
    outside = config_dir / "outside"
    outside.mkdir()
    os.symlink(outside, config_dir / "out" / "pkgdir")

    write_config(
        config_dir,
        {
            "output_dir": "out",
            "templates": [{"src": "Makefile.j2"}],
            "packages": {
                "demo": {
                    "name": "Demo",
                    "package_type": "none",
                    "dir": "pkgdir",
                }
            },
        },
    )

    result = run_cli("gen", "demo", cwd=config_dir)

    assert "pkgdir" in result.stderr, (
        "the declared package directory must be named in the refusal: "
        f"stderr={result.stderr!r}"
    )
    assert str(outside.resolve()) in result.stderr, (
        "the RESOLVED target the symlink actually points at must be named "
        f"too -- today's SUCCESS message names only the path inside the "
        f"tree, which is the harm the ticket records: stderr={result.stderr!r}"
    )


def test_cli_gen_dry_run_through_a_symlinked_package_dir_also_refuses(tmp_path):
    """Today ``--dry-run`` prints ``Would write: out/demo/Makefile`` -- the
    path INSIDE the tree, for bytes that would actually land outside via the
    symlink. That is precisely the lie stn-vhr was filed about: a preview
    must not report a write it would not perform, nor the wrong path for one
    it would.
    """
    import os

    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "out").mkdir()
    outside = config_dir / "outside"
    outside.mkdir()
    os.symlink(outside, config_dir / "out" / "demo")

    write_config(
        config_dir,
        {
            "output_dir": "out",
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        },
    )

    result = run_cli("gen", "demo", "--dry-run", cwd=config_dir)

    assert result.returncode != 0, (
        "a dry-run preview through a symlinked package directory must "
        f"refuse exactly like a real gen: rc={result.returncode}, "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    assert "out/demo/Makefile" not in result.stdout, (
        "TODAY: dry-run prints 'Would write: .../out/demo/Makefile' -- the "
        "inside path, naming bytes that would actually land outside. A "
        "preview must not report a write it would not perform, nor the "
        f"wrong path for one it would: stdout={result.stdout!r}"
    )
    assert "Traceback" not in result.stderr, result.stderr
    assert list(outside.iterdir()) == [], (
        "a dry-run must not write anything regardless -- found "
        f"{[p.name for p in outside.iterdir()]}"
    )


def test_generate_package_raises_valueerror_for_a_symlinked_package_dir(
    tmp_path,
):
    """Direct-call twin of the CLI test above, driven through
    ``generate_package`` itself rather than through the subprocess -- so a
    caller embedding stencil as a library, not only the CLI, is covered."""
    import os

    from stencil.generate import build_environment, generate_package, load_config

    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "out").mkdir()
    outside = config_dir / "outside"
    outside.mkdir()
    os.symlink(outside, config_dir / "out" / "demo")

    config_path = write_config(
        config_dir,
        {
            "output_dir": "out",
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        },
    )

    loaded = load_config(config_path)
    env = build_environment(loaded, config_dir)
    output_base = config_dir / "out"

    with pytest.raises(ValueError, match="demo"):
        generate_package(env, loaded, output_base, "demo", False, config_dir)

    assert list(outside.iterdir()) == [], (
        "nothing may be written outside on refusal -- found "
        f"{[p.name for p in outside.iterdir()]}"
    )


# --- stn-vhr, the regression guard -----------------------------------------


def test_an_ordinary_package_directory_still_generates(tmp_path):
    """The regression guard on refusing too much: an ORDINARY package
    directory -- not a symlink at all -- must keep working."""
    write_config(
        tmp_path,
        {
            "output_dir": "out",
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        },
    )

    result = run_cli("gen", "demo", cwd=tmp_path)

    assert result.returncode == 0, (
        f"rc={result.returncode}, stdout={result.stdout!r}, "
        f"stderr={result.stderr!r}"
    )
    assert (tmp_path / "out" / "demo" / "Makefile").exists()


def test_a_package_dir_symlinked_inside_the_output_tree_still_generates(
    tmp_path,
):
    """The other half of the regression guard: a package `dir` that IS a
    symlink, so long as it resolves to somewhere still under `output_base`
    -- e.g. a stable alias for a package that moved -- must not be refused
    by a containment check aimed at paths that escape the tree."""
    import os

    (tmp_path / "out").mkdir()
    real = tmp_path / "out" / "real-demo"
    real.mkdir()
    os.symlink(real, tmp_path / "out" / "demo")

    write_config(
        tmp_path,
        {
            "output_dir": "out",
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        },
    )

    result = run_cli("gen", "demo", cwd=tmp_path)

    assert result.returncode == 0, (
        "a package dir symlinked to somewhere INSIDE the output tree must "
        f"still generate: rc={result.returncode}, stderr={result.stderr!r}"
    )
    assert (real / "Makefile").exists()


# --- stn-9rn: package_name is a filename, not a path -------------------------
#
# package_name is the only package path key that never goes through
# check_config_path. Measured on this branch, the ticket's own reproduction:
#
#     packages: {demo: {package_type: zip, package_name: "../escape me.zip"}}
#     $ stencil gen demo   -> rc=0
#     out/demo/Makefile:145:  PKG ?= ../escape me.zip
#     manifest entries:       ['../escape me.zip', 'Makefile', ...]
#
# Two consequences: the generated Make recipe word-splits on the space and
# names a path outside the package directory -- the exact class
# check_config_path exists to refuse for docs, slides, dir, dest and
# package_sources -- and the unvalidated string is recorded VERBATIM as a
# manifest entry, so `clean` (which DOES run check_config_path over every
# manifest entry) refuses it by name forever: the package becomes
# permanently un-cleanable, rc=1, and the complaint points at a "manifest
# entry" the author never wrote.
#
# `package()` above defaults to `package_type: "none"`, which never
# CONSUMES package_name at all -- kept deliberately for the five tests below
# so they pin that the check applies whenever the key is PRESENT, not only
# for the zip/doc types that happen to read it.


def test_a_package_name_with_whitespace_is_refused_for_whitespace():
    """The ticket's own reproduction string. `check_config_path` tests
    whitespace BEFORE '..', so this value -- which has both -- must be
    pinned as the WHITESPACE refusal, not the escape: whitespace is what
    actually splits the generated Make recipe, and that ordering is correct
    for this value. Asserting the escape message here would pin nothing,
    since check_config_path never reaches its '..' check for this string."""
    with pytest.raises(ValueError, match="whitespace"):
        package_contexts(config(package_name="../escape me.zip"))


def test_a_package_name_with_dotdot_and_no_whitespace_is_refused_for_escaping():
    """The case the whitespace test above does not cover: no whitespace, so
    this must be refused for escaping the package directory instead. A test
    written only from '../escape me.zip' would assert the whitespace
    message and prove nothing about '..' at all."""
    with pytest.raises(ValueError, match="escapes the package directory"):
        package_contexts(config(package_name="../escape.zip"))


def test_a_package_name_with_a_path_separator_is_refused():
    """`check_config_path` deliberately permits a subdirectory --
    `dest: .vscode/settings.json` is documented -- but `package_name` names
    a file IN the package directory, not a path, so a separator
    check_config_path allows today must still be refused here."""
    with pytest.raises(ValueError, match="separator"):
        package_contexts(config(package_name="sub/thing.zip"))


def test_a_package_name_with_a_backslash_is_refused_for_metacharacter():
    """`_UNSAFE_IN_PATH` already contains a backslash, so `check_config_path`
    refuses this as a shell/Make METACHARACTER before any separator check
    even runs. Asserting a separator message here would pass for the wrong
    reason and pin nothing about the separator check at all."""
    with pytest.raises(ValueError, match="metacharacter"):
        package_contexts(config(package_name="a\\b"))


def test_a_package_name_with_a_glob_metacharacter_is_refused():
    """`package_name` is recorded VERBATIM as a manifest entry, so a glob
    metacharacter is the same shape `docs` and `slides` got in stn-2x4's
    `check_no_glob`."""
    with pytest.raises(ValueError, match="glob metacharacter"):
        package_contexts(config(package_name="[a-z].zip"))


def test_cli_gen_with_an_unsafe_package_name_refuses_and_writes_nothing(
    tmp_path,
):
    """The ticket's reproduction end to end. TODAY: gen exits 0, writes a
    Makefile whose PKG line word-splits on the space, and records the
    unvalidated string verbatim as a manifest entry -- which `clean` then
    refuses by name forever. The fix must refuse at gen time, so the
    un-cleanable manifest is never written in the first place: assert the
    Makefile does not exist at all, not only that the exit code is
    non-zero."""
    write_config(
        tmp_path,
        {
            "templates": [{"src": "Makefile.j2"}],
            "packages": {
                "demo": {
                    "name": "Demo",
                    "package_type": "zip",
                    "package_name": "../escape me.zip",
                }
            },
        },
    )

    result = run_cli("gen", "demo", cwd=tmp_path)

    assert result.returncode != 0, (
        f"rc={result.returncode}, stdout={result.stdout!r}, "
        f"stderr={result.stderr!r}"
    )
    assert "Traceback" not in result.stderr, result.stderr
    assert not (tmp_path / "demo" / "Makefile").exists(), (
        "the Makefile must not be written at all -- an unsafe package_name "
        "must be refused before anything is generated, not discovered later "
        f"when `clean` tries to read the manifest back: "
        f"stdout={result.stdout!r}"
    )


@pytest.mark.parametrize("name", ["workspace.zip", "hs1.zip", "hs2.pdf"])
def test_an_ordinary_package_name_still_generates(name):
    """The regression guard: the plain names every consumer config on this
    machine actually uses (checked across all seven before this epic's plan
    was written) must keep working."""
    contexts = package_contexts(config(package_type="zip", package_name=name))
    assert contexts["demo"]["package_name"] == name
