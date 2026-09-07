"""Every version the generated scaffolding installs, written down.

stn-s5b. The browser image installed ``puppeteer pa11y pdf-lib`` with no version
constraint at all, so a rebuild could change the Chromium that renders every PDF
and the engine behind ``make check-access`` without anything in a diff.

The argument the ticket makes is worth keeping in front of whoever reads this
file next. ``html-to-pdf.js`` pins ``tagged: true`` on ``page.pdf()`` precisely
because an accessibility property that is only a *default* is one a version bump
can remove silently -- and a pin on the option guards nothing about the runtime
that honours it. Against a Puppeteer predating the option the pin is a silent
no-op, and ``test_the_pdf_is_tagged`` still passes, because the default is
tagged. The floating install was the layer underneath that pin.

Three tiers of claim live here, and they are genuinely different claims:

- what the *rendered scaffolding says* -- unit assertions, no container;
- what the *built image contains* -- ``Dockerfile.browser`` naming
  ``pa11y@10.0.0`` and the image holding it are two separate facts;
- what the installed tools *do* -- pa11y had never been executed by any test in
  this repository, so a pin would otherwise have frozen a version nobody had
  run.

What is deliberately NOT asserted here is the Chromium version, because it is
not pinned either. The reasoning, with the measurements, is in
``Dockerfile.browser.j2`` beside the ``apk add`` it is about; what stands in for
it is ``tests/test_pdf.py`` and the PDF/UA suite measuring the output rather
than the version.
"""

from __future__ import annotations

import json
import re

import pytest
import yaml

from stencil import pipeline

# ---------------------------------------------------------------------------
# reading the rendered scaffolding


def text_files(package):
    """Every generated file that is text, with its path.

    A whole-package scan rather than a list of the two files that install
    something today: the point of the assertion below is that a *fourth*
    package added to some future template fails without anyone remembering to
    add a case for it here.
    """
    for path in sorted(package.rglob("*")):
        if not path.is_file():
            continue
        try:
            yield path, path.read_text()
        except UnicodeDecodeError:
            continue


def npm_install_specs(line: str) -> list[str]:
    """The package specs on one ``npm install`` line, flags removed.

    Stops at ``&&`` because format-md's line continues into the prettier
    invocation, and skips ``--prefix``'s value because a directory is not a
    package. Plain ``split()`` is enough: nothing in these lines is quoted, and
    ``shlex`` would choke on the trailing backslash of a shell continuation.
    """
    text = line[line.index("npm install") :]
    text = text.split("&&")[0].rstrip(" \\")
    tokens = text.split()[2:]

    specs: list[str] = []
    skip_value = False
    for token in tokens:
        if skip_value:
            skip_value = False
            continue
        if token.startswith("-"):
            skip_value = token == "--prefix"
            continue
        specs.append(token)
    return specs


def npm_installs(package):
    """(path, line, specs) for every ``npm install`` in the generated package."""
    for path, text in text_files(package):
        for line in text.splitlines():
            if "npm install" in line:
                yield path, line.strip(), npm_install_specs(line)


# An exact version, optionally on a scoped package: `pa11y@10.0.0`,
# `@awmottaz/prettier-plugin-void-html@2.2.1`. A caret or a tilde does not
# match, and that is deliberate -- see the module docstring.
EXACT_SPEC = re.compile(r"^(?:@[^@/]+/)?[^@/]+@\d+\.\d+\.\d+$")


# ---------------------------------------------------------------------------
# the rendered scaffolding


def test_every_npm_install_in_the_scaffolding_names_an_exact_version(doc_package):
    """The ticket's acceptance, asserted as a property rather than a list."""
    found = list(npm_installs(doc_package))
    assert found, "no npm install found at all -- this test would pass vacuously"

    for path, line, specs in found:
        assert specs, f"{path.name}: no package on an npm install line: {line}"
        for spec in specs:
            assert EXACT_SPEC.match(spec), (
                f"{path.name} installs {spec!r} with no exact version. "
                f"A rebuild can change what it gets, silently: {line}"
            )


