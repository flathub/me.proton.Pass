#!/usr/bin/env python3
"""
Prune the WebClients pnpm-lock.yaml to the packages Pass installs, before
running flatpak-node-generator.

Usage:
    python3 strip-private-registry-lockfile-entries.py <pnpm-lock.yaml> <output>

The monorepo lockfile covers every Proton app, including packages that only
exist on Proton's private registries (see WebClients/.npmrc). The build runs
`pnpm install --filter proton-pass-desktop... --filter @proton-pass-desktop/native...`,
so only the dependency closure of those workspaces is needed. Everything else
is dropped from the packages/snapshots sections, so new private scopes used by
other apps don't break the generator.

Fails if a package from a known private scope is still in the closure.
"""

import posixpath
import sys

import yaml

# pnpm also installs the workspace root's dependencies.
ROOTS = (".", "applications/pass-desktop", "applications/pass-desktop/native")
PRIVATE = (
    "@proton-meet/",
    "@proton-docs/",
    "@tpe/",
    "@proton/proton-foundation-search@",
    "xlsx@https://cdn.sheetjs.com/",
)


def split_documents(lines):
    docs, start = [], 0
    for i, line in enumerate(lines):
        if line.rstrip("\n") == "---" and i > start:
            docs.append((start, i))
            start = i
    docs.append((start, len(lines)))
    return docs


def dep_key(name, ver):
    # Aliased dependencies are written as <real-name>@<version>.
    if ver[:1].isdigit() or ver.startswith(("http:", "https:", "file:")):
        return f"{name}@{ver}"
    return ver


def closure(lock):
    importers = lock["importers"]
    snapshots = lock.get("snapshots") or {}
    seen_importers, seen, stack = set(), set(), []

    def visit_importer(path):
        if path in seen_importers:
            return
        seen_importers.add(path)
        for section in ("dependencies", "devDependencies", "optionalDependencies"):
            for name, spec in (importers[path].get(section) or {}).items():
                ver = spec["version"]
                if ver.startswith("link:"):
                    target = posixpath.normpath(posixpath.join(path, ver[5:]))
                    if target in importers:
                        visit_importer(target)
                else:
                    stack.append(dep_key(name, ver))

    for root in ROOTS:
        visit_importer(root)

    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        for section in ("dependencies", "optionalDependencies"):
            for name, ver in ((snapshots.get(key) or {}).get(section) or {}).items():
                stack.append(dep_key(name, ver))

    return seen, {key.split("(")[0] for key in seen}


def prune(lines, keep_snapshots, keep_packages):
    out, section, removed, i = [], None, 0, 0
    while i < len(lines):
        line = lines[i]
        if line.strip() and not line.startswith(" "):
            section = line.split(":")[0]
        if section in ("packages", "snapshots") and line.startswith("  ") and not line.startswith("   "):
            end = i + 1
            while end < len(lines) and (lines[end].startswith("   ") or not lines[end].strip()):
                end += 1
            key = next(iter(yaml.safe_load(line)))
            keep = keep_packages if section == "packages" else keep_snapshots
            if key in keep:
                out.extend(lines[i:end])
            else:
                removed += 1
            i = end
            continue
        out.append(line)
        i += 1
    return out, removed


def main(input_path, output_path):
    with open(input_path, encoding="utf-8") as f:
        lines = f.readlines()

    # pnpm 11+ lockfiles hold several YAML documents; prune the workspace one.
    docs = split_documents(lines)
    parsed = [yaml.safe_load("".join(lines[s:e])) for s, e in docs]
    idx = max(
        (n for n, d in enumerate(parsed) if d and "importers" in d),
        key=lambda n: len(parsed[n]["importers"]),
    )
    lock = parsed[idx]
    missing = [r for r in ROOTS if r not in lock["importers"]]
    if missing:
        sys.exit(f"ERROR: importers not found in lockfile: {missing}")

    keep_snapshots, keep_packages = closure(lock)

    private = sorted(p for p in keep_packages if p.startswith(PRIVATE))
    if private:
        sys.exit("ERROR: private-registry packages needed by Pass:\n  " + "\n  ".join(private))

    s, e = docs[idx]
    pruned, removed = prune(lines[s:e], keep_snapshots, keep_packages)
    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(lines[:s] + pruned + lines[e:])

    kept = len(keep_packages & set(lock["packages"]))
    print(f"Kept {kept} of {len(lock['packages'])} packages; removed {removed} entries.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {sys.argv[0]} <pnpm-lock.yaml> <output>")
    main(sys.argv[1], sys.argv[2])
