#!/usr/bin/env python3
"""
Generate package scaffolding from Jinja2 templates.

Usage:
    stencil [--config <path>] gen [--all] [pkg]   # Generate (default config: .config.yaml)
    stencil [--config <path>] clean [--all] [pkg]
    stencil [--config <path>] install
    stencil [--config <path>] list
    stencil help [COMMAND]                        # Same as -h / --help; optional COMMAND for subcommand help
    stencil version                               # Print the installed version
"""

import argparse
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path
from typing import NoReturn

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, Undefined, meta, nodes
from jinja2.exceptions import TemplateNotFound

from . import __version__, assets, pipeline

# Script directory
SCRIPT_DIR = Path(__file__).parent

# Gitignore markers
GITIGNORE_START = "# >>> stencil >>>"
GITIGNORE_END = "# <<< stencil <<<"

# The per-package manifest gen writes at the end of a successful
# generate_package (stn-2x4). MANIFEST_VERSION is an integer so a later,
# hardened reader (stn-2x4.4) can refuse a manifest whose version it does
# not recognize by name rather than guess at its shape.
MANIFEST_NAME = ".stencil-manifest.json"
MANIFEST_VERSION = 1


def load_config(config_path: Path) -> dict:
    """Load and parse the configuration file.

    Every failure here is a mistake the person at the terminal made -- running
    outside a configured project, or mistyping --config -- so each one exits
    with a line naming the file rather than a traceback through yaml.
    """
    example = SCRIPT_DIR / "config.example.yaml"

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        sys.exit(
            f"Error: no config file at {config_path}\n"
            f"Run stencil from a directory containing .config.yaml, or pass "
            f"--config <path>. See {example} for the format."
        )
    except IsADirectoryError:
        sys.exit(f"Error: {config_path} is a directory, not a config file")
    except yaml.YAMLError as e:
        sys.exit(f"Error: {config_path} is not valid YAML\n{e}")

    if config is None:
        sys.exit(f"Error: {config_path} is empty\nSee {example} for the format.")
    if not isinstance(config, dict):
        sys.exit(
            f"Error: {config_path} must be a mapping of settings, "
            f"not {type(config).__name__}"
        )

    return config


# What a `brand` value has to end in to be a picture rather than a name.
# Deliberately the same set frontmatter-filter.lua's image_target uses: a
# config default that classified differently from front matter would mean
# .config.yaml and a document disagreeing about what the same string means.
BRAND_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif"}


def brand_of(
    package: dict, config: dict, package_id: str = ""
) -> tuple[str | None, str | None]:
    """The brand and its alt text for a package: its own, else config-wide.

    Taken as a pair rather than resolved key by key. A package that sets its
    own brand and no alt means "this logo, no alt yet" -- inheriting the
    config's alt there would silently label one logo with another's name.
    """
    if package.get("brand") is not None:
        value, where = package.get("brand"), f"package {package_id!r}"
        alt = package.get("brand-alt")
    else:
        value, where = config.get("brand"), "config"
        alt = config.get("brand-alt")

    # Typed HERE rather than at either call site, because this is the one
    # place both of them read the key, and because the alternative is where
    # it used to blow up: brand_image_path calls value.startswith, so an
    # unquoted `brand: 2024` raised AttributeError from inside the
    # per-package context build. The pre-flight then reported that against
    # every package inheriting the config-level brand -- three innocent
    # files named, and the word `brand` nowhere in the message. Raising
    # ValueError with the SCOPE in it puts the mistake on the same channel
    # as every other config error, so it dedups to one line naming `config`.
    if value is not None and not isinstance(value, str):
        raise ValueError(
            f"{where}: brand must be a string -- a name, or a path to a "
            f"logo -- not {type(value).__name__}. Quote it if it is meant "
            f"to be a name that looks like a number."
        )

    return value, alt


def checked_brand_image(value: str | None, package_id: str) -> str | None:
    """brand_image_path, with the path actually checked (stn-ttg).

    Every other configured path goes through check_config_path; this one did
    not, and it is the only one that gets OPENED. copy_brand_image resolves it
    and hands it to shutil.copyfile, which follows a symlink -- so a `logo.png`
    pointing at any readable file copied that file's CONTENT into a generated
    folder AGENTS.md describes as routinely handed to someone as a project of
    their own. Measured in the ticket: an absolute `relative` makes
    `(config_dir / relative).resolve()` the absolute path, and `file://` is
    stripped before any of this, so the scheme was not a defence either.

    This checks the CONFIG STRING, and brand_problem separately contains the
    RESOLVED path. Both are needed: the string is what `gen`, `clean` and the
    managed .gitignore section all name the copy from (stn-8wt), so it has to
    be a plain relative filename; and a plain relative filename can still be
    a symlink whose target is anywhere readable, which only the resolved path
    can see.
    """
    relative = brand_image_path(value)
    if relative is None:
        return None
    return check_config_path(package_id, "brand", relative)


def brand_image_path(value: str | None) -> str | None:
    """The local file a brand points at, or None when it is a name or remote.

    `file://` is stripped for the reason frontmatter-filter.lua strips it:
    embed-images.lua treats any scheme:// as remote and would leave it alone.
    A genuinely remote URL returns None -- there is nothing local to copy, and
    a generated folder that depends on the network is the thing this copying
    exists to avoid.
    """
    if not value:
        return None
    if value.startswith("file://"):
        return value[len("file://") :]
    if value.startswith("data:") or re.match(r"^[a-zA-Z][\w+.-]*://", value):
        return None
    return value if Path(value).suffix.lower() in BRAND_IMAGE_SUFFIXES else None


# A path a generated Makefile interpolates must not be able to act like a
# command. docs, slides, pre_build.outputs and pre_build.inputs are all
# DECLARATIVE -- an author writing a filename does not expect it to execute --
# and all four land in Make text that /bin/sh then parses.
#
# NOT A PRIVILEGE BOUNDARY, and worth saying plainly: pre_build's `run` is
# arbitrary command execution by design, so anyone who can edit .config.yaml
# can already run anything. What this prevents is a filename quietly doing
# something other than naming a file -- a space splitting one argument into
# two, a `;` running a second command, a `..` escaping the package.
#
# Globs stay: * ? [ ] are the point of a `package_sources` pattern, which is
# documented and used (`md/*.md`). That is why this regex was NOT widened to
# cover them when the glob vocabulary below was tightened -- widening it here
# would refuse a working feature to close a manifest-entry hole that
# `check_glob_vocabulary` already closes at the only place an entry is
# expanded. `check_no_glob` is the narrow, opt-in refusal for the config keys
# where a pattern is meaningless instead.
_UNSAFE_IN_PATH = re.compile(r"[$`;|&<>\\\n]")

# Every character `Path.glob` gives a meaning other than "itself". Used two
# ways: `check_no_glob` refuses the whole class in a config value that names
# ONE file, and `check_glob_vocabulary` bounds where it may appear in a
# manifest entry.
_GLOB_IN_PATH = re.compile(r"[*?\[\]]")

# The ONLY shape a manifest entry's final component may have once it carries
# a metacharacter at all: a non-empty literal prefix, exactly one '*', and
# no '?' or bracket anywhere. This is what `package_entries` emits --
# `Guide*.html`, `Guide*.pdf` -- and nothing else.
_GLOB_SHAPE = re.compile(r"[^*?\[\]]+\*[^*?\[\]]*")

# The C0 controls and DEL, checked LAST so the two more specific messages keep
# the characters they already explain: `\n` stays a Make metacharacter and a
# tab stays whitespace, because "Make would split it into two arguments" says
# more than "that is a control character" does. What is left is the set nothing
# covered -- NUL, BEL, ESC, DEL and friends. ESC is the one with a demonstrable
# effect, since it is how a filename repaints the stderr line reporting it, but
# the class is what is refused rather than any one member.
_CONTROL_IN_PATH = re.compile(r"[\x00-\x1f\x7f]")


def show_download_default(package: dict, config: dict, package_id: str = "") -> bool:
    """Whether a package's pages carry the download button unless a
    document says otherwise. Package first, then config-wide, then on.

    Membership tests, not `or`, and that is the whole reason this is not a
    one-liner like `lang` above. `lang` can fall back with
    `package.get('lang') or config.get('lang') or 'en'` because nothing on
    its chain is a meaningful False; this key's whole point is that False is
    the one spelling a package or config has to opt out with, and
    `package.get('show_download') or config.get(...)` would read that False
    as "unset" and fall through to the wider scope -- exactly backwards.
    """
    for where, scope in ((f"package {package_id!r}", package), ("config", config)):
        if "show_download" in scope:
            value = scope["show_download"]
            if value is None:
                # `show_download:` with nothing after it. AUTHORING.md's rule
                # for front matter is that a blank key is the same as an
                # absent one, and the front-matter half of this very feature
                # follows it, so the config half agrees rather than raising on
                # a spelling that means "I have not decided" in one file and
                # "this is broken" in the other. Fall through to the wider
                # scope, and then to the default.
                continue
            if not isinstance(value, bool):
                # Naming WHERE it came from, the way check_config_path below
                # does. A config with a dozen packages otherwise sends the
                # author looking through all of them for a key that is set in
                # one -- or at the top level, which is not a package at all.
                raise ValueError(
                    f"{where}: show_download must be true or false, not "
                    f"{value!r}. .config.yaml is read as YAML 1.1, so an "
                    "unquoted no/off/false is already a real boolean -- a "
                    "value that is neither means it was quoted, or is a "
                    "number. Write show_download: true or "
                    "show_download: false, unquoted."
                )
            return value
    return True


def check_config_path(
    package_id: str, where: str, value, *, allow_parent: bool = False
) -> str:
    """Refuse a configured path that would not behave like a filename.

    `allow_parent` keeps every clause except the `..` refusal, for the one
    key whose escape is a documented feature rather than a mistake: a
    package-level `output_dir` (stn-1a4). Keyword-only and defaulting to
    False, so all six existing callers are byte-identical -- the escape has
    to be asked for by name, at the one call site entitled to it.
    """
    text = str(value)
    if _UNSAFE_IN_PATH.search(text):
        raise ValueError(
            f"Package {package_id}: {where} {text!r} contains a shell or Make "
            "metacharacter. These are filenames, not commands."
        )
    # Any whitespace, not just a space: Make splits a recipe word on tabs too,
    # and a tab is invisible in a config file.
    if any(c.isspace() for c in text):
        raise ValueError(
            f"Package {package_id}: {where} {text!r} contains whitespace. The "
            "generated Make recipe would split it into two arguments."
        )
    if _CONTROL_IN_PATH.search(text):
        raise ValueError(
            f"Package {package_id}: {where} {text!r} contains a control "
            "character. These are filenames, not terminal escapes. "
            "(repr'd above, so the escape cannot repaint this line.)"
        )
    # ~ is expanded by the shell, not by Make, so Path() does not see it as
    # absolute -- `~/x.md` reaches sh and becomes a path in $HOME, outside the
    # package entirely.
    if text.startswith("~"):
        raise ValueError(
            f"Package {package_id}: {where} {text!r} starts with '~', which "
            "the shell expands to a path outside the package."
        )
    if Path(text).is_absolute():
        raise ValueError(
            f"Package {package_id}: {where} {text!r} is absolute. Paths are "
            "relative to the package directory."
        )
    if not allow_parent and ".." in Path(text).parts:
        raise ValueError(
            f"Package {package_id}: {where} {text!r} escapes the package "
            "directory. Paths are relative to it and must stay inside."
        )
    return text


def check_output_dir(config: dict) -> str | None:
    """The top-level ``output_dir``'s shape check (stn-40a) and path check
    (stn-pe3) -- the third member of the set ``dir`` (stn-vhm) and ``dest``
    (stn-c25) belong to, and the only one still exempt from every path
    check.

    ORDER MATTERS, and this is deliberately not the obvious
    ``if not value: return None`` first: ``output_dir: 0``, ``false`` and
    ``[]`` are FALSY NON-STRINGS, and checking falsiness before checking the
    type would swallow all three into "output base is the config
    directory" -- the exact silent mis-read stn-40a exists to close. So the
    type check runs first and refuses them by naming the type; only THEN
    does an empty string fall through to today's behaviour.
    """
    value = config.get("output_dir")
    if value is None:
        return None
    if not isinstance(value, str):
        # Same message shape as the `dest` check just above in
        # package_contexts: "Package config: <key> <value> is <type>, not a
        # string."
        raise ValueError(
            f"Package config: output_dir {value!r} is "
            f"{type(value).__name__}, not a string. output_dir names the "
            "directory everything else is generated under."
        )
    if not value:
        # "" is what an empty-but-present key has always meant: the output
        # base is the config directory. Preserved rather than refused.
        return None
    try:
        text = check_config_path("config", "output_dir", value)
    except ValueError as error:
        # check_config_path's messages all end "escapes the package
        # directory. Paths are relative to it and must stay inside." --
        # wrong here twice over: there is no package, and this key is
        # relative to the CONFIG FILE's directory (see the comment at
        # _main's output_base computation). One sentence appended rather
        # than parameterising check_config_path itself, which would touch
        # dir, dest, docs, slides, package_sources and pre_build and their
        # tests -- outside this change's boundary.
        #
        # The appended sentence names no function. It is read by someone
        # editing YAML, who has no reason to know which internal check
        # produced the half above it -- the same argument package_contexts
        # makes for keeping Python class names out of config messages. So
        # it reads as a correction of the borrowed sentence instead.
        raise ValueError(
            f"{error} (There is no package here: the top-level output_dir "
            "is relative to the config file's directory and must stay "
            "under it. A package-level output_dir is the supported way to "
            "send a package's build products somewhere else.)"
        ) from error
    # OUTSIDE the wrapper above, deliberately. That sentence corrects
    # check_config_path's borrowed "escapes the package directory" ending
    # and has nothing to say about a leading `!` or a glob -- appended to
    # one of those it would answer a question the reader did not ask, and
    # push the part they need closer to `_safe`'s truncation limit.
    return check_gitignore_literal("config", "output_dir", text)


def check_package_dir(package_id: str, value) -> str:
    """`dir`'s string check: `check_config_path`, plus a refusal of a value
    that names no directory at all (stn-17h).

    `contained_path` is what actually holds this rule, because it sees the
    RESOLVED path and therefore catches a symlinked `dir` as well as a
    literal `"."`. This is the readable half: `Path(".").parts`,
    `Path("").parts` and `Path("./").parts` are all empty, so all three
    reach `output_base / value` as the output base itself, and reporting
    that as "resolves to /some/abs/path, which IS the output directory"
    asks the author to map an absolute path back to the two characters they
    typed. Refused here by name instead, in the pre-flight that lists every
    config problem at once.
    """
    text = check_gitignore_literal(
        package_id, "dir", check_config_path(package_id, "dir", value)
    )
    if not Path(text).parts:
        raise ValueError(
            f"Package {package_id}: dir {text!r} names no directory, so the "
            "package directory would be the output directory itself. A "
            "package needs its own directory -- `clean` resolves every path "
            "it removes under this one."
        )
    return text


# A gitignore line is a PATTERN, not a path, and two characters change what
# the whole line MEANS when they lead it: `!` negates (re-includes a path the
# author's own rules ignore) and `#` comments the line out. `dir` and the
# top-level `output_dir` are the two config values that become the leading
# segment of every line in the managed section, so they are the two that can
# do it.
#
# MEASURED, against a course-handout repository whose own .gitignore says
# `*.pdf`, with `dir: "!solutions"`:
#
#   $ stencil install
#   $ git check-ignore -v -- solutions/answers.pdf
#   .gitignore:7:!solutions/answers*.pdf    solutions/answers.pdf
#   $ git status --porcelain
#   ?? solutions/
#
# `stencil install` -- the command whose entire purpose is to stop generated
# files being committed -- UN-IGNORED the answer key and made it committable,
# silently. That is the worst outcome available in this tool's problem
# domain, and it is why the refusal is here rather than an escape (`\!` is
# the documented gitignore escape and would work, but a `dir` beginning with
# `!` is not a directory name anyone means).
_GITIGNORE_LEADING = {"!": "negates the line, re-including files git would "
                      "otherwise ignore", "#": "comments the line out"}


def check_gitignore_literal(package_id: str, where: str, value: str) -> str:
    """Refuse a value that would not behave like a literal path in the
    managed `.gitignore` section (found by the adversarial review of
    stn-jl3).

    Applied to `dir` and the top-level `output_dir` only -- the two values
    that lead a managed line. A metacharacter further along the line is
    literal to git, so `docs` and the rest need nothing here; the glob
    refusal below is the exception, because `*` matches anywhere in the
    pattern and would widen what the section ignores rather than narrow it.

    The glob message is spelled here rather than borrowed from
    `check_no_glob`: that one explains itself in terms of `clean` expanding
    a manifest entry back out, which is true for `docs` and `slides` and is
    not what is wrong with a `*` in a directory name.
    """
    for character, effect in _GITIGNORE_LEADING.items():
        if value.startswith(character):
            raise ValueError(
                f"Package {package_id}: {where} {value!r} starts with "
                f"{character!r}, which {effect} in the .gitignore section "
                "stencil manages. This names a directory, not a pattern."
            )
    if _GLOB_IN_PATH.search(value):
        raise ValueError(
            f"Package {package_id}: {where} {value!r} contains a glob "
            "metacharacter (one of * ? [ ]). This names a directory, not a "
            "pattern -- as the first segment of every line in the "
            ".gitignore section stencil manages, it would match "
            "directories stencil never wrote to."
        )
    return value


def check_package_output_dir(package_id: str, value) -> str | None:
    """The package-level ``output_dir``'s check (stn-1a4) -- the last package
    path key with no validation of any kind, and the only one that lands in a
    Make VARIABLE rather than a recipe word.

    ``OUT_HOST := <value>`` is expanded by four recipes, so a metacharacter
    here is a second command rather than a bad filename: the ticket's
    ``"../../../../../../tmp/pwn; echo OWNED"`` made ``make doc`` run
    ``mkdir -p .../tmp/pwn`` and then ``echo OWNED``.

    ``..`` IS PERMITTED, deliberately and by operator ruling. STENCIL.md
    documents a package-level ``output_dir`` that puts build products outside
    the package directory as the supported way to build somewhere else, and a
    consumer may depend on it -- so this is `check_config_path` with
    `allow_parent=True` rather than a verbatim call. Worth knowing why that
    distinction needed a ruling at all: the only `..` the test suite pinned
    was in the DERIVED ``package_output_dir`` (``../../build/demo``, which
    `get_template_context` computes), never in a DECLARED value, so a
    verbatim call would have kept the whole suite green while removing the
    feature. There is a test that declares the `..` now.

    TWO REFUSALS BEYOND `check_config_path`'s class, both because of where
    this value lands rather than what it is:

    - ``#``. It introduces a comment in a Make ``:=`` assignment, and nothing
      else stencil checks sits in one. Measured on GNU Make 3.81:
      ``build#x`` makes ``OUT_HOST`` read as ``build``, so the Makefile
      creates and ``rm -f``s one directory while the compose file's mount --
      YAML keeps the ``#`` mid-scalar -- sends the container's output to
      another. It is NOT added to `_UNSAFE_IN_PATH`, which every other path
      key shares: ``#`` is harmless in a recipe word and in a filename, and
      refusing ``notes#1.md`` would be a regression for no gain.
    - A glob metacharacter. This names one directory, the argument
      `check_no_glob` already makes for `docs` and `slides` -- and here a
      value beginning with ``*`` or ``!`` additionally makes the generated
      compose file unparseable YAML, since those are the alias and tag
      indicators.

    WHAT THIS KEY IS NOT CONTAINED AGAINST, said plainly because the
    convenient version of this sentence is false. `stencil` itself writes
    nothing there and `clean` deliberately removes nothing there (STENCIL.md
    records that limit). The PACKAGE IT GENERATES is another matter: the
    Makefile ``mkdir -p``s and ``rm -f``s under ``OUT_HOST`` and the compose
    file bind-mounts it into a container that runs as root. That is what the
    documented escape means, and it is the consumer's own build rather than
    a containment gap in stencil -- but it must not be described as "nothing
    writes there".
    """
    if value is None:
        return None
    # TYPE BEFORE FALSINESS, the ordering check_output_dir's docstring spends
    # a paragraph on and for the identical reason: `0`, `False` and `[]` are
    # falsy NON-STRINGS, and `if raw_output:` at the call site reads all
    # three as "not set". The type check therefore has to run above that
    # guard, not inside it.
    if not isinstance(value, str):
        raise ValueError(
            f"Package {package_id}: output_dir {value!r} is "
            f"{type(value).__name__}, not a string. output_dir names the "
            "directory this package's build products go to."
        )
    if not value:
        # "" is what an empty-but-present key has always meant here, and
        # what check_output_dir preserves it as for the top-level key:
        # products land beside the sources. The two keys must not disagree
        # about two characters.
        return None
    text = check_config_path(package_id, "output_dir", value, allow_parent=True)
    # ':' SEPARATES A COMPOSE VOLUME, and this value is interpolated into
    # one. Measured with `docker compose config` on a generated package,
    # `output_dir: "a:b"` emits `- ../a:b:/out:z` and parses as
    # source=<config_dir>/a, target=b:/out, with the `:z` flag SILENTLY
    # DROPPED -- so /out is never mounted, pandoc writes into the
    # container's own filesystem and the products vanish, while OUT_HOST
    # names a third directory. That is the `#` harm again, through a
    # character far likelier to appear by accident: `C:/build` from a
    # Windows author lands here, since Path() does not read it as absolute
    # on POSIX.
    if ":" in text:
        raise ValueError(
            f"Package {package_id}: output_dir {text!r} contains ':', which "
            "separates the parts of the generated compose file's volume "
            "mount. The output directory would not be mounted at all, and "
            "the build's products would be written inside the container."
        )
    # A quote cannot chain a command here -- `;`, `$` and backtick are all
    # refused above -- but it truncates one. Measured: `output_dir: "a'b"`
    # generates at exit 0 and `make doc` dies with `unexpected EOF while
    # looking for matching '`. Still a filename quietly doing something
    # other than naming a file, which is the whole class.
    if "'" in text or '"' in text:
        raise ValueError(
            f"Package {package_id}: output_dir {text!r} contains a quote "
            "character, which would truncate the shell word the generated "
            "Make recipe expands it into."
        )
    if "#" in text:
        raise ValueError(
            f"Package {package_id}: output_dir {text!r} contains '#', which "
            "starts a comment in the generated Makefile's OUT_HOST "
            "assignment. Make would read the part before it and the compose "
            "file's mount would keep the whole string, so the build would "
            "write to one directory and clean another."
        )
    return check_no_glob(package_id, "output_dir", text)


def check_no_glob(package_id: str, where: str, value: str) -> str:
    """Refuse a glob metacharacter in a config value that names ONE file.

    The other half of `check_glob_vocabulary` (below), and the reason it is
    a separate function rather than a widening of `_UNSAFE_IN_PATH`: a
    config value is where a metacharacter gets INTO a manifest entry in the
    first place. `docs: ["[!z].md"]` produced the entry `[!z]*.html`, which
    the vocabulary check now refuses -- so the package became permanently
    un-cleanable, and the complaint pointed at a "manifest entry" the author
    never wrote.

    Applied to `docs` and `slides` only. They are the two keys whose values
    `package_entries` splices into a glob of its own making
    (`<stem>*.html`), so an author metacharacter compounds with stencil's.
    `package_sources` deliberately keeps its globs -- `md/*.md` is
    documented and in use -- which is the whole reason this is opt-in.
    """
    if _GLOB_IN_PATH.search(value):
        raise ValueError(
            f"Package {package_id}: {where} {value!r} contains a glob "
            "metacharacter (one of * ? [ ]). This names one file, not a "
            "pattern -- `clean` would have to expand it back out of the "
            "manifest, and would refuse to."
        )
    return value


