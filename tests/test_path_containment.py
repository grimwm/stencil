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

WHAT THESE ARE AND ARE NOT A BOUNDARY FOR. The previous version of this
paragraph said they were not a privilege boundary at all, because "pre_build.run
is arbitrary execution by design, so whoever writes .config.yaml can already run
anything". That is false, it was the stated reason these checks were allowed to
be lenient, and it had been quoted forward into stn-9rn's and stn-vhr's scope
decisions (stn-axi). stencil never executes `pre_build.run`: it writes the
command into a generated Makefile, and execution happens only when a human later
runs `make` inside the generated package.

The true split, measured rather than reasoned about:

- `clean`, `install`, `list` and `version` execute NOTHING from the config.
  These checks ARE a boundary for them -- and the harms they close are real, not
  theoretical: per stn-h5q and stn-17h, a config got arbitrary named-file
  deletion out of `clean` and an arbitrary managed-.gitignore rewrite out of
  `install`, both at exit 0, from commands that ran no code at all. What closed
  those: #102, #115, #122, #124, #125.
- `gen` and `gen --dry-run` DO execute arbitrary code, and these checks are not
  a boundary for them. `render_templates` calls `template.render()` above its
  `dry_run` branch on a non-sandboxed jinja2.Environment whose FileSystemLoader
  searches the config's own `templates_dir` FIRST. Measured: a payload in a
  `templates/Makefile.j2` ran under `stencil gen demo --dry-run` at exit 0 with
  nothing written to the output tree, while `install` and `clean` over the same
  config executed nothing. That is the documented extension mechanism working as
  designed -- a template is code, and overriding one is the feature -- so
  whoever controls a `templates_dir` controls execution. It is stated here and in
  STENCIL.md rather than implied away; see test_gen_is_the_only_command_that_
  executes_anything below, which pins the half that must stay true.
- `make` on a generated package is outside all of this. These checks are not a
  boundary against whoever runs it, and are not trying to be.

