"""The browser image's own lockfile guard, run rather than read (stn-egv).

tests/test_compose_format_md.py already covers the format-md service's guard
against a tampered ``format-package-lock.json`` -- the earlier half of the
same story, stn-qge. This file is the other half: the epic is stn-e3m, and
Dockerfile.browser.j2 now carries the matching guard for its own lockfile,
``browser-package-lock.json`` (pipeline.BROWSER_LOCKFILE). The two guards
differ in where they run and what a false pass would look like, so the
duplication below is deliberate rather than copy-paste laziness -- see
AGENTS.md's "Both guards are checks, not gates" section for the shape they
share and the ways each is weaker than it looks.

WHERE THIS GUARD RUNS, AND WHY THAT CHANGES WHAT "REFUSED" MEANS. format-md's
guard fires inside a running container, against a file `cp`'d out of a bind
mount the host can still rewrite between the check and the copy. This guard
fires during `docker build`/`podman build`, against a file already snapshotted
into an image layer by `COPY` -- there is no window between the check and the
read for anything to rewrite, because a build has no host process racing it.
"Refused" here means the BUILD fails, not a container inside it, so the tool
under test is `compose build pdf`, and there is no running service to inspect
afterwards: a failed `RUN` produces no container, no logs volume, nothing but
the builder's own output.

THE CRITICAL ASSERTION, AND THE MEASUREMENT BEHIND IT. Both docker (BuildKit)
and podman print the FAILING INSTRUCTION'S OWN SOURCE TEXT when a `RUN` exits
non-zero -- the shell script inline in the Dockerfile, verbatim, as part of
the error report. The guard's refusal message contains the string
"is not the file stencil generated", and so does the Dockerfile source that
prints it. Measured on docker 29.6.2 / BuildKit / Compose v5.3.1, in a build
that actually reaches and fails the guard: that string appears SEVEN times in
the combined output, and only TWO of them are the guard executing. The other
five are the builder quoting the instruction it is about to run or just ran:
the `#N [5/9] RUN ...` step header, the `#N ERROR: process "..." did not
complete successfully` line, the numbered `108 | >>>   echo "..."` source
excerpt BuildKit prints as context, and the final
`failed to solve: process "..." did not complete successfully` summary line.
podman's plain-text builder does the same thing under different words --
`STEP n/m: RUN ...` and `Error: building at STEP "RUN ...": ...` both quote
the instruction's source too. A plain `REFUSAL in output` check is therefore
satisfied by a build that never ran the guard at all, as long as the builder
got far enough to print the instruction and fail it for some OTHER reason.
That is not a hypothetical for this Dockerfile in particular: the guard is
the second-to-last `RUN` before `npm ci`, so a build broken between here and
there could plausibly still show the guard's text in a step header while
never executing its body.

So the assertion filters to lines the builder did not echo back:

    spoken = [l for l in output.splitlines()
              if REFUSAL in l and "echo " not in l and ">>>" not in l]

A line containing `echo ` is the Dockerfile's own source, and on both engines
that tell is the one doing the work. Measured -- docker 29.6.2/BuildKit: seven
occurrences, two spoken. podman 6.1.1: three occurrences, one spoken, and both
of its quoted forms (`STEP n/m: RUN if ! echo "..."` and
`Error: building at STEP "RUN if ! echo "..."`) DO repeat the instruction's
`echo` calls verbatim, with the line continuations joined into one long line.

So `>>>` is belt-and-braces rather than load-bearing: the single docker line
that carries it (`108 | >>>   echo "..."`) contains `echo ` as well and would
be filtered without it. It is kept because it costs nothing and a future
BuildKit that prints the excerpt some other way would still be caught -- but
do not read it as the tell that covers podman. It is not; `echo ` is.

What survives the filter is a line the shell script actually PRINTED while
running.

THE SECOND ASSERTION IS THE ONE THAT ACTUALLY DISTINGUISHES TAMPERING FROM AN
UNREACHABLE HOST. The build failing proves nothing on its own: `npm ci`
against a lockfile that names a real but wrong host fails too, for a
completely different reason, and would satisfy every assertion above just as
well if the guard did not exist. `TAMPERED_HOST` is a `.invalid` TLD host
(RFC 2606 -- nothing can ever answer it, a tripwire rather than a lure), and
before this guard existed, a run against a tampered lockfile failed with
`ENOTFOUND stencil-tampered-lockfile.invalid` and printed that host in the
error. So the discriminator is not "did the build fail" but "did npm ever ask
DNS for the tampered host" -- and the guard sits between `COPY` and
`npm ci` specifically so the answer is no.

That assertion has a stated blind spot, and the failure message says so
rather than letting a reader discover it by re-deriving it: the guard sits
AFTER `apk add chromium font-noto ttf-dejavu` in the Dockerfile (deliberately
-- see the comment in Dockerfile.browser.j2 on why the COPY and the guard sit
where they do), so a runner that cannot reach Alpine's own mirrors fails the
build before the guard is ever reached, and `TAMPERED_HOST not in output`
then passes VACUOUSLY: the host was never asked for because nothing ran that
far, not because the guard stopped it. That is exactly why assertion (b),
the spoken-refusal check, has to be read first -- it is what tells a real
refusal apart from a build that stalled two RUN instructions earlier.

THE CONTROL. Restoring the original lockfile bytes and building again has to
both succeed AND not carry a spoken refusal line, and the second half of that
is not redundant with the first. `compose build` can serve the second build
entirely from cache -- the Chromium layer certainly will, and the guard's own
`RUN` layer might, if this exact digest already built successfully earlier in
the same test session or CI run. `returncode == 0` on a fully cached build
only proves an image already existed under this tag; it says nothing about
whether TODAY'S bytes would pass the guard. A CACHED guard step still means
something, though, and is not a false positive to worry about: BuildKit and
podman both cache a `RUN` layer keyed on the parent layer's content (here, the
COPY'd lockfile bytes) plus the instruction text, and neither engine ever
commits a layer for a `RUN` that exited non-zero -- a failed instruction
produces no cache entry to hit. So a cache hit on the guard's layer is not a
skip of the check; it is a replay of a PASS this exact digest already earned,
which is why "the control build was cached" is not a caveat on this test's
conclusion.

A GUARD THAT REFUSED EVERYTHING WOULD PASS EVERY ASSERTION ABOVE THE
CONTROL. That is the whole reason the control exists rather than trusting the
tampered run alone: a Dockerfile that unconditionally `exit 1`'d at this
point would also fail, also print the refusal spoken rather than merely
quoted, and also never mention the tampered host (it would never even read
the lockfile far enough to notice which host is in it). Only the control run
-- on stencil's own, untouched lockfile -- tells that guard apart from the
real one.

WHY THIS FILE DOES NOT SHARE `compose_impl`/`compose` WITH
tests/test_compose_format_md.py. `from tests.test_compose_format_md import
...` resolves locally, because the repo root sits on `sys.path` here, but
fails on CI with `ModuleNotFoundError` -- the same trap conftest.py documents
for its own fixtures. Both fixtures are copied verbatim below rather than
imported.

WHY THE `compose` FIXTURE'S TEARDOWN HERE ALSO REMOVES AN IMAGE. `compose
down` tears down containers and networks, never images -- and this test, for
the first time in this pair of files, drives a service with a `build:`
stanza (`pdf`, sharing its image with `check-access`), tagged
`localhost/{package_id}_browser:latest`. tests/test_pdf.py's `pdf_workspace`
fixture builds one shared, session-scoped image across every test that needs
one; this test builds its OWN, twice, under a package id fixed to
`"tampered"` so the resulting tag is deterministic and this test's alone.
Nothing here shares that tag with `pdf_workspace`'s
`localhost/stencil_browser:run-<id>`, so nothing here can starve or corrupt
that fixture -- but left untagged-and-forgotten, a tampered/control pair per
test run would accumulate one dangling image layer per invocation with
nothing to reclaim it. The fixture's teardown removes it with the same
warn-rather-than-raise shape `compose down` already uses, via
`pipeline.container_runtime()` -- the same probe the generated Makefile's own
`ensure_image` guard is built on -- so a wedged daemon reports a warning
instead of replacing the test's own assertion failure with a teardown error.

EVERY COMPOSE CALL PASSES AN EXPLICIT TIMEOUT, for the reason
test_compose_format_md.py's module docstring gives: a hung build burns the
whole run's budget silently instead of reporting a number back, and building
Chromium from scratch is the slower of the two builds this repository runs
routinely.

`--progress` IS NEVER PASSED. podman-compose rejects the flag outright, so
passing it would stop this test running under the one engine most worth
running it under. Measured: the refusal string survives the default
progress renderer, `COMPOSE_BAKE` either way, and `BUILDKIT_PROGRESS=plain`,
so there is nothing to gain by asking for a particular one and something to
lose.

NEVER ASSERT ON LINE ORDER. Compose interleaves stdout and stderr from the
build across two OS pipes, and this test's assertions are all membership
checks for exactly that reason.
"""