def check_no_separator(package_id: str, where: str, value: str) -> str:
    """Refuse a path separator in a config value that names ONE file IN the
    package directory, not a path.

    Separate from `check_config_path` rather than a widening of it, because
    the two disagree on purpose: `check_config_path` deliberately ALLOWS a
    subdirectory -- `dest: .vscode/settings.json` is documented in
    STENCIL.md -- while `package_name` names one file directly under the
    package directory, so the same subdirectory is not a feature to permit
    here, only a way to nest or escape.

    Refuses '/' only, not backslash: `_UNSAFE_IN_PATH` (above) already
    contains a backslash, and `check_config_path` runs before this check on
    every caller, so a backslash branch here would be unreachable dead
    code -- already refused upstream as a shell/Make metacharacter.
    """
    if "/" in value:
        raise ValueError(
            f"Package {package_id}: {where} {value!r} contains a path "
            "separator. This names one file in the package directory, not "
            "a path."
        )
    return value


def check_glob_vocabulary(package_id: str, where: str, entry: str) -> None:
    """Bound what a manifest entry's glob may look like (stn-2x4.6,
    adversarial CRITICAL 2).

    ``check_config_path`` guards the STRING -- no absolute path, no ``..``,
    no shell metacharacter -- but says nothing about what a survivor
    EXPANDS to, and ``*`` is deliberately not in its unsafe set. Measured on
    this interpreter (Python 3.14.7, so since-3.13 semantics apply): ``'**'``
    passes `check_config_path` outright, and ``Path.glob('**')`` yields
    FILES, not only directories -- so a single manifest entry can expand to
    the package's entire subtree, including the author's own source
    markdown, and containment says nothing against it because every match
    is legitimately inside the package.

    ``get_generated_files`` only ever emits ``'<stem>*.html'``,
    ``'<stem>*.pdf'`` and a literal archive name, so that is the entire
    legitimate vocabulary: a glob entry may contain ``*`` only in its FINAL
    path component, at most once, with a non-empty literal prefix before
    it. ``'**'``, a bare ``'*'``, and ``'*'`` in a non-final component such
    as ``'dir/*'`` are all refused by name -- none of them is a shape
    `get_generated_files` produces, so nothing legitimate is lost.

    THE LITERAL IS VALIDATED, NOT THE POSITION OF A ``'*'``. The first
    version of this function reasoned about ``'*'`` alone, and ``'?'`` and
    ``'['``/``']'`` are metacharacters ``Path.glob`` honours just as
    happily -- none of them is in ``_UNSAFE_IN_PATH`` either. Measured:
    ``'?*'``, ``'[!z]*'``, ``'[a-z]*'`` and ``'sub/?*'`` all passed BOTH
    checks, and ``'?*'`` is a bare ``'*'`` wearing a hat -- worse, because
    ``Path.glob`` matches dotfiles where ``glob.glob`` does not, so it also
    sweeps up ``.stencil-manifest.json`` itself. Reproduced end to end: a
    manifest carrying ``["?*", "sub/?*"]`` removed the author's own
    ``thesis.md``, ``research.bib`` and ``sub/keep.md``, and exited 0.

    There is deliberately no ``if '*' not in entry: return`` fast path --
    that is exactly what let ``'Makefil?'`` through. An entry with no
    metacharacter at all is the common case and is answered by the
    ``_GLOB_IN_PATH.search(final)`` test below, on the literal rather than
    on one character of it.
    """
    parts = entry.split("/")
    if any(_GLOB_IN_PATH.search(part) for part in parts[:-1]):
        raise ValueError(
            f"Package {package_id}: {where} {entry!r} has a glob "
            "metacharacter (one of * ? [ ]) outside its final path "
            "component. A glob may only vary the last component of a path."
        )
    final = parts[-1]
    if not _GLOB_IN_PATH.search(final):
        # An ordinary literal filename. Nothing to bound.
        return
    if not _GLOB_SHAPE.fullmatch(final):
        raise ValueError(
            f"Package {package_id}: {where} {entry!r} is not a recognized "
            "glob shape. Only '<literal-prefix>*<literal-suffix>', in the "
            "final path component, is allowed -- never '**', a bare '*', a "
            "'?' or a [character class]."
        )


def get_template_context(package_id: str, config: dict) -> dict:
    """Build the template context for a package."""
    package = config.get("packages", {}).get(package_id)

    if not package:
        raise ValueError(f"Unknown package: {package_id}")

    services = package.get("services", [])

    # Derive features from services
    has_web = "web" in services
    has_mysql = "mysql" in services
    has_services = len(services) > 0

    # Package type (required)
    package_type = package.get("package_type")
    if not package_type:
        raise ValueError(f"Package {package_id} is missing required 'package_type'")
    if package_type not in ("doc", "zip", "none"):
        raise ValueError(
            f"Package {package_id} has invalid package_type: {package_type}"
        )

    # package_name is required for zip packages (not for doc or none), but
    # validated whenever it is PRESENT (stn-9rn) -- not only for the
    # package_type that consumes it. Measured safe: every package_name
    # across the seven consumer configs on this machine is a plain filename,
    # so this runs unconditionally rather than gated on package_type.
    package_name = package.get("package_name")
    if package_name is not None:
        # Checked before check_config_path, which str()s its argument: a
        # non-string package_name would otherwise sail through as its str()
        # form and only fail later at `.endswith('.pdf')` (doc packages) as
        # an AttributeError that package_contexts' generic catch reports by
        # class name rather than by which key was wrong.
        if not isinstance(package_name, str):
            raise ValueError(
                f"Package {package_id}: package_name {package_name!r} is "
                f"{type(package_name).__name__}, not a string. package_name "
                "is the one file `pkg` builds, so it is a filename."
            )
        package_name = check_config_path(package_id, "package_name", package_name)
        # check_config_path deliberately ALLOWS a subdirectory --
        # `dest: .vscode/settings.json` is documented in STENCIL.md -- but
        # package_name names one file IN the package directory, not a path,
        # so a separator it lets through must still be refused here.
        package_name = check_no_separator(package_id, "package_name", package_name)
        # Recorded verbatim as a manifest entry, so a glob metacharacter is
        # the same shape stn-2x4's check_no_glob exists to refuse for docs
        # and slides.
        package_name = check_no_glob(package_id, "package_name", package_name)
    if package_type == "zip" and not package_name:
        raise ValueError(
            f"Package {package_id} is missing required 'package_name' (required for zip type)"
        )

    # package_folder was the zip-only spelling of package_sources. One key now
    # names what goes into the submission for every package type, so the old
    # one is a hard error rather than a silently ignored setting.
    if "package_folder" in package:
        raise ValueError(
            f"Package {package_id} uses 'package_folder', which was renamed to "
            f"'package_sources' and takes a list: "
            f"package_sources: [{package['package_folder']}]"
        )

    # package_sources: what `pkg` puts into package_name. A zip archives these
    # paths; a doc concatenates the markdown they name into one document. Zip
    # packages default to htdocs, which is what package_folder defaulted to.
    raw_sources = package.get("package_sources")
    if raw_sources is None:
        package_sources = ["htdocs"] if package_type == "zip" else []
    elif isinstance(raw_sources, str):
        package_sources = [raw_sources]
    else:
        package_sources = list(raw_sources)

    # Joined into PKG_SOURCE_SPECS, a Make variable that a recipe then expands
    # and hands to zip or pandoc. Same exposure as docs and slides, so the same
    # validation -- one helper, not three that drift apart.
    package_sources = [
        check_config_path(package_id, "package_sources", src)
        for src in package_sources
    ]

    if package_sources and package_type == "none":
        raise ValueError(
            f"Package {package_id} sets 'package_sources' but has no pkg "
            "target to consume them (package_type: none)"
        )

    # A doc package's pkg target prints to package_name, so it needs one and it
    # has to be the PDF -- the intermediate HTML is named after the same stem.
    if package_sources and package_type == "doc":
        if not package_name:
            raise ValueError(
                f"Package {package_id} sets 'package_sources' but is missing "
                "required 'package_name' (the PDF that pkg builds)"
            )
        if not package_name.endswith(".pdf"):
            raise ValueError(
                f"Package {package_id} builds {package_name} from "
                "'package_sources'; the name must end in .pdf"
            )

    # docs list for doc-type packages (markdown files to convert to HTML)
    # Validated for the same reason pre_build's paths are: they reach a Make
    # recipe that /bin/sh parses. A space in a filename silently becomes two
    # arguments and builds the wrong thing.
    docs = [
        check_no_glob(package_id, "docs", check_config_path(package_id, "docs", d))
        for d in package.get("docs", [])
    ]

    # slides list: markdown rendered as a slide deck instead of a flowing document.
    # Same pipeline, different pandoc template plus the slide-sections filter.
    slides = [
        check_no_glob(package_id, "slides", check_config_path(package_id, "slides", d))
        for d in package.get("slides", [])
    ]

    both = sorted(set(docs) & set(slides))
    if both:
        raise ValueError(
            f"Package {package_id} lists {both} in both 'docs' and 'slides'; "
            "a markdown file belongs to exactly one of them"
        )

    # Normalize pre_build to a list of {run, outputs, inputs} with list-valued
    # outputs and inputs, so the template never has to ask what shape it got.
    #
    # A hook is a DEPENDENCY, not a prelude. It exists because cs425 generates
    # sixteen SVGs from a matplotlib script that nothing in the build knew
    # about, so `make doc` built documents from stale figures and never said
    # so. A hook that ran unconditionally would be worse than none: matplotlib
    # output is not stable across versions, and rewriting sixteen files on
    # every build buries the real change in thousands of lines of diff.
    # A pre_build name becomes a Make target AND, upper-cased with - as _, a
    # Make variable. Anything outside this set produces a Makefile that does
    # not parse, or a target nobody can invoke.
    safe_name = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

    def check_path(where, value):
        return check_config_path(package_id, where, value)

    pre_build = []
    seen_names: set[str] = set()
    for index, entry in enumerate(package.get("pre_build") or []):
        if not isinstance(entry, dict) or not entry.get("run"):
            raise ValueError(
                f"Package {package_id}: pre_build[{index}] needs a 'run' command"
            )

        def as_list(value):
            if value is None:
                return []
            return list(value) if isinstance(value, list) else [value]

        outputs = as_list(entry.get("outputs"))
        if not outputs:
            raise ValueError(
                f"Package {package_id}: pre_build[{index}] needs 'outputs'. "
                "Without them the step has nothing to be stale against and "
                "would run on every build, which is the behaviour this "
                "feature exists to avoid."
            )

        inputs = as_list(entry.get("inputs"))
        outputs = [check_path(f"pre_build[{index}].outputs", o) for o in outputs]
        inputs = [check_path(f"pre_build[{index}].inputs", i) for i in inputs]

        name = entry.get("name") or f"pre-build-{index}"
        if not safe_name.match(str(name)):
            raise ValueError(
                f"Package {package_id}: pre_build[{index}] name {name!r} must "
                "be letters, digits, underscore or dash, starting with a "
                "letter or digit. It becomes a Make target and variable."
            )
        # Two entries with the same name emit the same target and the same
        # stamp variable; GNU Make keeps the later recipe, so the earlier hook
        # never runs and nothing says so.
        if name in seen_names:
            raise ValueError(
                f"Package {package_id}: two pre_build steps are named {name!r}. "
                "They would share a Make target and the first would silently "
                "never run."
            )
        seen_names.add(name)

        pre_build.append(
            {
                "index": index,
                "run": entry["run"],
                "outputs": outputs,
                "inputs": inputs,
                "name": name,
            }
        )
    # Where this package's build products go, relative to the package
    # directory. Declared relative to the .config.yaml the way `dir` is, and
    # turned into a package-relative path here because every generated path
    # is package-relative.
    raw_output = check_package_output_dir(package_id, package.get("output_dir"))
    if raw_output:
        # The package directory is <top-level output_dir>/<dir>, so the path
        # back out to a config-relative output directory has to climb BOTH.
        # Getting this wrong resolves `build/classroom` to
        # `<output_dir>/build/classroom` -- which a string assertion on the
        # Makefile cannot see, and a real build lands in the wrong place.
        package_root = Path(config.get("output_dir") or ".") / (
            package.get("dir") or package_id
        )
        try:
            package_output_dir = os.path.relpath(Path(raw_output), package_root)
        except ValueError as error:
            raise ValueError(
                f"Package {package_id}: output_dir {raw_output!r} cannot be "
                f"expressed relative to the package directory {package_root}"
            ) from error
    else:
        package_output_dir = ""

    # Normalize sql_import to a list of import configs (target, database, file)
    raw_sql_import = package.get("sql_import")
    if raw_sql_import is None:
        sql_imports = []
    elif isinstance(raw_sql_import, list):
        sql_imports = raw_sql_import
    else:
        sql_imports = [raw_sql_import]

    # Build context: standard + derived keys first
    context = {
        "package_id": package_id,
        "name": package.get("name", package_id),
        # The <html lang> a page falls back to when its front matter names no
        # language. Package first, then config-wide, then English -- which is
        # what both templates hardcoded before this, so an existing project
        # that sets neither renders exactly as it did.
        #
        # Note this is `lang` on the *package*, not `dir`: a package's `dir` is
        # already its output subdirectory, so text direction has no config-level
        # spelling and stays front matter only.
        "lang": package.get("lang") or config.get("lang") or "en",
        # The brand a document falls back to when its front matter names none.
        # A picture is named by the basename it is copied to rather than the
        # path it came from: the generated folder is frequently handed to
        # someone as their own project, so it has to carry the file itself
        # rather than reach back into the repository that produced it.
        "config_brand": (
            Path(
                checked_brand_image(
                    brand_of(package, config, package_id)[0], package_id
                )
            ).name
            if checked_brand_image(
                brand_of(package, config, package_id)[0], package_id
            )
            else brand_of(package, config, package_id)[0]
        ),
        "config_brand_alt": brand_of(package, config, package_id)[1],
        # The show_download a document falls back to when its front matter
        # names none: package first, then config-wide, then on. Always set,
        # so StrictUndefined has nothing to complain about and no
        # `| default(...)` is needed at the call site -- see AGENTS.md on why
        # papering over an undeclared key with a default is worse than this.
        "config_show_download": show_download_default(package, config, package_id),
        "package_name": package_name,
        # stn-vhm. `dir` is not decoration: get_generated_files prefixes every
        # entry with it, so it is every line of the managed .gitignore section
        # and every path clean_generated resolves and deletes. `dir: ../..`
        # deleted outside the output base. It gets the same check as every
        # other configured path rather than a containment check at the
        # deletion site, so the mistake is reported by the pre-flight that
        # names all of them at once.
        "package_dir": check_package_dir(
            package_id, package.get("dir", f"{package_id}")
        ),
        "package_type": package_type,
        "package_sources": package_sources,
        "has_package_sources": bool(package_sources) and package_type == "doc",
        # Basename the doc pkg target builds from, without the .pdf: `hs2`
        # builds hs2.html and hs2.pdf (hs2-hidden.* when WITH is set).
        "package_stem": (
            package_name.removesuffix(".pdf")
            if package_sources and package_type == "doc"
            else ""
        ),
        "docs": docs,
        "has_docs": bool(docs),
        "slides": slides,
        "has_slides": bool(slides),
        # True when the package renders any markdown through the pandoc pipeline
        "has_pages": bool(docs) or bool(slides) or bool(package_sources),
        "services": services,
        # Derived from services
        "has_web": has_web,
        "has_mysql": has_mysql,
        "has_services": has_services,
        # Explicit features
        # Per-package output_dir: where BUILD PRODUCTS go, as a path relative
        # to the package directory. `dir` says where the package's sources and
        # scaffolding live; this says where its .html and .pdf land. The two
        # are orthogonal -- one is an input location, the other an output one.
        #
        # Empty string when unset, which the Makefile turns into "." and the
        # compose file into no second mount, so a package that does not set it
        # is byte-identical to before.
        "package_output_dir": package_output_dir,
        "has_package_output_dir": bool(package_output_dir),
        "sql_imports": sql_imports,
        "pre_build": pre_build,
        "has_pre_build": bool(pre_build),
        # The pandoc invocation, from stencil/pipeline.py rather than spelled
        # out in the compose template, so a test can assert on the same argv
        # the generated package builds with.
        "pandoc_image": pipeline.PANDOC_IMAGE,
        "pandoc_argv_doc": pipeline.annotated_argv("doc"),
        "pandoc_argv_slide": pipeline.annotated_argv("slide"),
        # Same rule as the pandoc argv above: the check-pdf service's image
        # and script live in pipeline.py so a test can assert on the text
        # this file renders rather than on a copy of it.
        "verapdf_image": pipeline.VERAPDF_IMAGE,
        "verapdf_script": pipeline.VERAPDF_SCRIPT,
        # Same rule again for check-access, and for the same reason it was
        # needed: inlined in the compose file, its loop and its file:// URL
        # disagreed about where the HTML was and nothing ran it to find out.
        "check_access_script": pipeline.CHECK_ACCESS_SCRIPT,
        # And again for everything the scaffolding installs at build time. The
        # Dockerfile, the format-md service and the Makefile's ensure_image line
        # all name the same Node image, and both npm installs name an exact
        # version -- from here, so that bumping one is one edit and a test can
        # assert on the constant rather than on a copy of it. See stn-s5b, and
        # the comments at pipeline.NODE_IMAGE for why Chromium is not among
        # them.
        "node_image": pipeline.NODE_IMAGE,
        # Both installs go through `npm ci` against a committed lockfile, so
        # what the scaffolding carries is a manifest (derived from the pins,
        # written inline) and the lockfile itself (vendored, shipped verbatim).
        # An exact version at the top level left the other 43 packages in the
        # browser tree resolving within a range on every build; see stn-5hv and
        # the comment above BROWSER_LOCKFILE in pipeline.py.
        "browser_manifest": pipeline.npm_manifest(
            pipeline.BROWSER_MANIFEST_NAME, pipeline.BROWSER_NPM_PINS
        ),
        "format_manifest": pipeline.npm_manifest(
            pipeline.FORMAT_MANIFEST_NAME, pipeline.FORMAT_NPM_PINS
        ),
        "browser_lockfile": pipeline.read_lockfile(pipeline.BROWSER_LOCKFILE),
        "format_lockfile": pipeline.read_lockfile(pipeline.FORMAT_LOCKFILE),
        # And the digest of the file the line above renders, so the format-md
        # entrypoint can refuse a lockfile that is not the one stencil wrote.
        # The service reads its lockfile out of the mount, and `npm ci` fetches
        # whatever host each `resolved` names -- so without this, a
        # consumer-editable file chose which bytes became the prettier that
        # runs as uid 0 over that same mount (stn-qge). Derived from the same
        # call the file is rendered from, never written down, so a re-vendor
        # moves both at once.
        "format_lockfile_digest": pipeline.lockfile_digest(pipeline.FORMAT_LOCKFILE),
        # And the same for the browser image, which COPYs its lockfile out of
        # that directory and runs `npm ci` from it at BUILD time -- so without
        # this, a consumer-editable file chose which bytes became the puppeteer,
        # pa11y and pdf-lib that `make pdf` and `make check-access` then run as
        # uid 0 (stn-egv). Derived from the same call the file is rendered from,
        # never written down, so a re-vendor moves both at once.
        "browser_lockfile_digest": pipeline.lockfile_digest(pipeline.BROWSER_LOCKFILE),
        # The names those two land under in the package, which the Dockerfile
        # COPYs and the format-md entrypoint cps, and the directories each
        # install is rooted at.
        "browser_lockfile_name": pipeline.BROWSER_LOCKFILE,
        "format_lockfile_name": pipeline.FORMAT_LOCKFILE,
        "browser_tools_dir": pipeline.BROWSER_TOOLS_DIR,
        "browser_node_modules": pipeline.BROWSER_NODE_MODULES,
        "format_tools_dir": pipeline.FORMAT_TOOLS_DIR,
        # CSS, JS and webfonts inlined into the pandoc templates. Loaded here
        # rather than fetched at page load, so a handout is self-contained and
        # make pdf does not depend on the network.
        "assets": assets.load(),
    }

    # Everything above is DERIVED -- computed from the package config, or handed
    # over by pipeline.py. Everything below is CUSTOM, supplied by whoever wrote
    # the config. Snapshotting the boundary here is what lets the two be told
    # apart further down; there is no other marker distinguishing them once they
    # are in one flat dict.
    derived_keys = set(context)

    def reject_derived(where: str, env: dict) -> None:
        """A custom key may not take the name of a derived one.

        Not a style rule. `template_env: {pandoc_image: ...}` would replace the
        image every generated service runs, and package_type, docs and assets
        are shadowable the same way -- so a typo that happens to collide
        silently rewires the build rather than being ignored.

        Raising rather than dropping it, because a config that does this today
        is asking for something and would otherwise stop getting it without
        being told. That is the same call StrictUndefined makes elsewhere in
        this file: fail at generation time, not in whatever the template
        rendered.
        """
        clashes = sorted(set(env) & derived_keys)
        if not clashes:
            return
        raise ValueError(
            f"{where} template_env may not redefine "
            f"{'these derived context keys' if len(clashes) > 1 else 'the derived context key'}: "
            f"{', '.join(clashes)}. "
            "Derived keys come from the package config and from stencil itself; "
            "pick a different name for the custom one."
        )

    # Config-level template_env declares which custom keys these templates may
    # read and supplies the value for packages that do not set one. A
    # project can point several configs at one templates directory, where a
    # shared template then reads a key only some of those configs set. Without
    # a way to declare a key without setting it, StrictUndefined would make one
    # shared template impossible to serve from more than one config.
    config_env = config.get("template_env")
    if isinstance(config_env, dict):
        reject_derived("config-level", config_env)
        for key, value in config_env.items():
            context.setdefault(key, value)

    # Any remaining custom key some package sets, left *undefined* for the
    # packages that do not -- the lenient Undefined, not this environment's
    # StrictUndefined. It is falsy in a condition, renders as nothing, and
    # still satisfies `| default(...)`, which a concrete False does not. A
    # template writing `{{ front_controller | default('index.html') }}` against
    # a key defaulted to False puts the literal "False" where a filename
    # belonged, which is how that was found.
    for key in declared_template_env_keys(config):
        context.setdefault(key, Undefined())

    # Custom template vars: merge into top-level context so `when` conditions
    # and templates can access them directly.
    #
    # This is an update rather than a setdefault ON PURPOSE and unlike the two
    # passes above: a package's own value has to beat the config-wide default,
    # which is the entire point of declaring one. What it must NOT beat is a
    # derived key, and until 0.25.0 it did -- the comment here claimed
    # "setdefault throughout, so a config cannot shadow a derived key", which
    # was true of the passes above and false of this line directly beneath it.
    template_env = package.get("template_env", {})
    if isinstance(template_env, dict):
        reject_derived(f"package {package_id!r}", template_env)
        context.update(template_env)
    # Also keep as nested dict for backward compatibility
    context["template_env"] = template_env if isinstance(template_env, dict) else {}

    return context


def declared_template_env_keys(config: dict) -> set[str]:
    """Every custom context key the config declares, at either level."""
    keys: set[str] = set()
    config_env = config.get("template_env")
    if isinstance(config_env, dict):
        keys |= set(config_env)
    for package in config.get("packages", {}).values():
        custom = package.get("template_env")
        if isinstance(custom, dict):
            keys |= set(custom)
    return keys


