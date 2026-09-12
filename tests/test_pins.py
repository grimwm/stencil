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

import hashlib
import json
import re
import shutil
import subprocess

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

    # stn-8vi appended `@sha256:<64 hex>` after the tag, so the tag can no
    # longer be read with a bare rpartition(":") -- the digest's own ":" (the
    # one inside "sha256:<hex>") is now the LAST one in the string, and a bare
    # rpartition hands the version check 64 hex characters instead of a
    # version. Split the digest off first.
    reference, _, digest = pipeline.NODE_IMAGE.partition("@")
    tag = reference.rpartition(":")[2]
    assert re.fullmatch(r"\d+\.\d+\.\d+-alpine\d+\.\d+", tag), (
        f"{tag!r} does not pin both a Node version and an Alpine branch. "
        "The Alpine branch is what decides which Chromium `apk add` installs "
        "and which font packages are available, so a floating one moves the "
        "browser several majors at a time."
    )
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest), (
        f"pipeline.NODE_IMAGE carries no digest pin: {pipeline.NODE_IMAGE!r}"
    )


def test_every_image_the_scaffolding_pulls_carries_a_version_tag(doc_package):
    """`lts` and `latest` both float. A service that BUILDS its image is exempt:
    `localhost/<pkg>_browser:latest` is a local tag for the image the Dockerfile
    beside it produces, not something pulled from a registry.

    stn-8vi appended `@sha256:<64 hex>` after every pulled image's tag. A
    64-character hex digest satisfies `any(c.isdigit() for c in tag)` for
    essentially any digest at all, so extracting "the tag" with a bare
    `rpartition(":")` -- as this test used to -- made the version-tag check
    below pass no matter what the human-readable tag said, the moment a
    digest was appended. That is a silent regression, not a fix: split the
    digest off FIRST, then check the tag by itself, and also require the
    digest to be there at all so a package with no pin cannot pass this by
    virtue of having no digest to mis-parse.
    """
    compose = yaml.safe_load((doc_package / "docker-compose.yml").read_text())

    for name, service in compose["services"].items():
        if "build" in service:
            continue
        image = service["image"]
        assert ":" in image, f"service {name} names {image!r} with no tag at all"
        reference, _, digest = image.partition("@")
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest), (
            f"service {name} runs {image!r} with no digest pin"
        )
        tag = reference.rpartition(":")[2]
        # "contains a digit" rather than a deny-list of the floating names.
        # `lts-alpine` is not `lts`, and a deny-list written the obvious way
        # passes against it -- which is how the defect this file is about
        # survived a compose file that already pinned pandoc and veraPDF.
        assert any(character.isdigit() for character in tag), (
            f"service {name} runs {image!r}: the tag names no version, so it "
            "floats onto whatever that name points at on the day of the build"
        )


# ---------------------------------------------------------------------------
# stn-8vi: pinning the three images themselves by manifest digest.
#
# A registry TAG is mutable -- docker.io/pandoc/core:3.10.0.0 can be repushed,
# and every rebuild after that silently gets different bytes under a name that
# says otherwise. A digest (@sha256:<64 hex>) cannot. The tag stays what a
# maintainer edits; the digest is a vendored answer keyed by that exact tag, in
# stencil/assets/image-digests.json, resolved by scripts/resolve_image_digests.py
# -- the same request/answer shape stn-5hv already gave the npm pins and their
# lockfiles.


# <repo>:<tag>@sha256:<64 lowercase hex>. Anchored full-string so a reference
# that merely CONTAINS a well-formed digest somewhere -- appended to a comment,
# say -- does not satisfy it.
_REPO = r"[A-Za-z0-9](?:[A-Za-z0-9._/-]*[A-Za-z0-9])?"
_TAG = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
DIGEST_PINNED_IMAGE = re.compile(rf"^{_REPO}:{_TAG}@sha256:[0-9a-f]{{64}}$")


def test_every_image_the_scaffolding_pulls_is_pinned_by_digest(doc_package):
    """The acceptance criterion stn-8vi's ticket asks for, literally: "a test
    asserts the digest form for every image the scaffolding pulls".

    Three places name an image the scaffolding does not build itself, and each
    has its own syntax for it: a compose service's `image:` key, a Dockerfile's
    `FROM`, and a `$(call ensure_image,<image>,...)` site in the generated
    Makefile. A test that checked only one of them would leave the other two
    free to keep floating -- which is exactly how NODE_IMAGE stayed a tag while
    PANDOC_IMAGE and VERAPDF_IMAGE were pinned, the state this ticket exists to
    end. `pdf` and `check-access` are excluded because they `build:` their
    image from Dockerfile.browser rather than pulling one; that Dockerfile's
    own `FROM` is what is checked instead.
    """
    compose = yaml.safe_load((doc_package / "docker-compose.yml").read_text())
    named = {
        f"docker-compose.yml service {name!r}": service["image"]
        for name, service in compose["services"].items()
        if "build" not in service
    }
    assert named, "no compose service pulls an image any more"

    dockerfile = (doc_package / "Dockerfile.browser").read_text()
    from_lines = [
        line.removeprefix("FROM ").strip()
        for line in dockerfile.splitlines()
        if line.startswith("FROM ")
    ]
    assert from_lines, "Dockerfile.browser names no base image any more"
    for index, image in enumerate(from_lines):
        named[f"Dockerfile.browser FROM #{index}"] = image

    makefile = (doc_package / "Makefile").read_text()
    call_sites = re.findall(r"\$\(call ensure_image,([^,]+),", makefile)
    assert call_sites, "no ensure_image call sites in the generated Makefile"
    for index, image in enumerate(call_sites):
        named[f"ensure_image call site #{index}"] = image

    for source, image in named.items():
        assert DIGEST_PINNED_IMAGE.fullmatch(image), (
            f"{source} names {image!r}, which is not <repo>:<tag>@sha256:<64 hex>"
        )


# node and pandoc are multi-arch OCI indexes; verapdf is a single-arch v2
# manifest with no index at all, named here explicitly so a future multi-arch
# veraPDF release is a deliberate edit to this set rather than a silent pass
# over a check that quietly stopped checking anything.
INDEX_MEDIA_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
SINGLE_ARCH_MEDIA_TYPES = {
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
}


def test_the_pinned_digests_cover_the_architectures_we_build_on():
    """The ticket's "verify on both arm64 and amd64" requirement, expressed as
    something CI can hold rather than something a maintainer promises to have
    done by hand.

    Pinning a SINGLE-ARCH image digest for node or pandoc would build correctly
    on whichever architecture that digest happens to be, and silently produce
    the wrong bytes -- or refuse to build at all -- on the other. The manifest
    LIST digest resolves per-architecture and is what has to be pinned instead,
    which is only checkable if something records which architectures the
    pinned digest actually covers. `platforms` in image-digests.json is that
    record.

    veraPDF genuinely has no manifest list to pin -- measured, not assumed --
    so it is named here as the one exception rather than silently exempted by
    "skip whatever isn't an index", which would also skip a node or pandoc
    entry that regressed to a single-arch pin without anyone noticing.
    """
    digests = json.loads((ASSETS / "image-digests.json").read_text())

    for tag in (pipeline.NODE_TAG, pipeline.PANDOC_TAG):
        entry = digests[tag]
        assert entry["media_type"] in INDEX_MEDIA_TYPES, (
            f"{tag} is recorded as {entry['media_type']!r}, not a multi-arch "
            "manifest index -- the pin may resolve to only one architecture"
        )
        platforms = set(entry["platforms"])
        assert any(platform.startswith("linux/amd64") for platform in platforms), (
            f"{tag}'s recorded platforms {sorted(platforms)} do not cover linux/amd64"
        )
        assert any(platform.startswith("linux/arm64") for platform in platforms), (
            f"{tag}'s recorded platforms {sorted(platforms)} do not cover linux/arm64"
        )

    verapdf_entry = digests[pipeline.VERAPDF_TAG]
    assert verapdf_entry["media_type"] in SINGLE_ARCH_MEDIA_TYPES, (
        f"veraPDF is recorded as {verapdf_entry['media_type']!r}; if it has "
        "gained a manifest list, pin that and cover it above like the others "
        "instead of widening this set"
    )