from __future__ import annotations

import json
import subprocess
import uuid
import warnings
from pathlib import Path

import pytest

from stencil import pipeline

# RFC 2606 reserves .invalid for exactly this: a host guaranteed to resolve
# nowhere, so a DNS lookup for it is unambiguous evidence that npm was asked
# to fetch from it, and no answer could ever arrive to make the test flaky.
# It is a tripwire, not a lure.
TAMPERED_HOST = "stencil-tampered-lockfile.invalid"

# The string Dockerfile.browser.j2's guard writes to stderr on a digest
# mismatch. Shared with the Dockerfile's own comment and with
# tests/test_pins.py's `_browser_guard` assertions, so a wording change that
# breaks this test breaks those too rather than silently drifting apart.
REFUSAL = "is not the file stencil generated"


def outcome(label: str, result: subprocess.CompletedProcess) -> str:
    """Both streams, for a failure message worth reading.

    Same rationale as tests/test_compose_check_access.py's helper of the same
    name: compose splits itself across stdout and stderr, so reporting only
    one turns a real failure into "exit 1" and nothing else.
    """
    return (
        f"{label} exited {result.returncode}\n"
        f"stdout:\n{result.stdout[-4000:]}\n"
        f"stderr:\n{result.stderr[-4000:]}"
    )


