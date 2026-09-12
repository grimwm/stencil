"""What the two browser-backed services take from the mount, and what they don't.

stn-jeq and stn-7ki. Both defects came from the same place: the pdf and
check-access services ran with the consumer's own package directory as
``process.cwd()``, and ``html-to-pdf.js`` was rendered INTO that directory and
run from there. Two independent loaders followed from that, and neither is
closed by the other:

- ``process.cwd()`` decides what puppeteer's ``getConfiguration()`` finds. It
  calls ``lilconfig('puppeteer', {searchPlaces: [...]}).search()`` with no
  argument, and lilconfig's signature is ``search(searchFrom = process.cwd())``.
  The ``.cjs``/``.js`` entries in that list are ``require``d. There is no
  environment variable and no launch option that turns discovery off -- measured
  against the pinned puppeteer, not assumed -- so cwd is the only lever there is.
- The nearest ``package.json`` TO THE FILE decides whether Node parses a ``.js``
  script as CommonJS or as an ES module. With the script inside the mount, that
  is the consumer's package.json, and a course package legitimately has one.

WHY THE CONTROL AT THE TOP OF THIS FILE IS NOT OPTIONAL. Every test below
asserts that something did NOT happen: no sentinel file, no marker on stdout. An
absence is worth nothing unless something proves the same decoy, in the same
image, over the same mount, WOULD otherwise have fired -- a ``.puppeteerrc.cjs``
that is malformed, or planted at a name puppeteer never looks for, produces
exactly the same silence as a working fix. That is the vacuous pass
``tests/test_check_access.py``'s docstring is about and the one
``test_a_workspace_decoy_would_win_a_bare_require`` in ``tests/test_pins.py``
exists to rule out for the sibling defect. The control here does the same job.

WHY THE SENTINEL IS A FILE RATHER THAN A LINE ON STDOUT. Both are asserted, but
the file is the load-bearing one. stdout is captured per process, and pa11y runs
puppeteer in a child of the shell the check-access service starts, so a marker
printed there can go missing for reasons that have nothing to do with the fix. A
file written into the mount is visible to the test afterwards no matter which
process wrote it -- and writing into the bind-mounted course repository, as root,
is precisely the capability the ticket is about, so the sentinel demonstrates the
defect rather than standing in for it.

NEITHER TEST USES ``pdf_workspace`` AS ITS MOUNT. That fixture is session-scoped
and shared by every container test in the suite; a ``.puppeteerrc.cjs`` or a
``package.json`` planted in it would leak into all of them. The image it builds
is what is wanted here, so these tests depend on it for that and mount a fresh
directory of their own -- the same separation ``decoy_tools_workdir`` keeps in
``tests/test_pins.py``, and for the same reason.
"""

from __future__ import annotations

import shutil

import pytest

from stencil import pipeline

pytestmark = pytest.mark.integration

# Written by the decoy into the MOUNT, so the test can see it afterwards
# regardless of which process in the service ran the config.
SENTINEL = "PUPPETEERRC-EXECUTED"

# Printed by the decoy as well, so a failure says what happened rather than only
# that a file appeared.
MARKER = "MARKER: a workspace .puppeteerrc.cjs executed"

# One of the thirteen names puppeteer 25.x searches for, and the one a course
# repository would most plausibly receive: `.puppeteerrc.cjs` is what
# puppeteer's own documentation tells people to write.
DECOY_CONFIG = f"""\
require("node:fs").writeFileSync("/workspace/{SENTINEL}", "yes");
console.log("{MARKER} as uid=" + process.getuid());
module.exports = {{}};
"""

# stn-7ki's trigger, verbatim from the ticket's reproduction.
TYPE_MODULE_PACKAGE_JSON = '{"name":"course-package","type":"module"}\n'


@pytest.fixture(scope="session")
def rendered_page(pdf_workspace):
    """A real generated page, rendered once in the shared workspace.

    A real page rather than a hand-written stub: the pdf service waits for
    ``window.__mermaidReady`` and refuses on a failed asset request, so a
    minimal page would hang for two minutes and then fail for a reason that has
    nothing to do with what is being measured here. The same call
    ``tests/test_pins.py`` makes at its ``rendered_pdf_page`` fixture.
    """
    built = pipeline.render(
        "doc", "document.md", "document.html", workdir=pdf_workspace
    )
    assert built.returncode == 0, f"pandoc failed\n{built.stderr}"
    return pdf_workspace


