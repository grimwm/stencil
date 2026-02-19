#!/usr/bin/env python3
"""
Generate package scaffolding from Jinja2 templates.

Usage:
    python generate.py hs6            # Generate files for hs6
    python generate.py hs6 --dry-run  # Show what would be generated
    python generate.py --list         # List available packages
    python generate.py install        # Install .gitignore entries
"""

import argparse
import re
import shutil
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
    if package_type not in ("pdf", "zip"):
        raise ValueError(f"Package {package_id} has invalid package_type: {package_type}")

    # package_name is required for zip packages
    package_name = package.get("package_name")
    if package_type == "zip" and not package_name:
        raise ValueError(f"Package {package_id} is missing required 'package_name' (required for zip type)")

    # pdfs list for pdf-type packages
    pdfs = package.get("pdfs", [])

    # Build context
    context = {
        "package_id": package_id,
        "name": package.get("name", package_id),
        "package_name": package_name,
        "package_dir": package.get("dir", f"{package_id}"),
        "package_type": package_type,
        "package_folder": package.get("package_folder", "htdocs"),
        "pdfs": pdfs,
        "has_pdfs": bool(pdfs),
        "services": services,
        # Derived from services
        "has_web": has_web,
        "has_mysql": has_mysql,
        "has_services": has_services,
        # Explicit features
        "sql_import": package.get("sql_import"),
        "deps_script": package.get("deps_script"),
        "copy_files": package.get("copy_files"),
    }

    return context


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
                # Create parent directories if needed (for scripts/)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(content)
                print(f"Generated: {output_path}")

        except Exception as e:
            print(f"Error rendering {template_name}: {e}", file=sys.stderr)
            raise


def copy_scripts(context: dict, output_dir: Path, dry_run: bool = False):
    """Copy dependency scripts to the output directory."""
    deps_script = context.get("deps_script")
    if not deps_script:
        return

    scripts_src = SCRIPT_DIR / "scripts"
    scripts_dst = output_dir / "scripts"

    # Copy scripts for each OS type (each value is a list)
    for _, script_list in deps_script.items():
        for script_name in script_list:
            src_file = scripts_src / script_name
            dst_file = scripts_dst / script_name

            if src_file.exists():
                if dry_run:
                    print(f"Would copy: {src_file} -> {dst_file}")
                else:
                    scripts_dst.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dst_file)
                    print(f"Copied: {dst_file}")
            else:
                print(f"Warning: Script not found: {src_file}", file=sys.stderr)


def copy_files(context: dict, output_dir: Path, dry_run: bool = False):
    """Copy static files/directories from _generator/files/ to the output directory.

    Supports two formats:
      - Simple string: "filename" (copies to same name)
      - Dict with src/dest: {src: "filename", dest: "path/to/dest"}
    """
    copy_list = context.get("copy_files")
    if not copy_list:
        return

    files_src = SCRIPT_DIR / "files"

    for item in copy_list:
        # Handle both string and dict formats
        if isinstance(item, str):
            src_name = item
            dst_name = item
        else:
            src_name = item.get("src")
            dst_name = item.get("dest", src_name)

        src_path = files_src / src_name
        dst_path = output_dir / dst_name

        if not src_path.exists():
            print(f"Warning: File/directory not found: {src_path}", file=sys.stderr)
            continue

        if dry_run:
            if src_path.is_dir():
                print(f"Would copy directory: {src_path} -> {dst_path}")
            else:
                print(f"Would copy file: {src_path} -> {dst_path}")
        else:
            if src_path.is_dir():
                if dst_path.exists():
                    shutil.rmtree(dst_path)
                shutil.copytree(src_path, dst_path)
                print(f"Copied directory: {dst_path}")
            else:
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst_path)
                print(f"Copied: {dst_path}")


def list_packages(config: dict):
    """List all available packages."""
    print("Available packages:")
    for package_id, package in config.get("packages", {}).items():
        name = package.get("name", "")
        dir_name = package.get("dir", package_id)
        print(f"  {package_id:8} - {name:20} ({dir_name})")