def spoken_refusal_lines(output: str) -> list[str]:
    """Lines where the GUARD said the refusal, not the builder quoting it.

    See the module docstring's measurement: of seven occurrences of REFUSAL
    in a failing build's output, five are docker/BuildKit or podman quoting
    the Dockerfile's own source (a step header, an ERROR summary, a numbered
    ``>>>`` source excerpt, or a final "failed to solve" line) and only two
    are the guard's ``echo ... >&2`` lines actually executing. Every one of
    those quoted forms either repeats the instruction's own ``echo "..."``
    text or (docker only) is prefixed with ``>>>``, so filtering both out
    leaves only a line the running shell script printed.
    """
    return [
        line
        for line in output.splitlines()
        if REFUSAL in line and "echo " not in line and ">>>" not in line
    ]


@pytest.fixture(scope="session")
def compose_impl():
    """The compose implementation to drive, or a skip.

    Copied from tests/test_compose_format_md.py rather than imported from it:
    `from tests.test_compose_format_md import ...` resolves locally (the repo
    root is on sys.path) while failing on CI with ModuleNotFoundError, the
    same trap conftest.py documents for its own fixtures.
    """
    command = pipeline.compose_command()
    if command is None:
        pytest.skip(
            "no compose implementation found "
            "(docker compose, podman compose, docker-compose, podman-compose)"
        )
    return command


