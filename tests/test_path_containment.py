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