So what these close is a filename quietly doing something other than naming a
file -- and, for `brand`, a read of a file nobody asked to publish reaching a
distributed artifact. A reviewer or CI job running `clean` or `install` over a
pull request's config is the case that makes them worth more than their
severity suggests, because those commands execute nothing and still wrote and
deleted named files.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from stencil import __version__ as stencil_version
from stencil import generate
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

    Driven through the CLI rather than through `checked_output_base`
    directly, so the refusal is pinned on the path a user actually runs --
    the symlink is only visible once `output_dir` has been resolved against
    the config directory, and only `_main` does that.

    Reproduced verbatim per the ticket: `ln -s <outside> cfg/out`,
    `output_dir: out`, a victim file at `<outside>/demo/Makefile`, then
    `clean --all`. BEFORE 0.39.0: rc=0 and the victim file was gone.
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
    all, only the config. BEFORE 0.39.0: rc=0 and
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
    directory carries a manifest naming it. BEFORE 0.39.0, that manifest
    was enough to authorize a delete outside the config directory anyway.
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
    """Before 0.39.0 the success message named only the path INSIDE the tree
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
    """Before 0.39.0, ``--dry-run`` printed ``Would write:
    out/demo/Makefile`` -- the path INSIDE the tree, for bytes that would
    actually land outside via the symlink. That is precisely the lie stn-vhr
    was filed about: a preview must not report a write it would not perform,
    nor the wrong path for one it would.
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
        "Before 0.39.0, dry-run printed 'Would write: "
        ".../out/demo/Makefile' -- the "
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

    # NOT match="demo": the package id appears in essentially every message
    # this function can raise, so it would pass without the containment check
    # existing at all. Match the sentence only containment produces.
    with pytest.raises(ValueError, match="outside the output directory"):
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
    """The ticket's reproduction end to end. BEFORE 0.39.0: gen exited 0,
    wrote a Makefile whose PKG line word-split on the space, and recorded
    the unvalidated string verbatim as a manifest entry -- after which
    `clean` refused by name forever. The fix must refuse at gen time, so the
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


# --- stn-vhr, the report itself: escaped, and with no class name ------------


def test_gen_escapes_a_control_character_in_the_resolved_refusal_path(tmp_path):
    """The refusal message carries a RESOLVED FILESYSTEM PATH, and that path
    is a symlink's target -- so the escape sequence lives in the link target
    STRING, committed in the tree, with no control byte in any file and no
    need for the target to exist.

    `check_config_path` cannot help: it repr's what the CONFIG declared, and
    this is what the filesystem resolved to, which it never sees. So the
    escaping has to happen at the printer, and this pins that it does --
    `clean` escaped this same string via `_safe` while `gen` printed it raw,
    the two halves of one report disagreeing about one path.

    `--dry-run`, deliberately: the preview is the cheapest thing for a
    reviewer to run over an untrusted branch, and it was enough to repaint
    the terminal.
    """
    import os

    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "out").mkdir()
    os.symlink("../../evil\x1b[2Jpwned", config_dir / "out" / "demo")

    write_config(
        config_dir,
        {
            "output_dir": "out",
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        },
    )

    result = run_cli("gen", "demo", "--dry-run", cwd=config_dir)

    assert result.returncode != 0
    assert "\x1b" not in result.stderr, (
        "a raw escape byte reached the terminal from the resolved path: "
        f"{result.stderr!r}"
    )
    assert "\\x1b" in result.stderr, (
        "the control character must be reported in its escaped form, the "
        f"way clean's printer already does: {result.stderr!r}"
    )


def test_a_gen_refusal_carries_no_python_class_name(tmp_path):
    """`_generate`'s narrow ValueError handler exists so a containment
    refusal reads as the config message it is. Without it the broad handler
    below reports `demo: ValueError: Package demo: ...`, putting a Python
    class name in front of a message meant for someone editing YAML --
    which package_contexts' own docstring argues at length must never
    happen. Nothing pinned that, so the handler survived deletion."""
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

    assert result.returncode != 0
    assert "ValueError" not in result.stderr, (
        "a config refusal must not carry a Python class name: "
        f"{result.stderr!r}"
    )


# --- stn-17h: a package directory must be STRICTLY beneath the output base --
#
# Every containment check in this file asks whether a resolved path stays
# UNDER a root, and `Path.relative_to` answers yes for a path EQUAL to that
# root. So `dir: "."` passed all of them: `check_config_path` finds no `..`
# in `Path(".").parts` (it is empty), and `_validated_package_dirs` then
# places the package directory exactly ON the output base. With no top-level
# `output_dir`, that base is the config directory -- the repository root.
#
# Nothing escapes anywhere, which is why this is a separate ticket from
# stn-pe3 and stn-vhr rather than a case they missed.


@pytest.mark.parametrize("value", [".", "", "./"])
def test_a_package_dir_that_is_the_output_base_is_refused(value):
    """A package needs its own directory, and these three spellings all name
    the output base itself.

    `dir` is a delete list's anchor: `clean` unions the manifest found in the
    package directory with the config-derived entries and unlinks each one
    under it. Anchored at the repository root, a planted
    `.stencil-manifest.json` is a free-form list of files to remove -- see
    the end-to-end test below, which is the ticket's own reproduction.
    """
    with pytest.raises(ValueError, match="dir"):
        package_contexts(config(dir=value))


def test_clean_refuses_a_package_dir_that_is_the_config_directory(tmp_path):
    """stn-17h's reproduction, verbatim, and the reason this is a P1 rather
    than a tidy-up.

    BEFORE: `stencil clean demo` printed four `Removed` lines and exited 0,
    having deleted a hand-written markdown file, a dotfile holding a secret,
    and a whole source directory -- none of which stencil ever generated. The
    manifest that named them is a gitignored JSON file, so planting one is
    invisible in a diff.

    Asserted on the FILES, not on the exit code. A refusal that still deletes
    is the failure worth catching, and an exit code cannot see it.
    """
    import json

    write_config(
        tmp_path,
        {
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": {"name": "Demo", "package_type": "none", "dir": "."}},
        },
    )
    (tmp_path / "IMPORTANT.md").write_text("hand written\n")
    (tmp_path / ".env").write_text("SECRET=1\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print(1)\n")
    (tmp_path / MANIFEST_NAME).write_text(
        json.dumps(
            {
                "manifest_version": MANIFEST_VERSION,
                "stencil_version": "0.1.0",
                "package": "demo",
                "dir": ".",
                "entries": ["IMPORTANT.md", ".env", "src/app.py"],
            }
        )
    )

    result = run_cli("clean", "demo", cwd=tmp_path)

    assert result.returncode != 0, (
        f"rc={result.returncode}, stdout={result.stdout!r}, "
        f"stderr={result.stderr!r}"
    )
    assert "Traceback" not in result.stderr, result.stderr
    for survivor in ("IMPORTANT.md", ".env", "src/app.py"):
        assert (tmp_path / survivor).exists(), (
            f"{survivor} was deleted by a package that generated nothing: "
            f"stdout={result.stdout!r}"
        )


def test_a_package_dir_symlinked_to_the_output_base_is_refused(tmp_path):
    """The spelling no string check can see, and the reason the refusal
    belongs in `contained_path` rather than only in the pre-flight.

    `dir: demo` is an ordinary value that passes every character rule; the
    equality that matters appears only after `resolve()`, when `out/demo` is
    a link pointing back at `out`. The string check and the resolved check
    are two halves of one rule here exactly as they are for `output_dir`
    (stn-pe3) and for a symlinked package directory (stn-vhr).
    """
    import os

    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "out").mkdir()
    os.symlink(config_dir / "out", config_dir / "out" / "demo")

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
        f"rc={result.returncode}, stdout={result.stdout!r}, "
        f"stderr={result.stderr!r}"
    )
    assert "Traceback" not in result.stderr, result.stderr
    assert not (config_dir / "out" / "Makefile").exists(), (
        "gen wrote through the link into the output base itself: "
        f"stdout={result.stdout!r}"
    )


def test_gen_refuses_a_package_dir_that_is_the_output_base(tmp_path):
    """gen and clean must agree. `clean` refusing alone would leave a package
    that generates at exit 0 into the repository root and then cannot be
    cleaned -- the permanently un-cleanable package of stn-9rn, arrived at
    from the other side."""
    write_config(
        tmp_path,
        {
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": {"name": "Demo", "package_type": "none", "dir": "."}},
        },
    )

    result = run_cli("gen", "demo", cwd=tmp_path)

    assert result.returncode != 0, (
        f"rc={result.returncode}, stdout={result.stdout!r}, "
        f"stderr={result.stderr!r}"
    )
    assert not (tmp_path / "Makefile").exists(), (
        f"gen wrote into the config directory itself: stdout={result.stdout!r}"
    )


def test_gen_dry_run_refuses_a_package_dir_that_is_the_output_base(tmp_path):
    """`--dry-run` is the cheapest thing a reviewer runs over an untrusted
    branch, so it has to report the same refusal rather than previewing a
    write it would not perform -- the argument stn-vhr already made for the
    package directory it contains."""
    write_config(
        tmp_path,
        {
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": {"name": "Demo", "package_type": "none", "dir": "."}},
        },
    )

    result = run_cli("gen", "demo", "--dry-run", cwd=tmp_path)

    assert result.returncode != 0, (
        f"rc={result.returncode}, stdout={result.stdout!r}, "
        f"stderr={result.stderr!r}"
    )


def test_a_package_dir_one_level_down_still_generates(tmp_path):
    """The regression guard. Refusing the base must not start refusing the
    ordinary arrangement every consumer config uses."""
    write_config(
        tmp_path,
        {
            "output_dir": "out",
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        },
    )

    result = run_cli("gen", "demo", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out" / "demo" / "Makefile").exists()


# --- stn-h5q: contain the WRITES, not only the directory --------------------
#
# stn-vhr contains the package DIRECTORY. In every case below the package
# directory is a genuine directory that resolves cleanly and passes that
# check -- what leaves the tree is a component BELOW it, which no
# directory-level check can see. All four reproduced at exit 0, with the
# success message naming the path INSIDE the tree.
#
# The asymmetry is the point rather than the severity. `clean` already
# resolves each entry's PARENT and refuses one that lands outside
# (_remove_entries); `gen` resolved nothing at all. So gen wrote a file
# clean then refused to remove -- the permanently un-cleanable package of
# stn-9rn, through a different door.

pytestmark_h5q = pytest.mark.skipif(
    sys.platform == "win32",
    reason="O_NOFOLLOW, symlinks and hardlinks do not behave the same on Windows",
)


def _planted_package(tmp_path, **config_overrides):
    """A config directory with an `out/demo` package directory that already
    exists, and a file OUTSIDE the tree worth protecting.

    Returns (config_dir, package_dir, outside_file). The caller plants
    whatever link it is testing and then runs `gen`.
    """
    config_dir = tmp_path / "cfg"
    package_dir = config_dir / "out" / "demo"
    package_dir.mkdir(parents=True)
    outside = config_dir.parent / "outside"
    outside.mkdir()
    target = outside / "target.txt"
    target.write_text("PRECIOUS\n")

    cfg = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    cfg.update(config_overrides)
    write_config(config_dir, cfg)
    return config_dir, package_dir, target


@pytestmark_h5q
def test_gen_refuses_a_symlink_at_the_final_component(tmp_path):
    """Reproduction (1). `render_templates` called `output_path.write_text`,
    which opens O_WRONLY|O_CREAT|O_TRUNC with NO O_NOFOLLOW -- so a symlink
    standing where a generated file goes was followed, and the rendered
    Makefile landed on whatever it pointed at. rc=0, and the "Generated:"
    line named the path inside the tree.

    Asserted on the OUTSIDE FILE's content. An exit code cannot see a
    successful write to the wrong place."""
    config_dir, package_dir, target = _planted_package(tmp_path)
    (package_dir / "Makefile").symlink_to(target)

    result = run_cli("gen", "demo", cwd=config_dir)

    assert result.returncode != 0, (
        f"rc={result.returncode}, stdout={result.stdout[:400]!r}"
    )
    assert "Traceback" not in result.stderr, result.stderr
    assert target.read_text() == "PRECIOUS\n", (
        "gen followed a symlink at the file it writes and overwrote a file "
        "outside the output tree"
    )


@pytestmark_h5q
def test_gen_refuses_a_hardlink_at_the_final_component(tmp_path):
    """Reproduction (2), and the one no path check can ever catch.

    `resolve()` reports the path CONTAINED, because it is: the second name
    for the inode lives outside and no `relative_to` can see it. The only
    thing that distinguishes it is `st_nlink`, which is 2.

    This is why the fix is a stat of every component rather than another
    resolve-and-compare."""
    config_dir, package_dir, target = _planted_package(tmp_path)
    os.link(target, package_dir / "Makefile")
    assert (package_dir / "Makefile").stat().st_nlink == 2, "test setup"

    result = run_cli("gen", "demo", cwd=config_dir)

    assert result.returncode != 0, (
        f"rc={result.returncode}, stdout={result.stdout[:400]!r}"
    )
    assert "Traceback" not in result.stderr, result.stderr
    assert target.read_text() == "PRECIOUS\n", (
        "gen wrote through a hardlink and overwrote a file outside the tree"
    )


@pytestmark_h5q
def test_gen_refuses_a_symlinked_intermediate_directory(tmp_path):
    """Reproduction (3), and the gen/clean asymmetry stated as a test.

    A nested `dest` is documented (`.vscode/settings.json`), and
    `output_path.parent.mkdir(parents=True, exist_ok=True)` walks a link
    happily. BEFORE: gen wrote `outside/Makefile` at rc=0 and `clean` then
    refused that same entry by name, rc=1, on every run thereafter -- the
    package could never be cleaned again.

    So this asserts BOTH halves: nothing outside, and `clean` still works
    afterwards. A fix that only refuses the write would still be a fix; one
    that leaves clean broken would not."""
    config_dir, package_dir, target = _planted_package(
        tmp_path, templates=[{"src": "Makefile.j2", "dest": "sub/Makefile"}]
    )
    (package_dir / "sub").symlink_to(target.parent)

    result = run_cli("gen", "demo", cwd=config_dir)

    assert result.returncode != 0, (
        f"rc={result.returncode}, stdout={result.stdout[:400]!r}"
    )
    assert "Traceback" not in result.stderr, result.stderr
    assert not (target.parent / "Makefile").exists(), (
        "gen walked a symlinked intermediate directory and wrote outside "
        "the output tree"
    )

    assert str(package_dir / "sub") in result.stderr, (
        "the refusal must name the symlinked COMPONENT, not only the "
        f"declared dest -- the author edits the wrong line otherwise: "
        f"{result.stderr!r}"
    )

    # AND THE RESIDUAL STATE, pinned rather than assumed clean. `clean`
    # cannot clear this either: `_remove_entries` resolves the entry's
    # parent, finds it outside the package, and refuses -- deliberately and
    # permanently, because following the link is the one thing it must never
    # do. So the package is un-generable AND un-cleanable until someone
    # removes the link by hand, and the only thing that makes that a
    # recoverable situation rather than a dead end is gen's message saying
    # so. Asserting "nothing is left that clean cannot remove" would pass
    # vacuously here -- gen wrote nothing -- which is why this asserts on
    # clean's own exit code instead.
    cleaned = run_cli("clean", "demo", cwd=config_dir)
    assert "Traceback" not in cleaned.stderr, cleaned.stderr
    assert cleaned.returncode != 0, (
        "clean is expected to refuse this entry; if that changed, the "
        "recovery advice in gen's refusal has to change with it"
    )
    assert "delete the link yourself" in result.stderr, (
        "gen must say how to recover, because `stencil clean` cannot -- and "
        "the advice must survive _safe's truncation, which is why the "
        f"message is kept short: {result.stderr!r}"
    )


@pytestmark_h5q
def test_gen_refuses_a_symlink_at_the_brand_destination(tmp_path):
    """Reproduction (4). `copy_brand_image` takes real care with its SOURCE
    -- it opens with O_NOFOLLOW, and says in a comment that a link swapped
    in after the check must fail rather than quietly read elsewhere -- and
    then opened its DESTINATION with a bare `open(destination, "wb")`.

    The destination is the half that WRITES, so it is the half that
    mattered."""
    config_dir, package_dir, target = _planted_package(
        tmp_path,
        brand="logo.svg",
        **{"brand-alt": "Logo"},
        packages={
            "demo": {
                "name": "Demo",
                "package_type": "none",
                "docs": ["README.md"],
            }
        },
    )
    (config_dir / "logo.svg").write_text("<svg/>\n")
    (package_dir / "logo.svg").symlink_to(target)

    result = run_cli("gen", "demo", cwd=config_dir)

    assert result.returncode != 0, (
        f"rc={result.returncode}, stdout={result.stdout[:400]!r}"
    )
    assert "Traceback" not in result.stderr, result.stderr
    assert target.read_text() == "PRECIOUS\n", (
        "copy_brand_image followed a symlink at its destination"
    )


@pytestmark_h5q
def test_a_refused_write_target_stops_gen_before_anything_is_written(tmp_path):
    """The stn-vhr guarantee, extended from the package directory to the
    files inside it: a refused run must touch NOTHING, so the check is a
    pre-pass over every destination rather than a guard at each write.

    Without it, a package whose third template lands on a planted link is
    left with two rendered files and no manifest -- a half-generated
    package that looks generated."""
    config_dir, package_dir, target = _planted_package(
        tmp_path,
        templates=[
            {"src": "Makefile.j2"},
            {"src": "docker-compose.yml.j2"},
        ],
    )
    # A manifest from an earlier, successful run. generate_package unlinks
    # this before rendering, so the pre-pass has to sit ABOVE that unlink,
    # not merely above the mkdir: a refused run that destroyed the manifest
    # would push the next `clean` onto its config-derived fallback, which is
    # exactly the trustworthy-list guarantee the manifest exists to give.
    (package_dir / MANIFEST_NAME).write_text('{"kept": true}\n')
    (package_dir / "docker-compose.yml").symlink_to(target)

    before = {
        path.relative_to(package_dir): path.read_bytes()
        for path in package_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    }

    result = run_cli("gen", "demo", cwd=config_dir)

    assert result.returncode != 0, (
        f"rc={result.returncode}, stdout={result.stdout[:400]!r}"
    )
    after = {
        path.relative_to(package_dir): path.read_bytes()
        for path in package_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    assert after == before, (
        "a refused run changed the package directory: it must touch "
        f"nothing at all. stdout={result.stdout[:400]!r}"
    )
    assert not (package_dir / "Makefile").exists(), (
        "the first template was written before the second was refused: "
        f"stdout={result.stdout[:400]!r}"
    )


@pytestmark_h5q
def test_a_nested_dest_through_a_real_directory_still_generates(tmp_path):
    """The regression guard for the documented case. `.vscode/settings.json`
    is in STENCIL.md, and walking components to refuse a symlinked one must
    not start refusing an ordinary subdirectory that gen creates itself."""
    config_dir, package_dir, _target = _planted_package(
        tmp_path, templates=[{"src": "Makefile.j2", "dest": "sub/Makefile"}]
    )

    result = run_cli("gen", "demo", cwd=config_dir)

    assert result.returncode == 0, result.stderr
    assert (package_dir / "sub" / "Makefile").exists()


@pytestmark_h5q
def test_regenerating_over_gens_own_output_still_works(tmp_path):
    """The most common operation in the tool, and the one a component-walking
    check is most likely to break: `gen` overwrites its own output on every
    run, so an ordinary existing file (`st_nlink == 1`, not a link) must not
    be mistaken for a planted one. A slip of one character here stops
    stencil working on every existing project, which is a worse outcome than
    the bug being fixed.

    Deliberately exercises all three write sites on the second pass -- a
    template, a nested `dest` whose intermediate directory now exists, and
    the copied brand image, whose destination `copy_brand_image` names
    itself rather than taking it from the template list -- plus the manifest
    the first run left behind."""
    config_dir, package_dir, _target = _planted_package(
        tmp_path,
        templates=[
            {"src": "Makefile.j2"},
            {"src": "Makefile.j2", "dest": "sub/Makefile"},
        ],
        brand="logo.svg",
        **{"brand-alt": "Logo"},
        packages={
            "demo": {
                "name": "Demo",
                "package_type": "none",
                "docs": ["README.md"],
            }
        },
    )
    (config_dir / "logo.svg").write_text("<svg/>\n")

    first = run_cli("gen", "demo", cwd=config_dir)
    assert first.returncode == 0, first.stderr
    assert (package_dir / "logo.svg").is_file(), "test setup: no brand copied"

    second = run_cli("gen", "demo", cwd=config_dir)

    assert second.returncode == 0, (
        "regenerating over gen's own output was refused: "
        f"{second.stderr!r}"
    )
    assert (package_dir / "sub" / "Makefile").exists()
    assert (package_dir / "logo.svg").is_file()


@pytestmark_h5q
def test_gen_dry_run_refuses_a_planted_write_target(tmp_path):
    """`--dry-run` is the cheapest thing a reviewer runs over an untrusted
    branch -- this file's module docstring says exactly that about the brand
    read -- so a preview that prints `Would write: out/demo/Makefile` for a
    path the real run refuses is a lie about what `gen` would do.

    Under `--dry-run` nothing is created and nothing is written, so the
    pre-pass is the only thing that runs at all: this is the test that pins
    it running there rather than at each write site."""
    config_dir, package_dir, target = _planted_package(tmp_path)
    (package_dir / "Makefile").symlink_to(target)

    result = run_cli("gen", "demo", "--dry-run", cwd=config_dir)

    assert result.returncode != 0, (
        f"rc={result.returncode}, stdout={result.stdout[:400]!r}"
    )
    assert target.read_text() == "PRECIOUS\n"


@pytestmark_h5q
def test_gen_refuses_a_fifo_at_a_destination(tmp_path):
    """The node type a symlink-and-hardlink check does not look at, found by
    the adversarial review of this change's own plan.

    A FIFO is neither a symlink nor a regular file, `O_NOFOLLOW` has nothing
    to say about it, and `mkfifo` needs no privilege. Measured against the
    draft that refused only those two types: `os.open(fifo,
    O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW)` BLOCKED INDEFINITELY -- `stencil
    gen` hung with no timeout, and `clean` then exited 0 leaving the FIFO in
    place, because `_remove_entries` gates on `path.is_file()`. So the
    cheapest of the five unhandled types produced both harms the ticket is
    about at once.

    The check is an allowlist for exactly this reason: a regular file with
    one link at the last component, a directory at every other. Five node
    types are refused by saying what IS allowed rather than by enumerating
    what is not."""
    config_dir, package_dir, _target = _planted_package(tmp_path)
    os.mkfifo(package_dir / "Makefile")

    result = run_cli("gen", "demo", cwd=config_dir, timeout=60)

    assert result.returncode != 0, (
        f"rc={result.returncode}, stdout={result.stdout[:400]!r}"
    )
    assert "Traceback" not in result.stderr, result.stderr


@pytestmark_h5q
def test_gen_refuses_a_symlinked_subdirectory_pointing_back_inside(tmp_path):
    """gen is STRICTER than clean here, deliberately, and that is pinned
    rather than left to be discovered.

    `_remove_entries` permits an intermediate symlink that resolves back
    inside the package -- it resolves the parent and the containment holds,
    so `clean` cleans such a package happily. `gen` refuses it anyway: a
    link is not something `gen` created, and following one is how the three
    cases above went wrong.

    So the invariant this change establishes is "gen is at least as strict
    as clean", not "gen and clean agree". Anything gen writes, clean can
    remove; the converse is not claimed."""
    config_dir, package_dir, _target = _planted_package(
        tmp_path, templates=[{"src": "Makefile.j2", "dest": "sub/Makefile"}]
    )
    (package_dir / "real").mkdir()
    (package_dir / "sub").symlink_to(package_dir / "real")

    result = run_cli("gen", "demo", cwd=config_dir)

    assert result.returncode != 0, (
        f"rc={result.returncode}, stdout={result.stdout[:400]!r}"
    )
    assert "symlink" in result.stderr, result.stderr


def test_a_template_dest_derived_from_src_is_checked_too(tmp_path):
    """`package_contexts` checks a DECLARED `dest` and skips the key when it
    is absent -- and the destination is then `src` with `.j2` removed, while
    `src` goes through no check anywhere.

    Measured before the fix: `src: "a b.txt.j2"` generated `a b.txt` at exit
    0, recorded it in the manifest, and `stencil clean` then refused ITS OWN
    manifest entry for containing whitespace -- so the package was
    permanently un-cleanable and the complaint named a "manifest entry" the
    author never wrote. That is stn-9rn's harm exactly, arriving through the
    key nobody checked.

    The refusal names `src`, not `dest`: the author wrote `src`, and a
    message naming a key that is not in their config sends them hunting."""
    write_config(
        tmp_path,
        {
            "templates": [{"src": "a b.txt.j2"}],
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        },
    )

    result = run_cli("gen", "demo", cwd=tmp_path)

    assert result.returncode != 0, (
        f"rc={result.returncode}, stdout={result.stdout[:400]!r}"
    )
    assert "src" in result.stderr, result.stderr
    assert not (tmp_path / "demo" / "a b.txt").exists(), (
        "gen wrote a file clean would then refuse to remove"
    )


@pytestmark_h5q
@pytest.mark.parametrize("kind", ["dangling", "directory"])
def test_clean_removes_a_symlink_standing_where_a_generated_file_goes(
    tmp_path, kind
):
    """gen's refusal says "`stencil clean` removes it". It has to be TRUE.

    `_remove_entries` gated its removal on `Path.exists()` and
    `Path.is_file()`, and BOTH follow a symlink -- `exists()` is False for a
    dangling one, `is_file()` is False for one pointing at a directory. So
    the two shapes that loop's `unlink()` was written to handle were the
    exact two it never reached.

    Measured before the fix: `stencil clean demo` exited 0 with twelve
    `Removed` lines and the word `Makefile` in none of them, the link
    survived, and `gen` then refused forever -- a package locked out of
    regeneration by a refusal whose own advice did not work. The link points
    OUT of the tree, inside a directory AGENTS.md says is routinely handed
    to someone as a project of their own, and `clean` had just reported the
    package clean.

    Both halves are asserted: the link is gone, and `gen` works again."""
    config_dir, package_dir, target = _planted_package(tmp_path)
    link = package_dir / "Makefile"
    if kind == "dangling":
        link.symlink_to(target.parent / "not-there.txt")
    else:
        link.symlink_to(target.parent)

    assert run_cli("gen", "demo", cwd=config_dir).returncode != 0

    cleaned = run_cli("clean", "demo", cwd=config_dir)

    assert "Traceback" not in cleaned.stderr, cleaned.stderr
    assert not link.is_symlink(), (
        "clean left the link in place while reporting success: "
        f"rc={cleaned.returncode}, stdout={cleaned.stdout[:400]!r}"
    )
    assert target.parent.exists(), "clean removed the link's TARGET"

    again = run_cli("gen", "demo", cwd=config_dir)
    assert again.returncode == 0, (
        "the package is still locked out of regeneration after the clean "
        f"its own refusal recommended: {again.stderr!r}"
    )


@pytestmark_h5q
def test_the_recovery_advice_says_clean_for_a_link_that_lands_inside(tmp_path):
    """`clean`'s rule is about the resolved PARENT, not about links, so an
    intermediate symlink pointing back INSIDE the package is one it handles
    happily. Telling the author "clean cannot remove it either; delete the
    link yourself" was false for that case -- and for the final-component
    case it was already right -- so the advice branches on where the link
    resolves rather than on which component it is."""
    config_dir, package_dir, _target = _planted_package(
        tmp_path, templates=[{"src": "Makefile.j2", "dest": "sub/Makefile"}]
    )
    (package_dir / "real").mkdir()
    (package_dir / "sub").symlink_to(package_dir / "real")

    result = run_cli("gen", "demo", cwd=config_dir)

    assert result.returncode != 0
    assert "stencil clean" in result.stderr, result.stderr
    assert "delete the link yourself" not in result.stderr, result.stderr


@pytestmark_h5q
def test_the_refusal_survives_truncation_when_the_path_is_long(tmp_path):
    """`_safe` truncates a problem at `_MAX_PROBLEM_CHARS`, and this message
    carries a RESOLVED ABSOLUTE path -- whose length belongs to the machine,
    not to the author. A GitHub Actions checkout reaches the limit on its
    own, and `_generate` spends another sixty characters re-prefixing the
    package id.

    An earlier version of this message put the path in the middle, lost both
    the diagnosis and the recovery off the end, and carried a comment
    claiming it was kept short enough. What has to survive is the part that
    tells the reader what to do, so the path goes last and this pins it."""
    long_id = "a-reasonably-long-course-directory-name-for-2026-spring"
    config_dir = tmp_path / "cfg"
    package_dir = config_dir / "out" / long_id
    package_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "target.txt").write_text("PRECIOUS\n")
    write_config(
        config_dir,
        {
            "output_dir": "out",
            "templates": [{"src": "Makefile.j2"}],
            "packages": {long_id: {"name": "Demo", "package_type": "none"}},
        },
    )
    (package_dir / "Makefile").symlink_to(outside / "target.txt")

    result = run_cli("gen", long_id, cwd=config_dir)

    assert result.returncode != 0
    assert "symlink" in result.stderr, (
        f"the diagnosis was truncated away: {result.stderr!r}"
    )
    assert "stencil clean" in result.stderr, (
        f"the recovery advice was truncated away: {result.stderr!r}"
    )


@pytest.mark.parametrize("dest", ["*.txt", "x?.txt", "[abc].txt"])
def test_a_template_dest_with_a_glob_metacharacter_is_refused(tmp_path, dest):
    """The glob refusal is opt-in rather than part of `_UNSAFE_IN_PATH` --
    globs are the point of a `package_sources` pattern -- and it was wired
    to `docs`, `slides`, `dir` and both `output_dir`s, and not to this one.

    Measured: `dest: "*.txt"` generated at exit 0, went into the manifest
    verbatim, and `clean` then refused it as "not a recognized glob shape"
    on every run thereafter. stn-9rn's permanently-un-cleanable package,
    arriving through the very channel the shared destination list was added
    to close.

    There is a second edge: `install` then wrote `demo/*.txt` into the
    managed section, which made the author's own `demo/notes.txt`
    git-invisible."""
    write_config(
        tmp_path,
        {
            "templates": [{"src": "Makefile.j2", "dest": dest}],
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        },
    )

    result = run_cli("gen", "demo", cwd=tmp_path)

    assert result.returncode != 0, (
        f"rc={result.returncode}, stdout={result.stdout[:400]!r}"
    )
    assert "glob metacharacter" in result.stderr, result.stderr


# --- stn-jez: an ordinary dir with a planted manifest -----------------------
#
# NOT stn-17h. That ticket closed `dir: "."` -- a package directory that
# resolves to the output base itself -- which the containment checks above
# already refuse before any manifest is ever read. The attack here needs
# none of that: `src` is an ordinary, hand-populated source directory,
# strictly beneath the output base, with no `output_dir` prefix and no
# templates at all. It clears every containment check in this file. What
# lets a planted manifest delete a hand-written file here is
# `_clean_one_directory`'s ownership guard reading a MISSING `package` key
# as "no opinion" rather than as untrusted -- `isinstance(None, str)` is
# False, so `isinstance(manifest_pkg, str) and ...` was False too, and the
# manifest was used.


def test_an_ordinary_package_dir_with_a_manifest_missing_package_survives_clean(
    tmp_path,
):
    """The ticket's verbatim reproduction.

    BEFORE (stn-jez): `stencil clean src` printed a `Removed` line for
    `important.txt` and exited 0, having deleted a hand-written file that
    `gen` never produced -- through a manifest with no `package` key at all,
    which the old guard's `isinstance` check let straight through.

    Asserted on the FILE, not on the exit code alone. A refusal that still
    deletes is the failure worth catching, and an exit code cannot see it.
    """
    write_config(
        tmp_path,
        {
            "templates": [],
            "packages": {"src": {"package_type": "none"}},
        },
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "important.txt").write_text("HAND WRITTEN - DO NOT DELETE\n")
    (src / MANIFEST_NAME).write_text(
        json.dumps({"manifest_version": MANIFEST_VERSION, "entries": ["important.txt"]})
    )

    result = run_cli("clean", "src", cwd=tmp_path)

    assert result.returncode != 0, (
        f"rc={result.returncode}, stdout={result.stdout!r}, "
        f"stderr={result.stderr!r}"
    )
    assert "Traceback" not in result.stderr, result.stderr
    assert (src / "important.txt").exists(), (
        f"important.txt was deleted by a planted manifest missing "
        f"'package': stdout={result.stdout!r}"
    )
    combined = result.stdout + result.stderr
    assert '"package"' in combined, (
        f"the missing field should be named in the refusal: {combined!r}"
    )
    assert MANIFEST_NAME in combined, (
        f"the manifest should be named in the refusal: {combined!r}"
    )


# --- stn-avv: the object checked is the object written ----------------------
#
# stn-6mcb.7 is the MECHANISM ONLY -- the capability flag, the descriptor
# walk helper, and the `dir_fd` keyword on the two nofollow writers. No call
# site (render_templates, write_manifest, copy_brand_image, generate_package)
# changes here; that wiring is stn-6mcb.5. Every test below exercises the
# mechanism directly rather than through `stencil gen`, which is why this
# section calls `generate.walk_dir_fd` / `generate.open_for_write_nofollow` /
# `generate.write_text_nofollow` rather than `run_cli`.

pytestmark_avv = pytest.mark.skipif(
    not generate._DIR_FD_CAPABLE,
    reason=(
        "dir_fd is not supported on this platform -- "
        "generate._DIR_FD_CAPABLE is False"
    ),
)


def test_the_capability_flag_matches_the_measured_conditions():
    """Pins the exact formula stn-6mcb.7 measured, not just its value on
    this machine, so a future edit that quietly narrows or widens it fails
    here rather than only on whichever platform CI happens to run."""
    expected = (
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and os.open in os.supports_dir_fd
        and os.mkdir in os.supports_dir_fd
    )
    assert generate._DIR_FD_CAPABLE is expected


@pytestmark_avv
def test_walk_dir_fd_write_lands_in_the_original_inode_after_a_swap(tmp_path):
    """The direct unit test of the walk (stn-6mcb.7 spike note 2): once the
    walk has opened `sub`'s descriptor, replacing `sub` on disk with a
    symlink to a victim directory must not move where a write through that
    descriptor lands. The fd is pinned to the inode it opened, not to the
    name that found it.
    """
    root = tmp_path / "root"
    root.mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()

    base_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent_fd, name = generate.walk_dir_fd(base_fd, "sub/file.txt")
        try:
            # Move the REAL "sub" out of the way and plant a symlink to the
            # victim directory in its place. `parent_fd` was already opened
            # against the original inode before this happens.
            renamed = tmp_path / "sub-renamed"
            (root / "sub").rename(renamed)
            (root / "sub").symlink_to(victim)

            generate.write_text_nofollow(Path(name), "hello\n", dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        os.close(base_fd)

    assert (renamed / "file.txt").read_text() == "hello\n", (
        "the write did not land in the original inode the walk opened"
    )
    assert list(victim.iterdir()) == [], (
        "the victim directory received a write through the swapped symlink"
    )


@pytestmark_avv
def test_walk_dir_fd_hands_back_a_descriptor_the_caller_owns_for_a_top_level_name(
    tmp_path,
):
    """A single-component `relative` returns a fd the caller may close, and
    closing it must NOT close `base_fd`.

    This is the COMMON case -- `Makefile`, the manifest, the brand image and
    every other top-level file a package contains -- and it is the one where
    the natural implementation hands back `base_fd` itself. A caller doing
    the obvious thing with what it was given would then close its own
    long-lived package-directory descriptor after the first top-level write,
    and every write after that would fail on a stale fd. One `os.dup` makes
    "close what you were given" correct in both cases; this test is what
    holds that, because nothing about the happy path reveals it.
    """
    base_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        parent_fd, name = generate.walk_dir_fd(base_fd, "Makefile")
        assert name == "Makefile"
        assert parent_fd != base_fd, (
            "a top-level name must not hand back base_fd itself: the caller "
            "closes what it is given, and that would be the package directory"
        )

        os.close(parent_fd)

        # base_fd must still be usable -- this is the assertion the bug would
        # fail, with EBADF.
        second_fd, second_name = generate.walk_dir_fd(base_fd, "Makefile")
        try:
            assert second_name == "Makefile"
            generate.write_text_nofollow(
                Path("Makefile"), "after the first close\n", dir_fd=second_fd
            )
        finally:
            os.close(second_fd)

        assert (tmp_path / "Makefile").read_text() == "after the first close\n"
    finally:
        os.close(base_fd)


@pytestmark_avv
def test_walk_dir_fd_refuses_a_symlinked_intermediate_component(tmp_path):
    """A symlink at an intermediate component is refused, and the message
    names THAT component -- not only the declared relative path, which for
    a nested dest is a different string the author would edit in vain.
    """
    root = tmp_path / "root"
    root.mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    (root / "sub").symlink_to(victim)

    base_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with pytest.raises(ValueError) as excinfo:
            generate.walk_dir_fd(base_fd, "sub/file.txt")
    finally:
        os.close(base_fd)

    assert "sub" in str(excinfo.value), (
        f"the refusal must name the symlinked component: {excinfo.value}"
    )
    assert "Traceback" not in str(excinfo.value)
    assert list(victim.iterdir()) == [], (
        "nothing should have been written through the symlink"
    )


@pytestmark_avv
def test_walk_dir_fd_closes_every_intermediate_fd_on_the_failure_path(
    tmp_path, monkeypatch
):
    """No fd leak on the failure path. `a` and `b` are opened successfully as
    the walk descends; `c` is a symlink and refuses. Every fd the walk
    itself opened (tracked by the `dir_fd=` keyword, which only ITS opens
    use) must be closed by the time the ValueError propagates -- `gen --all`
    over many packages must not leak one descriptor per refused component.
    """
    root = tmp_path / "root"
    (root / "a" / "b").mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.mkdir()
    (root / "a" / "b" / "c").symlink_to(victim)

    opened = []
    closed = []
    real_open = os.open
    real_close = os.close

    def tracking_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        if "dir_fd" in kwargs:
            opened.append(fd)
        return fd

    def tracking_close(fd):
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "close", tracking_close)

    base_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with pytest.raises(ValueError):
            generate.walk_dir_fd(base_fd, "a/b/c/d.txt")
    finally:
        os.close(base_fd)

    assert opened, "test setup: the walk should have opened 'a' and 'b'"
    assert set(opened) <= set(closed), (
        f"the walk leaked a descriptor on the failure path: "
        f"opened={opened}, closed={closed}"
    )


@pytestmark_avv
def test_write_text_nofollow_writes_through_an_explicit_dir_fd(tmp_path):
    """Regression guard for the new keyword: `write_text_nofollow` still
    does exactly what it always did -- UTF-8, fchmod through the same
    descriptor for `executable` -- when the destination is named relative
    to a `dir_fd` instead of by an absolute path."""
    root = tmp_path / "root"
    root.mkdir()
    base_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        generate.write_text_nofollow(
            Path("file.txt"), "via dir_fd\n", executable=True, dir_fd=base_fd
        )
    finally:
        os.close(base_fd)

    written = root / "file.txt"
    assert written.read_text() == "via dir_fd\n"
    assert written.stat().st_mode & 0o111, "the executable bit must still be set"


# --- stn-avv: the write-time fstat gate --------------------------------------
#
# checked_write_target's pre-pass refuses three things -- a symlink, a
# hardlink, and "not the kind of file gen writes" -- but it is a snapshot.
# These tests plant the hardlink/FIFO AFTER any such pre-pass would have run
# (there is none here; this calls the write function directly) and confirm
# the refusal now holds at the moment of the write itself, against the
# object `os.open` actually returned.

pytestmark_fstat_gate = pytest.mark.skipif(
    sys.platform == "win32",
    reason="hardlinks, FIFOs and O_NOFOLLOW do not behave the same on Windows",
)


@pytestmark_fstat_gate
def test_open_for_write_nofollow_refuses_a_hardlink_at_write_time(tmp_path):
    """Reproduction of the snapshot gap the notes describe: a hardlink
    planted after any pre-pass is still refused, because the check now runs
    against the fd `os.open` returned rather than only a path stat taken
    earlier. Asserted on the file OUTSIDE the tree: a refusal that still
    truncates it would defeat the point.
    """
    outside = tmp_path / "outside.txt"
    outside.write_text("PRECIOUS\n")
    target = tmp_path / "target.txt"
    os.link(outside, target)
    assert target.stat().st_nlink == 2, "test setup"

    with pytest.raises(ValueError) as excinfo:
        generate.open_for_write_nofollow(target)

    assert "target.txt" in str(excinfo.value), (
        f"the refusal must name the file: {excinfo.value}"
    )
    assert outside.read_text() == "PRECIOUS\n", (
        "a hardlink refusal must not truncate the shared inode -- dropping "
        "O_TRUNC in favour of the fstat-gated ftruncate is what makes that "
        "true"
    )


@pytestmark_fstat_gate
def test_open_for_write_nofollow_refuses_a_fifo_before_fstat_is_reached(tmp_path):
    """Measured (stn-6mcb.7 spike): O_NONBLOCK refuses a FIFO with ENXIO at
    `os.open` itself, before fstat is ever reached -- so a FIFO planted at a
    destination cannot block `gen` forever waiting for a reader that will
    never come.

    The refusal now travels as a `ValueError` carrying the kernel's own
    `strerror`, like the two `fstat` refusals beside it, rather than as a
    raw `OSError` -- so this asserts on the CHAIN rather than on an errno
    the message no longer hardcodes. `__cause__` is where the ENXIO lives,
    and it is still ENXIO, which is the property this test is named for: had
    the open blocked instead, there would be no exception at all and this
    test would hang rather than fail.
    """
    target = tmp_path / "target"
    os.mkfifo(target)

    with pytest.raises(ValueError) as excinfo:
        generate.open_for_write_nofollow(target)

    cause = excinfo.value.__cause__
    assert isinstance(cause, OSError) and cause.errno == errno.ENXIO, (
        f"expected ENXIO from O_NONBLOCK under the ValueError, got: {cause!r}"
    )
    assert "refusing to write it" in str(excinfo.value)


def test_open_for_write_nofollow_still_replaces_a_longer_existing_file(tmp_path):
    """Regression guard for dropping O_TRUNC: `open_for_write_nofollow` now
    truncates via `os.ftruncate` AFTER the fstat gate approves the
    descriptor, rather than via O_TRUNC at open time. If that ftruncate
    were ever dropped or misordered, a shorter replacement would leave the
    old file's tail bytes behind."""
    target = tmp_path / "file.txt"
    target.write_text("a much longer line that must be fully replaced\n")

    generate.write_text_nofollow(target, "new\n")

    assert target.read_text() == "new\n"


