#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate a Minecraft Bedrock resource-pack folder or .mcpack archive."""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {path}: {exc}") from exc


def validate_folder(pack_dir: Path) -> dict[str, Any]:
    if not pack_dir.is_dir():
        raise ValueError(f"Resource pack directory does not exist: {pack_dir}")

    # Parse every JSON file so malformed nested metadata cannot slip into a build.
    for json_path in sorted(pack_dir.rglob("*.json")):
        _load_json(json_path)

    manifest_path = pack_dir / "manifest.json"
    contents_path = pack_dir / "contents.json"
    lang_path = pack_dir / "texts" / "th_TH.lang"
    languages_path = pack_dir / "texts" / "languages.json"
    names_path = pack_dir / "texts" / "language_names.json"
    for required in (manifest_path, contents_path, lang_path, languages_path, names_path):
        if not required.is_file():
            raise ValueError(f"Required pack file is missing: {required.relative_to(pack_dir)}")

    manifest = _load_json(manifest_path)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("header"), dict):
        raise ValueError("manifest.json must contain a header object")
    header = manifest["header"]
    for field in ("name", "uuid", "version"):
        if field not in header:
            raise ValueError(f"manifest.json header is missing {field!r}")
    if not UUID_RE.match(str(header["uuid"])):
        raise ValueError("manifest.json header.uuid is not a valid UUID")
    header_version = header["version"]
    if not (isinstance(header_version, list) and len(header_version) == 3 and all(type(x) is int and x >= 0 for x in header_version)):
        raise ValueError("manifest.json header.version must be three non-negative integers")

    modules = manifest.get("modules")
    if not isinstance(modules, list) or not modules:
        raise ValueError("manifest.json must contain at least one module")
    for index, module in enumerate(modules):
        if not isinstance(module, dict) or not UUID_RE.match(str(module.get("uuid", ""))):
            raise ValueError(f"manifest.json modules[{index}].uuid is invalid")
        version = module.get("version")
        if not (isinstance(version, list) and len(version) == 3 and all(type(x) is int and x >= 0 for x in version)):
            raise ValueError(f"manifest.json modules[{index}].version must be three non-negative integers")
        if version != header_version:
            raise ValueError(f"manifest version mismatch: header {header_version} vs module {version}")

    contents = _load_json(contents_path)
    entries = contents.get("content") if isinstance(contents, dict) else None
    if not isinstance(entries, list):
        raise ValueError("contents.json must contain a 'content' array")
    listed_paths: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError(f"contents.json content[{index}] must contain a path string")
        raw_path = entry["path"].replace("\\", "/")
        posix_path = PurePosixPath(raw_path)
        if posix_path.is_absolute() or ".." in posix_path.parts:
            raise ValueError(f"Unsafe path in contents.json: {raw_path}")
        normalized = posix_path.as_posix()
        if normalized in listed_paths:
            raise ValueError(f"Duplicate path in contents.json: {normalized}")
        listed_paths.add(normalized)
        if not (pack_dir / Path(*posix_path.parts)).is_file():
            raise ValueError(f"contents.json references a missing file: {normalized}")

    languages = _load_json(languages_path)
    if not isinstance(languages, list) or "th_TH" not in languages:
        raise ValueError("texts/languages.json must be an array containing 'th_TH'")
    names = _load_json(names_path)
    if not isinstance(names, list) or not any(
        isinstance(item, list) and len(item) >= 2 and item[0] == "th_TH" for item in names
    ):
        raise ValueError("texts/language_names.json must contain a 'th_TH' language-name entry")
    if "texts/th_TH.lang" not in listed_paths:
        raise ValueError("contents.json must register texts/th_TH.lang")

    try:
        lang_text = lang_path.read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        raise ValueError(f"th_TH.lang is not valid UTF-8: {exc}") from exc
    if not lang_text.strip():
        raise ValueError("texts/th_TH.lang is empty")

    all_files = [p for p in pack_dir.rglob("*") if p.is_file()]
    if not all_files:
        raise ValueError("Resource pack contains no files")

    return {
        "pack_name": str(header["name"]),
        "header_uuid": str(header["uuid"]),
        "version": header_version,
        "file_count": len(all_files),
        "contents_entries": len(entries),
        "language_bytes": lang_path.stat().st_size,
    }


def validate_mcpack(mcpack: Path) -> dict[str, Any]:
    if not mcpack.is_file():
        raise ValueError(f"MCPACK file does not exist: {mcpack}")
    try:
        with zipfile.ZipFile(mcpack, "r") as archive:
            bad_member = archive.testzip()
            if bad_member:
                raise ValueError(f"Corrupt ZIP member: {bad_member}")
            names = archive.namelist()
            if "manifest.json" not in names:
                raise ValueError("manifest.json must be at the root of the .mcpack archive")
            if not any(name == "texts/th_TH.lang" for name in names):
                raise ValueError("texts/th_TH.lang must be at the root-relative texts/ path")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            header = manifest.get("header", {})
            return {
                "file": mcpack.name,
                "archive_entries": len(names),
                "pack_name": header.get("name"),
                "version": header.get("version"),
                "size_bytes": mcpack.stat().st_size,
            }
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid .mcpack ZIP archive: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--folder", type=Path)
    group.add_argument("--mcpack", type=Path)
    args = parser.parse_args()
    try:
        result = validate_folder(args.folder) if args.folder else validate_mcpack(args.mcpack)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
