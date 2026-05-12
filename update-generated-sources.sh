#!/usr/bin/env bash
# update-generated-sources.sh — regenerate generated-sources.json and
# cargo-sources.json locally after bumping the upstream tag.
#
# This script replicates what the GitHub Actions workflow does, so you can run
# it on your own machine after bumping the upstream source tag in
# me.proton.Pass.yml (or when you want to regenerate for any other reason).
#
# Requirements
# ────────────
#   git             — to clone the upstream repo
#   python3         — to run strip-private-registry-stanzas.py
#   flatpak-node-generator — install from git (PyPI version may be outdated):
#                       pip install git+https://github.com/flatpak/flatpak-builder-tools.git#subdirectory=node
#                     or via pipx (recommended):
#                       pipx install git+https://github.com/flatpak/flatpak-builder-tools.git#subdirectory=node
#   flatpak-cargo-generator.py — checked out in flatpak-builder-tools/cargo/:
#                       git clone --depth=1 https://github.com/flatpak/flatpak-builder-tools.git
#
# Usage
# ─────
#   # Use the tag currently in me.proton.Pass.yml:
#   ./update-generated-sources.sh
#
#   # Override the tag explicitly:
#   ./update-generated-sources.sh proton-pass@1.37.0
#
# After running, review the diff with:
#   git diff generated-sources.json cargo-sources.json

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="$SCRIPT_DIR/me.proton.Pass.yml"
STRIP_SCRIPT="$SCRIPT_DIR/strip-private-registry-stanzas.py"
CARGO_GENERATOR="$SCRIPT_DIR/flatpak-builder-tools/cargo/flatpak-cargo-generator.py"

# ── Resolve the upstream tag ──────────────────────────────────────────────────
if [ -n "${1:-}" ]; then
    TAG="$1"
    echo "==> Using tag from command-line argument: $TAG"
else
    echo "==> Reading tag from $MANIFEST..."
    TAG=$(python3 - <<'PYEOF'
import re, sys
text = open("me.proton.Pass.yml").read()
m = re.search(r'tag:\s*"(proton-pass@[\d.]+)"', text)
if m:
    print(m.group(1))
else:
    sys.exit("ERROR: Could not find proton-pass tag in me.proton.Pass.yml")
PYEOF
    )
    echo "==> Resolved tag: $TAG"
fi

# ── Prerequisite checks ───────────────────────────────────────────────────────
missing=0
for cmd in git python3 flatpak-node-generator; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: '$cmd' not found in PATH."
        if [ "$cmd" = "flatpak-node-generator" ]; then
            echo "  Install with:"
            echo "    pip install git+https://github.com/flatpak/flatpak-builder-tools.git#subdirectory=node"
            echo "  or: pipx install git+https://github.com/flatpak/flatpak-builder-tools.git#subdirectory=node"
        fi
        missing=1
    fi
done
if [ "$missing" = "1" ]; then exit 1; fi

if [ ! -f "$CARGO_GENERATOR" ]; then
    echo ""
    echo "WARNING: flatpak-cargo-generator.py not found at:"
    echo "  $CARGO_GENERATOR"
    echo "  cargo-sources.json will NOT be regenerated."
    echo "  To fix: git clone --depth=1 https://github.com/flatpak/flatpak-builder-tools.git"
    SKIP_CARGO=1
else
    SKIP_CARGO=0
fi

# ── Sparse-clone upstream repo (only the two lock files needed) ───────────────
TMPDIR=$(mktemp -d)
trap 'echo "==> Cleaning up $TMPDIR"; rm -rf "$TMPDIR"' EXIT

echo ""
echo "==> Sparse-cloning ProtonMail/WebClients at tag '$TAG'..."
echo "    (shallow + sparse — only yarn.lock and Cargo.lock are fetched)"
git clone \
    --filter=blob:none \
    --sparse \
    --depth=1 \
    --branch "$TAG" \
    https://github.com/ProtonMail/WebClients.git \
    "$TMPDIR/WebClients"
git -C "$TMPDIR/WebClients" sparse-checkout set \
    yarn.lock \
    applications/pass-desktop/native/Cargo.lock

# ── Strip private-registry stanzas from yarn.lock ────────────────────────────
echo ""
echo "==> Stripping private-registry stanzas from yarn.lock..."
python3 "$STRIP_SCRIPT" \
    "$TMPDIR/WebClients/yarn.lock" \
    "$TMPDIR/yarn-public.lock"

# ── Generate generated-sources.json ──────────────────────────────────────────
echo ""
echo "==> Generating generated-sources.json..."
echo "    (this typically takes 5–15 minutes — packages are downloaded from npm)"
flatpak-node-generator \
    --electron-node-headers \
    yarn "$TMPDIR/yarn-public.lock" \
    --max-parallel 16 \
    -o "$SCRIPT_DIR/generated-sources.json"

# ── Strip Playwright browser binary entries ───────────────────────────────────
echo ""
echo "==> Stripping Playwright browser binary entries from generated-sources.json..."
python3 - << 'PYEOF'
import json
data = json.load(open('generated-sources.json'))
before = len(data)
data = [e for e in data if not (
    e.get('type') == 'archive' and 'cdn.playwright.dev' in e.get('url', '')
    or e.get('type') == 'inline' and 'ms-playwright' in e.get('dest', '')
)]
with open('generated-sources.json', 'w') as f:
    json.dump(data, f, indent=4)
    f.write('\n')
print(f'Removed {before - len(data)} Playwright entries ({len(data)} remaining).')
PYEOF

# ── Generate cargo-sources.json ───────────────────────────────────────────────
if [ "$SKIP_CARGO" = "0" ]; then
    echo ""
    echo "==> Generating cargo-sources.json..."
    python3 "$CARGO_GENERATOR" \
        "$TMPDIR/WebClients/applications/pass-desktop/native/Cargo.lock" \
        -o "$SCRIPT_DIR/cargo-sources.json"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "==> Done!"
echo "    Review changes with:  git diff generated-sources.json cargo-sources.json"
echo "    Commit when satisfied: git add generated-sources.json cargo-sources.json && git commit -m 'chore: regenerate sources for $TAG'"