# --- stn-avv: the fallback (capability flag forced False) -------------------
#
# `open_for_write_nofollow` and `write_text_nofollow` never consult
# `_DIR_FD_CAPABLE` themselves -- `dir_fd` is an explicit, caller-supplied
# keyword that defaults to None, which is exactly today's behaviour. The
# flag only gates whether a caller (stn-6mcb.5, not this task) may attempt
# the descriptor walk at all. These tests force the flag False -- something
# only possible because it is a module global read at CALL time rather than
# captured in a default argument -- to prove that forcing it has no effect
# on the code path every existing caller still uses.


def test_write_text_nofollow_ignores_the_capability_flag_on_the_default_path(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(generate, "_DIR_FD_CAPABLE", False)

    target = tmp_path / "file.txt"
    generate.write_text_nofollow(target, "hello\n")

    assert target.read_text() == "hello\n"


def test_walk_dir_fd_refuses_cleanly_when_the_capability_flag_is_forced_false(
    tmp_path, monkeypatch
):
    """Without this, the 'fallback still behaves as today' pin above could
    never actually run on a machine where dir_fd IS supported -- the flag
    would always read True and there would be no way to exercise the
    unsupported-platform path at all."""
    monkeypatch.setattr(generate, "_DIR_FD_CAPABLE", False)

    root = tmp_path / "root"
    root.mkdir()
    base_fd = os.open(root, os.O_RDONLY)
    try:
        with pytest.raises(NotImplementedError):
            generate.walk_dir_fd(base_fd, "a/b.txt")
    finally:
        os.close(base_fd)


# --- stn-6mcb.5: wiring the descriptor walk into gen -------------------------
#
# stn-6mcb.7 (above) is the MECHANISM: the capability flag, `walk_dir_fd`, and
# `dir_fd` on the two nofollow writers, exercised directly and with no call
# site changed. This section is the WIRING: `generate_package` now acquires a
# descriptor on the package directory with `_open_package_base_fd` and
# threads it through `render_templates`, `write_manifest` and
# `copy_brand_image`, so the object `checked_write_target`'s pre-pass looked
# at and the object those three functions write are the same inode by
# construction, below the package directory. Every test here calls
# `generate_package` directly (the same pattern as the stn-vhr direct-call
# tests above), not `run_cli`, because several of them need to reach in with
# `monkeypatch` at the exact moment between two writes.


def _direct_call_package(tmp_path, config_overrides):
    """Common setup for a direct ``generate_package`` call: a config
    directory with ``out/`` and the loaded config's env, exactly the shape
    ``test_generate_package_raises_valueerror_for_a_symlinked_package_dir``
    above already uses. Returns ``(env, loaded, output_base, config_dir)``.
    """
    from stencil.generate import build_environment, load_config

    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "out").mkdir()

    cfg = {"output_dir": "out", "packages": {"demo": {"name": "Demo", "package_type": "none"}}}
    cfg.update(config_overrides)
    config_path = write_config(config_dir, cfg)

    loaded = load_config(config_path)
    env = build_environment(loaded, config_dir)
    output_base = config_dir / "out"
    return env, loaded, output_base, config_dir


