#!/usr/bin/env python3
"""Build a clean, reproducible Blender addon release zip.

Run from the GitHub repository root:
  python scripts/build_release.py

Outputs under workspace ``dist/`` when present, else ``./dist``:
  io_import_forza_carbin-<version>.zip
  io_import_forza_carbin-<version>.zip.sha256
"""
from __future__ import annotations

import ast
import hashlib
import json
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "addon" / "io_import_forza_carbin"

EXCLUDED_FILES = {
    "parsing/mojo_pose_oracle.py",
    "parsing/mojo_bake_debug.py",
    "tools/gr2dump/granny2.dll",
    ".gitignore",
    "data/material_table.json",
    "data/material_table_fh6.json",
}
EXCLUDED_SUFFIXES = {".pyc", ".pdb", ".lib", ".exp", ".ilk", ".obj"}
EXCLUDED_PARTS = {
    "__pycache__",
    ".git",
    "scripts",
    "tests",
    ".github",
    "docs",
    "reports",
    "benchmarks",
}


def bl_info_version() -> str:
    init = (SOURCE / "__init__.py").read_text(encoding="utf-8")
    module = ast.parse(init)
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "bl_info":
                info = ast.literal_eval(node.value)
                ver = info["version"]
                return ".".join(str(x) for x in ver)
    raise SystemExit("bl_info['version'] not found in __init__.py")


def dist_dir() -> Path:
    workspace = REPO.parents[1]  # …/github/forzaport → …/workspace
    if (workspace / "github" / "forzaport").resolve() == REPO.resolve():
        return workspace / "dist"
    return REPO / "dist"


def included_files() -> list[Path]:
    out: list[Path] = []
    for path in SOURCE.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(SOURCE)
        rel_posix = rel.as_posix()
        if rel_posix in EXCLUDED_FILES:
            continue
        if any(part in EXCLUDED_PARTS for part in rel.parts):
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        out.append(path)
    return sorted(out, key=lambda p: p.relative_to(SOURCE).as_posix().lower())


def main() -> int:
    version = bl_info_version()
    dist = dist_dir()
    out_zip = dist / f"io_import_forza_carbin-{version}.zip"

    required = [
        SOURCE / "__init__.py",
        REPO / "LICENSE",
        REPO / "THIRD_PARTY.md",
        SOURCE / "tools" / "acl" / "forza_acl.dll",
        SOURCE / "tools" / "gr2dump" / "gr2dump.exe",
    ]
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        raise SystemExit("Missing release requirements:\n" + "\n".join(missing))

    files = included_files()
    for path in files:
        if path.name.lower() == "granny2.dll":
            raise SystemExit(f"Refusing to package proprietary file: {path}")

    dist.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    with zipfile.ZipFile(
        out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as zf:
        for path in files:
            rel = path.relative_to(SOURCE)
            arc = (Path("io_import_forza_carbin") / rel).as_posix()
            data = path.read_bytes()
            zf.writestr(arc, data)
            hashes[arc] = hashlib.sha256(data).hexdigest()

        for name in ("LICENSE", "THIRD_PARTY.md"):
            path = REPO / name
            arc = f"io_import_forza_carbin/{name}"
            data = path.read_bytes()
            zf.writestr(arc, data)
            hashes[arc] = hashlib.sha256(data).hexdigest()

        manifest = {
            "name": "io_import_forza_carbin",
            "version": version,
            "files": hashes,
            "excluded": sorted(EXCLUDED_FILES),
            "notes": [
                "granny2.dll is not bundled; FH5 animation users must supply it.",
                "Research modules mojo_pose_oracle / mojo_bake_debug are not bundled.",
                "Retired material_table JSON dumps are not bundled.",
            ],
        }
        zf.writestr(
            "io_import_forza_carbin/RELEASE_MANIFEST.json",
            json.dumps(manifest, indent=2).encode("utf-8"),
        )

    digest = hashlib.sha256(out_zip.read_bytes()).hexdigest()
    checksum = out_zip.with_suffix(out_zip.suffix + ".sha256")
    checksum.write_text(f"{digest}  {out_zip.name}\n", encoding="ascii")
    print(f"Built {out_zip}")
    print(f"Version: {version}")
    print(f"Files: {len(files)}, size: {out_zip.stat().st_size / (1024 * 1024):.2f} MiB")
    print(f"SHA256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