def get_generated_files(config: dict) -> list[str]:
    """Determine what files stencil will generate based on templates config."""
    entries = []

    # Add template output files
    templates = config.get("templates", [])
    for tdef in templates:
        src = tdef.get("src", "")
        dest = tdef.get("dest", src.removesuffix(".j2"))
        if dest:
            entries.append(dest)

    # Collect per-package generated files
    copied = set()
    has_scripts = False
    for package in config.get("packages", {}).values():
        # copy_files entries
        for item in package.get("copy_files", []):
            if isinstance(item, str):
                copied.add(item)
            else:
                copied.add(item.get("dest", item.get("src", "")))

        # deps_script creates scripts/ directory
        if package.get("deps_script"):
            has_scripts = True

        # pdfs generates .pdf files from .md files
        for md in package.get("pdfs", []):
            if md.endswith(".md"):
                copied.add(md.removesuffix(".md") + ".pdf")

    entries.extend(sorted(copied))
    if has_scripts:
        entries.append("scripts/")

    return entries


def install_gitignore(config: dict, dry_run: bool = False):
    """Install or update .gitignore with stencil-managed entries.

    Uses marker comments to manage a section within .gitignore, allowing
    stencil to update its entries without disturbing user entries.
    """
    gitignore_path = Path.cwd() / ".gitignore"

    # Derive ignore entries from templates config
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
    parser.add_argument("package", nargs="?", help="Package ID (e.g., hs6) or 'install' to setup .gitignore")
    parser.add_argument("--list", action="store_true", help="List available packages")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be generated")
    parser.add_argument(
        "--config",
        help="Path to config file",
        required=True,
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    config_dir = config_path.parent

    # Load configuration
    config = load_config(config_path)

    # Handle 'install' command (doesn't require packages in config)
    if args.package == "install":
        install_gitignore(config, args.dry_run)
        return

    if "packages" not in config:
        print("Error: 'packages' is required in config", file=sys.stderr)
        sys.exit(1)

    if args.list:
        list_packages(config)
        return

    # Resolve templates_dir relative to config file location (string or list)
    templates_dir_raw = config.get("templates_dir")

    if templates_dir_raw:
        if isinstance(templates_dir_raw, str):
            templates_dir_raw = [templates_dir_raw]
        template_dirs = [(config_dir / d).resolve() for d in templates_dir_raw]
    else:
        template_dirs = []

    # Always append bundled templates as fallback search path
    bundled = SCRIPT_DIR / "templates"
    if bundled.resolve() not in [d.resolve() for d in template_dirs]:
        template_dirs.append(bundled)

    # Resolve output_dir relative to CWD (defaults to CWD if omitted)
    output_dir_raw = config.get("output_dir")
    output_base = Path(output_dir_raw).resolve() if output_dir_raw else Path.cwd()

    if not args.package:
        parser.print_help()
        return

    # Get package config
    try:
        context = get_template_context(args.package, config)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        list_packages(config)
        sys.exit(1)

    # Setup Jinja2 environment
    env = Environment(
        loader=FileSystemLoader(template_dirs),
        extensions=["jinja2.ext.do"],
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )

    # Determine output directory
    output_dir = output_base / context["package_dir"]

    if not output_dir.exists():
        if args.dry_run:
            print(f"Would create directory: {output_dir}")
        else:
            output_dir.mkdir(parents=True)
            print(f"Created directory: {output_dir}")

    # Render templates
    template_defs = config.get("templates", [])
    if not template_defs:
        print("Error: No templates defined in config", file=sys.stderr)
        sys.exit(1)
    render_templates(env, template_defs, context, output_dir, args.dry_run)

    # Copy dependency scripts
    copy_scripts(context, output_dir, args.dry_run)

    # Copy static files/directories
    copy_files(context, output_dir, args.dry_run)

    if not args.dry_run:
        print(f"\nSuccessfully generated files for {args.package} in {output_dir}")


if __name__ == "__main__":
    main()