def test_the_recorded_digests_are_exactly_the_tags_the_scaffolding_pins():
    """`platforms` and `media_type` are RECORDS of what the registry answered
    on the day someone ran the resolver, not facts a test can re-derive and
    check -- there is no unit-tier way to know they are still true today. What
    IS checkable is that the file was produced FROM the tags currently in
    pipeline.py: that every tag stencil pins has an entry, and every entry is
    for a tag stencil still pins.

    That single equality catches three different mistakes at once: a
    hand-edited JSON entry for a tag nothing pins any more, a tag bumped in
    pipeline.py without re-running the resolver, and an entry the resolver
    never wrote at all. Any of the three otherwise looks fine right up until
    someone reads the diff closely.
    """
    digests = json.loads((ASSETS / "image-digests.json").read_text())
    assert set(digests) == set(pipeline.IMAGE_TAGS.values()), (
        "stencil/assets/image-digests.json does not match the tags in "
        "pipeline.IMAGE_TAGS -- re-resolve it: "
        "python3 scripts/resolve_image_digests.py"
    )


def test_a_tag_with_no_recorded_digest_fails_loudly():
    """A tag bumped in pipeline.py without re-running the resolver must not
    silently fall back to the tag alone -- that would quietly re-float the
    exact input this ticket exists to fix. It must fail, and name the fix.
    """
    with pytest.raises(pipeline.VendoredAssetError, match="resolve_image_digests"):
        pipeline.pinned_image("docker.io/library/node:0.0.0-not-a-real-release")


def test_the_tags_are_readable_without_resolving_them(monkeypatch):
    """The regression guard for the bootstrap deadlock (stn-e72.2).

    scripts/resolve_image_digests.py has to read pipeline.IMAGE_TAGS to learn
    which references to resolve -- necessarily BEFORE image-digests.json holds
    an entry for any of them. If IMAGE_TAGS were ever built from the resolved
    NODE_IMAGE / PANDOC_IMAGE / VERAPDF_IMAGE constants instead of the plain
    tags, reading it would require the very file the resolver has not written
    yet: a deadlock a bootstrap script cannot break on its own.

    Without this test, the first person to "simplify" IMAGE_TAGS into
    `{"node": NODE_IMAGE, ...}` -- which reads as a harmless dedup -- silently
    reintroduces exactly that. Forcing pinned_image() to blow up and confirming
    IMAGE_TAGS is unaffected is what makes the mistake fail here, on a
    fully-resolved image-digests.json, rather than only on the day the file is
    incomplete.
    """

    def explode(tag):
        raise AssertionError(f"reading IMAGE_TAGS must not resolve {tag!r}")

    monkeypatch.setattr(pipeline, "pinned_image", explode)

    assert pipeline.IMAGE_TAGS == {
        "node": pipeline.NODE_TAG,
        "pandoc": pipeline.PANDOC_TAG,
        "verapdf": pipeline.VERAPDF_TAG,
    }


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


def test_the_pdf_driver_is_baked_into_the_image_at_the_pinned_path(doc_package):
    """stn-7ki. The three places that name the baked script must agree.

    The filename is written down in the Dockerfile, in the compose entrypoint
    and in pipeline.BROWSER_SCRIPT_PATH rather than reaching the two templates
    through a shared context key, because adding one means editing generate.py.
    This is what stops those three drifting: a rename that misses one produces
    an image whose pdf service cannot start, and `Cannot find module` is a poor
    way to find that out.
    """
    dockerfile = (doc_package / "Dockerfile.browser").read_text()
    compose = yaml.safe_load((doc_package / "docker-compose.yml").read_text())

    assert (
        f"COPY {pipeline.BROWSER_SCRIPT} {pipeline.BROWSER_SCRIPT_PATH}" in dockerfile
    ), "Dockerfile.browser no longer bakes the pdf driver into the image"
    assert compose["services"]["pdf"]["entrypoint"] == [
        "node",
        pipeline.BROWSER_SCRIPT_PATH,
    ], "the pdf service is not running the baked script"

    # AFTER the install, or every edit to the script pays for `npm ci` again.
    assert dockerfile.index("COPY " + pipeline.BROWSER_SCRIPT) > dockerfile.index(
        f"RUN cd {pipeline.BROWSER_TOOLS_DIR} && npm ci"
    ), "the COPY was moved above npm ci, which invalidates the install layer"


def test_the_baked_scripts_manifest_cannot_reopen_either_defect(doc_package):
    """Two keys that must stay ABSENT from {BROWSER_TOOLS_DIR}/package.json.

    That file is the derived npm manifest, and baking html-to-pdf.js beside it
    made it load-bearing in a second way nobody would guess from its contents:

    - It is now the nearest package.json TO THE SCRIPT, so it is what decides
      the script is CommonJS. A ``"type": "module"`` key here would reopen
      stn-7ki *inside the image*, where no consumer could even see the cause.
    - ``package.json`` is one of puppeteer's thirteen lilconfig searchPlaces,
      and after the chdir this directory is the FIRST one on the upward walk.
      A ``puppeteer`` key here would be read as configuration -- stn-jeq, with
      the payload moved into stencil's own artifact rather than the consumer's.

    Both hold by construction today: npm_manifest emits name, version, private
    and dependencies and nothing else. Neither is asserted anywhere else, and
    the manifest is exactly the thing a future pin-map change edits.
    """
    manifest = json.loads(
        pipeline.npm_manifest(pipeline.BROWSER_MANIFEST_NAME, pipeline.BROWSER_NPM_PINS)
    )
    assert "type" not in manifest, (
        'the browser manifest declares a "type", which decides how Node parses '
        "the baked html-to-pdf.js beside it -- that is stn-7ki, reopened inside "
        "the image"
    )
    assert "puppeteer" not in manifest, (
        'the browser manifest declares a "puppeteer" key, which puppeteer reads '
        "as configuration from the very directory the script chdirs into -- that "
        "is stn-jeq, reopened in stencil's own artifact"
    )

    # And the two working directories the chdir depends on. After the script
    # moves itself out of the mount, these are the only reason a RELATIVE
    # argument from the generated Makefile (OUT := . for a package with no
    # output_dir) still resolves inside it.
    dockerfile = (doc_package / "Dockerfile.browser").read_text()
    compose = yaml.safe_load((doc_package / "docker-compose.yml").read_text())
    assert dockerfile.rstrip().endswith("WORKDIR /workspace"), (
        "Dockerfile.browser no longer leaves the image's working directory in "
        "the mount, so a relative argument to the pdf service resolves nowhere"
    )
    for name in ("pdf", "check-access"):
        assert compose["services"][name]["working_dir"] == "/workspace", name


def test_html_to_pdf_js_roots_its_resolution_at_the_pinned_tools_dir(doc_package):
    """stn-cnm: puppeteer and pdf-lib must resolve from pipeline.BROWSER_TOOLS_DIR
    (module.createRequire rooted at "{{ browser_tools_dir }}/package.json", per
    the approved plan for stn-cnm), not from wherever a bare `require` happens
    to land starting at /workspace.

    Asserted against the CONSTANT, the way
    test_the_browser_image_installs_from_the_pinned_manifest_and_lockfile above
    asserts the Dockerfile's ENV lines against pipeline.BROWSER_NODE_MODULES --
    so a rewiring of the tools directory cannot leave this test asserting a
    path the image stopped using.

    OVER code_lines, NOT read_text, for the same reason the bare-require scan
    below is -- and here it is the POSITIVE assertion that would go vacuous.
    stn-cnm.2 is required to explain in a comment why a bare require is a
    defect here, and the natural way to write that comment is to quote the
    very call this looks for. A raw-text scan would then be satisfied by the
    explanation alone, and would keep passing if the actual call were deleted.
    """
    needle = f'createRequire("{pipeline.BROWSER_TOOLS_DIR}/package.json")'
    rooted = [
        line
        for path, line in code_lines(doc_package)
        if path.name == "html-to-pdf.js" and needle in line
    ]
    assert rooted, (
        "html-to-pdf.js does not root a createRequire() at "
        f"{pipeline.BROWSER_TOOLS_DIR!r} in CODE (a comment mentioning it does "
        "not count), so puppeteer/pdf-lib still resolve from wherever a bare "
        "require lands starting at /workspace"
    )

    # The guard's prefix must be ANCHORED with a trailing separator. Dropping
    # it leaves a check that a sibling directory whose name merely starts with
    # the tools path would satisfy -- and that mutation survives every other
    # tier here, including the container one, because nothing can produce such
    # a directory through createRequire. The code says the separator is
    # load-bearing; this is what makes that true rather than aspirational.
    anchored = f'startsWith("{pipeline.BROWSER_NODE_MODULES}/")'
    assert any(anchored in line for _, line in code_lines(doc_package)), (
        f"the tools-tree prefix check is not anchored at {anchored!r} -- "
        "without the trailing separator it accepts any path merely beginning "
        f"with {pipeline.BROWSER_NODE_MODULES!r}"
    )