def _mount(tmp_path, rendered_page, *, config=True, package_json=False):
    """A fresh mount holding a real page and whichever decoys a test wants.

    ``html-to-pdf.js`` IS COPIED IN, and that is deliberate rather than
    leftover. Every real package has one -- generate.py writes it for each
    package, and Dockerfile.browser COPYs it out of the package directory to
    bake it -- so a mount without it would be a directory no consumer ever has.
    It also keeps these tests failing for the RIGHT reason before the fix: with
    the file absent they fail with Node's MODULE_NOT_FOUND, which is a fact
    about the fixture, and with it present they fail with the marker firing and
    with the ES-module parse error, which are the two defects.

    After the fix the service runs the copy baked into the image and this one is
    inert. It is not planted as a hostile variant to prove that, because the
    generated Makefile runs `$(DC) build pdf` before every invocation and would
    bake whatever is here -- a test asserting that the mount's copy is ignored
    would be asserting something the design does not promise.
    """
    workdir = tmp_path / "mount"
    workdir.mkdir()
    shutil.copy2(rendered_page / "document.html", workdir / "document.html")
    shutil.copy2(rendered_page / "html-to-pdf.js", workdir / "html-to-pdf.js")
    if config:
        (workdir / ".puppeteerrc.cjs").write_text(DECOY_CONFIG)
    if package_json:
        (workdir / "package.json").write_text(TYPE_MODULE_PACKAGE_JSON)
    return workdir


def _assert_untouched(workdir, result):
    """Neither the sentinel nor the marker, and say which one failed."""
    assert not (workdir / SENTINEL).is_file(), (
        f"the decoy wrote {SENTINEL} into the mount, so a .puppeteerrc.cjs "
        "taken from the consumer's package directory was executed as root. "
        "That is stn-jeq."
    )
    combined = result.stdout + result.stderr
    assert MARKER not in combined, (
        "the decoy's marker reached the service's output, so it ran:\n"
        f"{combined[-3000:]}"
    )


def test_a_workspace_puppeteerrc_would_execute_from_the_mount(
    tmp_path, rendered_page
):
    """CONTROL. The decoy must fire when cwd is the mount.

    Without this, every assertion below is an assertion about silence, and
    silence is what a misspelled filename produces too. This runs a script in
    the same image, over the same kind of mount, with cwd left at /workspace --
    the state the two services were in before this change -- and requires the
    marker and the sentinel to appear. If this test ever goes green-by-absence,
    the decoy stopped being a decoy and the three tests below stopped measuring
    anything.

    ``pipeline.run_in_browser`` is what leaves cwd at /workspace: it writes its
    script into the mount and runs ``node <name>`` from there, which is exactly
    the shape the pdf service used to have.
    """
    workdir = _mount(tmp_path, rendered_page)
    script = """
const { createRequire } = require("node:module");
const fromTools = createRequire("%s/package.json");
const puppeteer = fromTools("puppeteer");
puppeteer
  .launch({
    executablePath: process.env.PUPPETEER_EXECUTABLE_PATH,
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
  })
  .then((browser) => browser.close())
  .then(() => console.log("<<<LAUNCHED>>>"));
""" % pipeline.BROWSER_TOOLS_DIR

    result = pipeline.run_in_browser(script, workdir=workdir, timeout=120)

    assert "<<<LAUNCHED>>>" in result.stdout, (
        "the control never got a browser open, so it proves nothing about "
        f"whether the decoy would have run.\nstdout: {result.stdout[-2000:]}\n"
        f"stderr: {result.stderr[-2000:]}"
    )
    assert (workdir / SENTINEL).is_file(), (
        "a .puppeteerrc.cjs in the mount did NOT execute even with cwd left at "
        "/workspace. The decoy is malformed -- wrong filename, wrong module "
        "shape, or puppeteer stopped reading the cwd config -- so the tests "
        "below would pass vacuously against unfixed code."
    )
    assert MARKER in result.stdout, (
        "the sentinel appeared but the marker did not, which means something "
        f"other than this decoy wrote it.\nstdout: {result.stdout[-2000:]}"
    )