def scan_template(env: Environment, name: str, _seen=None) -> tuple[set[str], bool]:
    """Context keys a template reads, and whether that answer is complete.

    Transitive because a consuming project overrides a composition template and
    includes stencil's partials into it, so a key is often read a level below
    the template the config names.

    The flag is false when something in the tree could not be read statically.
    Jinja reports `{% include some_variable %}` as a reference of None, since
    the name is only known at render time, and a template that does not resolve
    contributes nothing rather than raising -- render_templates reports that
    with the path, and this should not pre-empt it with a worse message. Either
    way the set is a lower bound, and a caller that would reject something for
    being absent from it has to stop.
    """
    seen = set() if _seen is None else _seen
    if name in seen:
        return set(), True
    seen.add(name)
    try:
        source = env.loader.get_source(env, name)[0]
    except TemplateNotFound:
        return set(), False
    ast = env.parse(source, filename=name)
    found = set(meta.find_undeclared_variables(ast))
    complete = True
    for referenced in meta.find_referenced_templates(ast):
        if referenced is None:
            complete = False
            continue
        nested, nested_complete = scan_template(env, referenced, seen)
        found |= nested
        complete = complete and nested_complete
    return found, complete


def template_reads(env: Environment, name: str, _seen=None) -> tuple[set[str], bool]:
    """Every name a template could be reading, and whether that is the whole of
    it. Deliberately an over-approximation.

    A custom key does not have to reach a template as a variable. A consumer's
    nginx.conf.j2 writes `(template_env | default({})).get('docroot_subdir')`,
    where the key is a dict lookup and no analysis of variable names will ever
    see it. So string constants count as reads too, alongside loaded names --
    which also covers `{% set x = x | default(...) %}`, where Jinja calls x
    declared because the same statement assigns it.

    The only caller rejects a key for being ABSENT from this set, so every
    imprecision here makes it more permissive. What survives is the one thing
    that can be said soundly: this name appears nowhere in any template, in any
    form, which is what a typo looks like.
    """
    seen = set() if _seen is None else _seen
    if name in seen:
        return set(), True
    seen.add(name)
    try:
        source = env.loader.get_source(env, name)[0]
    except TemplateNotFound:
        return set(), False
    ast = env.parse(source, filename=name)
    found = {n.name for n in ast.find_all(nodes.Name) if n.ctx == "load"}
    found |= {
        n.value for n in ast.find_all(nodes.Const) if isinstance(n.value, str)
    }
    complete = True
    for referenced in meta.find_referenced_templates(ast):
        if referenced is None:
            complete = False
            continue
        nested, nested_complete = template_reads(env, referenced, seen)
        found |= nested
        complete = complete and nested_complete
    return found, complete


def referenced_variables(env: Environment, name: str) -> set[str]:
    """Context keys a template reads. See scan_template for the caveats."""
    return scan_template(env, name)[0]


def validate_config(
    config: dict, env: Environment, config_dir: Path | None = None
) -> None:
    """Reject custom keys that cannot do anything, in either direction.

    template_env accepts any key and `when:` tests any name, so a typo used to
    be silent both ways: an unknown key was set and read by nothing, and a
    `when:` naming a key nobody set read as None and skipped the template it
    guarded for every package. Neither produced output or an error.

    What a template could legitimately read -- everything stencil derives,
    plus every custom key any package declares -- now comes from
    package_contexts, which is itself an aggregating, fail-closed pre-flight:
    a package that cannot be read, or whose brand is broken, raises one named
    report instead of silently shrinking the available set the way this
    function's own loop used to. config_dir is passed through to it so a
    `stencil gen` (the only caller that has one) also gets brand's
    file-existence check before anything is written; see package_contexts's
    own docstring for what running behind it costs the checks below.
    """
    contexts = package_contexts(config, config_dir)
    available: set[str] = set()
    for context in contexts.values():
        available |= set(context)

    # stn-dl3r: built AFTER package_contexts, not before. `tdef.get("when")`
    # assumes every `templates:` entry is a mapping -- a shape
    # package_contexts now checks and raises on (via
    # _checked_template_defs) -- and this loop used to run ABOVE that call,
    # so a malformed entry reached `tdef.get` here as a bare `str` and
    # tracebacked with AttributeError before `gen`'s only pre-flight ever
    # got a chance to name the real problem. By the time this runs,
    # `package_contexts` has already raised on that shape, so every `tdef`
    # here is guaranteed to be a mapping.
    when_keys: set[str] = set()
    for tdef in config.get("templates", []):
        when = tdef.get("when")
        if when is None:
            continue
        when_keys |= {when} if isinstance(when, str) else set(when)

    unknown = sorted(when_keys - available)
    if unknown:
        raise ValueError(
            f"`when:` names {', '.join(unknown)}, which stencil does not derive "
            f"and no package sets in template_env. The template it guards is "
            f"skipped for every package. Known keys: {', '.join(sorted(available))}"
        )

    read = set(when_keys)
    complete = True
    for tdef in config.get("templates", []):
        src = tdef.get("src")
        if src:
            found, found_complete = template_reads(env, src)
            read |= found
            complete = complete and found_complete

    # Only an exhaustive reading of the templates can prove a key is unused. If
    # any of them hid part of itself, a missed typo is the better failure --
    # the alternative refuses a config that is perfectly correct.
    unread = sorted(declared_template_env_keys(config) - read) if complete else []
    if unread:
        raise ValueError(
            f"template_env sets {', '.join(unread)}, which no `when:` names and "
            f"no template reads. Nothing uses it."
        )


def build_environment(config: dict, config_dir: Path) -> Environment:
    """Build the Jinja environment with the configured template search path.

    Every templates_dir in config order, then the bundled templates; first match
    wins. Extracted from main so tests can render a template without the CLI.
    """
    templates_dir_raw = config.get("templates_dir")
    if templates_dir_raw:
        if isinstance(templates_dir_raw, str):
            templates_dir_raw = [templates_dir_raw]
        template_dirs = [(config_dir / d).resolve() for d in templates_dir_raw]
    else:
        template_dirs = []
    bundled = SCRIPT_DIR / "templates"
    if bundled.resolve() not in [d.resolve() for d in template_dirs]:
        template_dirs.append(bundled)

    return Environment(
        loader=FileSystemLoader(template_dirs),
        extensions=["jinja2.ext.do"],
        # A missing variable is an error, not an empty string. A consuming
        # project may override a composition template and include stencil's
        # partials into it, so a renamed context key would
        # otherwise render a Makefile with a hole where a recipe used to be,
        # found by whoever next ran make. See tests/test_template_contract.py.
        undefined=StrictUndefined,
        trim_blocks=True,
        # keep indentation on lines after {% ... %} so templates stay readable
        lstrip_blocks=False,
        keep_trailing_newline=True,
    )


def brand_problem(package: dict, config: dict, config_dir: Path | None) -> str | None:
    """The two ways a package's brand can be broken, or None when it is fine.

    Extracted from copy_brand_image's two raises so the same rule can be
    checked twice: once here, by the aggregating pre-flight, before anything
    is written, and again inside copy_brand_image itself, which still RAISES
    by calling this and treating a returned message as fatal --
    tests/test_brand.py's two ``pytest.raises(match=...)`` tests are what
    prove the direct generate_package API keeps that contract.

    config_dir is positional-required rather than optional=None. An
    existence check that silently skips itself when a caller forgets to pass
    a root is the same shape of defect this whole change exists to close, so
    forgetting it has to be a visible choice (an explicit None) rather than a
    default. Passing None is exactly what get_generated_files,
    install_gitignore and clean_generated do -- decision d-96da97d8: the
    existence check is gen-only, because the .gitignore entry and the clean
    list both derive from the brand STRING (see get_generated_files), never
    from the file on disk, so a missing file cannot make either list short.
    Only `stencil gen`, which is about to depend on the file being there,
    needs to know it exists.
    """
    # brand_of raises ValueError on a non-string brand, which is deliberate
    # and is why there is no type check here: package_contexts has already
    # collected that failure from the context build long before this runs.
    value, alt = brand_of(package, config)
    relative = brand_image_path(value)
    if not relative:
        # A plain name or a remote URL: nothing local to check, and nothing
        # that needs alt text. Must come before the alt check below, or a
        # name-only brand with no brand-alt would be reported as broken.
        return None

    if not alt:
        return (
            f"brand is an image ({value}) but brand-alt is not set. Add "
            "brand-alt: with the text a reader should hear in place of the "
            "logo, or use a plain string for brand to render it as a name."
        )

    if config_dir is not None:
        root = config_dir.resolve()
        source = (config_dir / relative).resolve()
        # stn-ttg. check_config_path has already refused `..` and an absolute
        # value, so the only way the resolved file can be outside the config's
        # directory is a symlink -- and a symlink is exactly what turns a
        # permitted-looking `logo.png` into a read of any file the user can
        # open. The copy is the one place a configured path becomes CONTENT
        # in a folder that gets handed to someone else, so the target has to
        # stay under the same root the config string was checked against.
        if not source.is_relative_to(root):
            return (
                f"brand points at {value}, which resolves to {source}, outside "
                f"the config file's directory ({root}). A brand image, or the "
                "symlink that names it, must stay inside that directory."
            )
        if not source.is_file():
            return (
                f"brand points at {value}, which is not a file. Paths are "
                f"resolved relative to the config file's directory "
                f"({config_dir})."
            )

    return None


def copy_brand_image(
    config: dict,
    package: dict,
    config_dir: Path,
    output_dir: Path,
    dry_run: bool = False,
    *,
    dir_fd: int | None = None,
) -> None:
    """Copy a config-level brand image into the package it brands.

    Copied rather than referenced, and copied without being asked for. The
    folders stencil generates are routinely handed to someone as a project of
    their own, separate from the repository that produced them -- so a logo
    that lived only next to .config.yaml would leave the recipient with a
    document referring to a file they were never given. A copy per package is
    the cost of each folder standing on its own, and the copies are ignored by
    git for the same reason every other generated file is.

    `dir_fd`, OPTIONAL AND KEYWORD-ONLY (stn-avv, stn-6mcb.5): a descriptor on
    `output_dir` itself, opened by `generate_package` with
    `_open_package_base_fd`. The copy always lands at a TOP-LEVEL name in the
    package directory -- `Path(relative).name`, never a nested path -- so
    writing through it needs no walk, only the base descriptor and that name.
    `None` -- today's behaviour, unchanged -- opens the full `destination`
    path instead.
    """
    problem = brand_problem(package, config, config_dir)
    if problem:
        raise ValueError(problem)

    value, _alt = brand_of(package, config)
    relative = brand_image_path(value)
    if not relative:
        return

    source = (config_dir / relative).resolve()
    # stn-8wt. The NAME comes from the config string, not from the resolved
    # path. `.resolve()` follows a symlink, and get_generated_files names this
    # same file from the raw config value -- so `logo.svg -> img/dated.svg`
    # was copied as `dated.svg`, which the managed .gitignore section never
    # listed and `clean` never removed. One rule with two spellings; this is
    # the spelling both other call sites already used. The symlink is still
    # followed for CONTENT, which is what a stable name pointing at a dated
    # asset is for.
    destination = output_dir / Path(relative).name
    if dry_run:
        print(f"Would copy: {source} -> {destination}")
        return
    # The path brand_problem checked and the bytes copied are the same file.
    # `source` is already fully resolved, so nothing on it should be a link
    # any more; opening it with O_NOFOLLOW makes a link swapped in after the
    # check fail here (ELOOP) instead of quietly reading somewhere else.
    # Windows has no O_NOFOLLOW, and no getattr default changes what a
    # resolved path opens there.
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    with (
        open(source, "rb", opener=lambda p, f: os.open(p, f | nofollow)) as src,
        # stn-h5q. The SOURCE has taken this care since stn-ttg; the
        # DESTINATION, which is the half that writes, was a bare
        # open(..., "wb") and followed a symlink straight out of the tree.
        # stn-avv: when `dir_fd` is given, the NAME passed alongside it must
        # be relative to that descriptor -- `destination`'s directory
        # portion would otherwise be resolved by path all over again,
        # throwing away the whole point of holding `dir_fd` open.
        open_for_write_nofollow(
            Path(destination.name) if dir_fd is not None else destination,
            dir_fd=dir_fd,
        ) as dst,
    ):
        shutil.copyfileobj(src, dst)
    print(f"Copied: {destination}")


_MAX_PROBLEM_CHARS = 400


def _safe(text: str) -> str:
    """Config-supplied text is data, not terminal control.

    A package id or brand value can contain anything, including a cursor-up
    or carriage-return escape sequence -- which, printed raw, could erase or
    overwrite the very lines reporting the problem. That would defeat the
    aggregation guarantee package_contexts exists to provide: the terminal
    would show something other than what the Python string says.

    NEWLINE IS ESCAPED TOO, and that is the whole point rather than an
    oversight. The guarantee being defended is a BULLET LIST, and a newline
    is the one character that can add lines to it: `package_type` is
    interpolated bare into its error, so an ordinary YAML config can put
    "none\n\n- everything is fine" in a value and forge an entry that reads
    exactly like a real finding. A package id can do it too, while the
    header above still says "this problem", singular. Every problem is one
    line; the caller joins them.

    Bidi overrides need no special case -- U+202E, U+200F, U+2066, U+2028,
    U+0085 and NBSP are all category Cf/Zl/Zs, so isprintable() is already
    False for them and they escape like any other control.
    """
    escaped = "".join(
        c if c == " " or c.isprintable() else repr(c)[1:-1] for c in str(text)
    )
    # ...and BOUNDED, which is the other half of stn-oty. Escaping stops a
    # value from rewriting the report; it does not stop one from burying it,
    # and yaml.safe_load returns a scalar of any size. The cap is per problem
    # line and deliberately generous -- a real message runs to a couple of
    # hundred characters -- so it only ever fires on something pathological,
    # and it says how much it dropped so the value stays identifiable.
    if len(escaped) > _MAX_PROBLEM_CHARS:
        return (
            escaped[:_MAX_PROBLEM_CHARS]
            + f"... [truncated, {len(escaped)} characters]"
        )
    return escaped


# The trailer gen and install have always printed: nothing was written, and
# the recovery instruction is to delete leftovers by hand rather than
# reaching for `clean` -- because before stn-2x4, `clean` refused for the
# same reason. stn-2x4.8 makes that no longer true for `clean` specifically
# (a manifest-backed package cleans anyway), so `clean`'s branch of `_main`
# passes a different trailer to `_raise_config_problems` instead of
# printing this one -- see the `trailer` parameter below. gen and install
# keep this default unchanged.
_DEFAULT_CONFIG_PROBLEM_TRAILER = (
    "Nothing was generated, removed or written. Fix the config and run "
    "again -- and if files from an earlier, working config are still on "
    "disk, remove that directory by hand rather than reaching for "
    "`clean`, which refuses for the same reason this did."
)

# `_main`'s `clean` branch passes this instead of the default above (stn-2x4
# architecture review finding D2). `clean` is the one command that does NOT
# refuse on this config problem -- see `clean_generated`'s `config_readable`
# path -- so the default trailer's claim that nothing was removed, and that
# clean refuses for the same reason, would both be false the moment this
# text reaches the terminal.
CLEAN_DEGRADED_TRAILER = (
    "`clean` does not need this config to be valid: any package with its "
    "own manifest is still cleaned from it. A package with neither a valid "
    "manifest nor a place in a readable config is named below, after this "
    "warning, and nothing under it is touched."
)


def _raise_config_problems(
    problems: list[str], trailer: str | None = None
) -> NoReturn:
    """Turn collected config problems into the one ValueError callers print.

    ``NoReturn``, not ``None``, and that is load-bearing rather than
    decorative: every caller relies on this never returning, and
    ``checked_output_base`` reads a name bound in the ``try`` immediately
    after its ``except`` calls this. Spelled ``-> None``, that reads to a
    type-checker -- and to a person -- as a possible ``UnboundLocalError``
    rather than as the config error it actually is, and any future edit that
    gave this function a non-raising path would turn that into a real one.

    Deliberately names no file: --config means the path is not always
    .config.yaml, and nothing down here is told which one it got. Every
    caller prints this behind an "Error: " prefix, so the first line reads
    as a continuation of one rather than as a second heading.

    ``trailer`` defaults to today's gen/install wording (stn-2x4 architecture
    review finding D2). `clean`'s degraded path passes its own: printed as a
    warning immediately before `clean` goes on to remove files and exit 0,
    the default trailer's "Nothing was generated, removed or written ...
    which refuses for the same reason this did" would be a direct
    contradiction of what just happened.
    """
    unique = list(dict.fromkeys(_safe(p) for p in problems))
    bullets = "\n".join(f"- {p}" for p in unique)
    count = "this problem" if len(unique) == 1 else f"these {len(unique)} problems"
    text = _DEFAULT_CONFIG_PROBLEM_TRAILER if trailer is None else trailer
    raise ValueError(f"the config has {count}:\n{bullets}\n\n{text}")


def _checked_template_defs(
    declared: object, who: str
) -> tuple[list[dict], list[str]]:
    """Shape-check a config's top-level ``templates:`` value. ONE spelling
    for a rule two callers need (stn-dl3r).

    `package_contexts` runs this as part of gen/install's fail-closed
    pre-flight (and of `clean`'s own `config_readable` probe), raising
    before anything is written; `_config_template_defs` runs it again for
    `clean`'s config-derived removal list, which a caller of
    `clean_generated(config_readable=True)` can reach without ever having
    called `package_contexts` (see that function's docstring). An earlier
    version of this file kept the rule in `_config_template_defs` alone and
    called that "belt and braces" for `package_contexts` -- it was not:
    `package_contexts` never checked this shape at all, so a malformed
    `templates:` reached `gen` and `install` as a bare
    AttributeError/KeyError while `clean` alone named it. Two independent
    implementations of the same shape check is the drift this file has
    already paid for three times (see `package_entries`'s docstring), so
    this is the one place the rule is written; each caller keeps its own
    failure mode -- `package_contexts` appends to a `problems` list it
    raises on once every package has been checked, `_config_template_defs`
    appends to one its own caller was handed.

    An ABSENT `templates:` key is not a problem. Every caller already reads
    `config.get("templates", [])`, so a config with no key at all sees `[]`
    here -- the same value an explicit `templates: []` produces -- and
    refusing an absent key would refuse every config that has no templates
    yet. `templates: []` is legal on its own merits too: it is the ordinary
    shape of a `package_type: none` package with no pages, and stn-jez's own
    reproduction config depends on it staying legal. A `templates:` key
    present with no value (YAML null) is NOT the same statement as an
    absent key, and is still refused below -- `None` is not a list either.

    Returns ``(kept, problems)``. `kept` is every entry that is a mapping
    with a non-empty string `src` -- the only shape a caller can go on to
    read `tdef["src"]` / call `tdef.get(...)` on without checking again.
    `problems` names, in words shared by every caller, each entry that was
    dropped and why, or (for a non-list `templates:`) that none of them
    could be identified at all -- worded so `gen`, `install` and `clean`
    report the identical shape mistake identically, which is the whole
    point of stn-dl3r.
    """
    if not isinstance(declared, list):
        return [], [
            f"Package(s) {who}: 'templates' must be a list of template "
            f"definitions, not {type(declared).__name__} -- the file(s) it "
            "renders cannot be identified"
        ]

    kept: list[dict] = []
    dropped = 0
    for tdef in declared:
        if not isinstance(tdef, dict):
            dropped += 1
            continue
        src = tdef.get("src")
        if not isinstance(src, str) or not src:
            dropped += 1
            continue
        kept.append(tdef)

    if not dropped:
        return kept, []

    return kept, [
        f"Package(s) {who}: {dropped} entr{'y' if dropped == 1 else 'ies'} "
        f"under 'templates' {'is' if dropped == 1 else 'are'} not a mapping "
        "with a `src:` -- the file(s) it renders cannot be identified"
    ]