@pytestmark_avv
def test_a_symlink_swapped_between_two_nested_writes_is_refused(tmp_path, monkeypatch):
    """The ticket's own reproduction (stn-avv), made deterministic instead of
    raced. Two nested destinations share one intermediate directory, `sub`:
    `checked_write_target`'s pre-pass clears BOTH before anything exists, so
    before this task `render_templates` wrote `sub/a.txt` (creating a REAL
    `sub`), and replacing `sub` with a symlink to a victim directory between
    the two writes sent `sub/b.txt` into the victim at exit 0 -- with
    `write_manifest` then recording `sub/b.txt`, so a later `clean` would
    delete there too.

    The swap is forced right after the FIRST nested write returns --
    `write_text_nofollow` is wrapped to perform it -- so this fails on every
    run rather than only when a race wins. `sub`'s directory entry is
    replaced (not merely its target); the fd `render_templates` already
    closed after the first write is not reused.
    """
    from stencil.generate import generate_package

    env, loaded, output_base, config_dir = _direct_call_package(
        tmp_path,
        {
            "templates": [
                {"src": "Makefile.j2", "dest": "sub/a.txt"},
                {"src": "docker-compose.yml.j2", "dest": "sub/b.txt"},
            ],
        },
    )
    package_dir = output_base / "demo"
    victim = config_dir / "victim"
    victim.mkdir()

    real_write = generate.write_text_nofollow

    def swap_after_first_write(path, text, *args, **kwargs):
        real_write(path, text, *args, **kwargs)
        # Only "a.txt" -- the FIRST of the two nested writes -- triggers the
        # swap. Several top-level, injected files (lockfiles and the like)
        # write before this one; swapping on the very first call of any kind
        # would fire before "sub" even exists.
        if path.name == "a.txt":
            sub = package_dir / "sub"
            shutil.rmtree(sub)
            sub.symlink_to(victim)

    monkeypatch.setattr(generate, "write_text_nofollow", swap_after_first_write)

    with pytest.raises(ValueError, match="sub"):
        generate_package(env, loaded, output_base, "demo", False, config_dir)

    assert list(victim.iterdir()) == [], (
        "the second nested write must not land in the victim directory"
    )
    manifest_path = package_dir / MANIFEST_NAME
    assert not manifest_path.exists(), (
        "a refused render must not leave a manifest naming the escaping "
        "entry -- write_manifest runs only after render_templates returns"
    )


