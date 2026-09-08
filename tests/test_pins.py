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

stn-5hv. Naming an exact version turned out to be the layer above ANOTHER
floating one: ``npm install puppeteer@25.10.0`` fixes puppeteer and the exact
puppeteer-core it declares, and leaves the other 43 packages in the resolved
tree to a range re-resolved on every build, verified by nothing. What fixes
those is a committed lockfile with a sha512 for each of them and ``npm ci``,
which refuses a tarball whose hash does not match -- measured, by flipping one
character of one ``integrity`` value:

    npm error code EINTEGRITY
    npm error sha512-A/mpyJ... integrity checksum failed when using sha512

So the pin maps in ``stencil/pipeline.py`` are now the REQUEST and
``stencil/assets/*-package-lock.json`` is the ANSWER, and the assertions here
are mostly about those two agreeing.

Three tiers of claim live here, and they are genuinely different claims:

- what the *rendered scaffolding says* -- unit assertions, no container;
- what the *committed lockfile holds* -- also unit assertions, and new: the
  pa11y/puppeteer dedupe invariant used to be measurable only in a container
  and is now a fact about a file in the repository;
- what the *built image contains* -- the Dockerfile naming a lockfile and the
  image holding the tree that lockfile describes are two separate facts;
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

ASSETS = pipeline.ASSETS_DIR

# (lockfile, manifest name, pins) for each install a generated package performs.
LOCKED = [
    pytest.param(
        pipeline.BROWSER_LOCKFILE,
        pipeline.BROWSER_MANIFEST_NAME,
        pipeline.BROWSER_NPM_PINS,
        id="browser",
    ),
    pytest.param(
        pipeline.FORMAT_LOCKFILE,
        pipeline.FORMAT_MANIFEST_NAME,
        pipeline.FORMAT_NPM_PINS,
        id="format-md",
    ),
]

# The same two, for the assertions that are about the lockfile's own shape and
# have no use for the pins it was resolved from. A narrower parametrization
# rather than three arguments a test ignores: an unused parameter reads as
# something the test forgot to check.
LOCKFILES = [
    pytest.param(pipeline.BROWSER_LOCKFILE, id="browser"),
    pytest.param(pipeline.FORMAT_LOCKFILE, id="format-md"),
]


def lockfile(name: str) -> dict:
    return json.loads((ASSETS / name).read_text())


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


def code_lines(package):
    """(path, line) for every generated line that is not a comment.

    COMMENTS ARE EXCLUDED, AND THAT IS LOAD-BEARING RATHER THAN TIDINESS.
    ``test_nothing_in_the_scaffolding_installs_by_name`` below searches for a
    phrase, and the scaffolding is heavily commented: Dockerfile.browser
    explains what it stopped doing by naming it, so a scan over raw text finds
    ``npm install`` in prose and reports the file as installing by name. The
    same trap in reverse used to make this file pass vacuously -- the old
    "every install names an exact version" property matched a commented example
    and asserted about that.

    ``#`` covers the Dockerfile, the compose file, the Makefile and the shell
    inside them; ``//`` covers html-to-pdf.js. Lua's ``--`` is deliberately not
    here: nothing generated in Lua installs anything, and ``--`` opens far too
    many lines that are not comments. The check is the first non-space
    characters of the line, which is where a whole-line comment starts in all
    of them.
    """
    for path, text in text_files(package):
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("#", "//")):
                continue
            yield path, stripped


# ---------------------------------------------------------------------------
# the rendered scaffolding


# Every spelling that fetches a package by name and resolves what is under it
# at run time. `npm install` is the one this ticket is about; the rest are the
# ways the same thing comes back -- `npm i` and `npm add` are aliases, and a
# template author reaching for a JavaScript tool is as likely to write npx or
# one of the other package managers.
#
# `apk add` is deliberately NOT here. The Chromium install is unversioned on
# purpose, with the measurements in Dockerfile.browser.j2 beside it; adding it
# would make this test demand the one pin that repository has decided against.
FLOATING_INSTALLS = re.compile(
    r"\b(?:npm\s+(?:install|i|add)|npx|yarn\s+add|pnpm\s+(?:add|install))\b"
)


def test_nothing_in_the_scaffolding_installs_by_name(doc_package):
    """stn-5hv's acceptance, asserted as a property rather than a list.

    ``npm install <name>@<version>`` resolves the tree below that name at build
    time; ``npm ci`` reads a committed lockfile and verifies every tarball
    against a hash. A generated package must do only the second, and the ``npm
    ci`` requirement is what keeps this from passing over a package that
    installs nothing at all because a template stopped being emitted.
    """
    installs = [
        (path, line)
        for path, line in code_lines(doc_package)
        if FLOATING_INSTALLS.search(line)
    ]
    assert not installs, (
        "these lines install by name, so everything below the named packages "
        f"re-resolves on every build: {[(p.name, line) for p, line in installs]}"
    )

    clean_installs = [
        (path.name, line) for path, line in code_lines(doc_package) if "npm ci" in line
    ]
    assert clean_installs, (
        "nothing in the package installs at all, so this test would pass "
        "vacuously over a package that stopped emitting the install entirely"
    )


def test_the_install_scan_does_not_read_comments(tmp_path):
    """The scanner above, proven on a file that mentions the thing it forbids.

    Written because the failure is silent in both directions: a comment saying
    ``npm install`` fails a package that does no such thing, and -- the way it
    actually went wrong before -- a commented example satisfied the old
    exact-version property while the real line went unchecked.
    """
    (tmp_path / "Dockerfile.browser").write_text(
        "# This used to run npm install pa11y, which left the tree floating.\n"
        "RUN cd /opt/tools && npm ci --ignore-scripts\n"
    )
    (tmp_path / "html-to-pdf.js").write_text(
        "// npm install is not what happens here\nconst x = 1;\n"
    )
    lines = [line for _, line in code_lines(tmp_path)]
    assert "RUN cd /opt/tools && npm ci --ignore-scripts" in lines
    assert not [line for line in lines if FLOATING_INSTALLS.search(line)]


def test_the_install_scan_catches_more_than_one_spelling():
    """`npm i`, `npm add`, npx, yarn and pnpm all fetch by name and resolve
    what is under it at run time. A deny-list one phrase wide would let every
    one of them back in."""
    for line in (
        "RUN npm install pa11y",
        "RUN npm i pa11y",
        "npm add prettier@3.9.6",
        "npx prettier@latest --write .",
        "yarn add pa11y",
        "pnpm add pa11y",
    ):
        assert FLOATING_INSTALLS.search(line), line

    for line in (
        "RUN npm ci --ignore-scripts --no-audit --no-fund",
        # The one unpinned install this repository has decided to keep, with
        # the reasoning in Dockerfile.browser.j2. This test must not demand it.
        "RUN apk add --no-cache chromium font-noto ttf-dejavu",
        "npm-install-notes.md",
    ):
        assert not FLOATING_INSTALLS.search(line), line


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


def test_the_browser_image_installs_from_the_pinned_manifest_and_lockfile(doc_package):
    """The rendered text is the constants, not a copy of them -- so bumping a
    pin is one edit, and a test cannot go on asserting a version the image
    stopped installing."""
    dockerfile = (doc_package / "Dockerfile.browser").read_text()
    manifest = pipeline.npm_manifest(
        pipeline.BROWSER_MANIFEST_NAME, pipeline.BROWSER_NPM_PINS
    )

    assert f"'{manifest}' > {pipeline.BROWSER_TOOLS_DIR}/package.json" in dockerfile
    assert (
        f"COPY {pipeline.BROWSER_LOCKFILE} "
        f"{pipeline.BROWSER_TOOLS_DIR}/package-lock.json" in dockerfile
    )
    assert (
        f"RUN cd {pipeline.BROWSER_TOOLS_DIR} && npm ci --ignore-scripts" in dockerfile
    )
    # The install is local, so these two are what make `require("puppeteer")`
    # and `pa11y` resolvable at all. A rewiring that moves the install without
    # moving them builds an image whose tools cannot be found.
    assert f"ENV NODE_PATH={pipeline.BROWSER_NODE_MODULES}\n" in dockerfile
    assert f"ENV PATH={pipeline.BROWSER_NODE_MODULES}/.bin:$PATH\n" in dockerfile


def test_format_md_installs_from_the_pinned_manifest_and_lockfile(doc_package):
    """Same rule for the formatter. An unpinned prettier decides how every
    markdown file in a package gets rewritten, which is a wider blast radius
    than the browser's and was floating for the same reason."""
    compose = (doc_package / "docker-compose.yml").read_text()
    manifest = pipeline.npm_manifest(
        pipeline.FORMAT_MANIFEST_NAME, pipeline.FORMAT_NPM_PINS
    )

    assert f"'{manifest}' > {pipeline.FORMAT_TOOLS_DIR}/package.json" in compose
    assert (
        f"cp {pipeline.FORMAT_LOCKFILE} "
        f"{pipeline.FORMAT_TOOLS_DIR}/package-lock.json" in compose
    )
    # And says what to do when the lockfile is absent. stencil emits it for
    # every compose file it can recognise, but a consumer template with a name
    # of its own that includes the partial is invisible from outside a template
    # body -- so the one case stencil cannot detect gets a message rather than
    # `cp: can't stat`.
    assert f"if [ ! -f {pipeline.FORMAT_LOCKFILE} ]" in compose
    assert "Run 'stencil gen' for" in compose
    assert (
        f"(cd {pipeline.FORMAT_TOOLS_DIR} && npm ci --ignore-scripts" in compose
    ), (
        "npm must run from the tools directory, not from /workspace -- a course "
        "package legitimately carries a package.json of its own"
    )


def test_a_package_that_renders_no_markdown_still_gets_the_format_lockfile(
    generate_package,
):
    """`templates:` is config-level, so a package with no docs and no slides
    still gets a docker-compose.yml -- and its format-md service still works,
    because `make format-md` formats the markdown a package CONTAINS rather
    than the markdown it renders.

    That service now copies a lockfile into place before installing. Keyed off
    has_pages, like everything else stencil injects, the lockfile would not be
    written for such a package and a target that works today would start
    failing with `cp: can't stat`. Caught in review before it shipped; this is
    the test that keeps it caught.
    """
    package = generate_package(
        {
            "templates": [{"src": "docker-compose.yml.j2"}],
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        }
    )
    compose = (package / "docker-compose.yml").read_text()
    assert f"cp {pipeline.FORMAT_LOCKFILE}" in compose
    assert (package / pipeline.FORMAT_LOCKFILE).is_file(), (
        "the compose file copies a lockfile stencil did not write"
    )
    # And nothing it has no use for: no Dockerfile.browser, so no lockfile for
    # the image that would have built from it.
    assert not (package / pipeline.BROWSER_LOCKFILE).exists()


def test_a_renamed_compose_file_still_gets_the_format_lockfile(generate_package):
    """`dest:` is an ordinary thing for a config to set, and `docker-compose.yaml`
    is an ordinary thing to rename it to. Matching only the destination filename
    would ship a compose file whose `cp` names a lockfile stencil did not write
    -- the same failure as keying the lockfile off has_pages, reached by a
    different route."""
    package = generate_package(
        {
            "templates": [
                {"src": "docker-compose.yml.j2", "dest": "docker-compose.yaml"}
            ],
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        }
    )
    assert (package / "docker-compose.yaml").is_file()
    assert (package / pipeline.FORMAT_LOCKFILE).is_file()


def test_a_package_with_no_compose_file_gets_no_format_lockfile(generate_package):
    """The other side of the predicate. A lockfile for a service the package
    does not have is a file `stencil clean` has to know about and nothing
    reads."""
    package = generate_package(
        {
            "templates": [{"src": "Makefile.j2"}],
            "packages": {
                "demo": {"name": "Demo", "package_type": "none", "docs": ["Guide.md"]}
            },
        }
    )
    assert not (package / pipeline.FORMAT_LOCKFILE).exists()
    # It renders markdown, so the browser image and its lockfile are both here.
    assert (package / pipeline.BROWSER_LOCKFILE).is_file()


@pytest.mark.parametrize("filename", [pipeline.BROWSER_LOCKFILE, pipeline.FORMAT_LOCKFILE])
def test_the_generated_lockfile_is_the_committed_one(doc_package, filename):
    """Byte-identical, not merely equivalent JSON.

    `npm ci` verifies tarballs against the hashes in the file it is given, so
    the file a package builds with has to be the file a maintainer resolved and
    a reviewer read. A rendering that reformatted it would still work and would
    make the two impossible to compare.
    """
    assert (doc_package / filename).read_bytes() == (ASSETS / filename).read_bytes()


# ---------------------------------------------------------------------------
# the committed lockfiles


@pytest.mark.parametrize(("filename", "manifest_name", "pins"), LOCKED)
def test_the_lockfile_agrees_with_the_pins(filename, manifest_name, pins):
    """THE ACCEPTANCE stn-5hv asks for: the lockfile and the pins in pipeline.py
    cannot drift.

    Both halves matter. The root entry is what `npm ci` compares the manifest
    against -- disagree and the build fails with EUSAGE rather than installing
    something else -- and the per-package entries are what actually gets
    installed. A lockfile could satisfy the first while pinning a different
    version of a top-level package in the second only if it were edited by
    hand, which is exactly the case worth refusing.
    """
    lock = lockfile(filename)
    root = lock["packages"][""]

    # The WHOLE root entry against the manifest, not just the dependencies.
    # `npm ci` compares name and version too, so changing BROWSER_MANIFEST_NAME
    # without re-vendoring breaks the build -- and would otherwise break it
    # only inside a container, which is the tier a contributor without docker
    # never runs. The manifest lives in pipeline.py precisely so the fast tier
    # can hold this.
    manifest = json.loads(pipeline.npm_manifest(manifest_name, pins))
    assert {key: root.get(key) for key in ("name", "version", "dependencies")} == {
        "name": manifest["name"],
        "version": manifest["version"],
        "dependencies": manifest["dependencies"],
    }, f"{filename} was resolved from a different manifest"
    assert lock["name"] == manifest_name and lock["version"] == manifest["version"]

    assert root["name"] == manifest_name
    assert root["dependencies"] == pins, (
        f"{filename} was resolved from a different set of pins. Re-vendor it: "
        f"python3 scripts/vendor_npm_locks.py"
    )
    for package, version in pins.items():
        entry = lock["packages"][f"node_modules/{package}"]
        assert entry["version"] == version, (
            f"{filename} pins {package} at {entry['version']}, not {version}"
        )


@pytest.mark.parametrize("filename", LOCKFILES)
def test_every_locked_package_carries_an_integrity_hash(filename):
    """The mechanism the acceptance names: "verified by integrity hash rather
    than by a version number".

    UNCONDITIONAL, over every entry but the root. Filtering to entries that
    have a `resolved` first -- which is the obvious way to write this -- makes
    the assertion depend on a field the thing being checked controls: a
    dependency with `"link": true`, `"inBundle": true`, or a `file:`/`git+ssh:`
    resolution has neither field, so it would be skipped rather than caught,
    and installed with nothing verified. That is the hole this ticket was filed
    about, reopened one package at a time.

    Measured: in both committed lockfiles the only entry without a `resolved`
    is the root, so the strict form costs nothing today. It is written this way
    for the day it would.
    """
    lock = lockfile(filename)
    packages = lock["packages"]
    assert "" in packages, f"{filename} has no root entry"

    for path, entry in packages.items():
        if path == "":
            continue
        assert entry.get("resolved"), f"{filename}: {path} resolves from nowhere"
        assert entry.get("integrity", "").startswith("sha512-"), (
            f"{filename}: {path} is installed without a sha512 to check it against"
        )
        assert not entry.get("link") and not entry.get("inBundle"), (
            f"{filename}: {path} is linked or bundled, so its bytes come from "
            f"somewhere the integrity hash does not cover"
        )


# Entry counts as vendored, so a truncated or half-reverted lockfile cannot
# satisfy every other assertion in this file. A floor rather than the exact
# number: a legitimate bump moves it, and a test that has to be edited on every
# bump is one that gets edited without being read.
LOCK_FLOOR = {pipeline.BROWSER_LOCKFILE: 40, pipeline.FORMAT_LOCKFILE: 3}


@pytest.mark.parametrize("filename", LOCKFILES)
def test_the_lockfile_describes_a_whole_tree(filename):
    """Non-vacuity, and the lockfile format the rest of this file assumes.

    Every other assertion here walks `packages`, which is a lockfileVersion 2
    or 3 key. A v1 lockfile has `dependencies` instead and would fail loudly,
    but a **v2** carries BOTH -- so each of these tests would pass over the
    modern half while npm installed from the legacy tree underneath it.
    """
    lock = lockfile(filename)
    assert lock["lockfileVersion"] == 3, (
        f"{filename} is lockfileVersion {lock['lockfileVersion']}; re-vendor it "
        f"with the npm in {pipeline.NODE_IMAGE}"
    )
    assert "dependencies" not in lock, (
        f"{filename} carries a legacy dependencies tree as well as packages"
    )
    count = len(lock["packages"])
    assert count >= LOCK_FLOOR[filename], (
        f"{filename} describes {count} packages, fewer than the {LOCK_FLOOR[filename]} "
        f"a whole tree has. A truncated lockfile satisfies every other "
        f"assertion in this file."
    )


@pytest.mark.parametrize("filename", LOCKFILES)
def test_every_locked_package_resolves_to_itself_on_the_public_registry(filename):
    """A lockfile decides WHERE each tarball is fetched from, and WHICH one,
    and neither is covered by the integrity hash.

    The host matters because a `resolved` pointing somewhere nobody reviewed is
    fetched by every consumer's build, and the hash would match whatever that
    host was serving when the lockfile was written.

    The path matters for a subtler reason: npm checks the tarball it downloads
    against `integrity`, and never checks that the URL names the package the
    key claims. A hand-edited lockfile can point `node_modules/pdf-lib` at
    .../evil/-/evil-1.0.0.tgz with a hash that matches evil, and npm installs
    evil as pdf-lib. Deriving the expected URL from the key and the version is
    what closes that.
    """
    lock = lockfile(filename)
    for path, entry in lock["packages"].items():
        if path == "":
            continue
        url = entry["resolved"]
        assert url.startswith("https://registry.npmjs.org/"), (
            f"{filename}: {path} is fetched from {url}"
        )
        name = path.rsplit("node_modules/", 1)[1]
        stem = name.rsplit("/", 1)[-1]
        expected = f"/{name}/-/{stem}-{entry['version']}.tgz"
        assert url.endswith(expected), (
            f"{filename}: {path} claims version {entry['version']} but is "
            f"fetched from {url}, which is a different package or version"
        )


# The packages that run code at install time, frozen. puppeteer's postinstall
# downloads Chromium and PUPPETEER_SKIP_DOWNLOAD turns it into a no-op; both
# installs pass --ignore-scripts anyway, so nothing here executes today.
#
# The assertion is not about today. A re-vendor that picks up a transitive
# package which has GAINED a postinstall arrives in a 48-entry JSON diff as one
# added boolean, and scripts/vendor_npm_locks.py only prints it to the
# maintainer's terminal -- which is not in a pull request. A new entry here is
# a stop-and-read, not a note.
INSTALL_SCRIPTS = {
    pipeline.BROWSER_LOCKFILE: {"node_modules/puppeteer"},
    pipeline.FORMAT_LOCKFILE: set(),
}


@pytest.mark.parametrize("filename", LOCKFILES)
def test_the_set_of_packages_with_install_scripts_is_the_frozen_one(filename):
    """See INSTALL_SCRIPTS above. Read the new package before widening this."""
    lock = lockfile(filename)
    declared = {
        path
        for path, entry in lock["packages"].items()
        if entry.get("hasInstallScript")
    }
    assert declared == INSTALL_SCRIPTS[filename], (
        f"{filename}: the set of packages running code at install time changed. "
        f"Both installs pass --ignore-scripts, so this is not an immediate "
        f"execution -- read what the package does and why it needs a hook, then "
        f"update INSTALL_SCRIPTS deliberately."
    )


def test_the_browser_lockfile_holds_exactly_one_puppeteer():
    """pa11y depends on puppeteer, and npm dedupes the two onto one copy only
    while the pinned puppeteer satisfies pa11y's declared range. Two copies mean
    `make check-access` drives a different browser than `make pdf` -- quietly,
    with every other test still passing.

    Until the lockfile existed this could only be measured by building the image
    (it still is, below). Now it is a fact about a file in the repository, so a
    pin bump that breaks the dedupe fails in the fast tier, before anyone waits
    on a Chromium build.
    """
    lock = lockfile(pipeline.BROWSER_LOCKFILE)
    copies = {
        path: entry["version"]
        for path, entry in lock["packages"].items()
        if path == "node_modules/puppeteer" or path.endswith("/node_modules/puppeteer")
    }
    assert copies == {
        "node_modules/puppeteer": pipeline.BROWSER_NPM_PINS["puppeteer"]
    }, f"expected one deduped puppeteer, the lockfile holds: {copies}"


def test_a_scoped_package_is_pinned_and_locked():
    """`@awmottaz/prettier-plugin-void-html` carries an `@` inside the package
    NAME as well as before a version, which is the shape that breaks the obvious
    `split("@")`. It is a real pin, so the manifest and the lockfile are
    asserted to round-trip it rather than a fabricated example."""
    scoped = "@awmottaz/prettier-plugin-void-html"
    assert scoped in pipeline.FORMAT_NPM_PINS

    manifest = json.loads(
        pipeline.npm_manifest(pipeline.FORMAT_MANIFEST_NAME, pipeline.FORMAT_NPM_PINS)
    )
    assert manifest["dependencies"][scoped] == pipeline.FORMAT_NPM_PINS[scoped]
    lock = lockfile(pipeline.FORMAT_LOCKFILE)
    assert f"node_modules/{scoped}" in lock["packages"]


# ---------------------------------------------------------------------------
# the manifest npm ci is handed


def test_a_manifest_is_sorted_and_deterministic():
    """An install line whose order changes between runs is a diff nobody asked
    for. That property used to belong to `npm_specs`; the install line is gone
    and the manifest inherited it."""
    one = pipeline.npm_manifest("x", {"b": "2.0.0", "a": "1.2.3"})
    two = pipeline.npm_manifest("x", {"a": "1.2.3", "b": "2.0.0"})
    assert one == two
    assert one == '{"name":"x","version":"0.0.0","private":true,"dependencies":{"a":"1.2.3","b":"2.0.0"}}'


def test_a_manifest_refuses_a_range():
    """npm accepts `^25.10.0` in a manifest quite happily. `npm ci` would then
    install the locked tree while the pin claimed something looser -- the pin
    would read as satisfied and mean nothing, which is the failure this whole
    file exists to prevent."""
    for loose in ("^25.10.0", "~25.10.0", "25.x", "latest", ">=25.10.0"):
        with pytest.raises(ValueError, match="not an exact version"):
            pipeline.npm_manifest("x", {"puppeteer": loose})


def test_a_manifest_refuses_a_name_that_would_escape_the_shell():
    """The manifest is written by `printf '%s\\n' '<json>'` -- a single-quoted
    shell word -- in both a Dockerfile RUN and a compose entrypoint. A `'` in a
    package name would end the quoting and hand the rest of the JSON to sh, at
    image build time, as root. Refused at generation time rather than escaped:
    every name here comes from a dict a maintainer edits by hand."""
    for hostile in ("pa11y'; rm -rf /; '", "pa11y\nRUN echo pwned", "../../etc/passwd"):
        with pytest.raises(ValueError, match="not a plain npm package name"):
            pipeline.npm_manifest("x", {hostile: "1.0.0"})
    with pytest.raises(ValueError, match="not a plain npm package name"):
        pipeline.npm_manifest("x'; echo pwned; '", {"pa11y": "1.0.0"})


def test_a_lockfile_must_end_in_exactly_one_newline(tmp_path, monkeypatch):
    """read_lockfile strips the trailing newline and the template puts one back,
    which is what makes the generated file byte-identical to the committed one.
    A file that picked up a second newline -- an editor, a merge -- would render
    a package that no longer matches, and the byte-identity test would fail
    somewhere far from the cause."""
    monkeypatch.setattr(pipeline, "ASSETS_DIR", tmp_path)
    (tmp_path / "x.json").write_text("{}\n\n")
    with pytest.raises(ValueError, match="exactly one newline"):
        pipeline.read_lockfile("x.json")

    (tmp_path / "y.json").write_text("{}\n")
    assert pipeline.read_lockfile("y.json") == "{}"

    with pytest.raises(FileNotFoundError, match="vendor_npm_locks"):
        pipeline.read_lockfile("absent.json")


# ---------------------------------------------------------------------------
# what the built image actually contains
#
# Everything below needs a container. The Dockerfile naming a lockfile and the
# image holding the tree that lockfile describes are two different claims, and
# only the second one is the thing every generated handout is rendered by.

PROBE = """
const fs = require("fs");
const path = require("path");
const pa11y = require("pa11y");

const prefix = "%(prefix)s";
const root = `${prefix}/node_modules`;
const out = { installed: {}, tree: {}, puppeteerCopies: [], pa11y: {} };

for (const name of %(names)s) {
  out.installed[name] = require(`${root}/${name}/package.json`).version;
}

// EVERY package in the installed tree, keyed the way the lockfile keys them --
// "node_modules/foo", "node_modules/foo/node_modules/bar" -- so the two can be
// compared entry for entry. The point of the lockfile is the tree BELOW the
// three pinned names, and a probe that reported only the top level would leave
// exactly that unmeasured.
function collect(dir, prefixPath) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    if (!entry.isDirectory() && !entry.isSymbolicLink()) continue;
    if (entry.name === ".bin") continue;
    const full = `${dir}/${entry.name}`;
    if (entry.name.startsWith("@")) {
      // A scope directory is not a package; its children are.
      collect(full, `${prefixPath}/${entry.name}`);
      continue;
    }
    const key = `${prefixPath}/${entry.name}`;
    try {
      out.tree[key] = require(`${full}/package.json`).version;
    } catch {
      continue;
    }
    if (entry.name === "puppeteer") {
      out.puppeteerCopies.push({ path: full, version: out.tree[key] });
    }
    collect(`${full}/node_modules`, `${key}/node_modules`);
  }
}
collect(root, "node_modules");

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
    Chromium once per theme -- is paid once either way. The tests still fail
    separately, because they assert on separate keys.
    """
    built = pipeline.render(
        "doc", "document.md", "document.html", workdir=pdf_workspace
    )
    assert built.returncode == 0, f"pandoc failed\n{built.stderr}"

    script = PROBE % {
        "names": json.dumps(sorted(pipeline.BROWSER_NPM_PINS)),
        "prefix": pipeline.BROWSER_TOOLS_DIR,
    }
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
    """What stn-s5b asked for, measured where it matters. The Dockerfile is a
    request; this is the answer."""
    assert installed["installed"] == pipeline.BROWSER_NPM_PINS


@pytest.mark.integration
def test_the_built_image_holds_the_tree_the_lockfile_describes(installed):
    """What stn-5hv asked for, and the reason this file's fast tier is not
    enough on its own: a lockfile in the repository is only a claim until an
    image is built from it.

    Every installed package, not just the three named ones. That is the whole
    point -- 44 of the 47 are transitive, and those are what used to be
    re-resolved on every build. Both directions are asserted, because a tree
    that matches the lockfile except for the packages it silently omitted is
    not the tree the lockfile describes.
    """
    lock = json.loads((ASSETS / pipeline.BROWSER_LOCKFILE).read_text())
    locked = {
        path: entry["version"]
        for path, entry in lock["packages"].items()
        if path and entry.get("version")
    }
    tree = installed["tree"]

    unexpected = {
        path: version for path, version in tree.items() if path not in locked
    }
    assert not unexpected, (
        f"the image holds packages the lockfile does not describe: {unexpected}"
    )

    wrong = {
        path: (version, locked[path])
        for path, version in tree.items()
        if locked[path] != version
    }
    assert not wrong, f"installed vs locked: {wrong}"

    # A lockfile entry may legitimately go uninstalled when it is optional or
    # constrained to another platform. Neither is true of anything here today;
    # skipping them keeps a future bump that introduces one from failing this
    # for the wrong reason.
    conditional = {
        path
        for path, entry in lock["packages"].items()
        if entry.get("optional") or entry.get("os") or entry.get("cpu")
    }
    missing = sorted(set(locked) - set(tree) - conditional)
    assert not missing, f"the lockfile describes packages the image lacks: {missing}"


@pytest.mark.integration
def test_exactly_one_puppeteer_is_installed(installed):
    """The dedupe invariant, in the image rather than in the lockfile. The
    fast-tier test above reads what npm resolved; this reads what npm laid
    down."""
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

    It carries a second job since stn-5hv moved the install off `--global`:
    pa11y is `require`d here through NODE_PATH and run from the
    node_modules/.bin symlink npm wrote, so a rewiring that left either pointing
    at the old /opt/tools/lib layout fails this before it fails a handout.

    The assertion is that pa11y RUNS and reports, on both generated configs,
    against a real rendered page. It is not an accessibility assertion about the
    fixture: that would make an unrelated content change fail a pin test."""
    for theme in ("light", "dark"):
        assert theme in installed["pa11y"], f"pa11y never ran for the {theme} theme"
        assert isinstance(installed["pa11y"][theme]["issues"], int)
