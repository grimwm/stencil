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
import os
import re
import shutil
import stat
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, Undefined, meta, nodes
from jinja2.exceptions import TemplateNotFound

from . import __version__, assets, pipeline

# Script directory
SCRIPT_DIR = Path(__file__).parent

# Gitignore markers
GITIGNORE_START = "# >>> stencil >>>"
GITIGNORE_END = "# <<< stencil <<<"


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
# Globs stay: * ? [ ] are the point of an outputs pattern.
_UNSAFE_IN_PATH = re.compile(r"[$`;|&<>\\\n]")

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


def check_config_path(package_id: str, where: str, value) -> str:
    """Refuse a configured path that would not behave like a filename."""
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
    if ".." in Path(text).parts:
        raise ValueError(
            f"Package {package_id}: {where} {text!r} escapes the package "
            "directory. Paths are relative to it and must stay inside."
        )
    return text


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

    # package_name is required for zip packages (not for doc or none)
    package_name = package.get("package_name")
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
        check_config_path(package_id, "docs", d) for d in package.get("docs", [])
    ]

    # slides list: markdown rendered as a slide deck instead of a flowing document.
    # Same pipeline, different pandoc template plus the slide-sections filter.
    slides = [
        check_config_path(package_id, "slides", d) for d in package.get("slides", [])
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
    raw_output = package.get("output_dir")
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
        "package_dir": check_config_path(
            package_id, "dir", package.get("dir", f"{package_id}")
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
    when_keys: set[str] = set()
    for tdef in config.get("templates", []):
        when = tdef.get("when")
        if when is None:
            continue
        when_keys |= {when} if isinstance(when, str) else set(when)

    contexts = package_contexts(config, config_dir)
    available: set[str] = set()
    for context in contexts.values():
        available |= set(context)

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
) -> None:
    """Copy a config-level brand image into the package it brands.

    Copied rather than referenced, and copied without being asked for. The
    folders stencil generates are routinely handed to someone as a project of
    their own, separate from the repository that produced them -- so a logo
    that lived only next to .config.yaml would leave the recipient with a
    document referring to a file they were never given. A copy per package is
    the cost of each folder standing on its own, and the copies are ignored by
    git for the same reason every other generated file is.
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
        open(destination, "wb") as dst,
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


def _raise_config_problems(problems: list[str]) -> None:
    """Turn collected config problems into the one ValueError callers print.

    Deliberately names no file: --config means the path is not always
    .config.yaml, and nothing down here is told which one it got. Every
    caller prints this behind an "Error: " prefix, so the first line reads
    as a continuation of one rather than as a second heading.
    """
    unique = list(dict.fromkeys(_safe(p) for p in problems))
    bullets = "\n".join(f"- {p}" for p in unique)
    count = "this problem" if len(unique) == 1 else f"these {len(unique)} problems"
    raise ValueError(
        f"the config has {count}:\n{bullets}\n\n"
        "Nothing was generated, removed or written. Fix the config and run "
        "again -- and if files from an earlier, working config are still on "
        "disk, remove that directory by hand rather than reaching for "
        "`clean`, which refuses for the same reason this did."
    )


def package_contexts(config: dict, config_dir: Path | None = None) -> dict[str, dict]:
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
    templates = config.get("templates")
    if isinstance(templates, list):
        for tdef in templates:
            if not isinstance(tdef, dict):
                continue
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
        _raise_config_problems(problems)

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
        _raise_config_problems(problems)

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

    if not output_dir.exists():
        if dry_run:
            print(f"Would create directory: {output_dir}")
        else:
            output_dir.mkdir(parents=True)
            print(f"Created directory: {output_dir}")

    config_templates = list(config.get("templates", []))
    template_defs = injected_templates(context) + config_templates
    if not template_defs:
        print(f"Error: No templates defined in config", file=sys.stderr)
        return None

    render_templates(env, template_defs, context, output_dir, dry_run)

    # After the templates, so a package that fails to render does not leave a
    # logo behind in a directory with nothing to use it.
    if context.get("has_pages"):
        copy_brand_image(
            config,
            config["packages"][package_id],
            config_dir or output_base.parent,
            output_dir,
            dry_run,
        )

    return output_dir


def render_templates(
    env: Environment,
    template_defs: list,
    context: dict,
    output_dir: Path,
    dry_run: bool = False,
):
    """Render all templates to the output directory."""
    templates = []

    for tdef in template_defs:
        if not when_holds(tdef, context):
            continue
        src = tdef["src"]
        templates.append((src, tdef.get("dest", template_dest(src))))

    for template_name, output_name in templates:
        try:
            template = env.get_template(template_name)
            content = template.render(**context)

            output_path = output_dir / output_name

            if dry_run:
                print(f"Would write: {output_path}")
                print("-" * 40)
                print(content)
                print()
            else:
                # Create parent directories if needed (for nested paths like .vscode/settings.json)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(content)
                # Set execute bit on shell scripts
                if output_path.suffix == ".sh":
                    output_path.chmod(
                        output_path.stat().st_mode
                        | stat.S_IXUSR
                        | stat.S_IXGRP
                        | stat.S_IXOTH
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
    """
    entries = set()
    config_templates = config.get("templates", [])

    contexts = package_contexts(config)

    for package_id, context in contexts.items():
        package = config["packages"][package_id]
        pkg_dir = package.get("dir", package_id)

        # Check each template's `when` condition against this package's context
        for tdef in config_templates:
            if not when_holds(tdef, context):
                continue
            dest = tdef.get("dest", template_dest(tdef.get("src", "")))
            if dest:
                entries.add(f"{pkg_dir}/{dest}")

        # What stencil injects, from the one list generate_package renders
        # from, on the same predicates -- spelling them twice is what left a
        # package_sources-only doc package with five generated files that clean
        # could not see.
        for src in injected_sources(context):
            entries.add(f"{pkg_dir}/{template_dest(src)}")

        if context["has_pages"]:
            # The copied brand image, which `clean` should be able to see and
            # git should not. Named by its basename, which is what it is
            # copied to. Not a template, so it is not in the list above.
            brand_image = brand_image_path(brand_of(package, config)[0])
            if brand_image:
                entries.add(f"{pkg_dir}/{Path(brand_image).name}")

        # docs and slides generate .html files from .md files, and `make pdf`
        # prints each of those to a .pdf beside it (glob for feature variants)
        for md in list(package.get("docs", [])) + list(package.get("slides", [])):
            if md.endswith(".md"):
                entries.add(f"{pkg_dir}/{md.removesuffix('.md')}*.html")
                entries.add(f"{pkg_dir}/{md.removesuffix('.md')}*.pdf")

        # package_name is the zip file created by pkg target
        package_name = package.get("package_name")
        if package_name and package.get("package_type") == "zip":
            entries.add(f"{pkg_dir}/{package_name}")

        # A doc package's pkg target concatenates package_sources into
        # <stem>.html and prints that to <stem>.pdf (glob for feature variants)
        if package.get("package_type") == "doc" and package_name:
            stem = package_name.removesuffix(".pdf")
            entries.add(f"{pkg_dir}/{stem}*.html")
            entries.add(f"{pkg_dir}/{stem}*.pdf")

    return sorted(entries)


def clean_generated(
    output_base: Path,
    config: dict,
    package_id: str | None = None,
    dry_run: bool = False,
) -> None:
    """Remove files and directories that stencil generates.

    If package_id is None, clean all packages; otherwise clean only that package.

    The membership check runs before get_generated_files, which now reads and
    validates every package. That ordering matters for a DIRECT caller of
    this function; it is not what protects the CLI, and an earlier version of
    this docstring wrongly claimed it was. `main` pre-flights the whole config
    before calling in here, so by this point a broken sibling has already
    stopped the run -- which is why main does its own membership check first,
    above that pre-flight. Both exist: this one so the API cannot be made to
    delete from a config it never checked, that one so a typo gets "Unknown
    package" rather than a lecture about a package the user did not mention.
    """
    if package_id is not None and package_id not in config.get("packages", {}):
        print(f"Error: Unknown package {package_id}", file=sys.stderr)
        list_packages(config)
        sys.exit(1)

    entries = get_generated_files(config)

    if package_id is not None:
        pkg_dir = config["packages"][package_id].get("dir", package_id)
        entries = [e for e in entries if e.startswith(f"{pkg_dir}/")]
        if not entries:
            print(f"No generated paths for package {package_id}", file=sys.stderr)
            return

    # Resolve to absolute paths; sort by depth descending so we remove files before parent dirs
    paths_with_depth = []
    for entry in entries:
        path = (output_base / entry).resolve()
        if "*" in path.name:
            # Glob pattern: expand and collect matches
            for p in path.parent.glob(path.name):
                paths_with_depth.append((len(p.parts), p))
        else:
            paths_with_depth.append((len(path.parts), path))

    paths_with_depth.sort(key=lambda x: -x[0])

    for _, path in paths_with_depth:
        if not path.exists():
            continue
        if path.is_file():
            if dry_run:
                print(f"Would remove {path}")
            else:
                path.unlink()
                print(f"Removed {path}")
        # (entries are file paths only; no dir entries in list)

    # Remove empty directories (e.g. .vscode, scripts/) under package dirs
    parent_dirs = set(p.parent for _, p in paths_with_depth)
    # Only consider dirs at least one level below package root (don't remove hs1-Setup itself)
    candidate_dirs = []
    for d in parent_dirs:
        if not d.exists() or not d.is_dir():
            continue
        try:
            if len(d.relative_to(output_base).parts) >= 2:
                candidate_dirs.append(d)
        except ValueError:
            # Path is not under output_base (e.g. glob matched files elsewhere)
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
                d.rmdir()
                print(f"Removed directory {d}")
            else:
                print(f"Skipped non-empty directory (leave as-is): {d}")


def install_gitignore(config: dict, dry_run: bool = False):
    """Install or update .gitignore with stencil-managed entries.

    Uses marker comments to manage a section within .gitignore, allowing
    stencil to update its entries without disturbing user entries.
    """
    gitignore_path = Path.cwd() / ".gitignore"

    entries = get_generated_files(config)

    # Build the stencil section
    stencil_section = f"{GITIGNORE_START}\n"
    for entry in entries:
        stencil_section += f"{entry}\n"
    stencil_section += f"{GITIGNORE_END}\n"

    if gitignore_path.exists():
        content = gitignore_path.read_text()

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
        gitignore_path.write_text(new_content)
        print(f"{action} {gitignore_path}")
        for entry in entries:
            print(f"  {entry}")


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
        install_gitignore(config, args.dry_run)
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
    output_dir_raw = config.get("output_dir")
    output_base = (config_dir / output_dir_raw).resolve() if output_dir_raw else config_dir

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
        try:
            package_contexts(config)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        clean_generated(
            output_base, config, package_id=package_id, dry_run=args.dry_run
        )
        return

    if args.command != "gen":
        return

    # gen: require --all or pkg
    if not args.all and not args.pkg:
        gen_p.print_help()
        return

    env = build_environment(config, config_dir)
    template_defs = config.get("templates", [])
    if not template_defs:
        print("Error: No templates defined in config", file=sys.stderr)
        sys.exit(1)

    try:
        validate_config(config, env, config_dir)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
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
                + "\n  ".join(failures),
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
        print(f"Error: {problem}", file=sys.stderr)
        if "nothing was generated" in problem:
            list_packages(config)
        sys.exit(1)
    out = output_base / config["packages"][package_id].get("dir", package_id)
    if not args.dry_run:
        print(f"\nSuccessfully generated files for {package_id} in {out}")


if __name__ == "__main__":
    main()