def test_the_scaffolding_names_one_node_image_and_it_is_pinned(doc_package):
    """Three files reach for Node -- the browser Dockerfile, format-md's compose
    service, and the ensure_image line in the Makefile that pre-pulls it. They
    have to name the SAME image: `make format-md` otherwise pulls one and runs
    another, which is a bug that reads as a slow first build rather than as a
    defect."""
    # A regex rather than a split: the Makefile names it inside
    # `$(call ensure_image,docker.io/library/node:...,format-md)`, where the
    # delimiters are commas and parentheses rather than whitespace.
    pattern = re.compile(r"docker\.io/library/node:[^\s,)\"']+")
    named = {
        match
        for _, text in text_files(doc_package)
        for match in pattern.findall(text)
    }
    assert named, "nothing in the package names a node image any more"

    assert named == {pipeline.NODE_IMAGE}, (
        f"the package names {sorted(named)}; pipeline.NODE_IMAGE is "
        f"{pipeline.NODE_IMAGE!r}"
    )

    tag = pipeline.NODE_IMAGE.rpartition(":")[2]
    assert re.fullmatch(r"\d+\.\d+\.\d+-alpine\d+\.\d+", tag), (
        f"{tag!r} does not pin both a Node version and an Alpine branch. "
        "The Alpine branch is what decides which Chromium `apk add` installs "
        "and which font packages are available, so a floating one moves the "
        "browser several majors at a time."
    )


def test_every_image_the_scaffolding_pulls_carries_a_version_tag(doc_package):
    """`lts` and `latest` both float. A service that BUILDS its image is exempt:
    `localhost/<pkg>_browser:latest` is a local tag for the image the Dockerfile
    beside it produces, not something pulled from a registry."""
    compose = yaml.safe_load((doc_package / "docker-compose.yml").read_text())

    for name, service in compose["services"].items():
        if "build" in service:
            continue
        image = service["image"]
        assert ":" in image, f"service {name} names {image!r} with no tag at all"
        tag = image.rpartition(":")[2]
        # "contains a digit" rather than a deny-list of the floating names.
        # `lts-alpine` is not `lts`, and a deny-list written the obvious way
        # passes against it -- which is how the defect this file is about
        # survived a compose file that already pinned pandoc and veraPDF.
        assert any(character.isdigit() for character in tag), (
            f"service {name} runs {image!r}: the tag names no version, so it "
            "floats onto whatever that name points at on the day of the build"
        )


def test_the_browser_image_installs_what_pipeline_pins(doc_package):
    """The rendered text is the constants, not a copy of them -- so bumping a
    pin is one edit, and a test cannot go on asserting a version the image
    stopped installing."""
    dockerfile = (doc_package / "Dockerfile.browser").read_text()
    line = next(
        line for line in dockerfile.splitlines() if line.startswith("RUN npm install")
    )
    assert npm_install_specs(line) == pipeline.npm_specs(pipeline.BROWSER_NPM_PINS)


def test_format_md_installs_what_pipeline_pins(doc_package):
    """Same rule for the formatter. An unpinned prettier decides how every
    markdown file in a package gets rewritten, which is a wider blast radius
    than the browser's and was floating for the same reason."""
    compose = (doc_package / "docker-compose.yml").read_text()
    line = next(
        line for line in compose.splitlines() if "npm install --prefix /tmp/fmt" in line
    )
    assert npm_install_specs(line) == pipeline.npm_specs(pipeline.FORMAT_NPM_PINS)


def test_a_pin_map_renders_as_install_specs():
    """The helper itself, so the two tests above cannot both be wrong the same
    way. Sorted, because an install line whose order changes between runs is a
    diff nobody asked for."""
    assert pipeline.npm_specs({"b": "2.0.0", "a": "1.2.3"}) == ["a@1.2.3", "b@2.0.0"]


def test_a_scoped_package_is_recognised_as_pinned():
    """`@awmottaz/prettier-plugin-void-html@2.2.1` carries an `@` inside the
    package NAME as well as before the version, so the obvious `split("@")`
    yields three parts and either crashes or mis-validates. The formatter pins
    contain exactly this shape, so the check above would have been asserting
    nothing about the one spec most likely to break it."""
    scoped = "@awmottaz/prettier-plugin-void-html@2.2.1"
    assert EXACT_SPEC.match(scoped)
    assert not EXACT_SPEC.match("@awmottaz/prettier-plugin-void-html")
    assert not EXACT_SPEC.match("@awmottaz/prettier-plugin-void-html@^2.2.1")


# ---------------------------------------------------------------------------
# what the built image actually contains
#
# Everything below needs a container. The Dockerfile saying `pa11y@10.0.0` and
# the image holding it are two different claims, and only the second one is the
# thing every generated handout is rendered by.