def test_make_pdf_ignores_a_workspace_puppeteerrc(tmp_path, rendered_page):
    """ACCEPTANCE for stn-jeq, pdf half.

    Same image, same mount and same entrypoint as the generated pdf service,
    over a directory carrying the decoy the control above just proved fires.
    """
    workdir = _mount(tmp_path, rendered_page)

    result = pipeline.html_to_pdf(
        "document.html", "document.pdf", workdir=workdir, timeout=180
    )

    assert result.returncode == 0, (
        f"the pdf service exited {result.returncode}\n"
        f"stderr: {result.stderr[-3000:]}"
    )
    assert (workdir / "document.pdf").is_file(), (
        "the pdf service exited 0 but wrote no document.pdf"
    )
    _assert_untouched(workdir, result)


def test_check_access_ignores_a_workspace_puppeteerrc(tmp_path, rendered_page):
    """ACCEPTANCE for stn-jeq, check-access half -- the one with no one-line fix.

    This half is not reachable by swapping to puppeteer-core: pa11y requires the
    puppeteer WRAPPER itself and calls ``launch()`` inside it, so the only lever
    is the working directory the service runs from.

    pa11y also carries three cwd-rooted ``require`` paths of its own --
    ``loadConfig``'s ``./pa11y.json`` default, ``loadReporter``'s
    ``path.join(process.cwd(), name)`` and ``loadRunnerFile``'s -- none of them
    reachable today, because the generated service passes an absolute
    ``--config`` and uses the built-in reporter and runner. Moving the working
    directory closes those by construction rather than leaving them one flag
    change away.
    """
    workdir = _mount(tmp_path, rendered_page)

    result = pipeline.check_access(workdir=workdir, timeout=300)

    assert result.returncode == 0, (
        f"check-access exited {result.returncode}\n"
        f"stdout: {result.stdout[-3000:]}\nstderr: {result.stderr[-3000:]}"
    )
    assert "Checked 1 HTML file(s)" in result.stdout, (
        "check-access did not report checking the page, so this says nothing "
        f"about what it read on the way.\nstdout: {result.stdout[-3000:]}"
    )
    _assert_untouched(workdir, result)


def test_make_pdf_runs_under_a_consumer_type_module_package_json(
    tmp_path, rendered_page
):
    """ACCEPTANCE for stn-7ki.

    Before this change the script was inside the mount, so a package.json
    declaring ``"type": "module"`` made Node parse it as an ES module and it
    died at ``const { createRequire } = require(...)`` -- before its own
    tools-directory guard, or any other guard in it, could say anything.

    A package.json of a consumer's own is not exotic: the compose file already
    says so in prose where the format-md service takes care to run npm from
    /tmp/fmt for exactly this reason.
    """
    workdir = _mount(tmp_path, rendered_page, config=False, package_json=True)

    result = pipeline.html_to_pdf(
        "document.html", "document.pdf", workdir=workdir, timeout=180
    )

    assert result.returncode == 0, (
        f"the pdf service exited {result.returncode} under a consumer "
        'package.json declaring "type": "module"\n'
        f"stderr: {result.stderr[-3000:]}"
    )
    assert (workdir / "document.pdf").is_file(), (
        "the pdf service exited 0 but wrote no document.pdf"
    )
    assert "require is not defined in ES module scope" not in result.stderr, (
        "the script was still parsed as an ES module, so it is still being "
        f"read from inside the mount.\nstderr: {result.stderr[-3000:]}"
    )


def test_make_pdf_survives_both_decoys_at_once(tmp_path, rendered_page):
    """The two defects are independent levers, so the pair is its own case.

    A consumer can legitimately hold both files, and a fix that closed one by
    moving the script and the other by moving the working directory could in
    principle have been implemented so that only one of the two moves survived.
    Measured before the fix: the ``"type": "module"`` parse error fires first
    and the ``.puppeteerrc.cjs`` never gets the chance to run, so neither of the
    single-decoy tests above covers this combination.
    """
    workdir = _mount(tmp_path, rendered_page, config=True, package_json=True)

    result = pipeline.html_to_pdf(
        "document.html", "document.pdf", workdir=workdir, timeout=180
    )

    assert result.returncode == 0, (
        f"the pdf service exited {result.returncode} with both decoys present\n"
        f"stderr: {result.stderr[-3000:]}"
    )
    assert (workdir / "document.pdf").is_file(), (
        "the pdf service exited 0 but wrote no document.pdf"
    )
    _assert_untouched(workdir, result)