def package_contexts(
    config: dict, config_dir: Path | None = None, trailer: str | None = None
) -> dict[str, dict]:
    """Every package's template context, read before anything is written.

    Replaces two former call sites -- validate_config's loop and
    get_generated_files' loop -- that each caught get_template_context's
    ValueError and silently `continue`d, so a config error in one package
    dropped that package from the managed .gitignore section, from what
    `clean` removes, and from a `gen --all` that still exited 0. Nothing said
    so. This raises instead, and aggregates: every package is checked, every
    problem is collected, and if anything was collected the single ValueError
    raised at the end names ALL of them -- not just the first. That
    preserves, for the class of package errors, what the comment this
    function replaced (previously at validate_config's old loop) was
    protecting: "a config error in one should not hide a naming error in
    another".

    That guarantee does NOT fully extend to validate_config's own `when:` and
    unread-template_env checks, and this docstring says so rather than
    claiming otherwise. Those two checks now run BEHIND this pre-flight --
    deferred by one round of fixing a config -- because they need a complete
    "available keys" set to report accurately, and that set can only be
    computed by successfully reading every package's context first. A config
    that is broken in a way this function reports will not also get a
    `when:`/template_env report in the same pass; fixing what is reported
    here and running again is what surfaces those. Nothing was free about
    this move; it traded one kind of silent gap for a one-round delay in a
    different, narrower kind of report.

    config_dir gates only brand's file-existence check (see brand_problem):
    None -- what get_generated_files, install_gitignore and clean_generated
    pass -- skips it; a real path -- what validate_config's `gen` caller
    passes -- runs it.

    trailer is forwarded to _raise_config_problems verbatim (None keeps its
    gen/install default); see that function's docstring for why `clean`'s
    degraded path passes a different one.
    """
    packages = config.get("packages", {})
    problems: list[str] = []
    contexts: dict[str, dict] = {}

    if "packages" not in config:
        # Checked HERE rather than in main, which is where it used to live
        # exclusively -- and `install` returned before reaching it. A typo
        # like `package:` therefore rewrote a populated managed .gitignore
        # section as an empty one, printed "Updated", and exited 0: the
        # exact harm this whole change exists to close, surviving on the
        # command the change is named after. Putting the rule in the
        # fail-closed function instead of in main's branch ordering is what
        # stops the next early-returning command from missing it too.
        #
        # An explicitly empty `packages: {}` is a different statement -- "I
        # have none yet" -- and still passes.
        problems.append(
            "'packages' is missing. Nothing can be generated, ignored or "
            "cleaned without it; check the key is spelled `packages:` at "
            "the top level."
        )
        packages = {}

    if not isinstance(packages, dict):
        problems.append(
            f"'packages' must be a mapping of package id to settings, not "
            f"{type(packages).__name__}"
        )
        packages = {}

    # A template's `dest` is config-level, so it is checked once here rather
    # than once per package: N identical bullets for one typo is exactly what
    # the dedup below exists to avoid, and this is cheaper than deduping.
    #
    # stn-c25. `output_dir / dest` took the value verbatim, so
    # `dest: ../../shared/Makefile` wrote outside the package on `gen` and
    # get_generated_files named the same path -- so `clean` REMOVED it. The
    # escape was symmetric across gen, clean and the managed .gitignore
    # section, which is what makes refusing it better than containing it on
    # one side. A nested dest is still fine: `.vscode/settings.json` is in the
    # config's own documentation, and check_config_path allows a subdirectory
    # while rejecting `..`, an absolute path and the rest.
    #
    # stn-dl3r. The SHAPE of `templates:` itself -- a non-list value, a
    # non-mapping entry, an entry with no (string) `src` -- used to be
    # unchecked here at all: this loop skipped a non-dict member silently and
    # did nothing when `templates` was not a list. `validate_config`'s
    # `when:` loop and `when_holds` (reached via `get_generated_files` ->
    # `package_entries` on `install`) both assume that shape, so a config
    # this broken tracebacked on `gen` and `install` while only `clean`
    # named it, via `_config_template_defs`. `_checked_template_defs` is the
    # one place that shape check is written now; both this function and
    # `_config_template_defs` call it.
    who = ", ".join(sorted(packages)) if packages else "(no packages configured)"
    kept_templates, template_problems = _checked_template_defs(
        config.get("templates", []), who
    )
    problems.extend(template_problems)
    for tdef in kept_templates:
        declared = tdef.get("dest")
        if declared is None:
            continue
        # check_config_path str()s what it is given, so `dest: 2024`
        # would pass it and then reach `output_dir / 2024` in
        # render_templates as a TypeError, after earlier templates had
        # already been written. A dest is a filename, so it is a string.
        if not isinstance(declared, str):
            problems.append(
                f"Package config: dest {declared!r} is "
                f"{type(declared).__name__}, not a string. A template's "
                "dest is the filename it renders to."
            )
            continue
        try:
            check_config_path("config", "dest", declared)
        except ValueError as error:
            problems.append(str(error))

    # The top-level output_dir (stn-40a, stn-pe3), config-level like `dest`
    # just above -- but this call is the SHAPE half only (check_output_dir).
    # The containment half (checked_output_base) needs a real config_dir to
    # resolve against, and package_contexts is called with none by
    # get_generated_files, install_gitignore and clean_generated -- so it
    # cannot run here.
    #
    # This still earns its place, for three reasons that are easy to mistake
    # for one:
    #  - `install` returns above _main's output_base computation entirely
    #    (generate.py, the `install` branch), so this is the only pre-flight
    #    a bad output_dir ever reaches on that command;
    #  - a direct library caller of get_generated_files / install_gitignore /
    #    clean_generated gets no output_base computation at all, only this;
    #  - it stops `Path(config.get("output_dir") or ".")`, inside
    #    get_template_context's package_root computation, from raising a bare
    #    TypeError on a non-string value before a single package context is
    #    built.
    #
    # It does NOT make gen or clean's report "aggregate with every other
    # config problem" the way the `dest` check above does -- _main computes
    # output_base (via checked_output_base) ABOVE gen's validate_config call
    # and ABOVE clean's own package_contexts call, so for those two commands
    # _main's check already raised and this line is never reached. Say so
    # here, or the next reader deletes this as a duplicate of that one.
    try:
        check_output_dir(config)
    except ValueError as error:
        problems.append(str(error))

    # Shapes first, for EVERY package, before a single context is built.
    #
    # Not tidiness -- correctness. get_template_context does not read only
    # the package it was asked about: declared_template_env_keys walks
    # `packages` in full and calls .get() on each value, so ONE package
    # whose value is a string makes get_template_context raise for every
    # package in the config. Interleaved with the context pass, that
    # attributed the string package's AttributeError to whichever innocent
    # package happened to be building at the time -- a report naming the
    # wrong file, which is worse than the silence this whole change
    # replaced. So: collect every shape problem, and if there are any,
    # stop here rather than producing a page of spurious failures behind
    # the one real cause.
    for package_id, package in packages.items():
        if not isinstance(package, dict):
            problems.append(
                f"Package {package_id}: must be a mapping of settings, not "
                f"{type(package).__name__}"
            )

    if problems:
        _raise_config_problems(problems, trailer)

    for package_id, package in packages.items():
        try:
            context = get_template_context(package_id, config)
        except ValueError as e:
            # Passed through verbatim, with NO class name and NO package
            # prefix of our own. Two separate reasons.
            #
            # No class name: ValueError is the channel get_template_context
            # deliberately reports config mistakes on, so "ValueError:" in
            # front of every line would put a Python type into a message
            # whose whole point is to be readable by someone editing YAML.
            # Nothing else in this file does it -- load_config prints the
            # yaml error bare -- and the unexpected classes below are where
            # a type name earns its place, precisely because it means
            # something has gone wrong that is NOT an ordinary config error.
            #
            # No package prefix: these messages already name where the
            # problem is, either the package ("Package demo ...") or the
            # config as a whole ("config: ..."). Adding package_id would
            # turn one config-wide complaint into a distinct string per
            # package sharing it, defeating the dedup below.
            #
            # That gap is closed (stn-hwo). pipeline.read_lockfile used to
            # raise ValueError when a vendored lockfile was damaged, which is
            # a broken INSTALL rather than a broken config -- and it arrived
            # here, on the config channel, under a heading saying the config
            # has a problem and a trailer telling the reader to fix it and
            # delete a directory by hand. It raises VendoredAssetError now,
            # which is deliberately not a ValueError, so it travels straight
            # past this collector to main, which reports it as what it is.
            problems.append(str(e))
            continue
        except (TypeError, AttributeError, KeyError) as e:
            # Unlike ValueError above, these never name the package -- e.g.
            # `docs: 7` raises "'int' object is not iterable" with nothing to
            # say which package -- so the id is added here.
            #
            # That prefix is a trade, not a free win, and the earlier version
            # of this comment claimed otherwise. A CONFIG-level key read
            # inside this per-package loop that raises a typed error is
            # reported once per package, each with a different id, which the
            # dedup below cannot collapse. `brand` was the reachable case and
            # is now typed in brand_problem; anything similar added later
            # should be typed at the point it is read, on the ValueError
            # channel, the way show_download_default already is.
            problems.append(f"Package {package_id}: {type(e).__name__}: {e}")
            continue

        # Gated on has_pages to match copy_brand_image's own predicate and
        # get_generated_files' -- a page-less package inheriting a
        # config-level image brand with no alt renders nothing that brand
        # could appear on, and must not newly fail because of it.
        if context.get("has_pages"):
            problem = brand_problem(package, config, config_dir)
            if problem:
                # Scope-aware, because brand_of falls back to the config:
                # ONE config-level brand mistake is inherited by every
                # package with pages, and prefixing each with its own
                # package id would produce N distinct strings that the
                # dedup below cannot collapse -- N bullets for one typo,
                # none of them naming the place it actually is.
                where = (
                    f"Package {package_id}" if package.get("brand") else "config"
                )
                problems.append(f"{where}: {problem}")
                continue

        contexts[package_id] = context

    if problems:
        _raise_config_problems(problems, trailer)

    return contexts


# The templates stencil injects itself, in render order. ONE list rather than
# one per caller: generate_package reads it as template definitions and
# get_generated_files reads it as destinations, and the two spellings drifting
# is not a hypothetical -- the comment in get_generated_files records what it
# cost last time, a package_sources-only doc package with five generated files
# that `clean` could not see and `.gitignore` did not cover.
#
# What still has to be kept in step is the PREDICATE each group is emitted on.
# tests/test_package_sources.py asserts every file a generated package holds is
# one get_generated_files names, which is the guard that catches the next
# addition whoever makes it.
SHARED_PAGE_TEMPLATES = [
    "frontmatter-filter.lua.j2",
    "hidden-filter.lua.j2",
    "mermaid-figure-filter.lua.j2",
    "figure-name-filter.lua.j2",
    "embed-images.lua.j2",
    # Decides whether the highlighter rides along; see
    # _CODE_BUNDLE_AFTER_HIDDEN in pipeline.py for why it runs last.
    "code-bundle-filter.lua.j2",
    # Drives the `pdf` compose service. Emitted for every package that renders
    # markdown, so `make pdf` needs no configuration to exist.
    "html-to-pdf.js.j2",
    # Shared image the pdf and check-access services build from, and the
    # lockfile its `npm ci` resolves through. The two travel together: the
    # Dockerfile COPYs the lockfile, so a package that has one and not the
    # other cannot build.
    "Dockerfile.browser.j2",
    "browser-package-lock.json.j2",
]

# Emitted for a doc package, and for one built only from package_sources --
# Makefile-pkg runs those through the doc service, which names this template.
DOC_PAGE_TEMPLATES = ["html-template.html.j2"]

SLIDE_PAGE_TEMPLATES = ["slide-template.html.j2", "slide-sections.lua.j2"]

# The compose file's format-md service copies this lockfile into place and runs
# `npm ci` against it, so the file has to exist wherever that service does.
#
# EMITTED FOR EVERY PACKAGE, WITH NO PREDICATE AT ALL, and the two predicates
# tried before it are why. Keyed off has_pages -- the obvious choice, matching
# everything else stencil injects -- it was not written for a `package_type:
# none` package with no docs, whose `templates:` list still produces a compose
# file, because `templates:` is config-level. `make format-md` worked for that
# package before this change and would have started failing on `cp: can't
# stat`. Keyed off the compose file's own name instead, it was not written for
# a config that renames the output, and then not for a consumer whose
# composition template has a name of its own and pulls stencil's partial in by
# include -- which nothing here can see, because an include statement lives
# inside a template body and this function has neither the environment nor the
# search path to resolve one.
#
# Each fix made the predicate narrower and left a case behind. The file is 1.3
# KB, `stencil clean` removes it and the managed .gitignore covers it, so the
# cost of emitting it for a package that turns out not to need it is a small
# unused file -- against a build target that fails for a consumer who did
# nothing wrong. That is not a close trade, and a predicate nobody can get
# right is worse than no predicate.
ALWAYS_TEMPLATES = ["format-package-lock.json.j2"]


def template_dest(src: str) -> str:
    """The filename a template renders to, absent an explicit ``dest``."""
    return src.removesuffix(".j2")


def when_holds(tdef: dict, context: dict) -> bool:
    """Whether a template definition's ``when`` condition is satisfied.

    A name, or a list of names, all of which must be truthy in the context.
    One spelling, because three copies of this loop is how a template starts
    being rendered under one condition and cleaned under another.
    """
    when = tdef.get("when")
    if when is None:
        return True
    if isinstance(when, str):
        when = [when]
    return all(context.get(key) for key in when)


def injected_sources(context: dict) -> list[str]:
    """Every template stencil adds to a package's own list, in render order."""
    sources: list[str] = []
    if context.get("has_pages"):
        if context.get("has_docs") or context.get("has_package_sources"):
            sources += DOC_PAGE_TEMPLATES
        sources += SHARED_PAGE_TEMPLATES
        if context.get("has_slides"):
            sources += SLIDE_PAGE_TEMPLATES
    sources += ALWAYS_TEMPLATES
    return sources


def injected_templates(context: dict) -> list[dict]:
    """``injected_sources`` as template definitions render_templates accepts."""
    return [{"src": src} for src in injected_sources(context)]


def write_manifest(
    output_dir: Path,
    package_id: str,
    pkg_dir: str,
    entries: set[str] | list[str],
    dry_run: bool = False,
    *,
    dir_fd: int | None = None,
) -> None:
    """Write ``<output_dir>/MANIFEST_NAME``, recording exactly what this
    generate_package call produced for one package.

    ``entries`` must come from ``package_entries`` -- see its docstring for
    why a second derivation of the same list is the drift bug this exists to
    avoid. Never written under ``--dry-run``: a preview's manifest would tell
    a later, manifest-driven `clean` about files that were never actually
    produced.

    `dir_fd`, OPTIONAL AND KEYWORD-ONLY (stn-avv, stn-6mcb.5): a descriptor
    on `output_dir` itself. The manifest is always a TOP-LEVEL name in the
    package directory, so writing through it needs no walk -- just the base
    descriptor and `MANIFEST_NAME`. `None` -- today's behaviour, unchanged
    -- writes through the full `manifest_path` instead.
    """
    manifest_path = output_dir / MANIFEST_NAME
    if dry_run:
        print(f"Would write: {manifest_path}")
        return

    document = {
        "manifest_version": MANIFEST_VERSION,
        "stencil_version": __version__,
        "package": package_id,
        "dir": pkg_dir,
        "entries": sorted(entries),
    }
    text = json.dumps(document, sort_keys=True, indent=2) + "\n"
    if dir_fd is not None:
        write_text_nofollow(Path(MANIFEST_NAME), text, dir_fd=dir_fd)
    else:
        write_text_nofollow(manifest_path, text)
    print(f"Generated: {manifest_path}")


class ManifestError(RuntimeError):
    """A per-package manifest is present but unreadable, damaged, or from an
    unrecognized version -- not a config mistake.

    stn-2x4.4, same reasoning as `pipeline.VendoredAssetError` (see its
    docstring): `ValueError` is the channel `package_contexts` collects
    CONFIG problems on, and a damaged manifest is not a config problem. No
    amount of editing `.config.yaml` fixes a manifest that will not parse;
    deleting the manifest and letting a manifest-aware `clean` fall back to
    deriving from the config does. Raising `ValueError` here would put a
    manifest problem under "the config has these problems", which is a
    false diagnosis pointing the reader at the wrong file.

    Deliberately NOT a subclass of `ValueError`, for the same reason
    `VendoredAssetError` is not one: making it a `ValueError` would let it
    travel silently on the config-problem channel instead of being caught
    and reported on its own terms.
    """


# 1 MiB is generous for a file that is a sorted list of relative paths --
# real manifests run to a few KiB. The cap exists so a manifest cannot make
# this command hang or exhaust memory before anything is even parsed; see
# read_manifest's docstring for the measurements behind it (review finding
# A7).
_MANIFEST_MAX_BYTES = 1024 * 1024
_MANIFEST_MAX_ENTRIES = 100_000


def _reject_duplicate_manifest_keys(pairs: list[tuple[str, object]]) -> dict:
    """``object_pairs_hook`` for ``json.loads``: refuse a repeated top-level key.

    ``json.loads('{"entries": ["safe"], "entries": ["EVIL"]}')`` returns
    ``{"entries": ["EVIL"]}`` -- the JSON spec permits duplicate keys and the
    stdlib parser silently keeps the last one. A manifest committed to a
    repo can therefore show a human one list in the diff and hand Python
    another. There is no legitimate reason for a generated manifest to
    repeat a key, so any repeat is refused outright rather than resolved by
    picking a value the human reviewing the diff might never have seen.
    """
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r}")
        seen.add(key)
    return dict(pairs)


def read_manifest(path: Path) -> dict:
    """Parse a per-package manifest file and return the parsed document.

    Takes the path to a manifest FILE and returns the parsed document (a
    ``dict``) -- ``manifest_version``, ``stencil_version``, ``package``,
    ``dir`` and ``entries``. Returning ``None`` for "no manifest" is NOT
    this function's job: the caller checks ``path.is_file()`` (never
    ``path.exists()``) before calling this at all, so a directory or a FIFO
    at the manifest's name is never opened here.

    Every rejection below raises ``ManifestError``, never ``ValueError`` --
    see ``ManifestError``'s docstring for why a damaged manifest must not
    travel on the channel ``package_contexts`` collects config problems on.
    A damaged manifest does NOT fall back to deriving from the config; that
    is not the same statement as no manifest being present, and quietly
    re-deriving would be exactly the guessing this manifest exists to
    remove. Every message here says so: delete the manifest to restore the
    config-derived fallback.

    FORWARD COMPATIBILITY (review finding D7): within a KNOWN
    ``manifest_version``, UNKNOWN EXTRA keys in the document are IGNORED, not
    refused. Strict key validation would make a v1 manifest written by a
    later stencil unreadable by this one the moment that later version adds
    a diagnostic field -- exactly backwards from what a version field is
    for. That guarantee is about keys nothing below requires; it says
    nothing about the ones that follow.

    REQUIRED FIELDS (stn-jez): ``manifest_version``, the shape of
    ``entries``, and -- checked last, below both of those -- the presence of
    ``package``, ``dir`` and ``stencil_version`` as strings. `write_manifest`
    has emitted all five fields since manifest v1 was introduced (verified:
    ``git show 1e25490``), so no manifest stencil ever wrote is refused by
    this. What it does refuse is a manifest missing one of them entirely --
    which used to reach ``_clean_one_directory``'s ownership guard and read
    as "no opinion", not as "untrusted" -- and requiring a field the writer
    has always emitted does not weaken the forward-compatibility guarantee
    above, which was never about these five.

    HARDENING (review finding A7), all measured on this interpreter:

    - a manifest over ``_MANIFEST_MAX_BYTES`` is refused by a ``stat()``
      BEFORE it is read at all, and ``len(entries)`` is capped after
      parsing. A manifest is a file on disk that nothing has validated, so
      an arbitrarily large one must not be allowed to make this command
      hang or exhaust memory -- the same reasoning ``_MAX_PROBLEM_CHARS``
      applies to config text, for a file that is strictly less trustworthy.
    - ``json.loads("[" * 200000 + "]" * 200000)`` raises ``RecursionError``,
      which is NEITHER a ``ValueError`` NOR a ``json.JSONDecodeError`` and
      would otherwise escape any handler that only catches those, reaching
      the terminal as a bare traceback -- from the one command whose entire
      premise is working when everything else is broken. Caught here
      alongside ``OSError`` (file unreadable) and ``ValueError`` (bad JSON,
      including a duplicate key) and re-raised as ``ManifestError``.
    - duplicate top-level keys are refused via ``object_pairs_hook`` (see
      ``_reject_duplicate_manifest_keys``): without it,
      ``json.loads('{"entries": ["safe"], "entries": ["EVIL"]}')`` silently
      returns ``EVIL``, so a manifest committed to a repo could show a
      human one list in the diff and hand Python another.

    EVERY manifest-derived value that reaches a message here goes through
    `_safe` (review finding A8) -- the filename, `manifest_version`, and
    the document's `package` / `dir` fields when available for context.
    `_safe` exists because config text can repaint the terminal and
    `_MAX_PROBLEM_CHARS` because it can bury the report; manifest text is
    strictly LESS trustworthy than config text, since it is a JSON file
    that nothing has validated yet, read at the exact moment a human is
    staring at the terminal because something is already wrong.
    """
    path = Path(path)
    safe_name = _safe(str(path))

    try:
        size = path.stat().st_size
    except OSError as error:
        raise ManifestError(f"cannot read manifest {safe_name}: {error}") from error
    if size > _MANIFEST_MAX_BYTES:
        raise ManifestError(
            f"manifest {safe_name} is {size} bytes, over the "
            f"{_MANIFEST_MAX_BYTES}-byte limit -- refusing to read it. "
            "Delete the manifest to fall back to deriving from the config."
        )

    try:
        document = json.loads(
            path.read_text(), object_pairs_hook=_reject_duplicate_manifest_keys
        )
    except (OSError, ValueError, RecursionError) as error:
        raise ManifestError(
            f"manifest {safe_name} could not be parsed: {_safe(str(error))}. "
            "Delete the manifest to fall back to deriving from the config."
        ) from error

    if not isinstance(document, dict):
        raise ManifestError(
            f"manifest {safe_name} is not a JSON object (found "
            f"{_safe(type(document).__name__)}). Delete the manifest to "
            "fall back to deriving from the config."
        )

    def _context() -> str:
        """A best-effort ``(package "x", dir "y")`` suffix for a message.

        A missing or malformed field just drops out of the suffix here --
        this helper only decides what the parenthetical SAYS, never whether
        the manifest is accepted. That used to make a missing ``package`` or
        ``dir`` "not itself a rejection" in the fuller sense too, but it no
        longer is: the required-field check below raises `ManifestError` for
        exactly that. This helper runs ABOVE that check (it is used by the
        ``manifest_version`` and ``entries`` messages too, which fire before
        a missing `package`/`dir` would even be checked), so it still has to
        tolerate both fields being absent -- it just no longer gets the last
        word on whether that absence is fine. Every value is `_safe`-guarded:
        it came from the same unvalidated document as everything else here.
        """
        parts = []
        for key in ("package", "dir"):
            value = document.get(key)
            if isinstance(value, str):
                parts.append(f'{key} "{_safe(value)}"')
        return f" ({', '.join(parts)})" if parts else ""

    version = document.get("manifest_version")
    if version != MANIFEST_VERSION:
        raise ManifestError(
            f"manifest {safe_name}{_context()} has an unrecognized "
            f"manifest_version {_safe(repr(version))} (this stencil knows "
            f"version {MANIFEST_VERSION!r}). Delete the manifest to fall "
            "back to deriving from the config."
        )

    entries = document.get("entries")
    if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
        raise ManifestError(
            f"manifest {safe_name}{_context()} has no valid \"entries\" "
            "list of strings. Delete the manifest to fall back to deriving "
            "from the config."
        )
    if len(entries) > _MANIFEST_MAX_ENTRIES:
        raise ManifestError(
            f"manifest {safe_name}{_context()} has {len(entries)} entries, "
            f"over the {_MANIFEST_MAX_ENTRIES}-entry limit -- refusing to "
            "read it. Delete the manifest to fall back to deriving from "
            "the config."
        )

    # stn-jez. Checked LAST -- below manifest_version and below entries --
    # because two existing parametrised regression tests in
    # tests/test_manifest.py assert on the message they name
    # (id=entries-not-list-of-strings expects "entries",
    # id=unknown-manifest-version expects "manifest_version") from a fixture
    # that also happens to omit `stencil_version`; checking this first would
    # turn both red for the wrong reason. See the REQUIRED FIELDS paragraph
    # above for why enforcing these does not weaken forward compatibility.
    #
    # `write_manifest` has emitted `package`, `dir` and `stencil_version` on
    # every manifest since v1 was introduced (verified: `git show 1e25490`),
    # so no manifest stencil ever wrote is refused here. What used to be
    # refused by nothing at all was a manifest missing one of them entirely:
    # `_clean_one_directory`'s ownership guard reads `manifest.get("package")`
    # and only refuses when that IS a string and unrecognized -- a missing
    # key made the guard's own `isinstance` check False, so it read as "no
    # opinion" instead of "untrusted".
    for key in ("package", "dir", "stencil_version"):
        value = document.get(key)
        if not isinstance(value, str):
            raise ManifestError(
                f'no valid "{key}"{_context()} (found {_safe(repr(value))}) '
                "-- every manifest stencil writes has one. Delete the "
                "manifest to fall back to deriving from the config. "
                f"Manifest: {safe_name}"
            )

    return document


def template_destinations(template_defs: list, context: dict) -> list[tuple[str, str]]:
    """Every (template name, destination) pair one package will actually
    render, `when` conditions already applied.

    Lifted out of `render_templates` (stn-h5q) because `generate_package`'s
    pre-pass has to check the same destinations BEFORE the renderer writes
    the first of them, and two spellings of "which templates survive their
    `when`" is the drift bug this file has already paid for three times --
    see `package_entries`' docstring for the other two.
    """
    destinations = []
    for tdef in template_defs:
        if not when_holds(tdef, context):
            continue
        src = tdef["src"]
        declared = tdef.get("dest")
        # PROVENANCE-INDEPENDENT (adversarial review of stn-h5q, HIGH 3).
        # `package_contexts` checks a DECLARED `dest` and skips the key when
        # it is absent -- but the destination is then `src` with `.j2`
        # removed, and `src` goes through no check anywhere. Measured:
        # `src: "a b.txt.j2"` generated `a b.txt` at exit 0, recorded it in
        # the manifest, and `clean` then refused ITS OWN manifest entry for
        # containing whitespace -- a permanently un-cleanable package whose
        # error names a "manifest entry" the author never wrote. That is
        # stn-9rn's harm arriving through the one key nobody checked, and
        # this is the single place both spellings of the destination now
        # pass through.
        #
        # `where` names the key the AUTHOR wrote, not the one stencil
        # derived, so the message points at the line to edit.
        dest = declared if declared is not None else template_dest(src)
        where = "dest" if declared is not None else "src"
        check_config_path("config", where, dest)
        # AND the glob refusal, which is opt-in rather than part of
        # `_UNSAFE_IN_PATH` (globs are the point of a `package_sources`
        # pattern) and was wired to docs, slides, dir and both output_dirs
        # -- and not to this one. Measured: `dest: "*.txt"` generated at
        # exit 0, went into the manifest verbatim, and `clean` then refused
        # it as "not a recognized glob shape" forever. stn-9rn's harm again,
        # through the very channel this function was added to close.
        check_no_glob("config", where, dest)
        destinations.append((src, dest))
    return destinations


