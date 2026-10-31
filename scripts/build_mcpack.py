#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a .mcpack ZIP with manifest.json at the archive root."""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path


def build_mcpack(pack_dir: Path, output_file: Path) -> Path:
    if not pack_dir.is_dir():
        raise ValueError(f"Pack directory not found: {pack_dir}")
    if not (pack_dir / "manifest.json").is_file():
        raise ValueError("manifest.json was not found at the resource-pack root")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    if output_file.exists():
        output_file.unlink()

    files = sorted(path for path in pack_dir.rglob("*") if path.is_file())
    if not files:
        raise ValueError("Cannot build an empty resource pack")

    with zipfile.ZipFile(output_file, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for file_path in files:
            relative = file_path.relative_to(pack_dir).as_posix()
            archive.write(file_path, arcname=relative)

    with zipfile.ZipFile(output_file, "r") as archive:
        bad_member = archive.testzip()
        if bad_member:
            output_file.unlink(missing_ok=True)
            raise ValueError(f"ZIP integrity check failed at {bad_member}")
        if "manifest.json" not in archive.namelist():
            output_file.unlink(missing_ok=True)
            raise ValueError("Built archive does not contain root manifest.json")

    return output_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("output_file", type=Path)
    args = parser.parse_args()
    try:
        result = build_mcpack(args.pack_dir, args.output_file)
        print(f"[OK] Created {result} ({result.stat().st_size:,} bytes)")
        return 0
    except (OSError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
