# Vendored assets

Two kinds of artifact live here, both fetched once by a script and committed, so
that `stencil gen` ships them without touching the network.

## Page assets

The CSS, JavaScript and webfonts every generated HTML file carries inline.
`stencil gen` reads these files and embeds them into the pandoc templates, so a
handout is one self-contained file and `make pdf` does not touch the network.

Refresh with:

```bash
python3 scripts/vendor_page_assets.py
```

Then commit the changed files with whatever template or version bump required
the refresh. Do not point the templates back at CDN URLs.

## npm lockfiles

`browser-package-lock.json` and `format-package-lock.json` are what the generated
`Dockerfile.browser` and `format-md` compose service install with, through
`npm ci`. They pin the whole transitive tree — 47 packages under the browser's
three — and carry a sha512 for each, which npm checks every tarball against.
The versions they were resolved from are `BROWSER_NPM_PINS` and
`FORMAT_NPM_PINS` in `stencil/pipeline.py`; the pins are the request and these
files are the answer.

Re-resolve after changing a pin, and commit both in one commit:

```bash
python3 scripts/vendor_npm_locks.py   # needs docker or podman, and the network
```

### Reviewing a re-vendoring diff

Most of this is checked for you. `tests/test_pins.py` refuses a lockfile whose
root dependencies disagree with the pins, an entry with no sha512, one that is
linked or bundled, one fetched from anywhere but registry.npmjs.org, one whose
URL names a different package or version than its key claims, a `lockfileVersion`
other than 3, and a tree that has grown a second puppeteer. The vendoring script
additionally installs from the lockfile before committing it, so the hashes have
been checked against real tarballs rather than only against registry metadata.

What no test can judge, and a reviewer must:

1. **The diff touches only these two files**, plus `pipeline.py` if a pin moved.
1. **The entry count moved by an amount the pin change explains.** Today: 48 in
   the browser lockfile, 3 in the format one.
1. **The set of packages declaring an install script is unchanged.** Today it is
   `node_modules/puppeteer` and nothing else. `tests/test_pins.py` freezes it, so
   a new one is a failing test — treat it as a stop-and-read rather than a list to
   update. Both installs pass `--ignore-scripts`, so nothing runs today; the
   question is what the package wants to run and why.
1. **Advisories for every package whose version moved**, not only the one you
   bumped. A transitive dependency can move on its own here. Be wary of a release
   only days old — that is the window in which a compromised one is usually still
   being found.

Say plainly what this checklist is carrying: a pin bumped without re-vendoring
fails loudly, in the tests and again in `npm ci`. **A lockfile re-vendored with a
newer transitive tree while the pins are unchanged passes every test and every
build.** That case is a person's job, and it is the reason this section exists.
