#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply exact-key replacements from mapping.json to a Minecraft .lang file.

Only existing lines beginning with ``<key>=`` are replaced. Missing keys are
reported but never appended. Line endings and a UTF-8 BOM are preserved.

The automation calls this module without --backup because it edits a temporary
copy of the upstream pack. For manual local use, --backup creates a timestamped
copy in a folder named ``เก่า`` beside the target file.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class UpdateReport:
    mapping_entries: int
    matched_keys: int
    missing_keys: list[str]
    matched_lines: int
    changed_lines: int
    unchanged_lines: int


def load_mapping(mapping_path: Path) -> dict[str, str]:
    try:
        raw: Any = json.loads(mapping_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Mapping file not found: {mapping_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {mapping_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("mapping.json must contain a JSON object of key -> value pairs")

    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            raise ValueError("Every mapping key must be a non-empty string")
        if not isinstance(value, str):
            raise ValueError(f"Mapping value for {key!r} must be a string")
        if "=" in key or "\n" in key or "\r" in key:
            raise ValueError(f"Invalid mapping key {key!r}: keys cannot contain '=', CR or LF")
        if "\n" in value or "\r" in value:
            raise ValueError(f"Invalid mapping value for {key!r}: values cannot contain CR or LF")
        result[key] = value
    return result


def _split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def apply_mapping(source: Path, destination: Path, mapping: dict[str, str]) -> UpdateReport:
    if not source.is_file():
        raise FileNotFoundError(f"Language file not found: {source}")

    data = source.read_bytes()
    has_bom = data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8-sig" if has_bom else "utf-8")

    matched_keys: set[str] = set()
    matched_lines = 0
    changed_lines = 0
    unchanged_lines = 0
    output_lines: list[str] = []

    # Longest-first protects unusual mappings where one key is a prefix of another.
    ordered_keys = sorted(mapping, key=len, reverse=True)

    for line in text.splitlines(keepends=True):
        core, ending = _split_line_ending(line)
        replacement = None
        matched_key = None

        for key in ordered_keys:
            prefix = f"{key}="
            if core.startswith(prefix):
                matched_key = key
                replacement = f"{prefix}{mapping[key]}{ending}"
                break

        if matched_key is None:
            output_lines.append(line)
            unchanged_lines += 1
            continue

        matched_keys.add(matched_key)
        matched_lines += 1
        assert replacement is not None
        if replacement != line:
            changed_lines += 1
        else:
            unchanged_lines += 1
        output_lines.append(replacement)

    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = "".join(output_lines).encode("utf-8")
    if has_bom:
        encoded = b"\xef\xbb\xbf" + encoded
    destination.write_bytes(encoded)

    missing = [key for key in mapping if key not in matched_keys]
    return UpdateReport(
        mapping_entries=len(mapping),
        matched_keys=len(matched_keys),
        missing_keys=missing,
        matched_lines=matched_lines,
        changed_lines=changed_lines,
        unchanged_lines=unchanged_lines,
    )


def make_backup(target: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = target.parent / "เก่า"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{target.stem}-{timestamp}{target.suffix}"
    backup.write_bytes(target.read_bytes())
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lang_file", type=Path, help="Source .lang file")
    parser.add_argument("mapping_file", nargs="?", type=Path, default=Path("mapping.json"))
    parser.add_argument("--output", type=Path, help="Output file (defaults to overwrite source)")
    parser.add_argument("--backup", action="store_true", help="Back up source before overwriting it")
    parser.add_argument("--report", type=Path, help="Write a JSON report")
    args = parser.parse_args()

    source = args.lang_file
    destination = args.output or source
    try:
        mapping = load_mapping(args.mapping_file)
        if args.backup and destination.resolve() == source.resolve():
            backup = make_backup(source)
            print(f"[INFO] Backup created: {backup}")
        report = apply_mapping(source, destination, mapping)
        result = asdict(report)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if report.matched_lines == 0:
            print("[ERROR] No mapping keys matched. Refusing to treat this as a successful update.", file=sys.stderr)
            return 2
        return 0
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