def write_targets(context: dict, template_defs: list) -> list[tuple[str, str]]:
    """Every (where, path-relative-to-the-package-directory) `gen` is about
    to write for one package: each surviving template's destination, the
    copied brand image, and the manifest.

    `where` is the config key to name in a refusal, so an author reading one
    is pointed at the line they wrote rather than at a filename stencil
    derived.

    Deliberately NOT `package_entries`. That function lists what a package
    PRODUCES, including build artifacts `gen` never writes itself -- the
    `Guide*.html` and `Guide*.pdf` globs `make` produces later. This lists
    what THIS CALL writes, which is the only set a write-side check can say
    anything about.
    """
    targets = [("dest", dest) for _src, dest in template_destinations(template_defs, context)]
    if context.get("has_pages"):
        # Named from the unresolved config string, which is how
        # copy_brand_image names its destination and how package_entries
        # reads it back (stn-8wt). A third spelling here would be the same
        # drift with a new author.
        brand_image = brand_image_path(context.get("config_brand"))
        if brand_image:
            targets.append(("brand", Path(brand_image).name))
    targets.append(("manifest", MANIFEST_NAME))
    return targets


def contained_entry_parent(
    package_id: str,
    where: str,
    declared: str,
    unresolved: Path,
    root: Path,
    pkg_path: Path,
) -> Path:
    """Resolve one entry's PARENT directory and require it under both the
    output base and the package directory. Returns the resolved parent.

    ONE RULE, TWO CALLERS, which is the whole of stn-h5q's thesis: `clean`
    has applied this since stn-2x4.6 (`_remove_entries`) and `gen` applied
    nothing, so gen wrote a file clean then refused to remove. Two sides
    resolving different components of the same path IS the defect class, so
    a second copy of this five-line rule -- agreeing today, drifting later --
    would have closed the symptom and left the cause. This file has paid for
    that shape three times already; see `package_entries`' docstring.

    Not folded into `contained_path`, which is a different rule: that one is
    rooted at ONE base and is about a package DIRECTORY, and stn-17h now has
    it refuse a candidate equal to its root -- correct for a package
    directory, wrong here, where `dest: Makefile` legitimately has the
    package directory itself as its parent.

    THE PARENT ONLY, never the entry's own final component. That asymmetry
    is deliberate on clean's side (`unlink` does not follow a final-component
    symlink, so refusing one would make such a package permanently
    un-cleanable) and gen checks that component separately, by lstat, in
    `checked_write_target`. What the two sides share is exactly this: the
    directory the entry sits in must be inside the tree.
    """
    try:
        parent_resolved = unresolved.parent.resolve()
    except OSError as error:
        raise ValueError(
            f"Package {package_id}: {where} {declared!r} could not be "
            f"resolved: {error} -- refusing to touch it"
        ) from error
    if not (
        parent_resolved.is_relative_to(root)
        and parent_resolved.is_relative_to(pkg_path)
    ):
        raise ValueError(
            f"Package {package_id}: {where} {declared!r} resolves to "
            f"{parent_resolved}, outside the package directory {pkg_path} "
            "-- refusing to touch it"
        )
    return parent_resolved


# stn-avv (stn-6mcb.7). Whether the descriptor-walk mechanism below --
# `walk_dir_fd`, and the `dir_fd=` keyword on the two nofollow writers --
# can run at all. MEASURED on this macOS (APFS, Python 3.13) and on Linux:
# all four conditions hold, so `os.open(component, ..., dir_fd=parent)` and
# `os.mkdir(component, dir_fd=parent)` both work; all four are absent on
# Windows, which has no dir_fd support whatsoever and keeps
# `checked_write_target`'s lstat pre-pass as its only defence -- stated here
# and in STENCIL.md rather than implied away.
#
# READ AT CALL TIME, EVERYWHERE THIS IS USED -- never capture it in a
# function's DEFAULT ARGUMENT. A default argument is evaluated exactly once,
# when the enclosing `def` statement runs at import time, so
# `def f(x, capable=_DIR_FD_CAPABLE):` would freeze whatever this measured
# on THIS platform forever; no test could then force the Windows-shaped
# fallback path on a machine where the flag is naturally True. Referencing
# the bare name `_DIR_FD_CAPABLE` inside a function BODY instead re-reads
# this module global on every call, which is what lets
# `monkeypatch.setattr(generate, "_DIR_FD_CAPABLE", False)` actually change
# behaviour in a test -- and is the only way the "the fallback still
# behaves as today" pin can run at all.
_DIR_FD_CAPABLE = (
    hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
    and os.mkdir in os.supports_dir_fd
)


def walk_dir_fd(base_fd: int, relative: str) -> tuple[int, str]:
    """Walk `relative`'s INTERMEDIATE components below `base_fd` by
    descriptor rather than by path, returning `(parent_fd, final_name)` for
    the caller to write through with `os.open(final_name, ..., dir_fd=
    parent_fd)`. The object a caller checks by opening it here and the
    object it later writes are then the same inode by construction --
    stn-avv's fix for the gap `checked_write_target`'s docstring names: a
    symlink swapped in at an INTERMEDIATE directory between a path-based
    pre-pass and a path-based write.

    Requires `_DIR_FD_CAPABLE` (read fresh here -- see the comment above
    it). A caller must not reach this function on a platform where that is
    False; it refuses loudly with `NotImplementedError` rather than passing
    an unsupported `dir_fd=` keyword to `os.open` and letting a raw
    `TypeError` stand in for a real diagnosis.

    OWNERSHIP, AND IT IS UNIFORM ON PURPOSE: the returned `parent_fd` is
    ALWAYS the caller's to close, in every case, with no test for which case
    this was. Intermediates this walk acquires and does not return are closed
    here on both the success and the failure path (try/finally); `base_fd`
    itself belongs to the caller and is never closed here.

    That uniformity costs one `os.dup` and is the whole reason for it. A
    single-component `relative` -- `Makefile`, and every other top-level file
    a package contains, i.e. the COMMON case -- has no intermediates at all,
    so the natural thing to hand back is `base_fd`. Then `parent_fd` is
    sometimes the caller's own long-lived package-directory descriptor and
    sometimes a fresh one, and a caller that closes what it was given (the
    obvious reading of "handed back for the caller to use and then close")
    closes the package directory out from under itself after the first
    top-level write, so every write after it fails on a stale descriptor.
    Returning a `dup` makes "close what you were given" correct always,
    rather than correct only for nested destinations.

    THE TRAP, measured (stn-6mcb.7 spike note 3) and the reason the loop
    below is not the "try mkdir, except FileExistsError: pass" pattern it
    looks like it should be. `os.mkdir(name, dir_fd=parent)` over an
    EXISTING SYMLINK raises `FileExistsError` -- the exact same exception a
    benign "the directory is already there" raises. A caught-and-ignored
    `FileExistsError` therefore proves NOTHING about what `name` actually
    is: swallowing it would treat an attacker's symlink as though it were
    the real directory it asked for. What saves this is that the walk
    ALWAYS re-opens the component afterwards with
    `O_RDONLY|O_DIRECTORY|O_NOFOLLOW`, and THAT open -- never the mkdir --
    is measured to refuse the symlink case. This is invisible on the happy
    path, which is exactly why it is written down here rather than left for
    whoever "simplifies" the mkdir/except pattern later to rediscover the
    hard way.

    DO NOT BRANCH ON ERRNO. Measured: the identical symlink-to-directory
    open above is refused with ENOTDIR on macOS and ELOOP on Linux -- two
    different errno values for the same attack, from two kernels that both
    correctly refuse it. Treating one errno as "the" refusal would silently
    stop refusing on whichever platform did not get tested against. ANY
    `OSError` from the component open is the refusal, and the message
    carries `strerror` rather than a hardcoded diagnosis of what the errno
    means. (A regular file at an intermediate component is refused the same
    way, for the same reason `checked_write_target` already gives it:
    `O_DIRECTORY` on a non-directory is ENOTDIR too.)
    """
    if not _DIR_FD_CAPABLE:
        raise NotImplementedError(
            "walk_dir_fd requires O_DIRECTORY/O_NOFOLLOW dir_fd support, "
            "which this platform does not have -- the caller must use the "
            "path-based fallback instead"
        )

    parts = Path(relative).parts
    if not parts:
        raise ValueError(f"{relative!r} has no components to walk")

    if len(parts) == 1:
        # No intermediates to walk. Duplicated rather than returned as-is so
        # the caller owns what it is handed in every case -- see OWNERSHIP
        # above for the bug that costs.
        return os.dup(base_fd), parts[-1]

    current_fd = base_fd
    # An fd THIS walk opened and has not yet closed -- None means there is
    # currently nothing pending closure. Distinct from `base_fd`, which is
    # never ours to close.
    pending_fd = None
    succeeded = False
    try:
        for part in parts[:-1]:
            try:
                os.mkdir(part, dir_fd=current_fd)
            except FileExistsError:
                # Proves nothing by itself -- see the docstring. The open
                # immediately below is what actually decides whether `part`
                # is a directory this walk may descend into.
                pass

            try:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current_fd,
                )
            except OSError as error:
                raise ValueError(
                    f"could not open {part!r} as a directory while writing "
                    f"{relative!r} ({error.strerror}) -- refusing to write "
                    "through it"
                ) from error

            if pending_fd is not None:
                os.close(pending_fd)
            pending_fd = next_fd
            current_fd = next_fd

        succeeded = True
        return current_fd, parts[-1]
    finally:
        if not succeeded and pending_fd is not None:
            os.close(pending_fd)


def checked_write_target(
    package_id: str, where: str, root: Path, pkg_path: Path, relative: str
) -> Path:
    """One path `gen` is about to write, checked at EVERY component below the
    package directory (stn-h5q). Returns the joined path.

    stn-vhr contains the package DIRECTORY. This is the half below it: in
    every case stn-h5q reproduces, the package directory is a genuine
    directory that resolves cleanly and passes that check, and what leaves
    the tree is a component underneath -- which no directory-level check can
    see.

    THREE REFUSALS, and the second is the reason this stats components
    rather than resolving the path again:

    - A SYMLINK at any component. `write_text` opens O_WRONLY|O_CREAT|O_TRUNC
      with no O_NOFOLLOW, and `mkdir(parents=True, exist_ok=True)` walks a
      link happily, so both the file itself and a subdirectory a nested
      `dest` writes through led straight out of the tree at exit 0.
    - A HARDLINK at the final component (`st_nlink > 1` on a regular file).
      `resolve()` reports such a path CONTAINED, because it is: the second
      name for the inode lives outside and no `relative_to` can ever see it.
      `st_nlink` is the only thing that distinguishes it.
    - ANYTHING THAT IS NOT THE KIND OF FILE `gen` WRITES: a component that
      exists but is not a directory where a directory must go, or a final
      component that exists and is not a regular file. A FIFO planted at a
      destination would otherwise block the whole run on `open`, and a
      device node would be written to.

    It also calls `contained_entry_parent`, the rule `clean` has applied to
    every entry since stn-2x4.6 -- resolve the PARENT, require it under both
    `root` and `pkg_path` -- because gen and clean resolving DIFFERENT
    components of one path is the defect class stn-h5q is about, not a
    detail of it.

    WHAT THAT MAKES SYMMETRIC, stated exactly rather than generously: the
    PARENT half. gen and clean now compute it with one function. The final
    component is deliberately NOT symmetric and must not be made so -- gen
    refuses a link there (it would follow it) while clean unlinks one
    without resolving it (removing the link is the only way such a package
    is ever cleanable again). Each side does what its own operation
    requires. The two computations of the parent also differ in one way
    worth knowing: at gen time the directory may not exist yet, so
    `resolve()` is lexical, while clean runs after gen created it and
    resolves for real. Same rule, same path, different ground truth
    available.

    `pkg_path` must already be resolved (`contained_path`'s return value),
    the same contract `_remove_entries` has.

    WHAT IT DOES NOT CLOSE, stated exactly rather than comfortably: a
    symlink planted at an INTERMEDIATE directory between this check and the
    write. The write sites add O_NOFOLLOW, which covers the final component
    only, and `render_templates` joins its path unresolved -- so such a
    write lands outside the output base with no containment left, and the
    manifest then names entries whose parent `clean` resolves through the
    same link, which means `clean` deletes there too.

    An earlier draft of this paragraph borrowed stn-vhr's sentence -- "anyone
    who can swap a directory for a symlink mid-run can already write wherever
    the running user can" -- and that is NOT true here and is not repeated.
    Swapping a component needs write permission on ONE directory inside the
    output tree; being the running user is a different and much larger
    capability, and the gap between them is exactly the deployment stencil
    has (a CI runner, a shared teaching machine, an `out/` that arrived with
    a merged pull request).

    It is closable, with a descriptor walk -- `os.open(component,
    O_RDONLY|O_DIRECTORY|O_NOFOLLOW)` per component and `dir_fd=` on the
    write, so the object checked and the object written are the same inode
    by construction. That is a larger change than this one and is filed with
    its reproduction rather than described here as impossible.
    """
    check_config_path(package_id, where, relative)
    target = pkg_path / relative

    # THE COMPONENT WALK RUNS FIRST, and the order is a message decision
    # rather than a correctness one. A symlinked intermediate directory
    # fails both checks; the parent check reports it as "dest 'sub/Makefile'
    # resolves to /somewhere/outside", which is true and sends the author to
    # edit a config line that is not the problem, while the walk names
    # `.../out/demo/sub` -- the link itself -- and says how to clear it.
    parts = Path(relative).parts
    current = pkg_path
    for index, part in enumerate(parts):
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            # Nothing here yet, so nothing below it either: `gen` creates
            # this component and everything under it.
            break
        except OSError as error:
            raise ValueError(
                f"Package {package_id}: {where} {relative!r} could not be "
                f"checked at {part!r}: {error} -- refusing to write it"
            ) from error

        last = index == len(parts) - 1
        if stat.S_ISLNK(info.st_mode):
            # NAMES THE COMPONENT, not only the declared value. For a nested
            # `dest` the two differ -- the config says `sub/Makefile` and the
            # link is `sub` -- and a message naming only the declared value
            # sends the author to edit a config line that is not the problem.
            #
            # The recovery differs by which component it is, so the message
            # says which. A link AT the file is removable by `stencil clean`,
            # which unlinks a final component without resolving it
            # (_remove_entries, and the test that pins it). A link at an
            # INTERMEDIATE directory is not: `clean` refuses that entry
            # because its parent resolves outside the package, deliberately
            # and permanently, so only `rm` clears it.
            #
            # KEPT SHORT ON PURPOSE. `_safe` truncates a problem at
            # _MAX_PROBLEM_CHARS, and these messages carry a resolved
            # absolute path -- a first draft of this one explained itself at
            # length and had the recovery advice, the part the reader needs,
            # cut off the end.
            # WHERE THE LINK GOES decides the advice, because `clean`'s
            # rule is about the resolved PARENT rather than about links.
            # A final-component link, and an intermediate one that lands
            # back inside the package, are both removable by `clean`; only
            # one resolving outside is not. Saying "clean cannot remove it"
            # for all three was false for two of them.
            try:
                inside = current.resolve().is_relative_to(pkg_path)
            except OSError:
                inside = False
            recovery = (
                "`stencil clean` removes it"
                if last or inside
                else "`clean` cannot remove it either, so delete the link "
                "yourself"
            )
            # THE PATH GOES LAST. `_safe` truncates at _MAX_PROBLEM_CHARS and
            # this message carries a RESOLVED ABSOLUTE path, whose length
            # belongs to the machine rather than to the author -- a GitHub
            # Actions checkout reaches the limit on its own, and `_generate`
            # spends another sixty characters re-prefixing the package id.
            # An earlier version put the path in the middle and lost both
            # the diagnosis and the recovery off the end, while still
            # carrying a comment claiming it was kept short enough. Ordered
            # so that what survives truncation is the part that tells the
            # reader what to do.
            raise ValueError(
                f"Package {package_id}: {where} {relative!r} passes through "
                f"a symlink -- refusing to write through it, "
                f"{recovery}. The link is: {current}"
            )

        if not last:
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError(
                    f"Package {package_id}: {where} {relative!r} passes "
                    f"through {part!r}, which exists and is not a directory "
                    "-- refusing to write through it."
                )
            continue

        if not stat.S_ISREG(info.st_mode):
            raise ValueError(
                f"Package {package_id}: {where} {relative!r} already exists "
                "and is not a regular file -- refusing to write over it."
            )
        # st_nlink ONLY on a regular final component. Directories always
        # have st_nlink >= 2 (`.` and the parent's entry), so leaking this
        # rule onto intermediate components would refuse every config on
        # earth -- and a directory hardlink is not a vector to cover anyway:
        # `os.link()` on a directory is EPERM on macOS and unsupported on
        # Linux.
        if info.st_nlink > 1:
            raise ValueError(
                f"Package {package_id}: {where} {relative!r} is a hardlink "
                f"(st_nlink={info.st_nlink}): the same file has another name "
                "outside this package directory, which no path check can "
                "see. Remove it and run `gen` again."
            )

    # Belt and braces, deliberately kept rather than trimmed as unreachable.
    # With no symlink at any component the parent cannot resolve outside, so
    # this should never fire for a path the walk just cleared -- but it is
    # the rule `clean` applies, called through the one function both sides
    # share, and the walk is a snapshot while this is a resolve. Cheap, and
    # the day the walk gains a `break` in the wrong place this is what still
    # holds the line.
    contained_entry_parent(package_id, where, relative, target, root, pkg_path)

    return target


def open_for_write_nofollow(path: Path, *, dir_fd: int | None = None):
    """`open(path, "wb")`, refusing a symlink at the final component.

    The check-to-write window is small and it is real: `checked_write_target`
    stats a path and the write happens afterwards. O_NOFOLLOW closes it for
    the component that matters most -- the file itself -- by failing with
    ELOOP rather than following a link swapped in meanwhile. This is the
    spelling `copy_brand_image` already used for its SOURCE, applied to the
    side that writes.

    Windows has no O_NOFOLLOW; `getattr` there leaves the flags unchanged,
    which is the same accommodation the brand read makes.

    `dir_fd`, OPTIONAL AND KEYWORD-ONLY (stn-avv, stn-6mcb.7): when given,
    `path` is resolved relative to it via `os.open`'s own `dir_fd=`
    parameter, exactly the way `walk_dir_fd` hands back a `(parent_fd,
    final_name)` pair for a caller to write through. `None` -- the default
    -- is today's behaviour, unchanged: `path` is resolved by the OS the
    ordinary way, absolute or relative to the process's cwd. Nothing else
    about this function's contract changes for either case.

    THE WRITE-TIME FSTAT GATE (stn-avv, stn-6mcb.7; architecture review).
    `checked_write_target` refuses three things at its pre-pass -- a
    symlink, a hardlink, and "not the kind of file gen writes" -- but a
    pre-pass is a snapshot. O_NOFOLLOW above closes the symlink refusal at
    write time, because the kernel itself refuses to open a link with that
    flag; the other two refusals were snapshot-only until now. This closes
    them the same way: check the descriptor `os.open` actually returned,
    not a path stat taken earlier.

    MEASURED sequence (spike, this macOS): no `O_TRUNC` at open time --
    `os.ftruncate` below does that job, but only AFTER the fstat gate
    approves what got opened. `os.fstat` the descriptor; refuse a
    non-regular file (the kind check) and refuse `st_nlink > 1` (the
    hardlink check) -- both against the fd's OWN inode, which is the object
    actually about to be written rather than whatever a second path lookup
    might find. A FIFO planted at the destination is refused before any of
    this: `O_NONBLOCK` makes the `os.open` call itself fail ENXIO, measured,
    because there is no reader -- so `fstat` is never reached and the walk
    never blocks waiting for one. The descriptor is closed on every refusal
    path here; one that leaked the fd it just opened would be the exact
    failure mode this gate exists to close.
    """
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    # O_NONBLOCK, for the one node type O_NOFOLLOW says nothing about.
    # `checked_write_target` refuses a FIFO at a destination, but that is a
    # snapshot; one swapped in afterwards would otherwise block this open
    # FOREVER -- no timeout, a CI job burning its whole wall clock -- and
    # `mkfifo` needs no privilege at all. With O_NONBLOCK and no reader the
    # same open fails ENXIO. On a regular file POSIX says the flag has no
    # effect, so this costs the ordinary path nothing.
    nonblock = getattr(os, "O_NONBLOCK", 0)
    # NO O_TRUNC. Truncating at open time would happily clear a hardlink's
    # shared inode, or a symlink's target on a platform with no O_NOFOLLOW,
    # before this function ever gets a chance to refuse it. `os.ftruncate`
    # below does the same job, moved to AFTER the fstat gate.
    flags = os.O_WRONLY | os.O_CREAT | nofollow | nonblock
    descriptor = os.open(path, flags, 0o666, dir_fd=dir_fd)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(
                "refusing to write: the descriptor just opened is not a "
                f"regular file (mode {oct(stat.S_IFMT(info.st_mode))}) -- "
                f"{path}"
            )
        if info.st_nlink > 1:
            raise ValueError(
                "refusing to write: the descriptor just opened is a "
                f"hardlink (st_nlink={info.st_nlink}), so another name for "
                f"this file exists outside the package, which no path "
                f"check can see -- {path}"
            )
        os.ftruncate(descriptor, 0)
        return os.fdopen(descriptor, "wb")
    except Exception:
        # fdopen can also raise between the open and the wrapper taking
        # ownership, and the descriptor would leak for the life of the
        # process. Every failure path here closes itself.
        os.close(descriptor)
        raise


def write_text_nofollow(
    path: Path, text: str, executable: bool = False, *, dir_fd: int | None = None
) -> None:
    """`Path.write_text` without following a symlink at the final component.

    UTF-8, DECIDED RATHER THAN INHERITED. `Path.write_text` with no encoding
    argument -- which is what these call sites used -- writes in the locale's
    preferred encoding, so the bytes a generated Makefile got depended on the
    shell that ran `stencil gen`. Everything stencil renders is UTF-8: the
    templates are, the config is read as UTF-8 by yaml, and the pandoc
    invocations in the generated compose file assume it. Switching to an
    explicit `os.open` is the moment that inconsistency has to be settled one
    way or the other, and settling it silently on a security fix is the thing
    worth avoiding, so it is settled here, out loud: UTF-8 on every platform.
    On any machine whose locale was already UTF-8 -- which is CI and every
    developer machine this has run on -- nothing about the bytes changes.

    `executable` marks the file `+x` through the descriptor just written,
    rather than by re-opening the path: see the call site in
    `render_templates`.

    `dir_fd`, OPTIONAL AND KEYWORD-ONLY (stn-avv, stn-6mcb.7): passed
    straight through to `open_for_write_nofollow`. `None` is today's
    behaviour; nothing else about this function's contract changes.
    """
    with open_for_write_nofollow(path, dir_fd=dir_fd) as handle:
        handle.write(text.encode("utf-8"))
        if executable:
            descriptor = handle.fileno()
            os.fchmod(
                descriptor,
                os.fstat(descriptor).st_mode
                | stat.S_IXUSR
                | stat.S_IXGRP
                | stat.S_IXOTH,
            )


