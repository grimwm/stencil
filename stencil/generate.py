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
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

# Script directory
SCRIPT_DIR = Path(__file__).parent

# Gitignore markers
GITIGNORE_START = "# >>> stencil >>>"
GITIGNORE_END = "# <<< stencil <<<"


def load_config(config_path: Path) -> dict:
    """Load and parse the configuration file."""
    with open(config_path) as f:
        return yaml.safe_load(f)


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
        raise ValueError(f"Package {package_id} has invalid package_type: {package_type}")

    # package_name is required for zip packages (not for doc or none)
    package_name = package.get("package_name")
    if package_type == "zip" and not package_name:
        raise ValueError(f"Package {package_id} is missing required 'package_name' (required for zip type)")

    # docs list for doc-type packages (markdown files to convert to HTML)
    docs = package.get("docs", [])

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
        "package_folder": package.get("package_folder", "htdocs"),
        "docs": docs,
        "has_docs": bool(docs),
        "services": services,
        # Derived from services
        "has_web": has_web,
        "has_mysql": has_mysql,
        "has_services": has_services,
        # Explicit features
        "sql_imports": sql_imports,
    }

    # Custom template vars: merge into top-level context so `when` conditions and templates can access them directly
    template_env = package.get("template_env", {})
    if isinstance(template_env, dict):
        context.update(template_env)
    # Also keep as nested dict for backward compatibility
    context["template_env"] = template_env if isinstance(template_env, dict) else {}

    return context


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
    # When package has docs, always include html template and Lua filters so config can't forget them
    if context.get("has_docs"):
        doc_templates = [
            {"src": "html-template.html.j2"},
            {"src": "hidden-filter.lua.j2"},
            {"src": "mermaid-figure-filter.lua.j2"},
        ]
        template_defs = doc_templates + template_defs
    if not template_defs:
        print(f"Error: No templates defined in config", file=sys.stderr)
        return None

    render_templates(env, template_defs, context, output_dir, dry_run)

    return output_dir


def render_templates(env: Environment, template_defs: list, context: dict, output_dir: Path, dry_run: bool = False):
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

    # Doc templates always included when package has docs (injected in generate_package)
    doc_template_files = [
        "html-template.html",
        "hidden-filter.lua",
        "mermaid-figure-filter.lua",
    ]

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

        # Add doc-only template outputs for packages with docs
        if package.get("docs"):
            for f in doc_template_files:
                entries.add(f"{pkg_dir}/{f}")

        # docs generates .html files from .md files (glob for feature variants)
        for md in package.get("docs", []):
            if md.endswith(".md"):
                entries.add(f"{pkg_dir}/{md.removesuffix('.md')}*.html")

        # package_name is the zip file created by pkg target
        package_name = package.get("package_name")
        if package_name:
            entries.add(f"{pkg_dir}/{package_name}")

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
    candidate_dirs = [
        d for d in parent_dirs
        if d.exists()
        and d.is_dir()
        and len(d.relative_to(output_base).parts) >= 2
    ]
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
    parser = argparse.ArgumentParser(description="Generate package scaffolding from templates")
    parser.add_argument("--config", default=".config.yaml", help="Path to config file (default: .config.yaml in working directory)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def _add_global_opts(p):
        p.add_argument("--config", default=".config.yaml", help="Path to config file (default: .config.yaml)")
        p.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")

    gen_p = sub.add_parser("gen", help="Generate scaffolding for a package or all packages")
    gen_p.add_argument("pkg", nargs="?", help="Package ID (e.g. hs6); omit when using --all")
    gen_p.add_argument("--all", action="store_true", help="Generate for every package in the config")
    _add_global_opts(gen_p)

    clean_p = sub.add_parser("clean", help="Remove generated files")
    clean_p.add_argument("pkg", nargs="?", help="Package ID to clean (required unless --all)")
    clean_p.add_argument("--all", action="store_true", help="Clean every package")
    _add_global_opts(clean_p)

    install_p = sub.add_parser("install", help="Install or update .gitignore with stencil-managed entries")
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
            parser.error("clean requires either --all or a package ID (e.g. stencil clean hs1)")
        package_id = None if args.all else args.pkg
        clean_generated(output_base, config, package_id=package_id, dry_run=args.dry_run)
        return

    if args.command != "gen":
        return

    # gen: require --all or pkg
    if not args.all and not args.pkg:
        gen_p.print_help()
        return

    # Resolve templates_dir for gen
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

    env = Environment(
        loader=FileSystemLoader(template_dirs),
        extensions=["jinja2.ext.do"],
        trim_blocks=True,
        lstrip_blocks=False,  # keep indentation on lines after {% ... %} so templates stay readable
        keep_trailing_newline=True,
    )
    template_defs = config.get("templates", [])
    if not template_defs:
        print("Error: No templates defined in config", file=sys.stderr)
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
