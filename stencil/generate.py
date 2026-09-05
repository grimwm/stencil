#!/usr/bin/env python3
"""
Generate package scaffolding from Jinja2 templates.

Usage:
    stencil [--config <path>] gen [--all] [pkg]   # Generate (default config: .config.yaml)
    stencil [--config <path>] clean [--all] [pkg]
    stencil [--config <path>] install
    stencil [--config <path>] list
    stencil help [COMMAND]                        # Same as -h / --help; optional COMMAND for subcommand help
"""

import argparse
import re
import stat
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, Undefined, meta, nodes
from jinja2.exceptions import TemplateNotFound

from . import assets, pipeline

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
    docs = package.get("docs", [])

    # slides list: markdown rendered as a slide deck instead of a flowing document.
    # Same pipeline, different pandoc template plus the slide-sections filter.
    slides = package.get("slides", [])

    both = sorted(set(docs) & set(slides))
    if both:
        raise ValueError(
            f"Package {package_id} lists {both} in both 'docs' and 'slides'; "
            "a markdown file belongs to exactly one of them"
        )

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
        "sql_imports": sql_imports,
        # The pandoc invocation, from stencil/pipeline.py rather than spelled
        # out in the compose template, so a test can assert on the same argv
        # the generated package builds with.
        "pandoc_image": pipeline.PANDOC_IMAGE,
        "pandoc_argv_doc": pipeline.annotated_argv("doc"),
        "pandoc_argv_slide": pipeline.annotated_argv("slide"),
        # CSS, JS and webfonts inlined into the pandoc templates. Loaded here
        # rather than fetched at page load, so a handout is self-contained and
        # make pdf does not depend on the network.
        "assets": assets.load(),
    }

    # Config-level template_env declares which custom keys these templates may
    # read and supplies the value for packages that do not set one. cs234
    # points three configs at a single templates directory, so its shared
    # Makefile.j2 reads has_playwright, which only answers/.config.yaml sets,
    # and deps_script, which only assignments/.config.yaml sets. Without a way
    # to declare a key without setting it, StrictUndefined would make one
    # shared template impossible to serve from more than one config.
    config_env = config.get("template_env")
    if isinstance(config_env, dict):
        for key, value in config_env.items():
            context.setdefault(key, value)

    # Any remaining custom key some package sets, left *undefined* for the
    # packages that do not -- the lenient Undefined, not this environment's
    # StrictUndefined. It is falsy in a condition, renders as nothing, and
    # still satisfies `| default(...)`, which a concrete False does not: cs234
    # writes `{{ front_controller | default('index.html') }}`, and defaulting
    # the key to False put the literal "False" where a filename belonged.
    # setdefault throughout, so a config cannot shadow a derived key.
    for key in declared_template_env_keys(config):
        context.setdefault(key, Undefined())

    # Custom template vars: merge into top-level context so `when` conditions and templates can access them directly
    template_env = package.get("template_env", {})
    if isinstance(template_env, dict):
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

    A custom key does not have to reach a template as a variable. cs234's
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
        # partials into it (cs234 does), so a renamed context key would
        # otherwise render a Makefile with a hole where a recipe used to be,
        # found by whoever next ran make. See tests/test_template_contract.py.
        undefined=StrictUndefined,
        trim_blocks=True,
        # keep indentation on lines after {% ... %} so templates stay readable
        lstrip_blocks=False,
        keep_trailing_newline=True,
    )


def generate_package(
    env: Environment,
    config: dict,
    output_base: Path,
    package_id: str,
    dry_run: bool = False,
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

    template_defs = list(config.get("templates", []))
    # When a package renders markdown, always include the pandoc templates and Lua
    # filters it needs so a config can't forget them.
    if context.get("has_pages"):
        doc_templates = [
            {"src": "hidden-filter.lua.j2"},
            {"src": "mermaid-figure-filter.lua.j2"},
            {"src": "embed-images.lua.j2"},
            # Drives the `pdf` compose service. Emitted for every package that
            # renders markdown, so `make pdf` needs no configuration to exist.
            {"src": "html-to-pdf.js.j2"},
            # Shared image the pdf and check-access services build from.
            {"src": "Dockerfile.browser.j2"},
        ]
        # Also for a doc package built from package_sources: Makefile-pkg runs
        # its sources through the doc service, which names this template.
        if context.get("has_docs") or context.get("has_package_sources"):
            doc_templates.insert(0, {"src": "html-template.html.j2"})
        if context.get("has_slides"):
            doc_templates.append({"src": "slide-template.html.j2"})
            doc_templates.append({"src": "slide-sections.lua.j2"})
        template_defs = doc_templates + template_defs
    if not template_defs:
        print(f"Error: No templates defined in config", file=sys.stderr)
        return None

    render_templates(env, template_defs, context, output_dir, dry_run)

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
        when = tdef.get("when")
        if when is not None:
            # Normalize to list
            if isinstance(when, str):
                when = [when]
            if not all(context.get(k) for k in when):
                continue
        src = tdef["src"]
        dest = tdef.get("dest", src.removesuffix(".j2"))
        templates.append((src, dest))

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

    # Templates always injected in generate_package for markdown-rendering packages
    shared_page_files = [
        "hidden-filter.lua",
        "mermaid-figure-filter.lua",
        "embed-images.lua",
        "html-to-pdf.js",
        "Dockerfile.browser",
    ]
    doc_template_files = ["html-template.html"]
    slide_template_files = ["slide-template.html", "slide-sections.lua"]

    # Process each package
    for package_id, package in config.get("packages", {}).items():
        pkg_dir = package.get("dir", package_id)

        # Build a minimal context for checking `when` conditions
        try:
            context = get_template_context(package_id, config)
        except ValueError:
            continue

        # Check each template's `when` condition against this package's context
        for tdef in config.get("templates", []):
            when = tdef.get("when")
            if when is not None:
                if isinstance(when, str):
                    when = [when]
                if not all(context.get(k) for k in when):
                    continue
            src = tdef.get("src", "")
            dest = tdef.get("dest", src.removesuffix(".j2"))
            if dest:
                entries.add(f"{pkg_dir}/{dest}")

        # Add template outputs for packages that render markdown. These read
        # the derived context rather than the raw keys, so the predicates are
        # the same ones generate_package injects on -- spelling them twice is
        # what left a package_sources-only doc package with five generated
        # files that clean could not see.
        if context["has_pages"]:
            for f in shared_page_files:
                entries.add(f"{pkg_dir}/{f}")
        if context["has_docs"] or context["has_package_sources"]:
            for f in doc_template_files:
                entries.add(f"{pkg_dir}/{f}")
        if context["has_slides"]:
            for f in slide_template_files:
                entries.add(f"{pkg_dir}/{f}")

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

    help_p = sub.add_parser("help", help="Show help (optionally for a subcommand)")
    help_p.add_argument("topic", nargs="?", help="Subcommand to show help for")

    args = parser.parse_args()

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

    # Resolve output_dir relative to CWD (defaults to CWD if omitted)
    output_dir_raw = config.get("output_dir")
    output_base = Path(output_dir_raw).resolve() if output_dir_raw else Path.cwd()

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
            generate_package(env, config, output_base, package_id, args.dry_run)
        return

    package_id = args.pkg
    out = generate_package(env, config, output_base, package_id, args.dry_run)
    if out is None:
        list_packages(config)
        sys.exit(1)
    if not args.dry_run:
        print(f"\nSuccessfully generated files for {package_id} in {out}")


if __name__ == "__main__":
    main()