def _open_package_base_fd(output_base: Path, package_dir: str, package_id: str) -> int:
    """Open a descriptor on the package directory itself -- creating it, and
    every intermediate component below `output_base`, purely by descriptor
    (stn-avv, architecture-review finding 1).

    THE ONE PATH-BASED OPEN LEFT, STATED RATHER THAN IMPLIED AWAY. A single
    `os.open(output_base, O_RDONLY|O_DIRECTORY|O_NOFOLLOW)` anchors the walk;
    everything BELOW it is then descended by descriptor with `walk_dir_fd`,
    so a symlink swapped in at any component of `package_dir` is refused the
    same way an intermediate component below the package directory already
    is (`render_templates`). What this does NOT close: `output_base` itself
    -- the user's declared output root -- and every one of ITS OWN ancestors
    are still resolved by the kernel's ordinary path lookup at the moment of
    this one call. There is no descriptor to start a walk from further up,
    because `output_base` IS the start. A component ABOVE it swapped for a
    symlink between `checked_output_base`'s one-time resolve (at the start
    of a whole `gen --all` run) and this call is not caught -- the stated
    residual (STENCIL.md), not an oversight: closing it would mean walking
    from the filesystem root on every single package, which nothing in this
    repository's threat model asks for. The declared output root is the
    user's own boundary, the same way `checked_output_base` already trusts
    `config_dir`.

    `package_dir` MAY BE MULTI-COMPONENT -- `check_package_dir` allows a
    `dir: a/b` -- so this walks it exactly like any other nested
    destination. A single `os.open` on the whole joined path would protect
    only its LAST component and leave every one above it open to the same
    swap `walk_dir_fd` exists to refuse.

    THE FINAL OPEN DELIBERATELY DROPS `O_NOFOLLOW`, unlike every other open
    in this walk -- see the comment at that call for why: the package
    directory's own name is the one component in this file `contained_path`
    has always permitted to be a symlink, so long as it resolves back under
    `output_base`. `os.mkdir` over an existing symlink still raises
    `FileExistsError`, indistinguishable from "the directory is already
    there", exactly like `walk_dir_fd`'s own intermediate-component trap --
    caught and ignored below for the same reason: the open that follows is
    what actually decides whether `final_name` leads somewhere usable.

    Raises `ValueError` naming `package_id`, in the same family as
    `checked_write_target`'s messages, on any refusal from the walk or the
    final open. The returned fd is the caller's to close.
    """
    # `output_base` need not exist yet -- `checked_output_base` resolves it
    # LEXICALLY, so a first `gen` on a fresh project reaches here with
    # nothing on disk at all. `output_dir.mkdir(parents=True)` used to be
    # what created it (and every one of ITS ancestors) as a side effect;
    # this is the same path-based creation, moved here so the descriptor
    # open immediately below has something to open. It is part of the
    # residual this function's docstring already names -- `output_base`
    # and its ancestors are resolved by path regardless -- not a new one.
    output_base.mkdir(parents=True, exist_ok=True)
    output_base_fd = os.open(
        output_base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    try:
        parent_fd, final_name = walk_dir_fd(output_base_fd, package_dir)
    finally:
        os.close(output_base_fd)

    try:
        try:
            os.mkdir(final_name, dir_fd=parent_fd)
        except FileExistsError:
            # Proves nothing by itself -- see the docstring, and
            # walk_dir_fd's own identical trap for an INTERMEDIATE
            # component. Here at the FINAL component it is not even a
            # trap: see the NO O_NOFOLLOW note below for why this one
            # case is deliberately allowed to be a symlink.
            pass
        try:
            # NO O_NOFOLLOW HERE, UNLIKE EVERY OTHER OPEN IN THIS WALK, AND
            # DELIBERATELY. `contained_path` (stn-vhr) already resolved
            # `output_dir` and confirmed it stays under `output_base`
            # BEFORE this function was ever called -- and that check has
            # always permitted the package directory itself to be a
            # symlink, e.g. a stable alias for a package that moved
            # (`test_a_package_dir_symlinked_inside_the_output_tree_still_generates`).
            # `walk_dir_fd`'s O_NOFOLLOW discipline is for a component that
            # is NOT supposed to be a link at all; the package directory's
            # own name is the one component in this whole file that is. An
            # intermediate component of a MULTI-part `package_dir` (`dir:
            # a/b`) gets no such exemption -- `walk_dir_fd`, above, already
            # refused a symlink there before reaching this line.
            return os.open(
                final_name,
                os.O_RDONLY | os.O_DIRECTORY,
                dir_fd=parent_fd,
            )
        except OSError as error:
            raise ValueError(
                f"Package {package_id}: package directory {package_dir!r} "
                f"could not be opened at {final_name!r} ({error.strerror}) "
                "-- refusing to write into it"
            ) from error
    finally:
        os.close(parent_fd)


def generate_package(
    env: Environment,
    config: dict,
    output_base: Path,
    package_id: str,
    dry_run: bool = False,
    config_dir: Path | None = None,
) -> Path | None:
    """Generate scaffolding for a single package. Returns output_dir on success, None on skip."""
    try:
        context = get_template_context(package_id, config)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return None

    output_dir = output_base / context["package_dir"]

    # stn-vhr: contain the package directory itself before anything is
    # created. A non-existent directory resolves to itself under
    # output_base, so this check is correct whether or not output_dir
    # exists yet -- and running it above the mkdir means a refused run
    # has touched nothing. Called for its refusal only: the return value
    # is discarded, because contained_path returns the RESOLVED path, and
    # every subsequent write and the "Generated: {output_path}" report
    # must keep going through the DECLARED output_dir. This covers only
    # the package directory itself, not a symlinked subdirectory nested
    # inside it or copy_brand_image's destination -- that gap is filed
    # separately as stn-h5q.
    pkg_resolved = contained_path(
        package_id, "dir", context["package_dir"], output_dir, output_base
    )

    # stn-h5q: check every path this call is about to write, at every
    # component below the package directory, BEFORE anything is created.
    # A pre-pass rather than a guard at each write site, so a refused run
    # touches nothing -- the guarantee stn-vhr already gives for the package
    # directory, extended to the files inside it. Without it, a package
    # whose third template lands on a planted link is left with two rendered
    # files and no manifest: a half-generated package that looks generated.
    #
    # `template_defs` is computed here rather than below the mkdir for the
    # same reason; nothing else about the order changes.
    config_templates = list(config.get("templates", []))
    template_defs = injected_templates(context) + config_templates
    for where, relative in write_targets(context, template_defs):
        checked_write_target(
            package_id, where, output_base, pkg_resolved, relative
        )

    existed_before = output_dir.exists()
    if not existed_before and dry_run:
        print(f"Would create directory: {output_dir}")

    # stn-avv (architecture review finding 1). The package-directory
    # descriptor is acquired UNCONDITIONALLY here, for every non-dry-run
    # call -- NOT only when `output_dir` does not exist yet. Regeneration
    # over an already-generated package is the ordinary case, not the
    # exceptional one, and it must be exactly as descriptor-protected as a
    # first run: every write below this point goes through `base_fd` on a
    # capable platform, whether or not this is the package's first
    # generation. Held for the rest of the function and closed in the
    # `finally` below, which covers every early return from here on --
    # including the "no templates defined" refusal a few lines down -- so a
    # malformed config cannot leak the descriptor `gen --all` would
    # otherwise open once per package with no matching close.
    #
    # `_DIR_FD_CAPABLE` is the module global, read fresh here rather than
    # captured anywhere -- the same discipline `walk_dir_fd` documents --
    # so a test can force the fallback with
    # `monkeypatch.setattr(generate, "_DIR_FD_CAPABLE", False)` even on a
    # platform where it is naturally True.
    base_fd = None
    if not dry_run and _DIR_FD_CAPABLE:
        base_fd = _open_package_base_fd(
            output_base, context["package_dir"], package_id
        )
    try:
        if not dry_run:
            if base_fd is None:
                # Windows, or any platform with no dir_fd support: today's
                # snapshot behaviour, completely unchanged, and now the
                # ONLY place this runs -- the capable branch above never
                # reaches it, because `_open_package_base_fd` already
                # created every component of `output_dir` by descriptor.
                if not existed_before:
                    output_dir.mkdir(parents=True)
            if not existed_before:
                print(f"Created directory: {output_dir}")

        # Before the first render, and never a stale manifest left behind:
        # a regeneration that fails partway must leave NO manifest, so a
        # manifest-driven `clean` falls back to deriving from the config --
        # today's behaviour exactly -- instead of trusting a list that
        # names the old files while whatever the failed run half-wrote
        # sits unnamed on disk.
        manifest_path = output_dir / MANIFEST_NAME
        if not dry_run:
            if base_fd is not None:
                # stn-avv (operator ruling). Routed through the SAME
                # descriptor as every write below it, rather than the
                # path-based `manifest_path.unlink()` this replaces --
                # which was the one delete sitting in the middle of a
                # function this task otherwise converts entirely to
                # descriptors. If `output_dir` were swapped for a symlink
                # to a victim directory after `base_fd` was opened, that
                # path-based unlink would delete the VICTIM's manifest: a
                # delete outside the tree, performed by the very run
                # meant to make `gen` descriptor-safe. `os.unlink` is in
                # `os.supports_dir_fd` on Linux and macOS, so this is
                # available everywhere `base_fd` is. `clean`'s own
                # resolve-then-unlink-by-path window stays OUT OF SCOPE
                # (filed as stn-cfby) -- this closes only the write side.
                try:
                    os.unlink(MANIFEST_NAME, dir_fd=base_fd)
                except FileNotFoundError:
                    pass
            elif manifest_path.exists():
                manifest_path.unlink()

        if not template_defs:
            print(f"Error: No templates defined in config", file=sys.stderr)
            return None

        render_templates(
            env, template_defs, context, output_dir, dry_run, dir_fd=base_fd
        )

        # After the templates, so a package that fails to render does not
        # leave a logo behind in a directory with nothing to use it.
        if context.get("has_pages"):
            copy_brand_image(
                config,
                config["packages"][package_id],
                config_dir or output_base.parent,
                output_dir,
                dry_run,
                dir_fd=base_fd,
            )

        # Last of all, from the same derivation get_generated_files uses
        # (see package_entries) -- so the manifest cannot name a file this
        # call did not itself just produce.
        write_manifest(
            output_dir,
            package_id,
            context["package_dir"],
            package_entries(
                package_id, config["packages"][package_id], context, config_templates
            ),
            dry_run,
            dir_fd=base_fd,
        )

        return output_dir
    finally:
        if base_fd is not None:
            os.close(base_fd)


def render_templates(
    env: Environment,
    template_defs: list,
    context: dict,
    output_dir: Path,
    dry_run: bool = False,
    *,
    dir_fd: int | None = None,
):
    """Render all templates to the output directory.

    `dir_fd`, OPTIONAL AND KEYWORD-ONLY (stn-avv, stn-6mcb.5): a descriptor
    on `output_dir` itself, opened by `generate_package` with
    `_open_package_base_fd` and held for the whole package. When given,
    EVERY destination -- a top-level name or a nested one like
    `.vscode/settings.json` alike -- is walked by descriptor with
    `walk_dir_fd` and written with `dir_fd=`, rather than joined onto
    `output_dir` and handed to `Path.mkdir`/`write_text_nofollow` as a bare
    path. That is the fix for the gap `checked_write_target`'s docstring
    names: a symlink swapped in at an intermediate directory between that
    pre-pass and this write is refused here instead of followed, because
    the object the walk opens and the object this writes through are the
    same inode by construction. `None` -- the default, and what a platform
    with no `dir_fd` support, or any `--dry-run` call, always passes --
    is today's behaviour, completely unchanged.
    """
    for template_name, output_name in template_destinations(
        template_defs, context
    ):
        try:
            template = env.get_template(template_name)
            content = template.render(**context)

            output_path = output_dir / output_name

            if dry_run:
                print(f"Would write: {output_path}")
                print("-" * 40)
                print(content)
                print()
            elif dir_fd is not None:
                # stn-avv. `walk_dir_fd` creates any missing intermediate
                # directory itself (dir_fd=), so there is nothing left for
                # this branch to `mkdir` -- and every intermediate fd the
                # walk opened along the way is closed by the time it
                # returns; only the final `parent_fd` is ours to close,
                # which the `finally` below does.
                parent_fd, final_name = walk_dir_fd(dir_fd, output_name)
                try:
                    write_text_nofollow(
                        Path(final_name),
                        content,
                        executable=output_path.suffix == ".sh",
                        dir_fd=parent_fd,
                    )
                finally:
                    os.close(parent_fd)
                print(f"Generated: {output_path}")
            else:
                # Create parent directories if needed (for nested paths like .vscode/settings.json)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                # stn-h5q: O_NOFOLLOW. generate_package has already refused
                # a symlink at this path; this closes the window between
                # that check and this write.
                # The execute bit is set THROUGH THE SAME DESCRIPTOR that
                # was just written, never by re-opening the path
                # (adversarial review of stn-h5q, MEDIUM 5). `Path.stat()`
                # and `Path.chmod()` both follow symlinks, so a link swapped
                # in between the write closing and the chmod would have got
                # an arbitrary file marked executable -- a race the
                # O_NOFOLLOW write closes for itself and reopened here.
                # `fchmod` on the open fd cannot name anything but the file
                # actually written.
                write_text_nofollow(
                    output_path, content, executable=output_path.suffix == ".sh"
                )
                print(f"Generated: {output_path}")

        except Exception as e:
            print(f"Error rendering {template_name}: {e}", file=sys.stderr)
            raise


def list_packages(config: dict):
    """List all available packages."""
    print("Available packages:")
    for package_id, package in config.get("packages", {}).items():
        name = package.get("name", "")
        dir_name = package.get("dir", package_id)
        print(f"  {package_id:8} - {name:20} ({dir_name})")


def package_entries(
    package_id: str, package: dict, context: dict, config_templates: list
) -> set[str]:
    """Every file (or build-artifact glob pattern) one package generates,
    relative to that package's own directory -- no ``<pkg_dir>/`` prefix.

    Extracted from what used to be get_generated_files' per-package loop
    body. This is the point of the change, not a tidy-up: get_generated_files
    feeds both `install`'s managed .gitignore section and `clean`, and the
    per-package manifest (see write_manifest) needs the exact same list. A
    manifest built by a second, parallel walk of the config would be a THIRD
    instance of a drift bug this repository has already paid for twice --
    `injected_sources` exists because the injected-template list and the
    clean list drifted, and stn-8wt exists because `copy_brand_image` and
    `get_generated_files` named the same file two different ways. One walk,
    two consumers (get_generated_files prefixes with the package dir;
    write_manifest records these entries verbatim), so drift is no longer
    possible between them.

    This does NOT unify everything that names a generated file.
    `render_templates` still computes `tdef.get('dest', template_dest(src))`
    on its own, and `copy_brand_image` still names the copied logo from the
    (unresolved) config string it was given rather than from this function's
    output -- those two remain separate spellings of the same fact, guarded
    by test_package_sources.py's and tests/test_manifest.py's rglob
    assertions rather than by a shared derivation.
    """
    entries = set()

    # Check each template's `when` condition against this package's context
    for tdef in config_templates:
        if not when_holds(tdef, context):
            continue
        dest = tdef.get("dest", template_dest(tdef.get("src", "")))
        if dest:
            entries.add(dest)

    # What stencil injects, from the one list generate_package renders
    # from, on the same predicates -- spelling them twice is what left a
    # package_sources-only doc package with five generated files that clean
    # could not see.
    for src in injected_sources(context):
        entries.add(template_dest(src))

    if context["has_pages"]:
        # The copied brand image, which `clean` should be able to see and
        # git should not. Named by its basename, which is what it is
        # copied to. Not a template, so it is not in the list above.
        # `context["config_brand"]` is already that basename (or the config
        # string unchanged, when brand names something other than a local
        # image) -- get_template_context computed it the same way
        # copy_brand_image names its destination, from the unresolved config
        # string, so reading it back here cannot drift from either.
        brand_image = brand_image_path(context.get("config_brand"))
        if brand_image:
            entries.add(Path(brand_image).name)

    # docs and slides generate .html files from .md files, and `make pdf`
    # prints each of those to a .pdf beside it (glob for feature variants)
    for md in list(package.get("docs", [])) + list(package.get("slides", [])):
        if md.endswith(".md"):
            entries.add(f"{md.removesuffix('.md')}*.html")
            entries.add(f"{md.removesuffix('.md')}*.pdf")

    # package_name is the zip file created by pkg target
    package_name = package.get("package_name")
    if package_name and package.get("package_type") == "zip":
        entries.add(package_name)

    # A doc package's pkg target concatenates package_sources into
    # <stem>.html and prints that to <stem>.pdf (glob for feature variants)
    if package.get("package_type") == "doc" and package_name:
        stem = package_name.removesuffix(".pdf")
        entries.add(f"{stem}*.html")
        entries.add(f"{stem}*.pdf")

    return entries


def get_generated_files(config: dict) -> list[str]:
    """Determine what files stencil will generate based on templates config.

    All entries are prefixed with the package directory. Respects `when` conditions
    on templates by checking against each package's context.

    Raises rather than silently dropping a package it cannot read -- this is
    the site both `install` and `clean` feed from, so a package that used to
    vanish here left the managed .gitignore section short and `clean`
    blind to its files, with nothing said about either. Consumes
    package_contexts' own contexts rather than building them a second time
    with its own get_template_context loop, which would double the work
    every `install` and `clean` do. No config_dir is passed: the brand
    file-existence check is gen-only (see package_contexts).

    Each package's own entries come from `package_entries` -- see there for
    why. The per-package manifest name is added on top, here, rather than in
    `package_entries`: the manifest does not list itself.
    """
    entries = set()
    config_templates = config.get("templates", [])

    contexts = package_contexts(config)

    # stn-jl3. Every entry carries the top-level `output_dir` as well as the
    # package `dir`, because that is where `gen` and `clean` both resolve to
    # and this list is the only view git gets of it. Without it, a config
    # with `output_dir: out` produced a managed section naming `demo/Makefile`
    # for a file at `out/demo/Makefile`, so the section ignored NOTHING
    # stencil writes and the whole generated tree was offered to the author
    # as untracked.
    #
    # THE VALUE COMES FROM check_output_dir, not from `config.get`. That is
    # the shape-checked declared string, and it is already computed on this
    # path -- package_contexts above calls it -- so this adds no second
    # spelling of a rule that lives in two places already. It is NOT
    # `checked_output_base`, which returns a resolved ABSOLUTE path: useless
    # as a gitignore prefix, and it needs a config_dir this function is not
    # given.
    #
    # Note what that means for `install` specifically: `_main`'s install
    # branch returns above `checked_output_base`, so on `install` this value
    # has had its string checks and NOT the containment check. `..` and an
    # absolute path are already refused by the string half, so the prefix
    # cannot leave the tree; the deferred half is the symlink case, which
    # `install` never writes through. Same split STENCIL.md records.
    #
    # `Path(value).parts` rather than a string test, so `.`, `./`, `./out`
    # and `out/` normalize the way git needs: a leading `./` matches nothing
    # at all in a gitignore pattern, which is a silent way to ignore nothing.
    declared = check_output_dir(config)
    output_parts = Path(declared).parts if declared else ()
    prefix = "/".join(output_parts) + "/" if output_parts else ""

    for package_id, context in contexts.items():
        package = config["packages"][package_id]
        pkg_dir = package.get("dir", package_id)

        # NORMALIZED, exactly like the output_dir prefix above and for the
        # identical reason -- the comment there stated the rule and the next
        # statement did not apply it to the segment that also leads every
        # line. Measured with git check-ignore: `dir: "./demo"` produced
        # `out/./demo/Makefile`, `dir: "demo/"` produced `out/demo//Makefile`,
        # and git matched NEITHER, so `install` printed every line and
        # ignored nothing -- stn-jl3's own failure mode one segment down.
        # A trailing slash is an ordinary typing habit.
        pkg_segment = "/".join(Path(pkg_dir).parts)

        for entry in package_entries(package_id, package, context, config_templates):
            entries.add(f"{prefix}{pkg_segment}/{entry}")

        entries.add(f"{prefix}{pkg_segment}/{MANIFEST_NAME}")

    return sorted(entries)


def _clean_scope(
    config: dict, package_id: str | None, problems: list[str]
) -> dict[str, dict] | None:
    """Which packages `clean` is asked to consider, shape-guarded (stn-2x4.8
    requirement 3). Returns None when the scope itself could not be
    enumerated at all -- the caller must treat that as a hard failure, never
    as an empty-but-fine scope, so a `packages:` typo cannot exit 0 having
    cleaned nothing (requirement 4; this is the `package_contexts` comment's
    own "exact harm this whole change exists to close", reintroduced here if
    it were allowed to pass silently).

    A malformed INDIVIDUAL package (not a mapping, or a non-string `dir`) is
    a named problem, not an abort: it is excluded from the returned mapping
    so the rest of the scope still gets a chance.

    When `package_id` is given, the CLI's own membership check (`_main`'s
    "Unknown package") has already proven `packages` is a mapping containing
    it, so only the shape of that one package's value is guarded here. A
    direct caller of `clean_generated` that skips that check gets the same
    guard rather than a bare AttributeError/TypeError.
    """
    packages_raw = config.get("packages")

    if package_id is not None:
        if not isinstance(packages_raw, dict) or package_id not in packages_raw:
            problems.append(f"Unknown package {package_id}")
            return None
        candidates = {package_id: packages_raw[package_id]}
    else:
        if not isinstance(packages_raw, dict):
            problems.append(
                "'packages' must be a mapping of package id to settings, not "
                f"{type(packages_raw).__name__}; no package can be "
                "identified to clean"
            )
            return None
        candidates = packages_raw

    scope: dict[str, dict] = {}
    for pid, package in candidates.items():
        if not isinstance(package, dict):
            problems.append(
                f"Package {pid}: must be a mapping of settings, not "
                f"{type(package).__name__}"
            )
            continue
        raw_dir = package.get("dir", pid)
        if not isinstance(raw_dir, str):
            problems.append(
                f"Package {pid}: dir {raw_dir!r} is {type(raw_dir).__name__}, "
                "not a string"
            )
            continue
        scope[pid] = package
    return scope


def contained_path(
    package_id: str, where: str, declared: str, candidate: Path, root: Path
) -> Path:
    """The stn-7t9 containment guarantee, in one place (stn-sl2.1): resolve
    `candidate` and refuse it unless it stays under resolved `root`.

    Lifted out of `_validated_package_dirs`, which used to be the only
    caller and inlined this as two lines -- fine when there was one call
    site, a duplicate spelling waiting to drift once `generate_package` and
    `checked_output_base` need the same rule. `declared` is why this takes
    five arguments rather than four: the message below names the STRING the
    config declared (e.g. a relative `dir`), not `candidate` itself, because
    `candidate` is usually built by joining that string onto a base the
    caller already resolved once -- and resolving BOTH `candidate` and
    `root` here, rather than trusting a pre-resolved `root`, is what lets a
    direct library caller pass either one unresolved.

    `resolve()` itself is guarded, though NOT for the symlink cases this
    exists to catch -- measured on this interpreter, none of them raises. A
    loop comes back unresolved, so it reports contained here and fails later
    at the write with ELOOP; a broken link resolves to its DANGLING TARGET,
    which is outside and is refused below; a path that does not exist yet
    resolves lexically, which is what lets this run before `mkdir`. The
    guard is for a genuine OSError -- a path component that is not a
    directory, an unreadable ancestor -- which must fail closed rather than
    propagate past every caller's own error handling, the way
    `_remove_entries` already guards its own `resolve()` and this code did
    not before it was lifted.
    """
    try:
        candidate_resolved = candidate.resolve()
        root_resolved = root.resolve()
    except OSError as error:
        raise ValueError(
            f"Package {package_id}: {where} {declared!r} could not be "
            f"resolved: {error} -- refusing to touch it"
        ) from error
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError:
        raise ValueError(
            f"Package {package_id}: {where} {declared!r} resolves to "
            f"{candidate_resolved}, outside the output directory "
            f"{root_resolved} -- refusing to touch it"
        ) from None
    # stn-17h. STRICTLY beneath, not merely "not outside". `relative_to`
    # answers yes for a path EQUAL to the root, so every containment check
    # in this file endorsed `dir: "."` -- which anchors the package
    # directory ON the output base, and with no top-level `output_dir` that
    # base is the config directory. Nothing escapes anywhere, which is why
    # no check saw it: a planted `.stencil-manifest.json` is then a
    # free-form list of files under the repository root for `clean` to
    # unlink, and it removed a hand-written source file, a dotfile and a
    # whole directory at exit 0.
    #
    # Checked HERE rather than only in the pre-flight because this is the
    # one helper `gen` (generate_package) and `clean`
    # (_validated_package_dirs) both go through, so one rule reaches both
    # sides -- and because the equality that matters is the RESOLVED one:
    # `dir: demo` is a perfectly ordinary string when `out/demo` is a
    # symlink pointing back at `out`, which no string check can see.
    if candidate_resolved == root_resolved:
        raise ValueError(
            f"Package {package_id}: {where} {declared!r} resolves to "
            f"{candidate_resolved}, which IS the output directory rather "
            "than a directory inside it. A package needs its own "
            "directory: every path `clean` removes is resolved under this "
            "one, so naming the output directory itself makes the whole "
            "tree above the package a delete list."
        )
    return candidate_resolved


def checked_output_base(config: dict, config_dir: Path) -> Path:
    """Resolve the top-level ``output_dir`` into ``output_base``, refusing
    one that escapes ``config_dir`` (stn-pe3) -- catching a SYMLINKED
    ``output_dir`` that ``check_output_dir``'s string check cannot see, the
    same way a symlinked package ``dir`` is caught below.

    Spells its own ``resolve()``/``relative_to()`` pair rather than routing
    through ``contained_path``: that helper is rooted at an OUTPUT BASE and
    its message says "outside the output directory", which is wrong here --
    there is no output base yet, only a config directory, and this call is
    what computes the very thing ``contained_path``'s other callers assume
    already exists. Two lines is not a second rule.

    Raises through ``_raise_config_problems`` rather than bare, so what the
    terminal prints is shaped like every other config error: the same
    heading, the same trailer saying nothing was generated, removed or
    written, and the same ``_safe`` pass over a value that came out of a
    config file. Without it this one key reported in a shape nothing else
    uses -- and on `clean`, without the sentence that says the run touched
    nothing, which is the part an author most needs to read.
    """
    try:
        value = check_output_dir(config)
    except ValueError as error:
        _raise_config_problems([str(error)])

    candidate = (config_dir / value) if value else config_dir
    # Guarded for the same reason contained_path guards its own pair, and
    # spelled here rather than inherited because this function deliberately
    # does NOT route through that helper: an unguarded OSError would leave
    # _main, which catches only ValueError around this call, printing the
    # bare traceback stn-40a exists to remove -- from the very function that
    # closes stn-40a. Measured on this interpreter, none of the symlink
    # cases raises (a loop returns the path unresolved, a broken link
    # resolves to its dangling target, a missing path resolves lexically);
    # the guard is for a genuine OSError, such as an unreadable ancestor or
    # a path component that is not a directory.
    try:
        resolved_config_dir = config_dir.resolve()
        resolved_candidate = candidate.resolve()
    except OSError as error:
        _raise_config_problems(
            [
                f"Package config: output_dir {value!r} could not be "
                f"resolved: {error}"
            ]
        )
    if not resolved_candidate.is_relative_to(resolved_config_dir):
        _raise_config_problems(
            [
                f"Package config: output_dir {value!r} resolves to "
                f"{resolved_candidate}, outside the config directory "
                f"{resolved_config_dir} -- refusing to touch it"
            ]
        )
    return resolved_candidate


def _validated_package_dirs(
    scope: dict[str, dict], output_base: Path, problems: list[str]
) -> dict[str, Path]:
    """check_config_path plus a containment check on every scoped package's
    `dir`, re-run here even for a package whose config already passed
    `package_contexts` (stn-2x4.8 requirement 2).

    On the degraded path `dir` has been validated by NOTHING: `dir` is only
    checked inside `get_template_context`, which the degraded path never
    reaches because the config did not parse as a whole. A config carrying
    `dir: ../../../victim` alongside a broken sibling would otherwise anchor
    the manifest lookup, and the containment root, outside the output tree.

    The string check alone does not catch a symlinked package directory, so
    `contained_path` (stn-sl2.1) is also required to place
    `output_base / pkg_dir` under `output_base` once resolved -- this is
    the stn-7t9 containment guarantee, applied at the one place every
    source (manifest or config-derived) goes through before anything is
    touched.

    A package failing either check is a named problem, not a silent drop.
    """
    result: dict[str, Path] = {}
    for pid, package in scope.items():
        raw_dir = package.get("dir", pid)
        try:
            check_package_dir(pid, raw_dir)
        except ValueError as error:
            problems.append(str(error))
            continue
        try:
            pkg_path = contained_path(
                pid, "dir", raw_dir, output_base / raw_dir, output_base
            )
        except ValueError as error:
            problems.append(str(error))
            continue
        result[pid] = pkg_path
    return result


def _remove_entries(
    package_id: str,
    root: Path,
    pkg_path: Path,
    entries: set[str],
    dry_run: bool,
    problems: list[str],
) -> list[Path]:
    """Unlink every entry (file, or glob pattern expanded against disk)
    under one package directory. Returns the paths actually removed (or that
    would be, under --dry-run), so the caller can clean up now-empty parent
    directories from exactly those. `pkg_path` is already resolved (the
    caller resolves the package directory ONCE per package, in
    `_validated_package_dirs` -- decision d-cfc315b8); this function does
    not resolve it again.

    An entry that fails a check is appended to `problems` and skipped --
    the rest of the package is still processed (stn-2x4.6, review finding
    A10: `test_manifest_survives_a_partial_clean`). Every entry, manifest-
    sourced or config-derived alike, goes through the same three checks,
    because running them on both costs nothing and is one code path:

    1. `check_config_path` -- no absolute path, no '..', no '~', no shell
       metacharacter, no control character, no whitespace.
    2. `check_glob_vocabulary` -- bounds what a survivor's '*' may mean, so
       a string that passed (1) cannot still expand to the whole package.
    3. TWO HALVES OF ONE RULE, THE SAME SHAPE stn-ttg'S FIX ALREADY USES
       (`brand_problem`, generate.py): the string is checked above; here the
       RESOLVED location is checked too, against a root that is not derived
       from the thing being checked. Only the entry's PARENT directory is
       resolved and checked -- never the entry's own final component. That
       is what makes the two symlink cases come out differently on purpose:

       - a symlinked SUBDIRECTORY inside the package (e.g. `linked/*.txt`
         where `linked` points outside) is caught here, because
         `(pkg_path / entry).parent.resolve()` follows `linked` to its real,
         outside location, which fails containment (architecture finding
         T1 second half / adversarial CRITICAL 1's shape, applied to an
         entry rather than to `dir`).
       - an ordinary symlink AS the final component (e.g. a generated
         `Makefile` replaced by a symlink to some file elsewhere) is NOT
         caught here, and must not be: `unlink()` does not follow a
         final-component symlink, so the path actually removed below is
         the UNRESOLVED join -- the link itself, sitting legitimately
         inside the package -- never its resolved target. Refusing the
         entry because its target resolves outside would make such a
         package permanently un-cleanable, which is the opposite of the
         guarantee this function exists to provide.
    """
    paths_with_depth = []
    for entry in entries:
        try:
            check_config_path(package_id, "manifest entry", entry)
            check_glob_vocabulary(package_id, "manifest entry", entry)
        except ValueError as error:
            problems.append(str(error))
            continue

        unresolved = pkg_path / entry
        try:
            # stn-h5q: the same helper `gen`'s write-side check calls, so
            # the two sides cannot drift. The messages below used to be
            # spelled here; they now come from that one function.
            parent_resolved = contained_entry_parent(
                package_id, "manifest entry", entry, unresolved, root, pkg_path
            )
        except ValueError as error:
            problems.append(str(error))
            continue

        # Rebuilt on `parent_resolved`, NEVER on `unresolved.parent`. The
        # parent is the half that was just containment-checked, so every
        # path that leaves here has a real, checked directory above it --
        # which is what makes `_remove_empty_parent_dirs`' `relative_to`
        # guards containment checks rather than lexical tests on a string.
        #
        # Measured before this: an intermediate directory that is a SYMLINK
        # pointing inside the package left `unresolved.parent` naming the
        # link, the sweep called `rmdir` on it, and `clean` ended in
        # NotADirectoryError -- a bare traceback AFTER the unlink, which is
        # the shape `_main`'s clean branch says in a comment that it refuses
        # to create.
        #
        # The FINAL component stays unresolved (it is joined on as a plain
        # name), so `unlink()` still removes a symlinked file rather than
        # its target -- see this docstring's third point, and
        # test_removing_a_generated_symlink_removes_the_link_not_its_target.
        name = Path(entry).name
        if "*" in name:
            for match in parent_resolved.glob(name):
                paths_with_depth.append((len(match.parts), match))
        else:
            resolved_join = parent_resolved / name
            paths_with_depth.append((len(resolved_join.parts), resolved_join))

    paths_with_depth.sort(key=lambda x: -x[0])

    removed = []
    for _, path in paths_with_depth:
        # `is_symlink()` FIRST, and it is not decoration. `Path.exists()`
        # follows a link (False for a dangling one) and `Path.is_file()`
        # follows it too (False for a link to a directory), so the two
        # symlink shapes this loop's `unlink()` was written to handle were
        # the exact two it never reached. Measured on the branch that added
        # gen's own symlink refusal: `gen` said "`stencil clean` removes
        # it", `clean` then exited 0 with twelve Removed lines and the word
        # Makefile in none of them, and the link -- pointing out of the
        # tree, inside a directory AGENTS.md says is handed to someone as a
        # project of their own -- survived, with the package locked out of
        # regeneration forever.
        if not (path.is_symlink() or path.exists()):
            continue
        if path.is_symlink() or path.is_file():
            if dry_run:
                print(f"Would remove {path}")
            else:
                # The final component is unresolved -- see the docstring
                # above. unlink() removes the directory entry itself and
                # never follows a final-component symlink, so this is
                # correct for an ordinary symlink living inside the package
                # even though the type checks above follow it.
                try:
                    path.unlink()
                except OSError as error:
                    # A named failure, never a traceback mid-delete: a
                    # read-only parent directory, a file removed by
                    # something else between the check and here, a
                    # filesystem going away. The rest of the package is
                    # still processed, exactly as a refused entry is.
                    problems.append(
                        f"Package {package_id}: {str(path)!r} could not be "
                        f"removed: {error}"
                    )
                    continue
                print(f"Removed {path}")
            removed.append(path)
    return removed


def _remove_empty_parent_dirs(
    root: Path,
    pkg_path: Path,
    removed_paths: list[Path],
    dry_run: bool,
    problems: list[str],
) -> None:
    """Remove now-empty directories (e.g. .vscode, scripts/) left behind
    under one package directory -- never the package directory itself.

    Containment-checked against BOTH `pkg_path` and `root` (stn-2x4.6,
    adversarial MEDIUM 11), and the two are not interchangeable. The
    `d.relative_to(pkg_path)` guard is the only reason this sweep does not
    already rmdir outside the package tree today -- verified directly by
    tests/test_manifest.py::test_sweep_never_rmdirs_a_directory_outside_the_package_tree
    -- so it stays exactly as it was. `root` is added ALONGSIDE it, never in
    place of it: checking against `root` alone would be strictly MORE
    permissive, because a directory can sit under `root` (inside
    `output_dir`) while still being a SIBLING of `pkg_path` rather than
    nested inside it -- exactly the escape that test pins.

    Every directory reaching here is `_remove_entries`' `parent_resolved`
    (or a resolved glob match's parent), so the two guards are containment
    checks on a real directory rather than lexical tests on a path that
    might still have a symlink in it.
    """
    parent_dirs = {p.parent for p in removed_paths}
    candidate_dirs = []
    for d in parent_dirs:
        if not d.exists() or not d.is_dir():
            continue
        try:
            if (
                len(d.relative_to(pkg_path).parts) >= 1
                and d.is_relative_to(root)
            ):
                candidate_dirs.append(d)
        except ValueError:
            # Not under pkg_path (e.g. a glob matched files elsewhere).
            pass
    candidate_dirs.sort(key=lambda d: -len(d.parts))
    for d in candidate_dirs:
        if not d.exists():
            continue
        is_empty = not any(d.iterdir())
        if dry_run:
            if is_empty:
                print(f"Would remove directory {d}")
            else:
                print(f"Would skip non-empty directory (leave as-is): {d}")
        else:
            if is_empty:
                try:
                    d.rmdir()
                except OSError as error:
                    # Same rule as the unlink above: an emptied directory
                    # whose parent is not writable is a named failure, not a
                    # traceback landing after the files underneath it are
                    # already gone.
                    problems.append(
                        f"directory {str(d)!r} could not be removed: {error}"
                    )
                    continue
                print(f"Removed directory {d}")
            else:
                print(f"Skipped non-empty directory (leave as-is): {d}")


def _remove_path(path: Path, dry_run: bool, problems: list[str]) -> None:
    """Unlink a single file (the manifest), the same way _remove_entries
    reports an ordinary entry -- kept separate so the manifest is always the
    LAST thing printed and removed for its package. Guarded the same way
    too: a manifest that cannot be unlinked is a named problem, never a
    traceback after every file it named is already gone.

    The `is_symlink()` half is the same fix `_remove_entries` needed: a
    manifest replaced by a dangling link would otherwise be skipped here and
    survive a clean that reported success."""
    if not (path.is_symlink() or path.exists()):
        return
    if dry_run:
        print(f"Would remove {path}")
        return
    try:
        path.unlink()
    except OSError as error:
        problems.append(f"manifest {str(path)!r} could not be removed: {error}")
        return
    print(f"Removed {path}")


def _config_template_defs(
    config: dict, who: str, problems: list[str]
) -> list[dict]:
    """The config's `templates:` list, shape-guarded, for the config-derived
    removal list.

    Delegates the shape rule itself to `_checked_template_defs` -- see its
    docstring for what it checks and why it is the one place that check is
    written. This function's own job is just to read `templates` off
    `config` and append onto the `problems` list its caller
    (`_config_derived_entries`) was handed, which is the failure mode
    `clean`'s config-derived fallback needs: a named problem, not a raise,
    since this runs interleaved with the deleting.

    `package_contexts` runs the identical check earlier, as part of
    gen/install's fail-closed pre-flight and of `clean`'s own
    `config_readable` probe (`_main`'s `clean` branch) -- so by the time
    `clean_generated` reaches here with `config_readable=True` on the CLI
    path, that pre-flight has already passed and `templates` is already
    known to be well-shaped. This still earns its place: `clean_generated`
    defaults `config_readable` to True for a caller that never ran
    `package_contexts` at all (see its docstring), and this is what that
    caller gets instead of a bare AttributeError/KeyError part way through
    a delete -- see stn-dl3r for what a config this broken used to do here.
    """
    kept, template_problems = _checked_template_defs(
        config.get("templates", []), who
    )
    problems.extend(template_problems)
    return kept


def _config_derived_entries(
    pids: list[str], config: dict, problems: list[str]
) -> set[str]:
    """`package_entries` for each of `pids`, unioned -- the same union
    `get_generated_files` has always produced for packages sharing a `dir`
    (a set, deduplicated), computed directly here instead of via a second
    full-config sweep.

    Every failure is a named problem rather than an exception, because this
    runs INSIDE `clean`, interleaved with the deleting. See
    `_config_template_defs` for the shape this exists to survive.
    """
    who = ", ".join(sorted(pids))
    config_templates = _config_template_defs(config, who, problems)
    entries: set[str] = set()
    for pid in pids:
        try:
            context = get_template_context(pid, config)
            entries |= package_entries(
                pid, config["packages"][pid], context, config_templates
            )
        except (ValueError, TypeError, AttributeError, KeyError) as error:
            problems.append(
                f"Package {pid}: its removal list could not be derived from "
                f"the config ({type(error).__name__}: {error}) -- nothing "
                "was removed for it"
            )
    return entries


def _clean_one_directory(
    root: Path,
    pkg_path: Path,
    members: list[str],
    all_members: list[str],
    config: dict,
    config_readable: bool,
    dry_run: bool,
    problems: list[str],
) -> None:
    """Clean everything under one resolved package directory, on behalf of
    every package_id in `members` that is configured with it (stn-2x4.8
    requirement 5: packages sharing a `dir` are processed once as a group,
    not once per package -- both because there is only one physical manifest
    to read there, and because checking that manifest's `package` field
    against ONE member at a time would falsely refuse it for every member
    but whichever one gen wrote it for last).

    `root` is the resolved output base, threaded through to `_remove_entries`
    and `_remove_empty_parent_dirs` -- see their docstrings.

    `members` is the SELECTION at this directory (what the command line
    asked for); `all_members` is every package in `config['packages']`
    configured with it. The two are different and both are needed:

    - The manifest's `package` field is checked against `all_members`.
      Checked against the selection instead, `stencil clean alpha` on a
      directory shared with `beta` was refused EVERY time -- the manifest
      names whichever package `gen` wrote last, so a one-package selection
      could never match it, and no user action cleared it. `clean --all`
      worked; `clean alpha` could not, ever.
    - The entries actually removed come from the selection, unioned into
      the manifest (below).
    """
    manifest_path = pkg_path / MANIFEST_NAME
    member_set = set(members)
    full_member_set = set(all_members) | member_set
    representative_id = sorted(member_set)[0]

    if manifest_path.is_file():
        try:
            manifest = read_manifest(manifest_path)
        except ManifestError as error:
            # Refused for the whole group, as a whole -- a damaged manifest
            # does not fall back to the config (see ManifestError's
            # docstring), and nothing is removed for any package sharing
            # this directory rather than guessing which one it belonged to.
            problems.append(str(error))
            return

        manifest_pkg = manifest.get("package")
        # INVERTED (stn-jez) from `isinstance(...) and ... not in
        # full_member_set` to `not isinstance(...) or ... not in
        # full_member_set`. The old spelling read a manifest with NO
        # `package` key as "no opinion" -- `isinstance(None, str)` is
        # False, so the whole `and` was False and the manifest was trusted
        # -- rather than as untrusted. `read_manifest` now refuses a
        # missing/non-string `package` on its own (see its REQUIRED FIELDS
        # paragraph), so this can never fire for a manifest that reached
        # here -- kept anyway, belt and braces, the same pattern
        # `checked_write_target`'s closing `contained_entry_parent` call
        # documents: cheap, and the day the check above it gains a gap in
        # the wrong place this is what still holds the line standing alone.
        if not isinstance(manifest_pkg, str) or manifest_pkg not in full_member_set:
            problems.append(
                f"manifest {manifest_path} names package {manifest_pkg!r}, "
                "which is not among the package(s) configured with this "
                f"directory ({', '.join(sorted(full_member_set))}); refusing "
                "to use it"
            )
            return

        entries = set(manifest["entries"])

        # stn-jez (operator ruling): A MANIFEST MAY NARROW WHAT THE CONFIG
        # AUTHORISES, NEVER WIDEN IT. The required-field check above stops a
        # MALFORMED manifest, not a FORGED one -- every field it checks is
        # free to an attacker: `stencil_version` is what `stencil version`
        # prints, `package` is a package id read straight off the config
        # being attacked, and `dir` defaults to the package id. A manifest
        # with every field correct and naming the right package can still
        # list an entry the config never derives for it, so field presence
        # was never the actual boundary.
        #
        # Checked ONLY on `config_readable`: on the degraded path the
        # manifest is the ONLY thing that can name what this directory
        # holds -- not the config, which does not parse, and not a sibling's
        # manifest, which names different files. That is the same trade
        # stn-p9a documents for the degraded path generally, restated here
        # rather than fixed: a documented limit, not a defect.
        #
        # Checked against the manifest's OWN entries, computed here BEFORE
        # the `unnamed` union below adds a sibling package's config-derived
        # entries on top -- unioning first and checking after would let a
        # sibling's legitimate entries mask a forged one sitting in THIS
        # manifest.
        if config_readable:
            authorised_problems: list[str] = []
            authorised = _config_derived_entries(
                sorted(full_member_set), config, authorised_problems
            )
            if authorised_problems:
                # The authorised set itself could not be derived, so there is
                # nothing reliable to narrow this manifest against.
                #
                # FAILS CLOSED (stn-dl3r), not "trust the manifest". Before
                # stn-dl3r, this branch fell through and used the manifest
                # anyway -- the one option that is never right on its own,
                # since the widen check above exists precisely because the
                # manifest is an unvalidated file on disk, and trusting it
                # because the thing that bounds it could not be computed
                # defeats the check. That was tolerated for exactly one
                # commit: a malformed top-level `templates:` entry used to
                # reach here because `package_contexts` skipped a
                # non-mapping member silently, and refusing here too (before
                # `package_contexts` named the shape mistake itself) would
                # have made `clean` LESS able to clean a package whose
                # manifest is perfectly good -- the capability stn-p9a
                # exists to provide.
                #
                # That door is closed now: `package_contexts` (via
                # `_checked_template_defs`) names a malformed `templates:`
                # itself, so `clean_generated`'s own `package_contexts` call
                # (in `_main`, above this) raises on it first,
                # `config_readable` drops to False, and this branch is never
                # reached through that door at all. What can still land here
                # is a narrower, rarer fault local to computing THIS
                # directory's authorised set (see `_config_derived_entries`)
                # -- and refusing is still the right answer for it: nothing
                # bounds what the manifest would be allowed to remove, so
                # nothing is removed for the group, and the underlying
                # problem(s) are surfaced so they can be fixed.
                problems.append(
                    f"Package(s) {', '.join(sorted(full_member_set))}: what "
                    "the config authorises for this directory could not be "
                    "derived, so its manifest cannot be checked against it "
                    "-- nothing was removed. Fix the problem(s) below and "
                    "run `clean` again."
                )
                problems.extend(authorised_problems)
                return
            else:
                # Compared as LITERAL STRINGS, unexpanded: both sides come
                # from `package_entries`, so a glob pattern like
                # `Guide*.html` appears the same way on both, and expanding
                # either would compare apples to a set that was never meant
                # to hold them. `MANIFEST_NAME` itself is exempt --
                # `package_entries` never lists it (see its docstring: "the
                # manifest does not list itself"), so it is not the
                # caller's entry to authorise, and `_remove_entries` never
                # receives it either.
                widened = sorted(
                    entry
                    for entry in entries
                    if entry != MANIFEST_NAME and entry not in authorised
                )
                expected_dir = config["packages"][manifest_pkg].get(
                    "dir", manifest_pkg
                )
                manifest_dir = manifest.get("dir")
                dir_mismatch = manifest_dir != expected_dir

                if widened or dir_mismatch:
                    complaints = []
                    if widened:
                        complaints.append(
                            "names "
                            + ", ".join(repr(entry) for entry in widened)
                            + ", which the config does not derive for it"
                        )
                    if dir_mismatch:
                        complaints.append(
                            f'declares "dir" {manifest_dir!r}, not '
                            f"{expected_dir!r} as configured"
                        )
                    # THE CONSEQUENCE, WORDED ON PURPOSE (stn-jez): an
                    # author who removes a template from the config and
                    # then runs `clean` hits this exact message -- the
                    # manifest legitimately names a file the current config
                    # no longer derives. That is fail-closed and intended,
                    # not a false positive, so the message says what to do
                    # about it rather than only that something is wrong:
                    # restore the config entry if the file is still wanted,
                    # or delete the file (and, if nothing else in the
                    # package needs cleaning, the manifest) by hand.
                    problems.append(
                        f"manifest for package {manifest_pkg!r} "
                        + "; and it ".join(complaints)
                        + " -- a manifest may narrow what the config "
                        "authorises but never widen it, so nothing was "
                        "removed for this package. Restore the removed "
                        "config entry if the file(s) are still wanted, or "
                        "delete the file(s) -- and the manifest, by hand. "
                        f"Manifest: {manifest_path}"
                    )
                    return

        entry_problems: list[str] = []

        # THE MANIFEST NAMES ONE PACKAGE; A DIRECTORY MAY HOLD SEVERAL.
        # `generate_package` unlinks the existing manifest and writes only
        # `package_entries(package_id)`, so after `gen --all` a shared
        # directory carries a manifest naming whichever package ran LAST.
        # Driving the whole group from it left the other package's
        # artifacts named by nothing: measured, `alpha.zip` survived
        # `clean --all`, which exited 0 and said nothing -- a manifest
        # making `clean` LESS thorough than the config-derived predecessor,
        # which is the opposite of what stn-p9a is for.
        unnamed = [pid for pid in sorted(member_set) if pid != manifest_pkg]
        if unnamed:
            if config_readable:
                entries |= _config_derived_entries(unnamed, config, entry_problems)
            else:
                # Nothing can name these files: not the manifest, which is
                # another package's, and not the config, which does not
                # parse. Named and non-zero, never silently dropped.
                for pid in unnamed:
                    entry_problems.append(
                        f"Package {pid}: the manifest at {manifest_path} "
                        f"names package {manifest_pkg!r}, and the config "
                        "could not be read -- nothing was cleaned for it. "
                        "Fix the config and run `clean` again."
                    )

        removed = _remove_entries(
            representative_id, root, pkg_path, entries, dry_run, entry_problems
        )
        _remove_empty_parent_dirs(root, pkg_path, removed, dry_run, entry_problems)
        if entry_problems:
            # stn-2x4.6, review finding A10
            # (test_manifest_survives_a_partial_clean): the manifest is NOT
            # removed when any entry was refused or failed. Every other
            # entry has already been unlinked above; leaving the manifest
            # in place is what lets the refused entry still be named, and
            # acted on, next time -- removing it here would erase the only
            # record that it was ever there.
            problems.extend(entry_problems)
            return
        # Last of all -- stn-2x4's whole point: a clean that fails partway
        # still has a manifest on disk naming what is left to resume from.
        _remove_path(manifest_path, dry_run, problems)
        return

    if manifest_path.exists():
        # A directory or a FIFO (or anything else) occupying the manifest's
        # name is a DAMAGED manifest, not the same statement as no manifest
        # being present -- stn-2x4.6 review finding: falling back to the
        # config here would be guessing, exactly what a manifest exists to
        # remove. NEVER opened: a FIFO with no writer blocks forever on
        # open() for reading, so every check below is stat-based
        # (`exists`/`stat`), never a `read_text` or `open` call.
        try:
            mode = manifest_path.stat().st_mode
        except OSError:
            mode = 0
        if stat.S_ISDIR(mode):
            kind = "a directory"
        elif stat.S_ISFIFO(mode):
            kind = "a FIFO"
        else:
            kind = "not a regular file"
        problems.append(
            f"Package(s) {', '.join(sorted(member_set))}: {manifest_path} "
            f"is {kind}, not a manifest -- refusing to guess whether one is "
            "present. Remove it by hand, or restore the manifest, then run "
            "`clean` again."
        )
        return

    # No manifest at this directory.
    if not config_readable:
        for pid in sorted(member_set):
            problems.append(
                f"Package {pid}: no manifest and the config could not be "
                "read -- nothing was cleaned for it. Delete the directory "
                "by hand, or fix the config and run `clean` again."
            )
        return

    # Config-derived fallback, unioned across every selected package
    # sharing this directory.
    entry_problems: list[str] = []
    entries = _config_derived_entries(sorted(member_set), config, entry_problems)
    removed = _remove_entries(
        representative_id, root, pkg_path, entries, dry_run, entry_problems
    )
    _remove_empty_parent_dirs(root, pkg_path, removed, dry_run, entry_problems)
    problems.extend(entry_problems)


def clean_generated(
    output_base: Path,
    config: dict,
    package_id: str | None = None,
    dry_run: bool = False,
    config_readable: bool = True,
) -> list[str]:
    """Remove files and directories that stencil generates.

    If package_id is None, clean all packages; otherwise clean only that
    package. Returns the list of problems encountered -- never exits and
    never raises for a per-package problem -- so the caller (`_main`)
    decides the process's exit status; see `_main`'s `clean` branch.

    Per package in scope: use its own manifest when one is present (see
    read_manifest); otherwise derive from the config when `config_readable`
    says the config parsed; otherwise remove nothing for that package, name
    it, and add a problem. The manifest wins over the config whenever both
    exist -- it records what `gen` actually produced, not what the config
    would produce if run again.

    `config_readable` defaults to True, which preserves this function's
    contract for a DIRECT caller that does not know about the manifest: the
    whole config is validated up front via `package_contexts`, and a broken
    config RAISES here exactly as it always has, rather than degrading to
    per-package problems. The CLI passes `config_readable=False` only after
    it has already run that same validation itself and the config failed --
    see `_main`'s `clean` branch, which prints that failure as a warning
    instead of exiting on it. The membership check below still runs first
    either way, so the API cannot be made to delete from a config it never
    checked, and a mistyped package_id is answered as a mistyped package_id
    rather than with an unrelated sibling's problem.
    """
    if package_id is not None and package_id not in config.get("packages", {}):
        print(f"Error: Unknown package {package_id}", file=sys.stderr)
        list_packages(config)
        sys.exit(1)

    problems: list[str] = []

    if config_readable:
        # Re-validates the whole config, same as get_generated_files always
        # has -- and lets the ValueError propagate, unmodified, for a direct
        # caller that never checked this itself. On the CLI path this is a
        # cheap (microseconds) repeat of a check `_main` already made and
        # already knows succeeded.
        package_contexts(config)

    scope = _clean_scope(config, package_id, problems)
    if scope is None:
        return problems

    package_dirs = _validated_package_dirs(scope, output_base, problems)

    # Resolved once here, and threaded through to every package's clean
    # rather than re-resolved per entry (decision d-cfc315b8) -- the same
    # resolved output base `_validated_package_dirs` already checked each
    # `pkg_path` against.
    root = output_base.resolve()

    groups: dict[Path, list[str]] = {}
    for pid, pkg_path in package_dirs.items():
        groups.setdefault(pkg_path, []).append(pid)

    # Every package configured with each directory, regardless of what the
    # command line selected -- which is what the manifest's `package` field
    # has to be checked against. Enumerated over the WHOLE config (scope
    # None) and with its problems discarded: a package outside the selection
    # is not this command's business to report, it only has to be known
    # about. See `_clean_one_directory` for what goes wrong without it.
    full_groups: dict[Path, list[str]] = {}
    discarded: list[str] = []
    full_scope = _clean_scope(config, None, discarded) or {}
    for pid, pkg_path in _validated_package_dirs(
        full_scope, output_base, discarded
    ).items():
        full_groups.setdefault(pkg_path, []).append(pid)

    for pkg_path, members in groups.items():
        _clean_one_directory(
            root,
            pkg_path,
            members,
            full_groups.get(pkg_path, members),
            config,
            config_readable,
            dry_run,
            problems,
        )

    return problems


def install_gitignore(config: dict, config_dir: Path, dry_run: bool = False):
    """Install or update .gitignore with stencil-managed entries.

    Uses marker comments to manage a section within .gitignore, allowing
    stencil to update its entries without disturbing user entries.

    BESIDE THE CONFIG FILE, not in the working directory (stn-jl3). Every
    entry this writes is relative to the config file's directory -- that is
    what `output_dir` and `dir` are relative to -- so a section written into
    a `.gitignore` somewhere else names paths that do not exist from there.
    `stencil --config sub/.config.yaml install` from a repository root wrote
    a section none of whose lines applied, while the directory a fresh clone
    actually opens got nothing.

    `config_dir` is positional-required for the reason `brand_problem`'s is:
    a default would let a caller forget it and get the old behaviour back
    silently, which is the shape of defect this whole epic exists to close.
    """
    gitignore_path = config_dir / ".gitignore"

    entries = get_generated_files(config)

    # Build the stencil section
    stencil_section = f"{GITIGNORE_START}\n"
    for entry in entries:
        stencil_section += f"{entry}\n"
    stencil_section += f"{GITIGNORE_END}\n"

    if gitignore_path.exists():
        # errors="replace", because this content is only ever pattern-matched
        # and re-emitted around the managed section -- and a `.gitignore`
        # that is not valid UTF-8 (a latin-1 comment, say) used to end the
        # run in a UnicodeDecodeError traceback. encoding is explicit for
        # the reason write_text_nofollow's docstring gives: the locale
        # default made these bytes depend on the shell that ran stencil.
        content = gitignore_path.read_text(encoding="utf-8", errors="replace")

        # Pattern to find existing stencil section (including markers)
        pattern = re.compile(
            rf"^{re.escape(GITIGNORE_START)}$.*?^{re.escape(GITIGNORE_END)}$\n?",
            re.MULTILINE | re.DOTALL,
        )

        if pattern.search(content):
            # Replace existing section
            new_content = pattern.sub(stencil_section, content)
            action = "Updated"
        else:
            # Append section (with blank line separator if file doesn't end with newlines)
            if content and not content.endswith("\n\n"):
                if not content.endswith("\n"):
                    content += "\n"
                content += "\n"
            new_content = content + stencil_section
            action = "Added stencil section to"
    else:
        new_content = stencil_section
        action = "Created"

    if dry_run:
        print(f"Would write to {gitignore_path}:")
        print("-" * 40)
        print(new_content)
    else:
        # Written with `write_text`, deliberately, and NOT through
        # `write_text_nofollow` like everything stn-h5q covers. This is the
        # AUTHOR's file, not one stencil generated: a `.gitignore` that is a
        # symlink into a dotfiles repository is a thing people really do, and
        # refusing it would break a working setup to guard a file whose whole
        # content the author already controls.
        gitignore_path.write_text(new_content, encoding="utf-8")
        print(f"{action} {gitignore_path}")
        for entry in entries:
            print(f"  {entry}")

    # The section this used to write, if it is somewhere else. Named rather
    # than removed: it is a file outside the config directory, possibly in
    # another repository, and deleting from there is a worse hazard than
    # leaving a stale block. But saying nothing would leave the author with
    # two managed sections and stencil maintaining only one.
    stale = Path.cwd() / ".gitignore"
    if stale != gitignore_path and stale.is_file():
        try:
            # UnicodeDecodeError is a ValueError, NOT an OSError, so the
            # guard below used to let it through -- and this block runs
            # AFTER the write, so a non-UTF-8 .gitignore in the working
            # directory ended a successful `install` with a traceback and
            # rc=1. Measured with a latin-1 comment in the file.
            if GITIGNORE_START in stale.read_text(
                encoding="utf-8", errors="replace"
            ):
                print(
                    f"Note: {stale} still holds a stencil section from an "
                    "older version, which stencil no longer maintains. "
                    "Delete that block; the managed section now lives beside "
                    "the config file."
                )
        except (OSError, UnicodeDecodeError):
            pass


def main():
    """The console-script entry point, and the one place install faults land.

    stn-hwo. `pipeline.VendoredAssetError` means a file stencil SHIPS is
    missing or damaged, which no amount of editing .config.yaml will fix. It
    deliberately does not travel on the ValueError channel package_contexts
    collects config problems on, so without this it reached the terminal as a
    traceback. Caught here rather than at each of the three call sites so the
    next command to read a vendored asset gets the same treatment for free.
    """
    try:
        _main()
    except pipeline.VendoredAssetError as error:
        print(f"Error: {error}", file=sys.stderr)
        print(
            "\nThis is stencil's own installation, not your config. Nothing "
            "was generated, removed or written.",
            file=sys.stderr,
        )
        sys.exit(1)


def _main():
    parser = argparse.ArgumentParser(
        description="Generate package scaffolding from templates"
    )
    parser.add_argument(
        "--config",
        default=".config.yaml",
        help="Path to config file (default: .config.yaml in working directory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def _add_global_opts(p):
        """The same two options again, on a subparser, defaulting to nothing.

        stn-w4v. These are declared twice on purpose -- both spellings are
        documented, and `stencil gen --config x` has to work as well as
        `stencil --config x gen`. What made the second one silently wrong was
        the DEFAULT: argparse parses the main parser first and stores the real
        value, then parses the subparser, whose default for the same `dest`
        overwrites what the main parser just stored. So the documented
        spelling -- generate.py's own module docstring says
        `stencil [--config <path>] gen [--all] [pkg]` -- read .config.yaml and
        said nothing about it, and `--dry-run` before the subcommand meant the
        preview someone ran actually wrote.

        SUPPRESS is the fix rather than a post-parse merge: with no default,
        argparse sets the attribute only when the option is actually present
        on the subcommand, so an absent one leaves the main parser's value
        alone and the main parser's own default is the single source of it.
        """
        p.add_argument(
            "--config",
            default=argparse.SUPPRESS,
            help="Path to config file (default: .config.yaml)",
        )
        p.add_argument(
            "--dry-run",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Show what would be done without making changes",
        )

    gen_p = sub.add_parser(
        "gen", help="Generate scaffolding for a package or all packages"
    )
    gen_p.add_argument(
        "pkg", nargs="?", help="Package ID (e.g. hs6); omit when using --all"
    )
    gen_p.add_argument(
        "--all", action="store_true", help="Generate for every package in the config"
    )
    _add_global_opts(gen_p)

    clean_p = sub.add_parser("clean", help="Remove generated files")
    clean_p.add_argument(
        "pkg", nargs="?", help="Package ID to clean (required unless --all)"
    )
    clean_p.add_argument("--all", action="store_true", help="Clean every package")
    _add_global_opts(clean_p)

    install_p = sub.add_parser(
        "install", help="Install or update .gitignore with stencil-managed entries"
    )
    _add_global_opts(install_p)

    list_p = sub.add_parser("list", help="List available packages")
    _add_global_opts(list_p)

    sub.add_parser("version", help="Print the installed stencil version")

    help_p = sub.add_parser("help", help="Show help (optionally for a subcommand)")
    help_p.add_argument("topic", nargs="?", help="Subcommand to show help for")

    args = parser.parse_args()

    # Answered before the config is touched: the question is usually asked
    # because an install is suspect, and that is no time to demand a project.
    if args.command == "version":
        print(f"stencil {__version__}")
        return

    if args.command == "help":
        topic = getattr(args, "topic", None)
        if topic and topic in sub.choices:
            sub.choices[topic].print_help()
        else:
            parser.print_help()
        return

    config_path = Path(args.config).resolve()
    config_dir = config_path.parent
    config = load_config(config_path)

    if args.command == "install":
        # Narrow on purpose: only the pre-flight read is inside the try, so
        # install_gitignore itself runs unguarded and any failure past this
        # point is a real traceback rather than a suppressed one-liner. See
        # the matching comment on the `clean` branch below.
        #
        # package_contexts rather than get_generated_files, though either
        # would raise: this call exists to VALIDATE, and computing a file
        # list only to discard it reads like a mistake the next person
        # would tidy away. install_gitignore then reads the packages a
        # second time, which is accepted -- a context is microseconds and
        # the alternative is threading the list through a signature this
        # ticket deliberately left alone.
        try:
            package_contexts(config)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        install_gitignore(config, config_dir, args.dry_run)
        return

    if "packages" not in config:
        print("Error: 'packages' is required in config", file=sys.stderr)
        sys.exit(1)

    if args.command == "list":
        list_packages(config)
        return

    # output_dir is resolved relative to THE CONFIG FILE, not to the working
    # directory. It used to be CWD-relative, which made it the only path in a
    # config that was: templates_dir and every `brand: file://` resolve against
    # the config. Same config, different shell, different output directory.
    #
    # Changed rather than kept, because a per-package output_dir was being
    # added and two keys of the same name resolving against different bases is
    # a trap worth more than backwards compatibility with a behaviour nothing
    # used -- checked across cs234 and cs425: no config sets it.
    #
    # checked_output_base (stn-40a, stn-pe3) replaces a bare join-and-resolve
    # with a shape check, a path check, and a containment check against
    # config_dir -- so a non-string value is a readable error instead of a
    # bare TypeError, and an escaping or symlinked value is refused instead
    # of silently becoming the ground `clean` deletes from.
    #
    # This MUST stay here, above the `clean` branch below. A bad output_dir
    # is FATAL for every command, including `clean` -- unlike a broken
    # PACKAGE, which `clean` only warns about and still cleans around via its
    # degraded path (see CLEAN_DEGRADED_TRAILER). That degraded path exists
    # so a package with its OWN manifest can still be cleaned when the rest
    # of the config does not parse; it has nothing to stand on without a
    # trustworthy output_base, since every path clean unlinks -- manifest or
    # config-derived -- is relative to it. Moving this below clean's
    # degraded pre-flight would reopen exactly the destructive path stn-pe3
    # closes: an escaping output_dir would work again any time the rest of
    # the config also happens to be broken. test_path_containment.py's
    # ordering test (M7) pins this and would be the only thing to notice.
    try:
        output_base = checked_output_base(config, config_dir)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.command == "clean":
        if not args.all and not args.pkg:
            parser.error(
                "clean requires either --all or a package ID (e.g. stencil clean hs1)"
            )
        package_id = None if args.all else args.pkg
        # Before the pre-flight, so a mistyped package id is answered as a
        # mistyped package id. Below it, `stencil clean typo-here` on a
        # config with an unrelated broken package reported that other
        # package's problem instead -- true, but not an answer to what was
        # asked, and it sends someone to fix a file they were not editing.
        configured = config.get("packages")
        if package_id is not None and (
            not isinstance(configured, dict) or package_id not in configured
        ):
            print(f"Error: Unknown package {package_id}", file=sys.stderr)
            if isinstance(configured, dict):
                list_packages(config)
            sys.exit(1)
        # Narrow on purpose: clean_generated unlinks files in a loop and then
        # rmdirs, so wrapping the whole call in `except ValueError` would
        # print a bare "Error: ..." with the traceback suppressed AFTER an
        # unknown number of files were already deleted, if anything below the
        # pre-flight ever raised one. Suppressing a traceback is right for
        # "your config is wrong" and wrong for "something failed halfway
        # through destroying files" -- so only the read is inside the try;
        # clean_generated itself runs outside it, same as install above.
        #
        # Unlike install and gen, a broken config here does NOT exit: it is
        # printed as a WARNING, and clean_generated still runs, because
        # clean (stn-2x4) does not need the config at all for a package that
        # has its own manifest. Passing CLEAN_DEGRADED_TRAILER instead of
        # the gen/install default is what stops that warning from
        # contradicting itself -- printed immediately before clean removes
        # files and exits 0, "Nothing was generated, removed or written ...
        # which refuses for the same reason this did" would be a direct
        # lie about the run that just happened (architecture review D2).
        config_readable = True
        try:
            package_contexts(config, trailer=CLEAN_DEGRADED_TRAILER)
        except ValueError as e:
            config_readable = False
            print(f"Warning: {e}", file=sys.stderr)
        problems = clean_generated(
            output_base,
            config,
            package_id=package_id,
            dry_run=args.dry_run,
            config_readable=config_readable,
        )
        if problems:
            # _safe on every line, for the reason _raise_config_problems runs
            # it on the warning printed a few lines above: a package id is
            # config text, and YAML's double-quoted style honours \x escapes,
            # so `"demo\x1b[2Jx":` puts a real control character in the id
            # without a raw control byte anywhere in the file. Measured: the
            # warning above rendered it as `demo\x1b[2Jx` while this message
            # printed the escape raw and repainted the terminal -- the two
            # halves of one report disagreeing about the same id, with the
            # unescaped half sitting directly underneath a sentence promising
            # the escape "cannot repaint this line".
            print(
                "Error: these packages could not be cleaned:\n  "
                + "\n  ".join(_safe(problem) for problem in problems),
                file=sys.stderr,
            )
            sys.exit(1)
        return

    if args.command != "gen":
        return

    # gen: require --all or pkg
    if not args.all and not args.pkg:
        gen_p.print_help()
        return

    env = build_environment(config, config_dir)

    try:
        validate_config(config, env, config_dir)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # stn-dl3r: moved BELOW validate_config (which runs package_contexts),
    # not above it. `{}`, `None` and `""` are all falsy just like `[]`, so
    # this check used to fire FIRST for a malformed, non-list `templates:`
    # -- printing "No templates defined in config" before package_contexts
    # ever got a chance to name the actual shape mistake. `install` has no
    # matching emptiness check and went straight to package_contexts, so the
    # two commands disagreed about the identical config. Below
    # validate_config, a shape mistake is reported identically on both;
    # only a config with a WELL-SHAPED but genuinely empty `templates:`
    # (`[]`, or the key absent) still reaches this line. `gen` refusing
    # `templates: []` while `install` accepts it is a separate,
    # pre-existing asymmetry -- about emptiness, not shape -- and is out of
    # scope here.
    template_defs = config.get("templates", [])
    if not template_defs:
        print("Error: No templates defined in config", file=sys.stderr)
        sys.exit(1)

    # stn-zfc. Two ways a package could fail while `gen` still exited 0, and
    # both of them are here rather than inside generate_package, because this
    # is the only place that knows whether one package's failure should stop
    # the others.
    #
    #   * render_templates prints its own message and RE-RAISES. Nothing
    #     caught it, so a StrictUndefined error -- which AGENTS.md keeps
    #     deliberately fatal, so a renamed context key cannot render as the
    #     empty string -- reached the terminal as a traceback, with the
    #     package that failed named nowhere in it.
    #   * generate_package returns None when a package has no templates at
    #     all, and the --all loop discarded the return value, so the loop
    #     continued and main returned 0.
    #
    # Collected rather than raised at the first failure, for the reason
    # package_contexts aggregates config problems: fixing one and running
    # again to discover the next is the thing being avoided.
    def _generate(package_id: str) -> str | None:
        """Returns a problem, or None when the package generated."""
        try:
            out = generate_package(
                env, config, output_base, package_id, args.dry_run, config_dir
            )
        except pipeline.VendoredAssetError:
            # Not this package's fault and not per-package at all: every
            # package reads the same vendored lockfiles, so reporting it once
            # per package would be N copies of one install problem. main()
            # catches it and says what it actually is.
            raise
        except ValueError as error:
            # Narrower than the broad handler below, and must come first: a
            # config-shaped refusal (e.g. stn-vhr's containment check)
            # already reads as a config message, and the broad handler would
            # prefix it with "ValueError: ", which package_contexts' own
            # docstring argues at length a config message must never carry.
            return f"{package_id}: {error}"
        except Exception as error:
            # Broad on purpose: this is the CLI boundary, and a traceback is
            # never the right report here. The type is kept in the message so
            # nothing is actually lost by not printing the stack.
            return f"{package_id}: {type(error).__name__}: {error}"
        if out is None:
            return f"{package_id}: nothing was generated"
        return None

    if args.all:
        failures = [
            problem
            for problem in (_generate(pid) for pid in config["packages"])
            if problem
        ]
        if failures:
            print(
                "Error: these packages could not be generated:\n  "
                # _safe on every line, exactly as clean's sibling printer
                # does it above, and for a sharper reason than a package id:
                # generate_package's containment refusal (stn-vhr) puts a
                # RESOLVED FILESYSTEM PATH in the message, and that path is
                # the target of a symlink -- so the escape lives in the link
                # target STRING, committed in the tree, with no control byte
                # in any file and no need for the target to exist. Measured
                # before this was added: `gen --dry-run` printed a raw ESC
                # and repainted the terminal, while `clean` rendered the
                # SAME string as `\x1b[2J`, because only this side was
                # missing. check_config_path cannot help here -- it repr's
                # what the CONFIG declared, and this is what the filesystem
                # resolved to, which it never sees.
                + "\n  ".join(_safe(failure) for failure in failures),
                file=sys.stderr,
            )
            print(
                "\nSome packages may be half-written; generation is "
                "idempotent, so fix the cause and run again.",
                file=sys.stderr,
            )
            sys.exit(1)
        return

    package_id = args.pkg
    problem = _generate(package_id)
    if problem:
        # _safe for the reason the --all printer above says at length.
        print(f"Error: {_safe(problem)}", file=sys.stderr)
        if "nothing was generated" in problem:
            list_packages(config)
        sys.exit(1)
    out = output_base / config["packages"][package_id].get("dir", package_id)
    if not args.dry_run:
        print(f"\nSuccessfully generated files for {package_id} in {out}")


if __name__ == "__main__":
    main()
