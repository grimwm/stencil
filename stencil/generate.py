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


def brand_of(package: dict, config: dict) -> tuple[str | None, str | None]:
    """The brand and its alt text for a package: its own, else config-wide.

    Taken as a pair rather than resolved key by key. A package that sets its
    own brand and no alt means "this logo, no alt yet" -- inheriting the
    config's alt there would silently label one logo with another's name.
    """
    if package.get("brand"):
        return package.get("brand"), package.get("brand-alt")
    return config.get("brand"), config.get("brand-alt")


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
            Path(brand_image_path(brand_of(package, config)[0])).name
            if brand_image_path(brand_of(package, config)[0])
            else brand_of(package, config)[0]
        ),
        "config_brand_alt": brand_of(package, config)[1],
        "package_name": package_name,
        "package_dir": package.get("dir", f"{package_id}"),
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


def validate_config(config: dict, env: Environment) -> None:
    """Reject custom keys that cannot do anything, in either direction.

    template_env accepts any key and `when:` tests any name, so a typo used to
    be silent both ways: an unknown key was set and read by nothing, and a
    `when:` naming a key nobody set read as None and skipped the template it
    guarded for every package. Neither produced output or an error.
    """
    when_keys: set[str] = set()
    for tdef in config.get("templates", []):
        when = tdef.get("when")
        if when is None:
            continue
        when_keys |= {when} if isinstance(when, str) else set(when)

    # What a template could legitimately read: everything stencil derives, plus
    # every custom key any package declares. Built per package because a config
    # error in one should not hide a naming error in another.
    available: set[str] = set()
    for package_id in config.get("packages", {}):
        try:
            available |= set(get_template_context(package_id, config))
        except ValueError:
            continue

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
    value, alt = brand_of(package, config)
    relative = brand_image_path(value)
    if not relative:
        return

    if not alt:
        raise ValueError(
            f"brand is an image ({value}) but brand-alt is not set. Add "
            "brand-alt: with the text a reader should hear in place of the "
            "logo, or use a plain string for brand to render it as a name."
        )

    source = (config_dir / relative).resolve()
    if not source.is_file():
        raise ValueError(
            f"brand points at {value}, which is not a file. Paths are resolved "
            f"relative to the config file's directory ({config_dir})."
        )

    destination = output_dir / source.name
    if dry_run:
        print(f"Would copy: {source} -> {destination}")
        return
    shutil.copyfile(source, destination)
    print(f"Copied: {destination}")


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
    """
    entries = set()
    config_templates = config.get("templates", [])

    # Process each package
    for package_id, package in config.get("packages", {}).items():
        pkg_dir = package.get("dir", package_id)

        # Build a minimal context for checking `when` conditions
        try:
            context = get_template_context(package_id, config)
        except ValueError:
            continue

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
    """
    entries = get_generated_files(config)

    if package_id is not None:
        if package_id not in config.get("packages", {}):
            print(f"Error: Unknown package {package_id}", file=sys.stderr)
            list_packages(config)
            sys.exit(1)
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
        p.add_argument(
            "--config",
            default=".config.yaml",
            help="Path to config file (default: .config.yaml)",
        )
        p.add_argument(
            "--dry-run",
            action="store_true",
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
        validate_config(config, env)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.all:
        for package_id in config["packages"]:
            generate_package(
                env, config, output_base, package_id, args.dry_run, config_dir
            )
        return

    package_id = args.pkg
    out = generate_package(
        env, config, output_base, package_id, args.dry_run, config_dir
    )
    if out is None:
        list_packages(config)
        sys.exit(1)
    if not args.dry_run:
        print(f"\nSuccessfully generated files for {package_id} in {out}")


if __name__ == "__main__":
    main()