@pytestmark_avv
def test_an_ordinary_nested_dest_still_generates_through_the_descriptor_walk(
    tmp_path, monkeypatch
):
    """Regression guard, and proof the NEW wiring is what ran rather than a
    coincidence: a `.vscode/settings.json`-shaped nested destination must
    still generate correctly now that it is written by walking `base_fd`
    with `walk_dir_fd` instead of `output_path.parent.mkdir(parents=True)`.
    """
    from stencil.generate import generate_package

    env, loaded, output_base, config_dir = _direct_call_package(
        tmp_path, {"templates": [{"src": "Makefile.j2", "dest": "sub/Makefile"}]}
    )

    calls = []
    real_walk = generate.walk_dir_fd

    def spying_walk(base_fd, relative):
        calls.append(relative)
        return real_walk(base_fd, relative)

    monkeypatch.setattr(generate, "walk_dir_fd", spying_walk)

    result = generate_package(env, loaded, output_base, "demo", False, config_dir)

    assert result == output_base / "demo"
    assert (output_base / "demo" / "sub" / "Makefile").exists()
    assert "sub/Makefile" in calls, (
        "the nested write must go through walk_dir_fd -- if this list is "
        "empty the old path-based mkdir(parents=True) ran instead"
    )


@pytestmark_avv
def test_regenerating_over_an_existing_package_is_still_descriptor_protected(
    tmp_path, monkeypatch
):
    """The most common operation this tool performs, and the one place the
    wiring must NOT be skipped just because `output_dir` already exists:
    `_open_package_base_fd` is acquired unconditionally on every non-dry-run
    call, not only inside the branch that creates a brand-new directory."""
    from stencil.generate import generate_package

    env, loaded, output_base, config_dir = _direct_call_package(
        tmp_path, {"templates": [{"src": "Makefile.j2"}]}
    )

    generate_package(env, loaded, output_base, "demo", False, config_dir)
    assert (output_base / "demo" / "Makefile").exists(), "test setup: first run"

    calls = []
    real_open_base_fd = generate._open_package_base_fd

    def spying_open(*args, **kwargs):
        calls.append(args)
        return real_open_base_fd(*args, **kwargs)

    monkeypatch.setattr(generate, "_open_package_base_fd", spying_open)

    result = generate_package(env, loaded, output_base, "demo", False, config_dir)

    assert result == output_base / "demo"
    assert calls, (
        "the base fd must be acquired on regeneration too, not only when "
        "output_dir does not exist yet"
    )
    assert (output_base / "demo" / "Makefile").exists()