# Every pinned browser package, as a require-CALL pattern rather than a
# mention of the name -- stn-cnm.2's guard legitimately contains
# fromTools.resolve("puppeteer"), which must NOT trip this. Iterated from
# pipeline.BROWSER_NPM_PINS rather than hardcoding "puppeteer" and "pdf-lib":
# a fourth pin added tomorrow (pa11y is already one) must be covered without
# anyone remembering to add a case for it here, the same property
# test_nothing_in_the_scaffolding_installs_by_name uses above.
BARE_PINNED_REQUIRE = {
    name: re.compile(r"""require\(\s*['"]""" + re.escape(name) + r"""['"]\s*\)""")
    for name in pipeline.BROWSER_NPM_PINS
}


def test_no_generated_js_bare_requires_a_pinned_browser_package(doc_package):
    """A bare `require("puppeteer")` (or pdf-lib, or pa11y) resolves starting
    from the requiring file's own directory and walks upward -- which, for
    html-to-pdf.js, starts at /workspace and lets a consumer's own
    node_modules outrank the image's pinned tree at pipeline.BROWSER_TOOLS_DIR.

    Scanned over every generated .js file, not only html-to-pdf.js -- a
    future template that reaches for one of these by a bare specifier must
    fail this too. Scanned over code_lines(doc_package), NOT read_text():
    code_lines strips `//` comments, and stn-cnm.2 is required to write a
    comment EXPLAINING why a bare require is a defect here -- a raw-text scan
    would find that explanation and report the file as still doing it.
    """
    violations = [
        (path.name, name, line)
        for path, line in code_lines(doc_package)
        # .mjs/.cjs as well as .js: a future template emitting either would
        # otherwise be scanned by nothing at all.
        if path.suffix in {".js", ".mjs", ".cjs"}
        for name, pattern in BARE_PINNED_REQUIRE.items()
        if pattern.search(line)
    ]
    assert not violations, (
        "these generated .js lines require a pinned package by bare "
        "specifier, which resolves starting from the file's own directory "
        f"rather than {pipeline.BROWSER_TOOLS_DIR}: {violations}"
    )


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


# One 64-character lowercase hex run. The entrypoint contains exactly one, and
# a test that reads it out of the rendered text rather than recomputing it is
# what makes the digest assertion below about the FILE stencil emitted rather
# than about a constant agreeing with itself.
_SHA256_HEX = re.compile(r"\b[0-9a-f]{64}\b")


def _format_md_script(package):
    """The format-md entrypoint's script, parsed out of the compose file.

    yaml.safe_load rather than a text search, for the reason
    tests/test_compose_format_md.py's fast-tier test gives: the change that
    adds this guard also adds a long comment explaining it, and a comment
    satisfies ``in compose.read_text()`` forever -- including after someone
    deletes the guard the comment describes.

    That file has a parser of its own shaped like this one, and the two are
    deliberately not shared: ``from tests.test_compose_format_md import ...``
    resolves locally and fails on CI with ModuleNotFoundError, which is the
    trap conftest.py documents for its own fixtures. Three lines of yaml is
    the cheaper half of that trade. What lives HERE is what the scaffolding
    installs -- the manifest, the lockfile, the digest that pins it; what
    lives there is what the service DOES once it has installed.
    """
    compose = yaml.safe_load((package / "docker-compose.yml").read_text())
    return compose["services"]["format-md"]["entrypoint"][-1]


def test_format_md_verifies_the_lockfile_it_installs_from(doc_package):
    """stn-qge. The install must refuse a lockfile that is not stencil's.

    The service copies ``format-package-lock.json`` out of /workspace -- the
    consumer's own directory, mounted read-write -- and ``npm ci`` fetches
    whatever host each ``resolved`` names, checking ``integrity`` against a
    value in that same file. So a consumer-editable file decided which bytes
    became the prettier that then ran as uid 0 over that mount.
    ``--ignore-scripts`` and stn-20h's ``--no-config`` do not touch it: nothing
    has to run at install time, the payload runs when prettier runs.

    THE ORDER IS THE PROPERTY, not the presence of a checksum somewhere. A
    check after ``npm ci`` verifies a file npm has already fetched from, and a
    check before the ``cp`` verifies a file in a directory the host can write
    while the container runs. Verifying the COPY, before the install, is the
    only arrangement where what was hashed is what npm reads.
    """
    script = _format_md_script(doc_package)
    copied = f"{pipeline.FORMAT_TOOLS_DIR}/package-lock.json"

    assert "sha256sum -c" in script, (
        "the format-md entrypoint installs from the lockfile in the mount "
        f"without checking it is the one stencil shipped (stn-qge):\n{script}"
    )

    check = script.index("sha256sum -c")
    assert script.index(f"cp {pipeline.FORMAT_LOCKFILE}") < check, (
        "the checksum is verified before the cp, so what it hashes is a file "
        "in the mount rather than the copy npm ci reads -- a host that rewrites "
        f"it between the two wins:\n{script}"
    )
    assert check < script.index("npm ci"), (
        f"npm ci runs before the lockfile is checked:\n{script}"
    )

    # And it hashes the copy, not the original: the same reason again, stated
    # where a reordering cannot quietly satisfy it. TWO SPACES between the hash
    # and the path, which is what GNU coreutils requires in text mode; busybox,
    # which is what the pinned Alpine image actually runs, accepts one space as
    # well. Writing the stricter of the two is free and keeps the line valid if
    # this ever runs anywhere but busybox -- so it is pinned here rather than
    # left to whoever next edits the format string.
    digest = pipeline.lockfile_digest(pipeline.FORMAT_LOCKFILE)
    assert f'"{digest}  {copied}"' in script, (
        "the checksum line does not name the copy in the two-space form "
        f"`<sha256>  <path>` that both sha256sum implementations read:\n{script}"
    )


def _guard_block(text: str, *, terminator: str = "fi &&") -> str:
    """The `if ! ... fi` a digest check lives in, lifted out of its surroundings.

    From the `if` to its `fi`, dropping whatever chains it to what follows --
    so what comes back is a complete shell command that can be run on its own.

    TWO CALLERS, TWO TERMINATORS, ONE HELPER (stn-egv). format-md's guard
    chains into its install, so it ends `fi &&`; the browser image's guard is
    the whole of its `RUN`, so it ends at a newline. A second copy of this
    function differing only in that string is not worth having.

    ``terminator`` MUST BE SPECIFIC ENOUGH NOT TO MATCH INSIDE A WORD, which
    is why it is not simply ``"fi"``. Both refusals say the word "file" -- more
    than once -- and ``text.index("fi", start)`` lands in the middle of the
    first one, returning a truncated block that is not valid shell. The
    executed polarity tests below would then fail on a syntax error rather
    than on the polarity they exist to check, which reads in a log like a
    broken guard rather than a broken test.
    """
    # The slice keeps the `fi` and drops whatever chains it onward, which only
    # works because every terminator BEGINS with `fi`. That coupling was
    # implicit while the length was hardcoded and the terminator was a
    # parameter; state it, so a caller passing `done` or `esac` fails here
    # rather than returning a block silently missing its last two characters.
    assert terminator.startswith("fi"), (
        f"terminator {terminator!r} does not start with 'fi', so the slice "
        "below would cut the block short"
    )
    start = text.index("if ! echo")
    end = text.index(terminator, start) + len("fi")
    return text[start:end]


