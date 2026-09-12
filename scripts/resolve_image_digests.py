#!/usr/bin/env python3
"""Resolve the image tags in stencil/pipeline.py into a manifest digest each.

Run from the repo root after bumping a tag in ``pipeline.IMAGE_TAGS``:

    python3 scripts/resolve_image_digests.py

The result lands in stencil/assets/image-digests.json. Commit it in the same
commit as the tag bump -- pipeline.pinned_image() refuses a tag with no entry,
so a bump without a re-resolve is a loud, offline failure rather than a build
that silently keeps the old image.

This is scripts/vendor_page_assets.py's shape -- urllib, a maintainer runs it
once with the network, the artifact is committed -- but HARDENED, because
unlike a CDN asset this script is the step that ESTABLISHES TRUST. What it
writes is pinned permanently and rendered into a Makefile, a compose file and
a Dockerfile FROM line for every package stencil generates from here on.

THE THREAT THIS IS HARDENED AGAINST, verified against the CPython source on
this machine rather than assumed:

- ``urllib.request.HTTPRedirectHandler.CONTENT_HEADERS`` strips only
  Content-Length and Content-Type from a redirected request. Authorization --
  the anonymous pull token this script sends -- is forwarded VERBATIM to
  whatever host a redirect names.
- Its scheme guard is ``if urlparts.scheme not in ('http', 'https', 'ftp',
  '')``, so an https-→http downgrade redirect is followed and the manifest
  arrives in cleartext. ``max_redirections`` is 10.
- So a compromised Hub edge, a TLS-terminating proxy, or a poisoned resolver
  can answer with ``307 -> http://attacker/``, and the attacker then supplies
  both the body AND a Docker-Content-Digest header that matches it. A
  "header == sha256(body)" check passes cleanly while this happens -- that
  check is a transport-integrity checksum, not an authenticity control. Once
  a wrong-but-internally-consistent digest is committed, the pin makes the
  compromise MORE durable than the mutable tag it replaced, because nothing
  after this script ever re-fetches the tag to notice it moved.

WHAT THIS SCRIPT DOES ABOUT IT, each measured on this machine rather than
assumed:

- A private opener refuses every redirect outright (redirect_request returns
  None). MEASURED: a Hub manifest GET does not redirect today, so refusing
  costs nothing.
- ``ProxyHandler({})`` in the same opener, so a configured HTTPS_PROXY is not
  silently trusted with the token and the manifest body.
- An explicit ``ssl.create_default_context()`` via HTTPSHandler, and https is
  asserted on every URL this script BUILDS -- never on one read from a
  response, a header, or a WWW-Authenticate challenge.
- The digest that gets WRITTEN is computed locally: ``"sha256:" +
  sha256(body).hexdigest()``. The advertised Docker-Content-Digest header is
  compared only if present (via the case-insensitive ``response.headers.get``
  -- ``dict(response.headers)["Docker-Content-Digest"]`` returns None because
  HTTP header names are case-insensitive and dict() does not know that, which
  would make an "advertised or computed" fallback evaporate the check
  silently) and a disagreement is fatal, not a warning.
- The auth flow is hardcoded to auth.docker.io / service=registry.docker.io
  rather than followed from a WWW-Authenticate challenge, because following
  the challenge is exactly an attacker-controlled redirect to an
  attacker-chosen token endpoint, just spelled as a 401 instead of a 3xx. Any
  reference outside docker.io is refused before a single request is made.
- Every response is read up to a fixed cap and refused if larger, with an
  explicit timeout on every request.
- The one place this script does follow a redirect is a config-blob GET
  (needed for veraPDF's single-arch platform -- see ``_resolve_platforms``):
  MEASURED, Docker Hub externalises blob storage to a CDN off docker.io, and
  a real ``docker pull`` follows that hop on every layer. ``_fetch_blob``
  follows it exactly once, requires https on the target, and drops the
  bearer token before the follow-up request -- the redirect target is a
  pre-signed URL that authenticates itself, so forwarding the token to
  whatever host it names would be exactly the leak this script's redirect
  refusal exists to prevent everywhere else. Manifest and token requests
  never redirect (MEASURED) and stay fully non-redirecting.
- The computed digest is cross-checked against a real pull before anything is
  written: this script requires a container runtime, pulls the tag, and
  requires the digest to be a MEMBER of that image's RepoDigests -- see
  ``cross_check`` for why membership rather than ``RepoDigests[0]``.
- All three tags are resolved before the file is written once, so a rate
  limit or a network failure on the third tag cannot leave one new digest
  sitting beside two stale ones in a file that looks complete.

SAY WHAT THIS DOES NOT DO, in the shape scripts/vendor_npm_locks.py's own
docstring uses for the same admission about lockfiles: nothing OFFLINE in
this script, or in pipeline.pinned_image(), verifies that the digest recorded
here is the one the tag points at in truth -- only that the bytes this
process received hash to the digest it wrote, and that a real pull of the tag
names that same digest among its RepoDigests. A registry that lies
consistently to both the manifest fetch and the cross-check pull -- the same
compromised edge serving both -- passes every check here and the whole
container tier that follows. The trust root is the reviewed commit: a
reviewer reads the diff, not just the test result, the way
scripts/vendor_npm_locks.py's docstring already asks for the npm lockfiles.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stencil import pipeline  # noqa: E402

OUT = ROOT / "stencil" / "assets" / "image-digests.json"

# Real manifests here run a few KB (an index listing three or four platforms
# plus a couple of attestation entries); a container config blob is smaller
# still. A generous cap that is nonetheless far below "a whole image layer"
# -- refusing a body this size is refusing something that is not a manifest
# at all, not tuning a limit against real payloads.
MAX_RESPONSE_BYTES = 1 * 1024 * 1024

TIMEOUT = 30  # seconds, per request -- there is no request this should wait longer for.

REGISTRY_HOST = "registry-1.docker.io"
AUTH_HOST = "auth.docker.io"

# Every media type a manifest reference in pipeline.IMAGE_TAGS may resolve to.
# Kept as two sets, mirroring tests/test_pins.py, because node and pandoc are
# required to land in INDEX (multi-arch) and only veraPDF is allowed to land
# in SINGLE_ARCH -- a resolved manifest of a type in neither set is refused
# outright rather than recorded with a media_type nothing downstream expects.
INDEX_MEDIA_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
SINGLE_ARCH_MEDIA_TYPES = {
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
}

# MEASURED 2026-09-12: Docker Hub does not content-negotiate the digest it
# returns for any of the three tags this resolves -- the same digest came
# back with this full Accept list, with a trimmed one, and with no Accept
# header at all. Sent anyway, and not trimmed to "whatever happened to work
# today": a registry is free to start content-negotiating tomorrow, and this
# is the set that recovers every reference these three tags can resolve to,
# an index or a single manifest, OCI or Docker's own media types.
ACCEPT = ", ".join(sorted(INDEX_MEDIA_TYPES | SINGLE_ARCH_MEDIA_TYPES))

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")

UA = "stencil-resolve-image-digests/1 (+https://github.com/)"


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect. See the module docstring for why.

    ``redirect_request`` returning None is urllib's documented way to make a
    redirect fail instead of being followed -- the caller then sees the
    original 3xx response as an HTTPError rather than urllib silently
    re-issuing the request (with its Authorization header) at a new host.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def _opener() -> urllib.request.OpenerDirector:
    """A private opener: no redirects, no ambient proxy, an explicit TLS context.

    Passing instances of these three handler classes to build_opener() is
    also what SUPPRESSES urllib's defaults for them -- a default
    HTTPRedirectHandler that follows redirects, and a default ProxyHandler
    that reads HTTP_PROXY/HTTPS_PROXY from the environment, would otherwise
    still be installed alongside these and win nothing back by being
    shadowed. See https://docs.python.org/3/library/urllib.request.html#urllib.request.build_opener.
    """
    return urllib.request.build_opener(
        _RefuseRedirects(),
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )


def _fetch(opener: urllib.request.OpenerDirector, url: str, headers: dict[str, str]) -> tuple[bytes, "urllib.request.addinfourl"]:
    """GET ``url``, https-only, capped, with a message for the failures a
    maintainer running this by hand will actually hit.

    Returns the body and the response object (kept open only long enough to
    read headers off it -- ``resp.headers`` stays valid after the ``with``
    block via the returned object, but callers should read what they need
    from it immediately rather than holding it).
    """
    if not url.startswith("https://"):
        # Asserted on the URL this script BUILT, never on one read back from
        # a server -- see the module docstring's threat model.
        raise SystemExit(f"refusing a non-https URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": UA, **headers})
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise SystemExit(
                    f"{url} returned more than {MAX_RESPONSE_BYTES} bytes; "
                    "refusing to read further. This is not a manifest or a "
                    "config blob."
                )
            return body, response
    except urllib.error.HTTPError as error:
        if error.code == 429:
            raise SystemExit(
                "Docker Hub rate-limited this IP; wait or authenticate. "
                "MEASURED 2026-09-12: anonymous manifest GETs are limited to "
                "100 per hour."
            ) from error
        if error.code in (301, 302, 303, 307, 308):
            # Left as an HTTPError, not converted to SystemExit: _RefuseRedirects
            # turns a refused redirect into exactly this exception (see its
            # docstring), and _fetch_blob() needs the original object -- its
            # .headers carries Location -- to decide whether Hub's one
            # legitimate off-registry redirect applies. Every other caller of
            # _fetch() genuinely wants a redirect treated as fatal, and gets
            # that for free: they don't catch HTTPError, so it propagates as
            # an uncaught traceback -- deliberately loud, since a manifest or
            # token redirect was MEASURED never to happen and one appearing
            # is the exact attack the module docstring describes, not a
            # condition to word nicely.
            raise
        detail = error.read(2048)
        raise SystemExit(f"HTTP {error.code} fetching {url}: {detail!r}") from error
    except urllib.error.URLError as error:
        raise SystemExit(f"could not reach {url}: {error.reason}") from error


def _split_reference(reference: str) -> tuple[str, str]:
    """``docker.io/library/node:24.20.0-alpine3.24`` -> ``("library/node", "24.20.0-alpine3.24")``.

    Refuses anything but docker.io: the auth flow below is hardcoded to
    auth.docker.io and registry-1.docker.io on purpose (see the module
    docstring), so a reference naming another registry would silently be
    resolved against the wrong one rather than failing.

    The repo scope for an OFFICIAL image (no namespace, like ``node``) is
    ``library/<name>``, never the bare name -- a token scoped to the bare
    name comes back valid but useless, and the manifest GET 401s in a way
    that reads like an unrelated network problem. pipeline.IMAGE_TAGS already
    spells every official tag as ``docker.io/library/<name>:<tag>``, so this
    is a defensive re-derivation rather than a fixup: it is here so a future
    tag added without the ``library/`` prefix fails this assertion instead of
    silently resolving against an empty scope.
    """
    prefix = "docker.io/"
    if not reference.startswith(prefix):
        raise SystemExit(
            f"refusing {reference!r}: only docker.io is supported by this "
            "script's hardcoded auth flow"
        )
    repo, _, tag = reference.removeprefix(prefix).rpartition(":")
    if not repo or not tag:
        raise SystemExit(f"could not split {reference!r} into <repo>:<tag>")
    if "/" not in repo:
        repo = f"library/{repo}"
    assert "/" in repo, repo  # the official-image case above always adds one
    return repo, tag


def _token(opener: urllib.request.OpenerDirector, repo: str) -> str:
    """An anonymous pull token for ``repo``, from auth.docker.io directly.

    Hardcoded rather than driven off the manifest endpoint's WWW-Authenticate
    challenge -- see the module docstring: following that challenge is an
    attacker-controlled redirect to an attacker-chosen token endpoint, merely
    spelled as a 401 rather than a 3xx.
    """
    query = urllib.parse.urlencode(
        {"service": "registry.docker.io", "scope": f"repository:{repo}:pull"}
    )
    url = f"https://{AUTH_HOST}/token?{query}"
    body, _ = _fetch(opener, url, headers={"Accept": "application/json"})
    payload = json.loads(body)
    token = payload.get("token") or payload.get("access_token")
    if not token:
        raise SystemExit(f"auth.docker.io returned no token for {repo}: {payload!r}")
    return token


def _fetch_blob(
    opener: urllib.request.OpenerDirector, repo: str, digest: str, token: str, accept: str
) -> bytes:
    """GET a content-addressed blob, following Docker Hub's one real redirect.

    Manifests are served directly by the registry (MEASURED: no redirect).
    Blobs are not -- Hub externalises blob storage to a CDN off docker.io,
    and a real ``docker pull`` follows that redirect on every layer. Refusing
    it outright, the way _fetch() does for the manifest and token endpoints,
    would make this script unable to read veraPDF's config blob at all.

    So this is the one redirect this script follows, and it is followed
    DELIBERATELY rather than by relaxing _RefuseRedirects: caught here as the
    HTTPError _fetch() raises, checked to be https, and re-requested WITHOUT
    the Authorization header. The registry's redirect target is a pre-signed
    URL that authenticates itself; forwarding a Hub bearer token to whatever
    host that URL names is exactly the cross-host leak the module docstring's
    threat model describes, so the token is dropped rather than carried
    across the hop. Followed exactly once -- a second redirect from the CDN
    itself hits _fetch() again and is refused like any other, so this cannot
    become an unbounded chain.
    """
    url = f"https://{REGISTRY_HOST}/v2/{repo}/blobs/{digest}"
    headers = {"Accept": accept, "Authorization": f"Bearer {token}"}
    try:
        body, _ = _fetch(opener, url, headers)
        return body
    except urllib.error.HTTPError as redirected:
        if redirected.code not in (301, 302, 303, 307, 308):
            raise
        location = redirected.headers.get("Location")
        if not location:
            raise SystemExit(f"{url} redirected with no Location header") from redirected
        if not location.startswith("https://"):
            raise SystemExit(
                f"{url} redirected to a non-https location; refusing: {location}"
            ) from redirected
        print(
            f"    blob redirected off-registry to "
            f"{urllib.parse.urlsplit(location).hostname}; following once, "
            "without the bearer token"
        )
        body, _ = _fetch(opener, location, headers={"Accept": accept})
        return body


def _verify_blob(digest: str, body: bytes) -> bytes:
    """A blob is content-addressed, so check it against the name we asked for.

    This is what makes following the CDN redirect above safe rather than
    merely reasoned-about. Everything else this script fetches is either
    non-redirecting (the manifest, the token) or checked against a real pull
    (the manifest digest); the config blob is the one body that arrives from
    a host nobody vetted, over a hop a hostile registry chooses. But we asked
    for it BY DIGEST -- the manifest named it -- so a substituted blob is
    detectable offline, for the cost of one hash. Without this, a redirect to
    a blob claiming a different architecture would be recorded as this pin's
    platform coverage and nothing would notice.
    """
    computed = "sha256:" + hashlib.sha256(body).hexdigest()
    if computed != digest:
        raise SystemExit(
            f"config blob {digest} hashes to {computed}; the registry or the "
            f"CDN it redirected to served something other than what was asked "
            f"for. Refusing to record a platform read from it."
        )
    return body


def _resolve_platforms(
    opener: urllib.request.OpenerDirector, repo: str, token: str, manifest: dict
) -> list[str]:
    """The platforms a manifest covers, however it has to be learned.

    An INDEX lists them directly, one per child manifest -- except for
    attestation manifests, whose ``platform`` is ``{"architecture":
    "unknown", "os": "unknown"}``; those are not architectures this pin
    covers and are skipped rather than recorded as one.

    A SINGLE manifest carries no platform block at all: the platform lives in
    its config blob, fetched separately. MEASURED 2026-09-12: veraPDF's
    manifest has no top-level platform field; without this fetch its entry
    would record an empty platform list, which
    test_the_pinned_digests_cover_the_architectures_we_build_on would then
    have nothing to check veraPDF's exemption against.
    """
    if "manifests" in manifest:
        platforms = []
        for child in manifest["manifests"]:
            platform = child.get("platform", {})
            if platform.get("os") == "unknown" or platform.get("architecture") == "unknown":
                continue
            parts = [platform.get("os", ""), platform.get("architecture", "")]
            if platform.get("variant"):
                parts.append(platform["variant"])
            platforms.append("/".join(parts))
        return sorted(set(platforms))

    config_digest = manifest["config"]["digest"]
    body = _fetch_blob(
        opener,
        repo,
        config_digest,
        token,
        accept="application/vnd.docker.container.image.v1+json, "
        "application/vnd.oci.image.config.v1+json",
    )
    config = json.loads(_verify_blob(config_digest, body))
    parts = [config.get("os", ""), config.get("architecture", "")]
    if config.get("variant"):
        parts.append(config["variant"])
    return ["/".join(parts)]


def resolve(opener: urllib.request.OpenerDirector, reference: str) -> dict:
    """Resolve one ``docker.io/...:tag`` reference to a digest record."""
    repo, tag = _split_reference(reference)
    token = _token(opener, repo)

    url = f"https://{REGISTRY_HOST}/v2/{repo}/manifests/{tag}"
    headers = {"Accept": ACCEPT, "Authorization": f"Bearer {token}"}
    body, response = _fetch(opener, url, headers)

    # THE DIGEST WRITTEN IS COMPUTED HERE, LOCALLY, FROM THE BYTES RECEIVED --
    # never taken from a header. See the module docstring for why a header
    # match alone is a checksum, not an authenticity control.
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    if not _DIGEST.fullmatch(digest):  # unreachable in practice; asserts the format regardless
        raise SystemExit(f"computed digest {digest!r} for {reference} is not well-formed")

    # response.headers.get() is case-insensitive (it is an email.message.Message);
    # dict(response.headers)["Docker-Content-Digest"] is NOT, would silently
    # return None for a server that cased the header differently, and a
    # defensively written "advertised or computed" fallback would then make
    # this whole check evaporate without ever failing. Compared only if
    # present -- absence is not itself suspicious, Hub does not always send it
    # for every media type -- and a disagreement is fatal.
    advertised = response.headers.get("Docker-Content-Digest")
    if advertised is not None and advertised != digest:
        raise SystemExit(
            f"{reference}: Docker-Content-Digest header ({advertised}) disagrees "
            f"with sha256(body) ({digest}); refusing to trust either"
        )

    manifest = json.loads(body)
    media_type = manifest.get("mediaType") or response.headers.get_content_type()
    if media_type not in (INDEX_MEDIA_TYPES | SINGLE_ARCH_MEDIA_TYPES):
        raise SystemExit(
            f"{reference} resolved to an unexpected media type {media_type!r}; "
            "this script only knows how to record an OCI/Docker image index "
            "or a single OCI/Docker image manifest"
        )

    platforms = _resolve_platforms(opener, repo, token, manifest)

    return {
        "digest": digest,
        "media_type": media_type,
        "platforms": platforms,
        "resolved": datetime.datetime.now(datetime.timezone.utc).date().isoformat(),
    }


def cross_check(runtime: str, reference: str, digest: str) -> None:
    """Pull ``reference`` for real and require ``digest`` among its RepoDigests.

    Precedented by scripts/vendor_npm_locks.py, which already refuses to
    commit a lockfile it has not proven installs -- this is the same
    "prove it, don't just compute it" step, against a real pull instead of a
    real ``npm ci``.

    MEMBERSHIP, never ``RepoDigests[0]``: MEASURED 2026-09-12, docker's
    ``RepoDigests`` holds exactly the index digest for a multi-arch pull,
    while podman's holds BOTH the arch-specific child manifest's digest and
    the index digest. Taking ``[0]`` on podman is index-order-dependent and,
    for veraPDF's single-arch image, would compare the one recorded digest
    against whichever entry the runtime happened to list first for reasons
    that have nothing to do with correctness.
    """
    pull = subprocess.run([runtime, "pull", reference], capture_output=True, text=True)
    if pull.returncode != 0:
        raise SystemExit(
            f"{runtime} pull {reference} failed:\n{pull.stdout[-2000:]}\n{pull.stderr[-2000:]}"
        )

    inspect = subprocess.run(
        [runtime, "inspect", "--format", "{{json .RepoDigests}}", reference],
        capture_output=True,
        text=True,
    )
    if inspect.returncode != 0:
        raise SystemExit(
            f"{runtime} inspect {reference} failed:\n"
            f"{inspect.stdout[-2000:]}\n{inspect.stderr[-2000:]}"
        )
    try:
        repo_digests = json.loads(inspect.stdout)
    except json.JSONDecodeError as error:
        raise SystemExit(
            f"{runtime} inspect {reference} did not print JSON: {inspect.stdout!r}"
        ) from error

    seen = {entry.rsplit("@", 1)[-1] for entry in (repo_digests or []) if "@" in entry}
    if digest not in seen:
        raise SystemExit(
            f"{reference}: the digest this script resolved ({digest}) is not "
            f"among {runtime}'s RepoDigests for a real pull of the tag "
            f"({sorted(seen) or 'none recorded'}). Refusing to write it."
        )


def main() -> None:
    runtime = pipeline.container_runtime()
    if runtime is None:
        raise SystemExit(
            "no container runtime found (looked for docker, podman). This "
            "script cross-checks every resolved digest against a real pull "
            "before writing anything -- see cross_check() -- so it needs one "
            "even though the resolve step itself is plain urllib."
        )

    opener = _opener()

    # Read from pipeline.IMAGE_TAGS -- the plain, eager tags -- never from
    # pipeline.NODE_IMAGE/PANDOC_IMAGE/VERAPDF_IMAGE, which resolve lazily
    # through pinned_image() and would raise on exactly the tag this script
    # exists to give an entry to. See pipeline.py's comment beside
    # _LAZY_IMAGES for the bootstrap deadlock that would otherwise cause.
    tags = sorted(set(pipeline.IMAGE_TAGS.values()))

    # RESOLVE ALL THREE, THEN WRITE ONCE: a 429 or a failed cross-check on the
    # third tag must not leave one new digest sitting beside two stale ones
    # in a file that looks complete.
    print(f"Resolving {len(tags)} image tag(s) via {runtime} for the cross-check")
    records: dict[str, dict] = {}
    for reference in tags:
        print(f"  resolving {reference} ...")
        record = resolve(opener, reference)
        print(f"    {record['digest']}  {record['media_type']}  {record['platforms']}")
        cross_check(runtime, reference, record["digest"])
        print(f"    confirmed present in {runtime}'s RepoDigests for a real pull")
        records[reference] = record

    OUT.parent.mkdir(parents=True, exist_ok=True)
    # Written from scratch from the tags currently pinned -- not merged with
    # whatever was there before -- so a tag that was bumped away cannot leave
    # its old entry behind as a stale, never-checked leftover.
    OUT.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    print("done. Commit it with the tag change that required it.")


if __name__ == "__main__":
    main()