@pytest.fixture
def compose(compose_impl):
    """Drive a generated package's compose file in a project of its own.

    Hands back a callable so a test reads as the Makefile does --
    ``compose("build", "pdf")``. Every project it opens is torn down when the
    test ends, passing or failing, so a run leaves no container and no
    network behind. Unlike tests/test_compose_format_md.py's copy of this
    fixture, ``_for`` also accepts an ``image`` tag to remove in the same
    teardown: ``compose down`` never touches images, and this file is the
    first of the pair to build one of its own (the ``pdf``/``check-access``
    services' shared ``localhost/<package_id>_browser:latest``), which would
    otherwise accumulate one dangling image per test run with nothing to
    reclaim it.
    """
    opened: list[tuple[Path, str, str | None]] = []

    def _for(package: Path, *, image: str | None = None):
        project = f"stencil-test-{uuid.uuid4().hex[:12]}"
        opened.append((package, project, image))

        def _compose(*args: str, timeout: float | None = None):
            return pipeline.compose(
                list(args),
                workdir=package,
                project=project,
                command=compose_impl,
                timeout=timeout,
            )

        return _compose

    yield _for

    for package, project, image in opened:
        # Warn rather than raise for the same reason
        # tests/test_compose_check_access.py's identical teardown does: a
        # teardown that raises replaces the assertion error the test was
        # actually reporting, and a wedged daemon raises TimeoutExpired
        # rather than returning non-zero, which would otherwise abandon
        # every project after this one in the loop.
        try:
            removed = pipeline.compose(
                ["down", "--remove-orphans", "--volumes"],
                workdir=package,
                project=project,
                command=compose_impl,
                timeout=600,
            )
        except (OSError, subprocess.SubprocessError) as error:
            warnings.warn(
                f"compose down left project {project} behind: {error!r}",
                stacklevel=1,
            )
            continue

        if removed.returncode != 0:
            warnings.warn(
                f"compose down left project {project} behind "
                f"(exit {removed.returncode}): {removed.stderr[-500:]}",
                stacklevel=1,
            )

        if image is None:
            continue

        # `compose down` tears down containers and networks, never images,
        # so the build this test triggers would otherwise leave
        # `localhost/tampered_browser:latest` behind on every run. Removed
        # with the same runtime the generated Makefile's own `ensure_image`
        # guard probes with, and the same warn-rather-than-raise shape as
        # the `down` above, for the same reason: a teardown failure here
        # must never replace the test's own assertion result.
        runtime = pipeline.container_runtime()
        if runtime is None:
            continue
        try:
            image_removed = subprocess.run(
                [runtime, "image", "rm", "-f", image],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as error:
            warnings.warn(
                f"{runtime} image rm left {image!r} behind: {error!r}",
                stacklevel=1,
            )
            continue

        if image_removed.returncode != 0:
            warnings.warn(
                f"{runtime} image rm left {image!r} behind "
                f"(exit {image_removed.returncode}): "
                f"{image_removed.stderr[-500:]}",
                stacklevel=1,
            )


@pytest.mark.integration
def test_a_tampered_browser_lockfile_is_refused_before_npm_fetches_anything(
    demo_config, generate_package, compose
):
    """stn-egv, run rather than read -- see the module docstring for the case.

    Tampers one ``resolved`` host in the package's own copy of
    ``browser-package-lock.json``, the smallest edit that redirects where a
    tarball comes from, then runs ``compose build pdf`` -- which builds
    Dockerfile.browser, the image the ``pdf`` and ``check-access`` services
    share -- and asserts, in order:

    1. the build fails,
    2. the guard itself spoke the refusal (not merely had it quoted back by
       the builder -- see ``spoken_refusal_lines``, and the measurement in
       the module docstring on why a plain substring check is not enough),
    3. the tampered host appears nowhere in the build's output, so npm never
       asked for it -- the assertion that actually tells tampering apart
       from an unreachable host, with the caveat that it passes vacuously if
       the build never reached the guard at all, which is exactly why (2)
       must be checked first.

    Then it restores the vendored bytes and builds again, as the control: a
    guard that refused every lockfile, stencil's own included, would satisfy
    every assertion above and would ship a browser image that never builds.
    """
    config = demo_config
    config["packages"] = {"tampered": config["packages"].pop("demo")}
    package = generate_package(config, "tampered")

    lockfile = package / pipeline.BROWSER_LOCKFILE
    vendored = lockfile.read_bytes()
    lock = json.loads(vendored)
    pdf_lib = lock["packages"]["node_modules/pdf-lib"]
    assert pdf_lib["resolved"].startswith("https://registry.npmjs.org/"), (
        "the fixture edits pdf-lib's resolved URL; it no longer looks like "
        f"one, so this test would not be tampering with what it thinks it "
        f"is: {pdf_lib['resolved']!r}"
    )
    # BYTE-LEVEL, so the file differs from stencil's in exactly the way the
    # ticket reproduced and in no other way. Re-serialising with
    # json.dumps(..., indent=2) would reformat all 20KB, and then a guard that
    # noticed only the reformatting would look like a guard that noticed the
    # redirected host. The json round-trip above is a FIXTURE CHECK -- it is
    # how this test knows pdf-lib is still resolved from the registry -- not
    # how the edit is made.
    tampered = vendored.replace(
        b"registry.npmjs.org", TAMPERED_HOST.encode(), 1
    )
    assert tampered != vendored, (
        "bytes.replace found nothing to replace, so this test would build from "
        "an untampered lockfile and report the guard as broken when it is not"
    )
    lockfile.write_bytes(tampered)

    image = "localhost/tampered_browser:latest"
    run = compose(package, image=image)
    refused = run("build", "pdf", timeout=1800)
    output = refused.stdout + refused.stderr

    assert refused.returncode != 0, (
        "the browser image built from a lockfile stencil did not write:\n"
        f"{outcome('compose build pdf', refused)}"
    )

    spoken = spoken_refusal_lines(output)
    assert spoken, (
        "the build failed, but nothing in its output looks like the guard "
        "actually running -- only lines the builder echoes back regardless "
        "of why a RUN failed (a step header, an ERROR summary, a numbered "
        "source excerpt, or a final 'failed to solve' line all repeat the "
        "instruction's own text and would satisfy a plain substring check "
        "for the refusal message without the guard ever having executed). "
        "Measured on docker 29.6.2 / BuildKit / Compose v5.3.1: of seven "
        "occurrences of the refusal string in a failing build, five are the "
        f"builder quoting the Dockerfile and only two are the guard "
        f"speaking; podman 6.1.1 quotes 3 times with 1 spoken, under its own "
        f"'STEP'/'Error' lines. "
        f"See spoken_refusal_lines and the module docstring.\n"
        f"{outcome('compose build pdf', refused)}"
    )

    assert TAMPERED_HOST not in output, (
        "npm asked for the host the tampered lockfile named, so the install "
        "read it before the guard (or anything else) checked it -- a host "
        "that happened to answer would have been installed and run as uid "
        "0 by the pdf and check-access services. (If the build never "
        "reached the guard at all -- e.g. this runner cannot reach Alpine's "
        "own package mirrors, since the guard sits after 'apk add "
        "chromium' -- this assertion would pass vacuously; the spoken-"
        "refusal assertion above is what rules that out, which is why it "
        f"is checked first.)\n{outcome('compose build pdf', refused)}"
    )

    # The control. Same package, same project, same everything but the bytes
    # this test changed.
    lockfile.write_bytes(vendored)
    built = run("build", "pdf", timeout=1800)
    control_output = built.stdout + built.stderr

    assert built.returncode == 0, (
        "the guard refuses the lockfile stencil itself generated, so the "
        "refusal above proves nothing about tampering:\n"
        f"{outcome('compose build pdf', built)}"
    )
    assert not spoken_refusal_lines(control_output), (
        "the guard spoke a refusal against stencil's own, untampered "
        "lockfile. A build can exit 0 while this happens if only the LAST "
        "of several build steps is what compose reports on, so this checks "
        "the guard's own voice rather than trusting the exit code alone -- "
        "and a build that refuses everything, cached or not, would satisfy "
        f"a returncode-only check just as well as a real guard would.\n"
        f"{outcome('compose build pdf', built)}"
    )