def test_the_guard_refuses_on_mismatch_rather_than_on_match(doc_package, tmp_path):
    """The polarity, RUN rather than read -- and no container needed.

    Dropping one `!` inverts this guard: stencil's own lockfile is refused and
    a tampered one is installed from. Every other assertion in this file
    survives that edit, because `sha256sum -c` is still present, still between
    the cp and the npm ci, and still carrying the right digest and path. An
    adversarial review found it by mutating the template and watching this
    tier stay green.

    So the check is executed here against two files, which is what makes a
    reversed condition fail: the digest's own file must pass and a changed one
    must not. Only the path literal is substituted -- /tmp/fmt does not exist
    on the host and is not this test's to create -- so the `if !`, the
    pipeline, the redirection and the `exit 1` are the template's own text.

    THE CONTAINER TIER IS NOT A SUBSTITUTE, and neither is this for it. That
    tier runs the real busybox against the real service and skips where there
    is no compose; this runs the real shell condition anywhere sha256sum -c
    works, which includes CI. Both, because the failure this is about is a
    one-character regression in a file nobody runs locally.
    """
    script = _format_md_script(doc_package)
    block = _guard_block(script)

    target = tmp_path / "package-lock.json"
    shutil.copyfile(doc_package / pipeline.FORMAT_LOCKFILE, target)
    runnable = block.replace(f"{pipeline.FORMAT_TOOLS_DIR}/package-lock.json", str(target))
    assert str(target) in runnable, (
        f"the guard no longer names the copy it checks:\n{block}"
    )

    # PROBED SEPARATELY, and it has to be. Darwin's sha256sum takes no -c and
    # prints its usage; the guard sends both streams to /dev/null, so on that
    # host a missing -c and a refused lockfile are the same exit code and the
    # same silence. Asking the tool directly, outside the guard, is the only
    # way to tell "this host cannot run the check" from "the check says no".
    probe = subprocess.run(
        ["sh", "-c", f'echo "{hashlib.sha256(target.read_bytes()).hexdigest()}  '
         f'{target}" | sha256sum -c'],
        capture_output=True, text=True, timeout=60,
    )
    if probe.returncode != 0:
        pytest.skip(
            "sha256sum -c does not work here, so the guard cannot be executed "
            f"on this host: {(probe.stderr or probe.stdout).strip()[:200]}"
        )

    def run() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["sh", "-c", runnable], capture_output=True, text=True, timeout=60
        )

    accepted = run()
    assert accepted.returncode == 0, (
        "the guard refuses the lockfile whose digest it carries, so a package "
        f"stencil generated would not build:\n{accepted.stderr}"
    )

    target.write_bytes(target.read_bytes().replace(b"registry.npmjs.org", b"evil.invalid.host"))
    refused = run()
    assert refused.returncode != 0, (
        "the guard accepted a lockfile that is not the one it carries the "
        "digest of -- a dropped `!` reads exactly like this, and every other "
        f"assertion here survives it:\n{refused.stdout}{refused.stderr}"
    )
    assert "is not the file stencil generated" in refused.stderr, (
        f"it refused, but said nothing the reader can act on:\n{refused.stderr}"
    )


def test_the_guard_is_written_as_a_refusal(doc_package):
    """The same polarity, asserted textually, for where the test above skips.

    One character, and the executed test cannot run on a host whose sha256sum
    has no -c. This one runs everywhere and says the same thing about the
    shape: the condition is negated, so the branch that fires is the failure.
    """
    script = _format_md_script(doc_package)
    assert "if ! echo" in script, (
        "the digest guard is not written as `if ! echo ... | sha256sum -c`. If "
        "it was rewritten, make sure the new shape still refuses on MISMATCH "
        f"and update the executed test above with it:\n{script}"
    )

    # The other half of the polarity, and it was missing here too until stn-egv
    # went looking. A negated condition decides WHICH branch fires; `exit 1` is
    # what makes that branch mean anything. Mutate it to `exit 0` and the
    # service announces the refusal and then installs from the file anyway,
    # while every other assertion in this file stays green.
    assert "exit 1" in _guard_block(script), (
        "the guard's refusal branch does not exit non-zero, so a tampered "
        f"lockfile is announced and then installed from anyway:\n{script}"
    )


def test_the_format_md_entrypoint_stays_out_of_reach_of_compose_and_the_shell(
    doc_package,
):
    """Two characters this script must not contain, for two different reasons.

    ``$``: compose substitutes ``$VAR`` in a service definition before the
    shell ever sees it, which is why check-pdf doubles every ``$`` on its way
    into the same file. This entrypoint has never needed one, so the honest
    assertion is that it still has none -- a ``$`` added here without doubling
    reaches the shell as the empty string, and an empty string is how a guard
    stops guarding without anything failing.

    `` ` ``: backticks inside a double-quoted ``echo`` are command
    substitution, not quotation marks. Every message in this entrypoint is a
    double-quoted echo, and the prose in them wants to name commands --
    `stencil gen` is exactly the phrase someone reaches for backticks to set
    off. The file's own convention is single quotes for that, and this is what
    keeps it.
    """
    script = _format_md_script(doc_package)

    assert "$" not in script, (
        "a `$` reached the format-md entrypoint. compose interpolates it out "
        "before the shell sees it, so it must be doubled the way check-pdf's "
        f"script is -- and then this test updated deliberately:\n{script}"
    )
    assert "`" not in script, (
        "a backtick reached the format-md entrypoint. Inside the double-quoted "
        "echos here that is command substitution, so the message would run "
        f"what it meant to name. Use single quotes, as the rest do:\n{script}"
    )


def test_the_refusal_tells_the_consumer_what_to_do(doc_package):
    """A checksum mismatch answers nobody's question.

    Whoever hits this guard either edited the lockfile for a reason of their
    own or was handed a package by someone who did, and "sha256sum: FAILED"
    speaks to neither. The message has to say three things: the file is
    stencil's, editing it has no supported effect, and `stencil gen` puts it
    back. Asserted here rather than only in the container tier, which skips on
    every machine without a compose implementation -- and asserted against the
    parsed entrypoint, so the comment above the service cannot satisfy it.

    The existing presence guard's message is pinned the same way, one test
    below, and for the same reason.
    """
    script = _format_md_script(doc_package)
    refusal = script[script.index("sha256sum -c") :]

    assert "is not the file stencil generated" in refusal, (
        f"the refusal does not say what is wrong:\n{refusal}"
    )
    assert "no supported effect" in refusal, (
        "the refusal does not tell the reader that editing the lockfile is not "
        f"a supported thing to do, so they will try again:\n{refusal}"
    )
    assert "Run 'stencil gen'" in refusal, (
        f"the refusal does not say how to get back to a working package:\n{refusal}"
    )


def test_the_rendered_digest_is_the_digest_of_the_lockfile_in_the_package(doc_package):
    """The guard cannot go stale, because both come from the same bytes.

    A digest written down once and a lockfile re-vendored later is a guard that
    refuses every honest build -- the failure mode of every checksum kept by
    hand. ``pipeline.lockfile_digest`` hashes what ``read_lockfile`` returns
    plus the newline the template restores, which is exactly the file
    ``stencil gen`` writes; this asserts that against the file in a real
    generated package rather than against the constant it was computed from.
    """
    script = _format_md_script(doc_package)
    digests = set(_SHA256_HEX.findall(script))
    assert len(digests) == 1, (
        f"expected exactly one sha256 in the entrypoint, found {sorted(digests)}"
    )

    emitted = (doc_package / pipeline.FORMAT_LOCKFILE).read_bytes()
    assert digests == {hashlib.sha256(emitted).hexdigest()}, (
        "the digest rendered into the entrypoint is not the digest of the "
        "lockfile rendered beside it, so `make format-md` refuses a package "
        "stencil itself generated"
    )
    assert digests == {pipeline.lockfile_digest(pipeline.FORMAT_LOCKFILE)}


# ---------------------------------------------------------------------------
# the same guard, one service over and one phase earlier (stn-egv)


def _browser_guard(package) -> str:
    """The digest guard's `RUN`, lifted out of the generated Dockerfile.browser.

    Read from the GENERATED file, never from the template, for the reason every
    other assertion in this module is: a template's `{{ browser_lockfile_digest }}`
    satisfies a substring check while saying nothing about what the
    interpolation produced, and the digest is the entire point.
    """
    dockerfile = (package / pipeline.BROWSER_DOCKERFILE).read_text()
    return _guard_block(dockerfile, terminator="fi\n")


