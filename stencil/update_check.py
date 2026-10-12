"""Compare the installed stencil with the newest release tag, and offer to install it.

Two doors, one decision. `stencil update` always looks. Every other command
looks only when this install has not been run for at least an hour, which is
what "check on startup after it has been sitting unused" means: a `make gen`
loop does not hit the network, and coming back the next session does.

The newest version is the highest `vX.Y.Z` tag on the public repository, not
`main`. Tags are how a release is cut (AGENTS.md); `main` can sit ahead of
the tag without being a version anyone can ask for by number.

A source checkout is never replaced. `pip install --force-reinstall` from
inside an editable tree is how a development install turns into a released
one without anyone noticing. The check still runs for an install under
site-packages, which is what `make reinstall` produces.

A lookup failure, a missing git, or a cache directory that cannot be written
does not fail the command that woke stencil up. `stencil update` is the one
that reports those, because that command's only job is the check.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

from . import __version__

REPO_URL = "https://github.com/grimwm/stencil.git"
IDLE_SECONDS = 60 * 60
_DISABLE_VALUES = {"1", "true", "yes"}


def updates_disabled() -> bool:
    return os.environ.get("STENCIL_NO_UPDATE_CHECK", "").strip().lower() in _DISABLE_VALUES


def running_from_checkout() -> bool:
    """True when this module was imported from a tree, not from an install.

    An editable install's `__file__` is the checkout, because the path file
    points at it. That is the case this exists to catch: the code under test
    and a `pip install -e` of the same tree must not be offered a reinstall.
    """
    parts = Path(__file__).resolve().parts
    return "site-packages" not in parts and "dist-packages" not in parts


def stamp_path() -> Path:
    """Per-install idle stamp. Two venvs do not share an hour."""
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    prefix = Path(sys.prefix).resolve()
    ident = prefix.name
    # sys.prefix alone collides when two trees both use `.venv`. The full
    # path does not. Keep it out of the filename: a path separator there is
    # a directory.
    digest = _digest(str(prefix))
    return Path(cache) / "stencil" / f"last-used-{ident}-{digest}"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def release_key(name: str) -> tuple[int, int, int] | None:
    """`v1.6.0` and `1.6.0` parse. A pre-release or a local suffix does not."""
    text = name[1:] if name.startswith("v") else name
    parts = text.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return int(parts[0]), int(parts[1]), int(parts[2])


def tag_names(ls_remote: str) -> list[str]:
    """Tag names from `git ls-remote --tags`, peeled annotations included once."""
    names: list[str] = []
    for line in ls_remote.splitlines():
        if "\t" not in line:
            continue
        ref = line.split("\t", 1)[1].strip()
        if ref.endswith("^{}"):
            ref = ref[: -len("^{}")]
        prefix = "refs/tags/"
        if ref.startswith(prefix):
            names.append(ref[len(prefix) :])
    return names


def select_latest(names: list[str]) -> str | None:
    """The highest X.Y.Z, without the leading v. Non-releases are ignored."""
    best: tuple[int, int, int] | None = None
    for name in names:
        key = release_key(name)
        if key is not None and (best is None or key > best):
            best = key
    if best is None:
        return None
    return ".".join(str(part) for part in best)


def version_tag(version: str) -> str:
    return version if version.startswith("v") else f"v{version}"


def fetch_latest(run=None, timeout: float = 5.0) -> tuple[str | None, str | None]:
    """Return `(version, error)`. One of them is None."""
    if run is None:
        run = _default_run
    try:
        proc = run(
            ["git", "ls-remote", "--tags", REPO_URL],
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"could not list stencil tags: {exc}"
    if getattr(proc, "returncode", 1) != 0:
        detail = (getattr(proc, "stderr", None) or getattr(proc, "stdout", None) or "")
        detail = str(detail).strip() or "git ls-remote failed"
        return None, f"could not list stencil tags: {detail}"
    latest = select_latest(tag_names(getattr(proc, "stdout", "") or ""))
    if latest is None:
        return None, "could not list stencil tags: no release tags found"
    return latest, None


def _default_run(args, timeout):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def due(stamp: Path, now: float, idle: float = IDLE_SECONDS) -> bool:
    """True when the stamp is missing or at least `idle` seconds old."""
    try:
        used = stamp.stat().st_mtime
    except OSError:
        return True
    return now - used >= idle


def record_use(stamp: Path, now: float, version: str = __version__) -> None:
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(version + "\n")
        os.utime(stamp, (now, now))
    except OSError:
        return


def _notice(installed: str, latest: str) -> str:
    return f"stencil {installed} is installed; {latest} is the latest tag."


def _ask(prompt: str, input_fn) -> bool:
    print(prompt, file=sys.stderr, end="")
    try:
        answer = input_fn("")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def install_version(version: str, run=None) -> bool:
    if run is None:
        run = _default_install
    spec = f"stencil @ git+{REPO_URL}@{version_tag(version)}"
    proc = run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--disable-pip-version-check",
            spec,
        ]
    )
    return getattr(proc, "returncode", 1) == 0


def _default_install(args):
    return subprocess.run(args, check=False)


def _reexec() -> None:
    os.execv(sys.executable, [sys.executable, "-m", "stencil.generate", *sys.argv[1:]])


def _consider(
    *,
    installed: str,
    latest: str | None,
    error: str | None,
    interactive: bool,
    assume_yes: bool,
    announce: bool,
    ask,
    install,
    reexec,
) -> int:
    """0 when the command may continue, 1 when an explicit check failed."""
    if error or latest is None:
        if announce:
            print(error or "could not list stencil tags", file=sys.stderr)
            return 1
        return 0
    installed_key = release_key(installed)
    latest_key = release_key(latest)
    if installed_key is None or latest_key is None:
        if announce:
            print(
                f"stencil {installed} cannot be compared with tag {latest}.",
                file=sys.stderr,
            )
            return 1
        return 0
    if installed_key > latest_key:
        if announce:
            print(
                f"stencil {installed} is installed; the latest tag is {latest}.",
                file=sys.stderr,
            )
        return 0
    if installed_key == latest_key:
        if announce:
            print(f"stencil {installed} is the latest.", file=sys.stderr)
        return 0
    if not interactive and not assume_yes:
        print(
            _notice(installed, latest) + " Run `stencil update` to install it.",
            file=sys.stderr,
        )
        return 0
    accepted = assume_yes or _ask(
        _notice(installed, latest) + f" Update to {latest}? [y/N] ",
        ask,
    )
    if not accepted:
        if announce:
            print("Not updated.", file=sys.stderr)
        return 0
    if not install(latest):
        print(f"Could not install stencil {latest}.", file=sys.stderr)
        return 1 if announce else 0
    print(f"Installed stencil {latest}.", file=sys.stderr)
    if reexec is not None:
        reexec()
    return 0


def on_startup(
    *,
    disabled: bool | None = None,
    checkout: bool | None = None,
    stamp: Path | None = None,
    now: float | None = None,
    idle: float = IDLE_SECONDS,
    fetch=None,
    tty: bool | None = None,
    ask=None,
    install=None,
    reexec=None,
    installed: str = __version__,
) -> None:
    """Look for a newer tag when this install has been idle for `idle` seconds.

    The stamp is refreshed on every startup that is allowed to look, including
    the ones that are not yet due, so the hour is measured from the last use
    rather than from the last lookup.
    """
    if disabled is None:
        disabled = updates_disabled()
    if disabled:
        return
    if checkout is None:
        checkout = running_from_checkout()
    if checkout:
        return
    if stamp is None:
        stamp = stamp_path()
    if now is None:
        now = time.time()
    is_due = due(stamp, now, idle)
    record_use(stamp, now, installed)
    if not is_due:
        return
    if fetch is None:
        fetch = fetch_latest
    latest, error = fetch()
    if tty is None:
        tty = sys.stdin.isatty()
    if ask is None:
        ask = input
    if install is None:
        install = install_version
    if reexec is None:
        reexec = _reexec
    _consider(
        installed=installed,
        latest=latest,
        error=error,
        interactive=tty,
        assume_yes=False,
        announce=False,
        ask=ask,
        install=install,
        reexec=reexec,
    )


def command(
    *,
    yes: bool = False,
    disabled: bool = False,
    checkout: bool | None = None,
    stamp: Path | None = None,
    now: float | None = None,
    fetch=None,
    tty: bool | None = None,
    ask=None,
    install=None,
    installed: str = __version__,
) -> int:
    """Always look. Does not re-exec: the user asked for the update itself."""
    if disabled:
        print("Update check skipped (STENCIL_NO_UPDATE_CHECK).", file=sys.stderr)
        return 0
    if checkout is None:
        checkout = running_from_checkout()
    if checkout:
        print(
            "stencil is running from a source checkout, so it was not replaced.",
            file=sys.stderr,
        )
        return 0
    if stamp is None:
        stamp = stamp_path()
    if now is None:
        now = time.time()
    record_use(stamp, now, installed)
    if fetch is None:
        fetch = fetch_latest
    latest, error = fetch()
    if tty is None:
        tty = sys.stdin.isatty()
    if ask is None:
        ask = input
    if install is None:
        install = install_version
    return _consider(
        installed=installed,
        latest=latest,
        error=error,
        interactive=tty,
        assume_yes=yes,
        announce=True,
        ask=ask,
        install=install,
        reexec=None,
    )