@pytestmark_avv
def test_the_package_base_fd_is_closed_when_render_templates_raises(
    tmp_path, monkeypatch
):
    """Without a `try/finally` around the descriptor, `gen --all` leaks one
    open fd per package the moment any single package fails to render --
    every package after the first failure in a long run, in the worst case.
    `render_templates` itself is replaced with something that always raises,
    so this is independent of WHY it might fail.
    """
    from stencil.generate import generate_package

    env, loaded, output_base, config_dir = _direct_call_package(
        tmp_path, {"templates": [{"src": "Makefile.j2"}]}
    )

    captured = {}
    real_open_base_fd = generate._open_package_base_fd

    def capturing_open(*args, **kwargs):
        fd = real_open_base_fd(*args, **kwargs)
        captured["fd"] = fd
        return fd

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(generate, "_open_package_base_fd", capturing_open)
    monkeypatch.setattr(generate, "render_templates", boom)

    with pytest.raises(RuntimeError, match="boom"):
        generate_package(env, loaded, output_base, "demo", False, config_dir)

    assert "fd" in captured, "test setup: the base fd must have been opened"
    with pytest.raises(OSError):
        os.fstat(captured["fd"])  # a closed fd raises EBADF -- an open one would not


def test_generate_package_falls_back_to_path_based_writes_when_dir_fd_is_unsupported(
    tmp_path, monkeypatch
):
    """Windows, or `_DIR_FD_CAPABLE` forced False on a capable platform for
    this test: `generate_package` must not acquire a base fd at all, and
    must keep generating exactly as it did before this task. Not marked
    `pytestmark_avv` -- forcing the flag makes this exercise the fallback on
    every platform, which is the whole reason the flag is a module global
    read at call time rather than captured anywhere.
    """
    from stencil.generate import generate_package

    monkeypatch.setattr(generate, "_DIR_FD_CAPABLE", False)

    env, loaded, output_base, config_dir = _direct_call_package(
        tmp_path, {"templates": [{"src": "Makefile.j2", "dest": "sub/Makefile"}]}
    )

    def refuses_if_called(*args, **kwargs):
        raise AssertionError("the fallback path must not acquire a base fd at all")

    monkeypatch.setattr(generate, "_open_package_base_fd", refuses_if_called)

    result = generate_package(env, loaded, output_base, "demo", False, config_dir)

    assert result == output_base / "demo"
    assert (output_base / "demo" / "sub" / "Makefile").exists()


@pytestmark_avv
def test_the_stale_manifest_delete_goes_through_the_base_fd(tmp_path, monkeypatch):
    """Operator ruling: `manifest_path.unlink()` was the one path-based
    delete left in the middle of a function everything else in this task
    converts to descriptors. If `output_dir` were swapped for a symlink to a
    victim directory right after `base_fd` is opened, a path-based unlink
    here would delete the VICTIM's manifest -- a delete OUTSIDE the tree,
    performed by the very run meant to make `gen` descriptor-safe.

    The swap happens inside a wrapped `_open_package_base_fd`, immediately
    after the real one returns: `package_dir`'s real directory entry (with
    its stale manifest already inside) is renamed aside, pinned by the fd
    that is already open, and `package_dir`'s NAME is replaced with a
    symlink to a victim directory that carries its own, different,
    manifest.
    """
    from stencil.generate import generate_package

    env, loaded, output_base, config_dir = _direct_call_package(
        tmp_path, {"templates": [{"src": "Makefile.j2"}]}
    )
    package_dir = output_base / "demo"
    package_dir.mkdir(parents=True)
    (package_dir / MANIFEST_NAME).write_text('{"kept": "real"}\n')

    victim = config_dir / "victim"
    victim.mkdir()
    (victim / MANIFEST_NAME).write_text('{"kept": "victim"}\n')

    renamed = tmp_path / "demo-renamed"
    real_open_base_fd = generate._open_package_base_fd

    def swap_after_open(*args, **kwargs):
        fd = real_open_base_fd(*args, **kwargs)
        package_dir.rename(renamed)
        package_dir.symlink_to(victim)
        return fd

    monkeypatch.setattr(generate, "_open_package_base_fd", swap_after_open)

    generate_package(env, loaded, output_base, "demo", False, config_dir)

    new_manifest = json.loads((renamed / MANIFEST_NAME).read_text())
    assert new_manifest["package"] == "demo", (
        "the manifest in the PINNED original directory must be the one "
        "this run rewrote, not left as the stale one"
    )
    assert (renamed / "Makefile").exists(), (
        "the render must land in the pinned original directory, not "
        "through the swapped symlink"
    )
    assert json.loads((victim / MANIFEST_NAME).read_text())["kept"] == "victim", (
        "the victim's manifest must be untouched -- a path-based "
        "manifest_path.unlink() here would have deleted it instead of the "
        "stale one in the real directory"
    )
    assert not (victim / "Makefile").exists(), (
        "nothing may be written into the victim directory"
    )