def test_the_browser_image_checks_the_lockfile_before_installing_from_it(doc_package):
    """stn-egv: stn-qge's defect, at image BUILD time instead of run time.

    `npm ci` fetches whatever host each `resolved` names and checks `integrity`
    against a value in that same file, so whoever can edit the lockfile decides
    which bytes become the puppeteer, pa11y and pdf-lib this image runs as uid 0
    over the mounted package. The ticket reproduced it: one `resolved` host
    changed, and npm requested that host.

    THE ORDER IS THE PROPERTY, and it is reached differently here than in
    format-md. There the guard had to verify the COPY rather than the original
    because the source sits in a bind mount the host can rewrite between the
    check and the `cp`. Here there is no such window to close: a `RUN` cannot
    read the build context at all, and `COPY` has already snapshotted the bytes
    into a layer nothing outside the build can reach. The check still goes after
    the COPY and before the install, for the same reason arrived at from the
    other side -- what is hashed is exactly what npm reads.
    """
    dockerfile = (doc_package / pipeline.BROWSER_DOCKERFILE).read_text()
    copied = f"{pipeline.BROWSER_TOOLS_DIR}/package-lock.json"

    assert "sha256sum -c" in dockerfile, (
        "the browser image installs from the lockfile in the package directory "
        f"without checking it is the one stencil shipped (stn-egv):\n{dockerfile}"
    )

    # EACH ANCHOR IS THE WHOLE INSTRUCTION, NOT A PHRASE INSIDE IT. The comment
    # block above the guard necessarily discusses `npm ci` -- it explains why
    # the guard is not folded into that line -- and a bare `index("npm ci")`
    # finds the COMMENT, which sits before the guard, and reports the ordering
    # backwards. That is the same trap code_lines() exists for at the top of
    # this file, and it fired here for real on the first green run.
    install = f"RUN cd {pipeline.BROWSER_TOOLS_DIR} && npm ci"
    copy = f"COPY {pipeline.BROWSER_LOCKFILE} {copied}"
    for anchor in (install, copy):
        assert anchor in dockerfile, (
            f"Dockerfile.browser no longer contains {anchor!r}, so the ordering "
            f"below is comparing against something else:\n{dockerfile}"
        )

    check = dockerfile.index("sha256sum -c")
    assert dockerfile.index(copy) < check, (
        "the checksum is verified before the COPY, which is not a thing a RUN "
        "can do -- it would be hashing a path that does not exist in the image "
        f"yet:\n{dockerfile}"
    )
    assert check < dockerfile.index(install), (
        f"npm ci runs before the lockfile is checked:\n{dockerfile}"
    )

    # TWO SPACES between the hash and the path. Measured on the pinned image,
    # busybox 1.37.0 accepts one space as well -- but GNU coreutils requires two
    # in text mode, and writing the stricter of the two is free. Pinned here
    # rather than left to whoever next edits the format string.
    digest = pipeline.lockfile_digest(pipeline.BROWSER_LOCKFILE)
    assert f'"{digest}  {copied}"' in dockerfile, (
        "the checksum line does not name the copy in the two-space form "
        f"`<sha256>  <path>` that both sha256sum implementations read:\n{dockerfile}"
    )


