#!/usr/bin/env python3
"""Resolve the npm pins in stencil/pipeline.py into committed lockfiles.

Run from the repo root after bumping a version in ``BROWSER_NPM_PINS`` or
``FORMAT_NPM_PINS``:

    python3 scripts/vendor_npm_locks.py

The lockfiles land in stencil/assets/. Commit them in the same commit as the
pin change -- `npm ci` refuses a manifest its lockfile does not satisfy, so a
pin bumped without a re-vendor is a broken build rather than a silent drift.

This is scripts/vendor_page_assets.py's shape applied to npm: a maintainer runs
it once, with the network, and the artifact is committed. ``stencil gen`` then
ships it having touched nothing.

WHY npm RUNS IN A CONTAINER RATHER THAN ON THE MAINTAINER'S MACHINE. The
lockfile records what one npm resolved on one day, and the npm that reads it
back is the one inside pipeline.NODE_IMAGE. Resolving with a different major
can write a lockfileVersion the image's npm handles differently, and a
maintainer without node could not run this at all. Same argument as
pipeline.render() using the pandoc container rather than a local pandoc.

WHAT A REVIEWER OF A RE-VENDORING DIFF SHOULD CHECK, because a lockfile is a
supply-chain artifact and this script cannot judge any of it:

- every changed ``resolved`` URL still points at registry.npmjs.org;
- the advisories for everything that moved, not just the package you bumped --
  a transitive dependency can move on its own here;
- a release only days old, which is the window in which a compromised one is
  usually still being found.

The mechanical half is checked for you: tests/test_pins.py refuses a lockfile
whose root dependencies disagree with the pins, one with an entry that carries
no integrity hash, and one that resolves a tarball from anywhere but the public
registry.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stencil import pipeline  # noqa: E402

OUT = ROOT / "stencil" / "assets"

# (lockfile name, manifest name, pins) -- the two installs a generated package
# performs, read from pipeline.py so this script cannot pin something the
# scaffolding does not install.
TARGETS = (
    (
        pipeline.BROWSER_LOCKFILE,
        pipeline.BROWSER_MANIFEST_NAME,
        pipeline.BROWSER_NPM_PINS,
    ),
    (
        pipeline.FORMAT_LOCKFILE,
        pipeline.FORMAT_MANIFEST_NAME,
        pipeline.FORMAT_NPM_PINS,
    ),
)


# The registry to resolve against, named rather than left to whatever
# configuration the runtime picks up. The committed lockfile records a URL per
# package and tests/test_pins.py refuses anything but this host -- but that
# catches a wrong registry after the fact, and saying it here makes the intent
# legible in the script that produced the artifact.
REGISTRY = "https://registry.npmjs.org/"


def ownership_flags(runtime: str) -> list[str]:
    """Make the container write into the bind mount as the maintainer.

    Not the same flag for the two runtimes, and the difference is not cosmetic.
    Under docker, `--user <uid>:<gid>` writes files owned by that uid on the
    host, which is what is wanted. Under ROOTLESS PODMAN the same flag names a
    uid INSIDE the user namespace, which maps to a subuid on the host -- so the
    lockfile and npm's cache come back owned by an id the maintainer can
    neither read reliably nor delete without `podman unshare`, and the
    temporary directory outlives the run. `--userns=keep-id` is podman's
    spelling of the thing `--user` means here.
    """
    if Path(runtime).name == "podman":
        return ["--userns=keep-id"]
    return ["--user", f"{os.getuid()}:{os.getgid()}"]


def npm(work: str, runtime: str, args: list[str]) -> subprocess.CompletedProcess:
    """Run npm in ``work``, inside NODE_IMAGE, as the maintainer.

    HOME points into the mount because npm writes a cache under it, and with
    $HOME unset npm falls back to a path the unprivileged uid cannot create.
    """
    return subprocess.run(
        [
            runtime,
            "run",
            "--rm",
            *ownership_flags(runtime),
            "-e",
            "HOME=/work",
            "-e",
            "NPM_CONFIG_UPDATE_NOTIFIER=false",
            "-v",
            f"{work}:/work:z",
            "-w",
            "/work",
            pipeline.NODE_IMAGE,
            "npm",
            *args,
            "--registry",
            REGISTRY,
            "--no-audit",
            "--no-fund",
        ],
        capture_output=True,
        text=True,
    )


def resolve(manifest: str, runtime: str) -> str:
    """Resolve ``manifest`` into a lockfile, then prove the lockfile installs.

    THE SECOND HALF IS NOT CEREMONY. `--package-lock-only` takes every
    integrity hash from the registry's packument and downloads no tarball, so
    the hashes it writes have never been checked against real bytes. If the
    metadata and the tarball disagree, nothing here would notice and it would
    surface months later as an EINTEGRITY in a consumer's build, from a file
    that passed every test in this repository. Installing once from the
    lockfile is what turns the hashes into a measurement; it costs about half a
    second over the browser tree.

    `--ignore-scripts` because the generated scaffolding installs that way too,
    and this should exercise the same path a build does.
    """
    with tempfile.TemporaryDirectory(
        prefix="stencil-npm-lock-", ignore_cleanup_errors=True
    ) as work:
        (Path(work) / "package.json").write_text(manifest + "\n", encoding="utf-8")
        result = npm(work, runtime, ["install", "--package-lock-only"])
        if result.returncode != 0:
            raise SystemExit(
                f"npm could not resolve the manifest:\n"
                f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
            )
        lock = Path(work) / "package-lock.json"
        if not lock.is_file():
            raise SystemExit(
                "npm wrote no package-lock.json. It reports 'up to date' and "
                "writes nothing when a lock is already present and satisfied; "
                "there is none in a fresh temporary directory, so this means "
                "something else went wrong:\n" + result.stdout[-2000:]
            )
        text = lock.read_text(encoding="utf-8")

        verify = npm(work, runtime, ["ci", "--ignore-scripts"])
        if verify.returncode != 0:
            raise SystemExit(
                f"the lockfile resolved but does not install, so its integrity "
                f"hashes do not match the tarballs the registry serves. NOT "
                f"committed:\n{verify.stdout[-2000:]}\n{verify.stderr[-2000:]}"
            )
        return text


def summarise(name: str, text: str) -> None:
    data = json.loads(text)
    packages = data.get("packages", {})
    resolved = [entry for entry in packages.values() if entry.get("resolved")]
    integrity = [entry for entry in resolved if entry.get("integrity")]
    scripts = sorted(
        path
        for path, entry in packages.items()
        if entry.get("hasInstallScript")
    )
    print(f"  {name}")
    print(f"    lockfileVersion {data.get('lockfileVersion')}")
    print(f"    {len(packages)} entries, {len(resolved)} resolved, "
          f"{len(integrity)} with an integrity hash")
    if scripts:
        # Not a failure: puppeteer has one, and PUPPETEER_SKIP_DOWNLOAD makes it
        # a no-op. Printed because an install script is code that runs as root
        # at image build time, and a NEW one appearing is worth a look.
        print(f"    install scripts: {', '.join(scripts)}")


def main() -> None:
    runtime = pipeline.container_runtime()
    if runtime is None:
        raise SystemExit(
            "no container runtime found (looked for docker, podman). This "
            "script resolves the pins with the npm inside "
            f"{pipeline.NODE_IMAGE}, not with a local one."
        )

    OUT.mkdir(parents=True, exist_ok=True)
    print(f"Resolving npm pins with {pipeline.NODE_IMAGE} via {runtime}")
    for filename, manifest_name, pins in TARGETS:
        manifest = pipeline.npm_manifest(manifest_name, pins)
        text = resolve(manifest, runtime)
        (OUT / filename).write_text(text, encoding="utf-8")
        summarise(filename, text)
    print("done. Commit the lockfiles with the pin change that required them.")


if __name__ == "__main__":
    main()