@pytestmark_avv
def test_a_symlinked_ancestor_of_the_output_base_is_not_caught(tmp_path):
    """THE STATED RESIDUAL (STENCIL.md), pinned so the statement stays
    honest rather than aspirational. `_open_package_base_fd` starts its
    descriptor walk FROM `output_base` -- everything below it, including
    every component of a multi-part package `dir`, is now protected.
    `output_base` ITSELF, and everything ABOVE it, is still resolved by the
    kernel's ordinary path lookup at the single `os.open(output_base, ...)`
    call: there is no descriptor to start a walk from further up, because
    `output_base` IS the start. `O_NOFOLLOW` on that call only ever governs
    the LAST name in the path it is given; an ANCESTOR component is
    resolved by the kernel exactly as any ordinary path resolves one,
    symlink and all.

    A DIRECT UNIT TEST of `_open_package_base_fd`, deliberately not routed
    through `generate_package`/`checked_write_target`: that pre-pass has its
    own, unrelated containment check (`contained_entry_parent`) which
    compares a resolved write target against the CALLER'S OWN, unresolved
    `output_base` string -- so it refuses this exact setup for a reason that
    has nothing to do with the fd wiring under test here, before
    `_open_package_base_fd` is ever reached. Calling the helper directly is
    what isolates the one property this test is pinning.
    """
    real_mid = tmp_path / "real_mid"
    (real_mid / "out").mkdir(parents=True)
    mid_link = tmp_path / "mid"
    mid_link.symlink_to(real_mid)
    # output_base's path runs THROUGH the symlinked ancestor "mid" -- not
    # at "mid" itself, and not below "out".
    output_base = tmp_path / "mid" / "out"

    victim = tmp_path / "victim"
    (victim / "out").mkdir(parents=True)

    fd = generate._open_package_base_fd(output_base, "demo", "demo")
    os.close(fd)
    assert (real_mid / "out" / "demo").is_dir(), "test setup: first open"

    # Swap the ANCESTOR "mid" for a symlink to a victim tree. `output_base`
    # is the same Path/string as before; only what "mid" resolves to on
    # disk has changed.
    mid_link.unlink()
    mid_link.symlink_to(victim)

    fd = generate._open_package_base_fd(output_base, "demo", "demo")
    os.close(fd)

    # NOT refused -- this is the residual. The second call opened a
    # descriptor inside the victim tree, through the swapped ancestor,
    # which is exactly the outcome the walk below `output_base` exists to
    # prevent for anything AT OR BELOW it. This swap is ABOVE
    # `output_base`, which the walk cannot see.
    assert (victim / "out" / "demo").is_dir(), (
        "the residual test setup is wrong if this does not hold -- the "
        "point being pinned is that this second call is NOT refused"
    )


@pytest.mark.skipif(
    not generate._DIR_FD_CAPABLE,
    reason="no O_DIRECTORY/O_NOFOLLOW dir_fd support on this platform",
)
def test_the_package_directorys_own_name_is_followed_and_that_is_the_second_residual(
    tmp_path,
):
    """The package directory's OWN final component is opened WITHOUT
    `O_NOFOLLOW`, so a symlink there is followed. Pinned because STENCIL.md
    states it as a limit, and a limit no test holds is a sentence that goes
    stale the first time someone tightens the code around it.

    THIS IS NOT AN OVERSIGHT, and the same line of code is two things at
    once. `contained_path` has always PERMITTED the package directory itself
    to be a symlink, so long as it resolves back under the output base --
    `test_a_package_dir_symlinked_inside_the_output_tree_still_generates`
    depends on exactly that, and adding `O_NOFOLLOW` to this one open would
    reverse a deliberate decision from stn-vhr rather than close a gap.

    What it costs is stated here rather than implied away: for that ONE
    component, containment rests on `contained_path`'s earlier resolve and
    not on the open, so a swap between the two is not caught. Every
    component BELOW the package directory is descriptor-walked and is not
    exposed this way, which is what stn-avv actually closed. The other
    residual -- a swapped ancestor of the output base -- is pinned just
    above; these are the only two left on the write side, and the delete
    side is stn-cfby.
    """
    output_base = tmp_path / "out"
    output_base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    # The package directory IS a symlink at the moment the fd is opened.
    (output_base / "demo").symlink_to(outside)

    base_fd = generate._open_package_base_fd(output_base, "demo", "demo")
    try:
        generate.write_text_nofollow(Path("probe.txt"), "followed\n", dir_fd=base_fd)
    finally:
        os.close(base_fd)

    # NOT refused -- the residual. The bytes landed through the link.
    assert (outside / "probe.txt").read_text() == "followed\n", (
        "the residual test setup is wrong if this does not hold -- the "
        "package directory's own name is meant to be followed here"
    )
    assert not (output_base / "demo").is_dir() or (output_base / "demo").is_symlink()


# --- stn-bux: a make file of higher precedence beside the generated one ----
#
# GNU Make prefers GNUmakefile, then makefile, then Makefile (in that exact
# order) when no makefile is named on the command line. A GNUmakefile
# planted beside a generated Makefile REPLACES it entirely for every `make`
# invocation that follows, silently -- measured, on the architecture
# review's reproduction: `make format-md` printed `SHADOW-WINS uid=501`
# with the generated Makefile untouched and unread on disk. Nothing inside
# the generated Makefile's own contents can defend against this, because
# `make` never opens it, so the mitigation is a refusal at `gen` time.


def _is_case_insensitive_fs(directory: Path) -> bool:
    """Detect rather than assume (stn-6mcb.4's amendment): write a name and
    ask the filesystem about its upper-cased spelling. True on this
    checkout's macOS APFS volume and expected on Windows; False on ext4 and
    most Linux CI, which is exactly why this is a runtime probe and not a
    `sys.platform` guard -- `sys.platform` cannot see a case-sensitive
    volume mounted on a Mac, or the reverse."""
    probe = directory / "stn-bux-case-probe.txt"
    probe.write_text("x")
    return probe.with_name(probe.name.upper()).exists()


def test_gen_refuses_a_gnumakefile_of_higher_precedence(tmp_path):
    """The reproduction itself. `GNUmakefile` is the highest name in make's
    own precedence and coexists with `Makefile` as a distinct directory
    entry on every filesystem, case-sensitive or not (measured: `listdir`
    shows both), so this test needs no skip anywhere."""
    config_dir, package_dir, _target = _planted_package(tmp_path)
    (package_dir / "GNUmakefile").write_text("SHADOW\n")

    result = run_cli("gen", "demo", cwd=config_dir)

    assert result.returncode != 0, (
        f"rc={result.returncode}, stdout={result.stdout[:400]!r}"
    )
    assert "Traceback" not in result.stderr, result.stderr
    assert "GNUmakefile" in result.stderr, result.stderr


def test_the_refusal_happens_before_anything_is_written_on_a_fresh_output_directory(
    tmp_path,
):
    """The stn-h5q guarantee extended to this refusal: a fresh package
    directory -- gen has never succeeded here -- must come out of a refused
    run holding exactly what was planted, nothing more. Without this, `gen`
    could refuse the Makefile it is not allowed to write while still
    rendering the other templates and the manifest around it -- a
    half-generated package that looks generated."""
    config_dir, package_dir, _target = _planted_package(
        tmp_path,
        templates=[
            {"src": "Makefile.j2"},
            {"src": "docker-compose.yml.j2"},
        ],
    )
    (package_dir / "GNUmakefile").write_text("SHADOW\n")
    before = sorted(p.name for p in package_dir.iterdir())

    result = run_cli("gen", "demo", cwd=config_dir)

    assert result.returncode != 0, (
        f"rc={result.returncode}, stdout={result.stdout[:400]!r}"
    )
    after = sorted(p.name for p in package_dir.iterdir())
    assert after == before, (
        f"a refused run changed the package directory: before={before}, "
        f"after={after}"
    )


def test_a_config_declaring_dest_gnumakefile_is_not_refused_by_its_own_output(
    tmp_path,
):
    """GENERALISED, not hardcoded to `Makefile`: a consumer that declares
    `dest: GNUmakefile` writes the HIGHEST-precedence name there is, so
    nothing can ever shadow it and regenerating over its own prior output
    must not be refused by that very output."""
    write_config(
        tmp_path,
        {
            "output_dir": "out",
            "templates": [{"src": "Makefile.j2", "dest": "GNUmakefile"}],
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        },
    )

    first = run_cli("gen", "demo", cwd=tmp_path)
    assert first.returncode == 0, first.stderr
    assert (tmp_path / "out" / "demo" / "GNUmakefile").exists()

    second = run_cli("gen", "demo", cwd=tmp_path)
    assert second.returncode == 0, (
        "a package's own previously-generated GNUmakefile must not shadow "
        f"itself on regeneration: {second.stderr!r}"
    )