def test_the_browser_guard_refuses_on_mismatch_rather_than_on_match(
    doc_package, tmp_path
):
    """The polarity, RUN rather than read -- and no container needed.

    Dropping one `!` inverts this guard: stencil's own lockfile is refused and a
    tampered one is installed from. Every other assertion in this file survives
    that edit, because `sha256sum -c` is still present, still between the COPY
    and the npm ci, and still carrying the right digest and path. The same
    mutation went undetected in the format-md tier until an adversarial review
    made it by hand.

    THE CONTAINER TIER IS NOT A SUBSTITUTE, and neither is this for it. That
    tier builds the real image and skips where there is no compose; this runs
    the real shell condition anywhere sha256sum works, which includes CI's unit
    job. Both, because the failure this is about is a one-character regression
    in a file nobody runs locally.
    """
    block = _browser_guard(doc_package)

    target = tmp_path / "package-lock.json"
    shutil.copyfile(doc_package / pipeline.BROWSER_LOCKFILE, target)
    runnable = block.replace(
        f"{pipeline.BROWSER_TOOLS_DIR}/package-lock.json", str(target)
    )
    assert str(target) in runnable, (
        f"the guard no longer names the copy it checks:\n{block}"
    )

    # PROBED SEPARATELY. Darwin's sha256sum takes no -c and prints its usage, so
    # on that host "this machine cannot run the check" and "the check says no"
    # are both a non-zero exit. Asking the tool directly, outside the guard, is
    # the only way to tell them apart.
    probe = subprocess.run(
        [
            "sh",
            "-c",
            f'echo "{hashlib.sha256(target.read_bytes()).hexdigest()}  '
            f'{target}" | sha256sum -c',
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if probe.returncode != 0:
        pytest.skip(
            "sha256sum -c does not work here, so the guard cannot be executed "
            f"on this host: {(probe.stderr or probe.stdout).strip()[:200]}"
        )

    def run() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["sh", "-c", runnable], capture_output=True, text=True, timeout=60
        )

    accepted = run()
    assert accepted.returncode == 0, (
        "the guard refuses the lockfile whose digest it carries, so a package "
        f"stencil generated would not build:\n{accepted.stderr}"
    )

    # Asserted, because `bytes.replace` of an absent needle is a silent no-op:
    # if the vendored lockfile ever stops naming this host, the "tampered" file
    # below would be byte-identical to the good one and the refusal check would
    # fail as "the guard accepted a tampered lockfile" -- a confusing way to
    # learn the fixture went stale.
    good = target.read_bytes()
    target.write_bytes(good.replace(b"registry.npmjs.org", b"evil.invalid.host"))
    assert target.read_bytes() != good, (
        "the fixture edits the vendored lockfile by replacing its registry "
        "host, and that host no longer appears in it, so nothing was tampered"
    )
    refused = run()
    assert refused.returncode != 0, (
        "the guard accepted a lockfile that is not the one it carries the "
        "digest of -- a dropped `!` reads exactly like this, and every other "
        f"assertion here survives it:\n{refused.stdout}{refused.stderr}"
    )
    assert "is not the file stencil generated" in refused.stderr, (
        f"it refused, but said nothing the reader can act on:\n{refused.stderr}"
    )


def test_the_browser_guard_lets_sha256sum_say_why_it_failed(doc_package):
    """`>/dev/null` WITHOUT `2>&1`, and the difference is the consumer's morning.

    Measured in the pinned image (busybox 1.37.0, /usr/bin/sha256sum ->
    /bin/busybox), the four ways this line can end:

        match           'path: OK' on stdout                          exit 0
        mismatch        'WARNING: 1 of 1 ... did NOT match' on stderr exit 1
        file absent     "can't open 'path'" on stderr                 exit 1
        sha256sum gone  'sh: sha256sum: not found' on stderr          exit 127

    All four are fail-closed -- verified with PATH=/nonexistent, the `if !`
    branch fires -- so the guard never passes for the wrong reason. But `2>&1`
    would discard the one line that says WHICH, and then a base-image regression
    that broke sha256sum would tell the consumer their lockfile is not the file
    stencil generated and send them to re-run `stencil gen` forever over a file
    that was correct all along.

    `>/dev/null` alone keeps success silent -- the `OK` goes to stdout -- and
    puts the real cause on stderr immediately above stencil's own explanation.
    It leaks nothing: that stderr line names no digest.

    stn-jjw is the same fix for format-md, whose guard still carries `2>&1`.
    """
    block = _browser_guard(doc_package)

    assert ">/dev/null" in block, (
        "the guard no longer silences sha256sum's success line, so every build "
        f"prints a checksum result nobody asked for:\n{block}"
    )
    assert "2>&1" not in block, (
        "the guard sends sha256sum's stderr to /dev/null, so a missing or "
        "broken sha256sum is reported to the consumer as a tampered lockfile "
        f"and 'stencil gen' will never fix it (stn-jjw):\n{block}"
    )


def test_the_browser_guard_is_written_as_a_refusal(doc_package):
    """The same polarity, asserted textually, for where the test above skips.

    One character, and the executed test cannot run on a host whose sha256sum
    has no -c -- Darwin's has none, so on a maintainer's laptop this tier is the
    ONLY thing standing between a mutated guard and a green `pytest`.

    BOTH ASSERTIONS ARE OVER SOMETHING THE HELPER DID NOT ALREADY GUARANTEE,
    which the first version of this test got wrong and an adversarial review
    caught. `_browser_guard` slices from `text.index("if ! echo")`, so
    `block.startswith("if ! echo")` is true by construction and can never fail;
    it looked like the format-md sibling's genuine `"if ! echo" in script` while
    asserting nothing. Search the FILE for the shape, the way that sibling does.

    And the negation is only half the polarity. Measured: mutating the rendered
    `exit 1` to `exit 0` leaves the guard printing its refusal and then
    installing anyway -- and every other assertion in this group passes, because
    the wording, the digest, the ordering, the character exclusions and the echo
    count are all untouched by it. Nothing in the unit tier caught that. The
    `exit 1` assertion is what closes it, and the format-md guard has the same
    hole, so it is asserted there too.
    """
    dockerfile = (doc_package / pipeline.BROWSER_DOCKERFILE).read_text()
    assert "if ! echo" in dockerfile, (
        "the digest guard is not written as `if ! echo ... | sha256sum -c`. If "
        "it was rewritten, make sure the new shape still refuses on MISMATCH "
        f"and update the executed test above with it:\n{dockerfile}"
    )

    block = _browser_guard(doc_package)
    assert "exit 1" in block, (
        "the guard's refusal branch does not exit non-zero, so a tampered "
        "lockfile is announced and then installed from anyway -- and the build "
        f"goes on to succeed:\n{block}"
    )


def test_the_browser_refusal_tells_the_consumer_what_to_do(doc_package):
    """A checksum mismatch answers nobody's question.

    Whoever hits this guard either edited the lockfile for a reason of their own
    or was handed a package by someone who did, and "sha256sum: FAILED" speaks
    to neither. The message has to say three things: the file is stencil's,
    editing it has no supported effect, and `stencil gen` puts it back.

    Asserted against the lifted block rather than the whole file. The comment
    above the RUN happens to contain none of these three phrases today -- so
    this is insurance rather than an active trap, and saying it is one would be
    a measured-sounding claim nobody measured. What it does buy is that a future
    comment explaining the refusal, which is the natural thing to write there,
    cannot start satisfying the test on the message's behalf.
    """
    block = _browser_guard(doc_package)

    assert "is not the file stencil generated" in block, (
        f"the refusal does not say what is wrong:\n{block}"
    )
    assert "no supported effect" in block, (
        "the refusal does not tell the reader that editing the lockfile is not "
        f"a supported thing to do, so they will try again:\n{block}"
    )
    assert "Run 'stencil gen'" in block, (
        f"the refusal does not say how to get back to a working package:\n{block}"
    )


def test_the_rendered_browser_digest_is_the_digest_of_the_lockfile_in_the_package(
    doc_package,
):
    """The guard cannot go stale, because both come from the same bytes.

    A digest written down once and a lockfile re-vendored later is a guard that
    refuses every honest build -- the failure mode of every checksum kept by
    hand. ``pipeline.lockfile_digest`` hashes what ``read_lockfile`` returns plus
    the newline the template restores, which is exactly the file ``stencil gen``
    writes; this asserts that against the file in a real generated package.

    SCANNED OVER THE GUARD BLOCK, NOT THE FILE, and that is not tidiness. Since
    stn-8vi the `FROM` line carries pipeline.NODE_IMAGE's own `@sha256:<64 hex>`
    digest, so a whole-file scan finds two and the "exactly one" assertion that
    works for format-md's entrypoint fails here for a reason that has nothing to
    do with this guard. The second half of this test pins that, so nobody
    "simplifies" the scope back out.
    """
    block = _browser_guard(doc_package)
    digests = set(_SHA256_HEX.findall(block))
    assert len(digests) == 1, (
        f"expected exactly one sha256 in the guard, found {sorted(digests)}"
    )

    emitted = (doc_package / pipeline.BROWSER_LOCKFILE).read_bytes()
    assert digests == {hashlib.sha256(emitted).hexdigest()}, (
        "the digest rendered into the guard is not the digest of the lockfile "
        "rendered beside it, so `make pdf` refuses a package stencil itself "
        "generated"
    )
    assert digests == {pipeline.lockfile_digest(pipeline.BROWSER_LOCKFILE)}

    # Why the scan above is scoped: the file as a whole carries the base image's
    # digest too. If this ever stops being true, the scoping is free to relax --
    # but it must be a deliberate edit, not a silent one.
    whole = set(_SHA256_HEX.findall((doc_package / pipeline.BROWSER_DOCKERFILE).read_text()))
    assert len(whole) > 1, (
        "Dockerfile.browser now carries exactly one sha256, so this test no "
        "longer demonstrates why the scan is scoped to the guard block. Check "
        "whether pipeline.NODE_IMAGE still pins a digest (stn-8vi) before "
        f"loosening anything: {sorted(whole)}"
    )


def test_the_browser_guard_stays_out_of_reach_of_the_dockerfile_parser_and_the_shell(
    doc_package,
):
    """Three characters this block must not contain, for three different reasons.

    ``$``: a Dockerfile `RUN` expands `$VAR` from ENV and ARG before /bin/sh ever
    sees the line, and this image sets NODE_PATH, PATH, PUPPETEER_* and
    NPM_CONFIG_UPDATE_NOTIFIER above it. An unintended expansion to the empty
    string is how a guard stops guarding with nothing failing. Scoped to the
    block, because the file legitimately carries `$PATH` in its ENV lines.

    `` ` ``: backticks inside a double-quoted `echo` are command substitution,
    not quotation marks -- and the prose here wants to name commands, which is
    exactly where someone reaches for them. The file's convention is single
    quotes; this is what keeps it.

    ``#``: THE ONE THAT IS SPECIFIC TO A DOCKERFILE. The parser strips a comment
    line inside a `\\`-continued instruction; /bin/sh does not. Measured:

        RUN echo "one" && \\
        # a comment
            echo "two"

    builds as `RUN echo "one" &&     echo "two"` with no warning at all. So a
    comment placed inside this block -- or an `echo` line that happens to begin
    with `#` -- vanishes from the built command silently, and the polarity test
    above would then be lifting a truncated guard out of the file and reporting
    whatever it did as the guard's behaviour. Keep the commentary ABOVE the RUN.

    The stderr assertion is the other half: every line of the refusal must reach
    stderr, so a message half-redirected to stdout cannot pass while a consumer
    sees nothing on a failed build.
    """
    block = _browser_guard(doc_package)

    assert "$" not in block, (
        "a `$` reached the browser guard. A Dockerfile RUN expands it from ENV "
        "or ARG before the shell sees it, so it must be escaped and then this "
        f"test updated deliberately:\n{block}"
    )
    assert "`" not in block, (
        "a backtick reached the browser guard. Inside the double-quoted echos "
        "here that is command substitution, so the message would run what it "
        f"meant to name. Use single quotes, as the rest do:\n{block}"
    )

    inside = [line.strip() for line in block.splitlines()]
    assert not [line for line in inside if line.startswith("#")], (
        "a comment line sits inside the guard's RUN. The Dockerfile parser "
        "deletes it and /bin/sh never sees it, so the built command is not the "
        f"one written here. Put the commentary above the RUN:\n{block}"
    )

    echoes = [line for line in inside if line.startswith("echo ")]
    assert len(echoes) >= 4, (
        "the refusal is down to fewer than four lines, which is not enough to "
        "say the file is stencil's, that editing it has no supported effect, "
        f"and how to restore it:\n{block}"
    )
    assert all(">&2" in line for line in echoes), (
        "a line of the refusal does not redirect to stderr, so it lands on "
        f"stdout where a failed build's reader is not looking:\n{block}"
    )


def test_lockfile_digest_hashes_the_file_the_package_gets(tmp_path, monkeypatch):
    """The helper's own contract, including the failure channel it inherits.

    It hashes ``read_lockfile(...) + "\n"`` rather than the bytes on disk, so
    the two cannot disagree about the trailing newline -- and it raises
    VendoredAssetError, not ValueError, for a damaged install, for the reason
    stn-hwo gives at read_lockfile.
    """
    monkeypatch.setattr(pipeline, "ASSETS_DIR", tmp_path)
    (tmp_path / "y.json").write_text("{}\n")
    assert pipeline.lockfile_digest("y.json") == hashlib.sha256(b"{}\n").hexdigest()

    with pytest.raises(pipeline.VendoredAssetError, match="vendor_npm_locks"):
        pipeline.lockfile_digest("absent.json")

    (tmp_path / "z.json").write_text("{}\n\n")
    with pytest.raises(pipeline.VendoredAssetError, match="exactly one newline"):
        pipeline.lockfile_digest("z.json")


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


def test_the_format_lockfile_is_emitted_with_no_predicate_at_all(generate_package):
    """Even for a package that renders no compose file today.

    Three predicates were tried and each left a case behind: has_pages missed a
    package whose config-level `templates:` produces a compose file anyway; the
    compose file's own name missed a renamed `dest:`; and adding the
    conventional spellings still missed a consumer whose composition template
    has a name of its own and pulls the partial in by include -- which nothing
    outside a template body can see.

    So there is no predicate. The file is 1.3 KB, `stencil clean` removes it and
    the managed .gitignore covers it; the cost of shipping it to a package that
    does not use it is a small unused file, against a `make format-md` that
    fails for a consumer who did nothing wrong.
    """
    package = generate_package(
        {
            "templates": [{"src": "Makefile.j2"}],
            "packages": {
                "demo": {"name": "Demo", "package_type": "none", "docs": ["Guide.md"]}
            },
        }
    )
    assert (package / pipeline.FORMAT_LOCKFILE).is_file()
    # The browser lockfile still has one, because it pairs with a Dockerfile
    # that COPYs it: a package with one and not the other cannot build.
    assert (package / pipeline.BROWSER_LOCKFILE).is_file()


def test_a_package_that_renders_nothing_still_gets_no_browser_lockfile(
    generate_package,
):
    """The browser lockfile is not unconditional, and the asymmetry is the
    point: it exists to be COPYed by Dockerfile.browser, which is emitted only
    for a package that renders markdown."""
    package = generate_package(
        {
            "templates": [{"src": "Makefile.j2"}],
            "packages": {"demo": {"name": "Demo", "package_type": "none"}},
        }
    )
    assert not (package / "Dockerfile.browser").exists()
    assert not (package / pipeline.BROWSER_LOCKFILE).exists()
    assert (package / pipeline.FORMAT_LOCKFILE).is_file()


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
        name = path.rsplit("node_modules/", 1)[1]
        stem = name.rsplit("/", 1)[-1]
        # THE WHOLE URL, not a prefix and a suffix. A fragment is not sent in
        # the request, so `https://evil.example/x.tgz#/pdf-lib/-/pdf-lib-1.17.1.tgz`
        # satisfies a startswith on the registry only if the host matches -- but
        # a registry URL for a DIFFERENT tarball can carry the expected path in
        # its fragment and satisfy an endswith while npm fetches something else.
        # npm checks the bytes against `integrity`; it never checks that the URL
        # names the package the lockfile key claims.
        expected = f"https://registry.npmjs.org/{name}/-/{stem}-{entry['version']}.tgz"
        assert url == expected, (
            f"{filename}: {path} claims version {entry['version']}, which is "
            f"served at {expected}, but the lockfile fetches {url}"
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
    somewhere far from the cause.

    BOTH failures raise VendoredAssetError, and the TYPE is load-bearing
    (stn-hwo). This test used to assert ValueError for the malformed case,
    which is the channel generate.package_contexts collects config problems
    on -- so a damaged install was reported to the reader as a broken
    .config.yaml. It is not a ValueError subclass for exactly that reason.
    """
    monkeypatch.setattr(pipeline, "ASSETS_DIR", tmp_path)
    (tmp_path / "x.json").write_text("{}\n\n")
    with pytest.raises(pipeline.VendoredAssetError, match="exactly one newline"):
        pipeline.read_lockfile("x.json")

    (tmp_path / "y.json").write_text("{}\n")
    assert pipeline.read_lockfile("y.json") == "{}"

    with pytest.raises(pipeline.VendoredAssetError, match="vendor_npm_locks"):
        pipeline.read_lockfile("absent.json")

    assert not issubclass(pipeline.VendoredAssetError, ValueError), (
        "VendoredAssetError must not travel on the config-problem channel"
    )


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


# ---------------------------------------------------------------------------
# stn-cnm: html-to-pdf.js must resolve puppeteer and pdf-lib only from
# pipeline.BROWSER_TOOLS_DIR, never from a node_modules a consumer's own npm
# install left in /workspace. Beside the installed-tree assertions above
# because it needs the same built image; NEVER planted inside pdf_workspace
# itself -- the PROBE script above opens with a bare require("pa11y"), so a
# decoy there would corrupt every assertion `installed` makes, not merely
# these.

DECOY_MARKERS = {
    "puppeteer": "STN_CNM_DECOY_PUPPETEER_7f2a",
    "pdf-lib": "STN_CNM_DECOY_PDF_LIB_9c3b",
}

# A phrase only the guard's own message carries. Node's raw MODULE_NOT_FOUND
# names the tools directory too -- via its "Require stack:" -- so the
# directory alone cannot tell the guard firing apart from the guard being
# absent. See test_the_missing_tools_guard_names_the_pinned_dir.
GUARD_PHRASE = "installed the tools somewhere other than"


def _copy_rendered_page(pdf_workspace, dest):
    """document.html and html-to-pdf.js, copied out of pdf_workspace.

    Generated pages are self-contained -- assets are inlined at `stencil gen`
    time -- so these two files are everything the pdf service needs, and
    nothing else about pdf_workspace (in particular its own decoy-free
    node_modules layout) is disturbed by whatever gets planted in ``dest``.
    """
    shutil.copy2(pdf_workspace / "document.html", dest / "document.html")
    shutil.copy2(pdf_workspace / "html-to-pdf.js", dest / "html-to-pdf.js")


def _plant_decoy(workdir, name, marker):
    """A node_modules/<name> a bare `require(name)` from /workspace would
    reach, whose module body THROWS at require time rather than on first use.

    The shape is load-bearing, not incidental. Node's resolution algorithm
    treats a directory it cannot load as a package (no `main`, no
    `index.js` it can find) as ABSENT and CONTINUES to the next candidate
    rather than raising -- so a decoy missing either of these would be
    silently skipped in favour of NODE_PATH, and the acceptance test below
    would pass vacuously against unfixed code rather than because the fix
    works.
    """
    pkg_dir = workdir / "node_modules" / name
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text(
        json.dumps({"name": name, "version": "0.0.0-decoy", "main": "index.js"})
    )
    (pkg_dir / "index.js").write_text(f'throw new Error("{marker}");\n')


@pytest.fixture(scope="session")
def rendered_pdf_page(pdf_workspace):
    """document.html, rendered once into the shared pdf_workspace.

    Exactly what the `installed` fixture above does at test_pins.py:809 --
    pipeline.render("doc", ...) over the same session-scoped workspace pytest
    already paid to build the browser image for.
    """
    built = pipeline.render(
        "doc", "document.md", "document.html", workdir=pdf_workspace
    )
    assert built.returncode == 0, f"pandoc failed\n{built.stderr}"
    return pdf_workspace


@pytest.fixture(scope="session")
def decoy_tools_workdir(rendered_pdf_page, tmp_path_factory):
    """The real generated html-to-pdf.js and a real rendered page, in an
    isolated directory of its own carrying decoy node_modules/{puppeteer,
    pdf-lib} -- never inside pdf_workspace itself, per the module comment
    above. Session-scoped so the cost is paid once.
    """
    workdir = tmp_path_factory.mktemp("tools-resolution-decoy")
    _copy_rendered_page(rendered_pdf_page, workdir)
    for name, marker in DECOY_MARKERS.items():
        _plant_decoy(workdir, name, marker)
    return workdir


@pytest.fixture(scope="session")
def bare_tools_workdir(rendered_pdf_page, tmp_path_factory):
    """The same two real generated files, with NO node_modules planted at
    all -- kept separate from decoy_tools_workdir so
    test_the_missing_tools_guard_names_the_pinned_dir is not also, silently,
    a test about the decoy fixture above.
    """
    workdir = tmp_path_factory.mktemp("tools-resolution-bare")
    _copy_rendered_page(rendered_pdf_page, workdir)
    return workdir


@pytest.mark.integration
def test_html_to_pdf_ignores_a_decoy_in_the_workspace(decoy_tools_workdir):
    """ACCEPTANCE for stn-cnm. Same image, same mounts and same entrypoint as
    the generated pdf compose service, over a directory that also carries decoy
    node_modules/{puppeteer,pdf-lib}, planted above.

    A decoy /workspace/node_modules/puppeteer with a wrong version must not
    be used by make pdf: exit 0, a PDF actually written, and neither decoy's
    marker anywhere in stderr.

    WHAT THIS STOPPED BEING ABLE TO PROVE, said plainly rather than left for a
    reader to discover. Since stn-7ki the script runs from
    pipeline.BROWSER_SCRIPT_PATH, inside the image -- so module resolution
    starts at /opt/tools and a decoy at /workspace/node_modules is out of reach
    of a BARE require as well as a rooted one. This test would now pass with
    the createRequire guard deleted, which it would not have before.

    It is kept, and it is not the guard's test any more. WHAT HOLDS stn-cnm NOW
    IS THE FAST, STATIC TIER IN THIS SAME FILE, by name so a reader can go and
    check rather than take this on trust:

    - test_html_to_pdf_js_roots_its_resolution_at_the_pinned_tools_dir -- the
      createRequire needle and the resolved-path startsWith check;
    - test_no_generated_js_bare_requires_a_pinned_browser_package -- the scan
      over every generated .js;
    - test_the_missing_tools_guard_names_the_pinned_dir -- the one runtime test
      that still proves the guard FIRES, which it does by running the script
      under a plain node image with no pipeline.BROWSER_TOOLS_DIR at all.

    None of those is touched by the move and all stayed exactly as strict. The
    first two run with no container runtime, so unlike this test they cannot be
    skipped into silence. What this one still buys is a runtime proof that the
    PINNED tree is what actually launches Chromium and writes the PDF, in a
    directory built to tempt it otherwise: defence in depth, for one container
    minute. Do not read its green as evidence about the guard.
    """
    result = pipeline.html_to_pdf(
        "document.html",
        "document.pdf",
        workdir=decoy_tools_workdir,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"html-to-pdf.js exited {result.returncode} instead of 0\n"
        f"stderr:\n{result.stderr[-3000:]}"
    )
    assert (decoy_tools_workdir / "document.pdf").is_file(), (
        "html-to-pdf.js exited 0 but wrote no document.pdf"
    )
    for name, marker in DECOY_MARKERS.items():
        assert marker not in result.stderr, (
            f"the {name} decoy's marker appeared in stderr, so the decoy "
            f"ran instead of the pinned tree at {pipeline.BROWSER_TOOLS_DIR}:\n"
            f"{result.stderr[-3000:]}"
        )


@pytest.mark.integration
def test_a_workspace_decoy_would_win_a_bare_require(decoy_tools_workdir):
    """CONTROL, and it must assert the RIGHT half.

    IT CONTROLS FOR A LOCATION THE SCRIPT NO LONGER OCCUPIES. This runs through
    pipeline.run_in_browser, which writes its script INTO the workdir, so it
    proves what a bare require reaching out of /workspace would find -- and
    since stn-7ki the pdf driver runs from pipeline.BROWSER_SCRIPT_PATH
    instead. Kept and relabelled rather than deleted: it is still the only
    thing in this file that demonstrates the decoy fixture is well-formed, and
    a malformed decoy is what would make the acceptance test above pass for the
    wrong reason. Read it as "the fixture is real", not as "the acceptance test
    discriminates" -- that second claim is gone, and the acceptance test's own
    docstring now says so.

    The acceptance test's load-bearing assertion is exit 0; "the marker is
    absent from stderr" is vacuous unless something proves the decoy WOULD
    have produced that marker. Node's resolution algorithm treats a
    directory it cannot load as a package as ABSENT and silently continues to
    the next candidate rather than erroring -- so a decoy fixture that is
    malformed in a way an implementer plausibly gets wrong on the first try
    (no `main`, no `index.js` Node can find) would be skipped in favour of
    NODE_PATH, and the acceptance test above would then pass vacuously
    against unfixed code. This control is what would catch that: it proves,
    in the same image and the same directory, that a bare `require` issued
    from /workspace really does reach the decoy, before trusting the
    acceptance test's silence about it.

    pdf-lib gets its own assertion here rather than "the same as puppeteer by
    symmetry": both decoys throw at module load and html-to-pdf.js's
    puppeteer require comes first, so in the red state the script dies on
    puppeteer and never reaches its pdf-lib require at all -- pdf-lib's
    exposure would otherwise go completely unmeasured by the acceptance test,
    and pdf-lib is the more damaging hijack of the two: it is what writes the
    PDF/UA role map `make check-pdf` exists to require.
    """
    script = """
const results = {};
for (const name of ["puppeteer", "pdf-lib"]) {
  const entry = { resolved: require.resolve(name) };
  try {
    require(name);
    entry.threw = false;
  } catch (error) {
    entry.threw = true;
    entry.message = String(error.message);
  }
  results[name] = entry;
}
console.log("<<<CONTROL>>>" + JSON.stringify(results));
"""
    result = pipeline.run_in_browser(script, workdir=decoy_tools_workdir, timeout=120)

    marker = "<<<CONTROL>>>"
    line = next(
        (line for line in result.stdout.splitlines() if line.startswith(marker)), None
    )
    assert line is not None, (
        f"the control script printed no result (exit {result.returncode})\n"
        f"stdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
    )
    results = json.loads(line[len(marker):])

    for name, expected_marker in DECOY_MARKERS.items():
        entry = results[name]
        assert entry["threw"], (
            f"require({name!r}) issued from /workspace did not throw at all; "
            f"the decoy fixture is malformed (Node treated it as absent "
            f"rather than as a package), so the acceptance test would pass "
            f"vacuously against unfixed code"
        )
        assert expected_marker in entry["message"], (
            f"require({name!r}) threw {entry['message']!r}, which does not "
            f"contain {expected_marker!r} -- something other than the "
            f"planted decoy ran"
        )
        assert entry["resolved"].startswith("/workspace"), (
            f"require.resolve({name!r}) resolved to {entry['resolved']!r}, "
            f"not under /workspace -- the decoy would not have been reached "
            f"by a bare require issued from there"
        )


@pytest.mark.integration
def test_the_missing_tools_guard_names_the_pinned_dir(bare_tools_workdir):
    """THE GUARD MUST BE PROVEN TO FIRE. AGENTS.md is explicit that a guard
    which silently does not run is worse than no guard at all
    (tests/test_export_drift.py exists for exactly that reason), and nothing
    else in this file would notice if the resolution guard were ever deleted.

    THE TWO ASSERTIONS BELOW ARE BOTH DISCRIMINATORS, AND NEITHER IS
    NEGOTIABLE. The obvious pair -- "exited non-zero" and "stderr names the
    tools directory" -- does NOT test the guard, measured: with the guard
    block deleted from the rendered script, the UNGUARDED require throws
    Node's own

        Error: Cannot find module 'puppeteer'
        Require stack:
        - /opt/tools/package.json

    which exits 1 and names /opt/tools twice, satisfying both. What separates
    the guard from the raw MODULE_NOT_FOUND is the exit code it chooses (2,
    matching the usage path, where an uncaught throw gives 1) and a phrase
    only the guard's own message contains. Assert those, or this test is
    measuring the createRequire root that the fast tier already covers.

    pipeline.NODE_IMAGE is the plain node base image the generated
    Dockerfile.browser starts FROM, before anything under
    pipeline.BROWSER_TOOLS_DIR is installed -- so running html-to-pdf.js
    there, over the same kind of mount the pdf service uses, is "somebody ran
    `node html-to-pdf.js` on their laptop" made real rather than simulated.
    One container invocation; it builds no second image, only running the
    public tag pipeline.NODE_IMAGE already names (already pulled locally, as
    the base layer of the browser image pdf_workspace built).
    """
    result = pipeline.html_to_pdf(
        "document.html",
        "document.pdf",
        workdir=bare_tools_workdir,
        # THE MOUNTED COPY, EXPLICITLY, and this is the one caller that wants
        # it. Since stn-7ki the pdf service runs the copy BAKED into the
        # browser image at pipeline.BROWSER_SCRIPT_PATH -- but the whole point
        # of this test is a plain node image, which has no
        # pipeline.BROWSER_TOOLS_DIR and therefore no baked script either.
        # Left to the default, this would exit 1 with Node's own
        # `Cannot find module '/opt/tools/html-to-pdf.js'` and the assertions
        # below would be measuring the absence of a file rather than the guard
        # they are named for. bare_tools_workdir puts the real generated script
        # in the mount for exactly this.
        script="/workspace/html-to-pdf.js",
        tag=pipeline.NODE_IMAGE,
        timeout=60,
    )
    assert result.returncode == 2, (
        "html-to-pdf.js did not refuse through its own guard under a plain "
        f"node image with no {pipeline.BROWSER_TOOLS_DIR} at all. Exit 2 is "
        "the guard (and the usage path); exit 1 is an uncaught throw, which "
        "is what an UNGUARDED require produces here -- so exit 1 means the "
        f"guard is gone, not that it fired.\nexit: {result.returncode}\n"
        f"stderr: {result.stderr[-2000:]}"
    )
    assert GUARD_PHRASE in result.stderr, (
        f"the failure did not carry the guard's own message ({GUARD_PHRASE!r}). "
        "Node's raw MODULE_NOT_FOUND also names "
        f"{pipeline.BROWSER_TOOLS_DIR}, via its 'Require stack', so naming "
        "the directory proves nothing on its own.\n"
        f"stderr: {result.stderr[-2000:]}\nstdout: {result.stdout[-2000:]}"
    )
    assert pipeline.BROWSER_TOOLS_DIR in result.stderr, (
        f"the failure did not name {pipeline.BROWSER_TOOLS_DIR}:\n"
        f"stderr: {result.stderr[-2000:]}\nstdout: {result.stdout[-2000:]}"
    )
