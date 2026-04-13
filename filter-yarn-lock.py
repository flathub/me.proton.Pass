"""
Filter private-registry package blocks out of WebClients/yarn.lock before
running flatpak-node-generator.

Three npm scopes in .yarnrc.yml point to Proton's internal registries and
are unreachable without a CI token:

  @tpe/*         — gitlab.protontech.ch          (test utils, proton-e2e)
  @proton-meet/* — nexus.protontech.ch/meet-npm  (meet core, proton-meet)
  @proton/*      — nexus.protontech.ch/foundation-npm
                   BUT only packages resolved as npm: (not workspace:).
                   Workspace-local @proton/* packages are fine.

flatpak-node-generator processes every entry in yarn.lock and hits a 404
when it tries to resolve these from the public npm registry.  Removing
those blocks before running the generator is sufficient.

None of these packages are needed to build pass-desktop:
  • @tpe/*                      only used by proton-e2e  (tests/)
  • @proton-meet/*              only used by proton-meet (applications/meet)
  • @proton/proton-foundation-search  only used by proton-drive and proton-lumo

The Flatpak manifest excludes the unneeded application workspaces from
`yarn install` by replacing the applications/* glob with an explicit
allowlist of only applications/pass and applications/pass-desktop.

Usage (run from the repo root, i.e. ~/Documents/GitHub/me.proton.Pass):
    python3 filter-yarn-lock.py

Writes the filtered lockfile to WebClients/yarn.lock.  The original is
saved as WebClients/yarn.lock.orig (only on the first run).
"""

import re
import shutil
from pathlib import Path

LOCK = Path("WebClients/yarn.lock")
ORIG = Path("WebClients/yarn.lock.orig")

if not LOCK.exists():
    raise SystemExit(
        f"ERROR: {LOCK} not found — clone the repo first:\n"
        "  git clone --depth 1 --branch 'proton-pass@1.36.0' "
        "https://github.com/ProtonMail/WebClients.git"
    )

# Back up the original once (don't overwrite an existing backup so the
# script is safe to re-run).
if not ORIG.exists():
    shutil.copy2(LOCK, ORIG)
    print(f"Backed up original to {ORIG}")

content = LOCK.read_text(encoding="utf-8")

# Yarn 4 Berry lockfile: entries are separated by a blank line.
# Each entry starts at column 0 with a quoted descriptor, e.g.:
#   "@tpe/minimum-test-set@npm:0.1.1":
#     version: 0.1.1
#     ...
#
# We split on \n\n, drop any block whose first non-empty line starts with
# "@tpe/, then reassemble.

blocks = content.split("\n\n")

kept = []
removed_names = []

# Scopes that are always private (@tpe/ and @proton-meet/)
PRIVATE_SCOPES = ('"@tpe/', '"@proton-meet/')

for block in blocks:
    first = block.lstrip("\n")

    # @tpe/ and @proton-meet/ are always from private registries
    if any(first.startswith(scope) for scope in PRIVATE_SCOPES):
        m = re.match(r'^"(@[^@"]+)', first)
        removed_names.append(m.group(1) if m else "???")
        continue

    # @proton/ packages are private only when resolved as npm: (not workspace:).
    # workspace: packages are part of the monorepo and are always fine.
    if first.startswith('"@proton/'):
        res_match = re.search(r'resolution: "([^"]+)"', block)
        if res_match:
            res = res_match.group(1)
            if "npm:" in res and "workspace:" not in res:
                m = re.match(r'^"(@[^@"]+)', first)
                removed_names.append(m.group(1) if m else "???")
                continue

    kept.append(block)

filtered = "\n\n".join(kept)
# Ensure a single trailing newline
filtered = filtered.rstrip("\n") + "\n"

LOCK.write_text(filtered, encoding="utf-8")

print(f"Removed {len(removed_names)} @tpe/ block(s):")
for name in removed_names:
    print(f"  {name}")
print(f"Filtered lockfile written to {LOCK}")
print(
    "\nNext step:\n"
    "  flatpak-node-generator yarn WebClients/yarn.lock -o generated-sources.json\n\n"
    "Note: the Flatpak manifest already excludes the workspaces that used\n"
    "these packages (tests/ and applications/meet) so yarn install will not\n"
    "need them at build time either."
)