PROBE = """
const fs = require("fs");
const pa11y = require("pa11y");

const root = "/opt/tools/lib/node_modules";
const out = { installed: {}, puppeteerCopies: [], pa11y: {} };

for (const name of %(names)s) {
  out.installed[name] = require(`${root}/${name}/package.json`).version;
}

// Every copy of puppeteer anywhere in the installed tree, not just the top
// one. pa11y DEPENDS on puppeteer, so a pin outside pa11y's declared range
// installs a second copy underneath it and check-access quietly starts
// driving a different browser than make pdf does.
function walk(dir, depth) {
  if (depth > 4) return;
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const path = `${dir}/${entry.name}`;
    if (entry.name === "puppeteer") {
      try {
        out.puppeteerCopies.push({
          path,
          version: require(`${path}/package.json`).version,
        });
      } catch {}
    }
    if (entry.name === "node_modules" || !entry.name.startsWith(".")) {
      walk(path, depth + 1);
    }
  }
}
walk(root, 0);

(async () => {
  for (const theme of ["light", "dark"]) {
    const config = require(`/opt/pa11y-${theme}.json`);
    const results = await pa11y("file:///workspace/document.html", {
      ...config,
      standard: "WCAG2AA",
    });
    out.pa11y[theme] = {
      issues: results.issues.length,
      codes: results.issues.map((issue) => issue.code),
    };
  }
  console.log("<<<PROBE>>>" + JSON.stringify(out));
})().catch((error) => {
  console.log("<<<PROBE>>>" + JSON.stringify({ error: String(error) }));
  process.exit(1);
});
"""


@pytest.fixture(scope="session")
def installed(pdf_workspace):
    """One run inside the built image, answering every question below at once.

    A container start per assertion would pay that cost three times for facts
    one script can report together, and the expensive half -- pa11y launching
    Chromium once per theme -- is paid once either way. Three tests still fail
    separately, because they assert on separate keys.
    """
    built = pipeline.render(
        "doc", "document.md", "document.html", workdir=pdf_workspace
    )
    assert built.returncode == 0, f"pandoc failed\n{built.stderr}"

    script = PROBE % {"names": json.dumps(sorted(pipeline.BROWSER_NPM_PINS))}
    result = pipeline.run_in_browser(script, workdir=pdf_workspace, timeout=600)

    marker = "<<<PROBE>>>"
    line = next(
        (line for line in result.stdout.splitlines() if line.startswith(marker)), None
    )
    assert line is not None, (
        f"the probe printed no result (exit {result.returncode})\n"
        f"stdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
    )
    probe = json.loads(line[len(marker) :])
    assert "error" not in probe, f"the probe failed inside the image: {probe['error']}"
    return probe


@pytest.mark.integration
def test_the_built_image_holds_the_pinned_versions(installed):
    """What the ticket asked for, measured where it matters. The Dockerfile is a
    request; this is the answer."""
    assert installed["installed"] == pipeline.BROWSER_NPM_PINS


@pytest.mark.integration
def test_exactly_one_puppeteer_is_installed(installed):
    """pa11y depends on puppeteer and npm dedupes it onto the top-level copy
    only while the pinned version satisfies pa11y's declared range. Move either
    pin out of step and the tree grows a second Chromium driver: `make pdf` and
    `make check-access` then measure different browsers, both quietly, and every
    other test in this suite keeps passing."""
    copies = installed["puppeteerCopies"]
    assert [copy["version"] for copy in copies] == [
        pipeline.BROWSER_NPM_PINS["puppeteer"]
    ], f"expected one deduped puppeteer, found: {copies}"


@pytest.mark.integration
def test_pa11y_runs_in_both_generated_theme_configs(installed):
    """Nothing in this repository had ever executed pa11y -- `make check-access`
    is a compose service and no test drives it -- so pinning it would have
    frozen a version that had never been run here. pa11y 10.0.0 was released
    ten days before this pin and floated into the image unnoticed.

    The assertion is that pa11y RUNS and reports, on both generated configs,
    against a real rendered page. It is not an accessibility assertion about the
    fixture: that would make an unrelated content change fail a pin test."""
    for theme in ("light", "dark"):
        assert theme in installed["pa11y"], f"pa11y never ran for the {theme} theme"
        assert isinstance(installed["pa11y"][theme]["issues"], int)