def test_a_package_that_writes_no_makefile_is_not_refused_by_a_gnumakefile(
    tmp_path,
):
    """A GNUmakefile beside a package that never writes any make-file name
    at all is none of this check's business -- there is nothing here for
    `make` to run instead of, so nothing is shadowed."""
    config_dir, package_dir, _target = _planted_package(
        tmp_path, templates=[{"src": "docker-compose.yml.j2"}]
    )
    (package_dir / "GNUmakefile").write_text("unrelated\n")

    result = run_cli("gen", "demo", cwd=config_dir)

    assert result.returncode == 0, result.stderr
    assert (package_dir / "docker-compose.yml").exists()


def test_gen_refuses_a_lowercase_makefile_of_higher_precedence(tmp_path):
    """`makefile` (all-lowercase) sits between `GNUmakefile` and `Makefile`
    in make's precedence, so it shadows a generated `Makefile` exactly the
    way `GNUmakefile` does.

    THIS TEST CANNOT BE CONSTRUCTED on a case-insensitive filesystem
    (stn-6mcb.4's amendment): `makefile` and `Makefile` are ONE directory
    entry there -- measured, writing `makefile` beside an existing
    `Makefile` overwrites it in place and `os.listdir` still shows one
    name. The fixture would silently collapse into the same-inode case
    covered separately below, so this skips with the reason rather than
    asserting on a setup that is not what it claims to be."""
    config_dir, package_dir, _target = _planted_package(tmp_path)
    if _is_case_insensitive_fs(package_dir):
        pytest.skip(
            "case-insensitive filesystem: 'makefile' and 'Makefile' are "
            "one directory entry here, so this fixture cannot be built "
            "(stn-6mcb.4 amendment)"
        )
    (package_dir / "makefile").write_text("SHADOW\n")

    result = run_cli("gen", "demo", cwd=config_dir)

    assert result.returncode != 0, (
        f"rc={result.returncode}, stdout={result.stdout[:400]!r}"
    )
    assert "Traceback" not in result.stderr, result.stderr
    assert "makefile" in result.stderr, result.stderr


def test_a_same_inode_makefile_entry_does_not_false_refuse(tmp_path):
    """The amendment's central point: on a case-insensitive filesystem the
    directory entry for the file this call is about to write as `Makefile`
    can be STORED as `makefile` -- one inode, one entry, either case reads
    it back. That is a higher-precedence NAME in the listing that is in
    fact stencil's own file, and refusing it would break regeneration for
    every package on such a filesystem.

    Skips on a case-sensitive filesystem, where renaming `Makefile` to
    `makefile` leaves a GENUINE second name with nothing at `Makefile`
    beneath it -- a real shadow, not the false positive this test is
    about."""
    config_dir, package_dir, _target = _planted_package(tmp_path)
    if not _is_case_insensitive_fs(package_dir):
        pytest.skip(
            "requires a case-insensitive filesystem, where 'makefile' and "
            "'Makefile' collapse to one directory entry (stn-6mcb.4 "
            "amendment); on a case-sensitive one this setup is a genuine "
            "shadow rather than the false positive under test"
        )

    first = run_cli("gen", "demo", cwd=config_dir)
    assert first.returncode == 0, first.stderr

    # Case-only rename: still one inode, now stored lower-case -- measured
    # with os.path.samefile() returning True for this exact pair.
    os.rename(package_dir / "Makefile", package_dir / "makefile")
    entries = os.listdir(package_dir)
    assert "makefile" in entries and "Makefile" not in entries, (
        f"test setup: {entries}"
    )

    second = run_cli("gen", "demo", cwd=config_dir)
    assert second.returncode == 0, (
        "regenerating over its own file, spelled in a different case, "
        f"must not be refused: {second.stderr!r}"
    )


def test_gen_dry_run_refuses_a_gnumakefile_of_higher_precedence(tmp_path):
    """`--dry-run` runs the same pre-pass and nothing else (stn-h5q), so a
    preview must report the same refusal a real run would rather than
    printing a `Would write: out/demo/Makefile` line for a write `gen`
    could never actually make."""
    config_dir, package_dir, _target = _planted_package(tmp_path)
    (package_dir / "GNUmakefile").write_text("SHADOW\n")

    result = run_cli("gen", "demo", "--dry-run", cwd=config_dir)

    assert result.returncode != 0, (
        f"rc={result.returncode}, stdout={result.stdout[:400]!r}"
    )
    assert "GNUmakefile" in result.stderr, result.stderr
    assert not (package_dir / "Makefile").exists()


def test_an_ordinary_package_with_only_the_generated_makefile_still_generates(
    tmp_path,
):
    """The regression guard: an ORDINARY package that writes only `Makefile`
    and nothing of higher precedence must keep generating, on this
    checkout's case-insensitive filesystem included -- the same platform
    the same-inode guard above exists for."""
    config_dir, package_dir, _target = _planted_package(tmp_path)

    result = run_cli("gen", "demo", cwd=config_dir)

    assert result.returncode == 0, result.stderr
    assert (package_dir / "Makefile").exists()


def test_a_gnumakefile_symlinked_to_the_makefile_is_not_refused_on_a_fresh_checkout(
    tmp_path,
):
    """`GNUmakefile -> Makefile` is a self-alias, not a shadow, and must be
    accepted whether or not `Makefile` is on disk when `gen` runs.

    Measured: `os.path.samefile` raises FileNotFoundError when the target
    does not exist yet, so before the `readlink` fallback this alias was
    ACCEPTED on a regenerate and REFUSED on a fresh checkout or the run
    after `stencil clean` -- a refusal that depended on whether the
    directory happened to have been cleaned. Measured on GNU Make 3.81:
    such an alias runs the real Makefile's recipes, so refusing it protects
    nothing.
    """
    config = {
        "output_dir": "out",
        "templates": [{"src": "Makefile.j2"}],
        "packages": {"demo": {"name": "Demo", "package_type": "none"}},
    }
    write_config(tmp_path, config)

    package = tmp_path / "out" / "demo"
    package.mkdir(parents=True)
    # The alias exists; its target does NOT yet -- the fresh-checkout shape.
    (package / "GNUmakefile").symlink_to("Makefile")
    assert not (package / "Makefile").exists(), "setup: target must be absent"

    result = run_cli("gen", "demo", cwd=tmp_path)

    assert result.returncode == 0, (
        f"a self-alias must not be refused just because its target is not "
        f"on disk yet: {result.stderr!r}"
    )
    assert (package / "Makefile").exists()


def test_gen_is_the_only_command_that_executes_anything():
    """stn-axi. Pins the half of this module's docstring that must stay true.

    The claim being defended is narrow and precise: `generate.py` contains no
    process-spawning surface at all, so `clean`, `install`, `list` and `version`
    execute nothing from the config, and the ONE execution surface `gen` has is
    `render_templates`' `template.render()`.

    SCANNED BY AST, not by grepping the text. A text scan for "subprocess" is
    green today while the render hole is wide open, and it would fail the day
    someone writes the word in a comment -- in a file whose comments discuss
    exactly this subject at length, including the stn-vd6v comment a few hundred
    lines away that explains why a `git config` subprocess was NOT added.

    `pipeline.py` is deliberately exempt and that is not a gap. Its one
    `subprocess.run` is `compose_command`'s probe for a compose implementation,
    which takes no config value and which `generate.py` never calls -- grep the
    callers. Do not "strengthen" this to the whole package; it will fail
    immediately, on the wrong thing, for the wrong reason.
    """
    import ast

    source = (Path(generate.__file__)).read_text(encoding="utf-8")
    tree = ast.parse(source)

    spawning_modules = {"subprocess", "runpy", "ctypes", "multiprocessing", "pty"}
    spawning_os_calls = {
        "system",
        "popen",
        "execv",
        "execve",
        "execl",
        "execlp",
        "execvp",
        "spawnv",
        "spawnve",
        "spawnl",
        "posix_spawn",
        "posix_spawnp",
        "fork",
        "forkpty",
    }

    imported = set()
    os_called = set()
    rendered_at = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if (
                    isinstance(func.value, ast.Name)
                    and func.value.id == "os"
                    and func.attr in spawning_os_calls
                ):
                    os_called.add(func.attr)
                if func.attr in {"render", "get_template"}:
                    rendered_at.append(node.lineno)

    assert not (imported & spawning_modules), (
        f"generate.py now imports {sorted(imported & spawning_modules)}. The "
        "module docstring's claim that clean/install/list/version execute "
        "nothing rests on this file having no spawning surface -- update the "
        "docstring deliberately, or drop the import."
    )
    assert not os_called, (
        f"generate.py now calls os.{sorted(os_called)}, so the docstring's "
        "claim is no longer true as written."
    )

    # The render surface is expected, and expected to stay in ONE function, so
    # "gen executes templates" does not quietly become "several things do".
    assert rendered_at, (
        "no .render()/.get_template() call found at all -- if template "
        "rendering moved, this test and the module docstring both need updating"
    )
    enclosing = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            span = range(node.lineno, (node.end_lineno or node.lineno) + 1)
            if any(line in span for line in rendered_at):
                enclosing.add(node.name)
    assert enclosing == {"render_templates"}, (
        f"templates are now rendered from {sorted(enclosing)}, not only from "
        "render_templates. Every one of those is a code-execution surface "
        "reachable from whichever command calls it -- update this module's "
        "docstring to say so before widening this assertion."
    )
