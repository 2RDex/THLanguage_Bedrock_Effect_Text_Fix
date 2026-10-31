#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check MineGarp upstream, apply the custom mapping and prepare a .mcpack.

This script is designed for GitHub Actions. It downloads upstream only when the
upstream commit or the local mapping/build logic changes. It never edits the
repository's checked-in THLanguage_RP folder; all work happens under .work/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_REPO = "MineGarp/THLanguage_Bedrock"
UPSTREAM_BRANCH = "main"
API_URL = f"https://api.github.com/repos/{UPSTREAM_REPO}/commits/{UPSTREAM_BRANCH}"
ARCHIVE_URL = f"https://codeload.github.com/{UPSTREAM_REPO}/zip/"
STATE_FILE = ROOT / "upstream-state.json"
WORK_DIR = ROOT / ".work"
DIST_DIR = ROOT / "dist"
PACK_FOLDER = "THLanguage_RP"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(folder: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(path for path in folder.rglob("*") if path.is_file())
    if not files:
        raise ValueError(f"Resource pack folder is empty: {folder}")
    for path in files:
        relative = path.relative_to(folder).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def logic_sha256() -> str:
    digest = hashlib.sha256()
    script_dir = ROOT / "scripts"
    for path in sorted(script_dir.glob("*.py")):
        relative = path.relative_to(ROOT).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def read_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read {STATE_FILE.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("upstream-state.json must contain a JSON object")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def request_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "THLanguage-Bedrock-Effect-Text-Fix-Actions",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            value = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not contact GitHub API: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("Unexpected GitHub API response")
    return value


def get_upstream_commit() -> dict[str, Any]:
    result = request_json(API_URL)
    sha = result.get("sha")
    commit = result.get("commit", {})
    if not isinstance(sha, str) or len(sha) < 12:
        raise ValueError("GitHub API did not return a valid upstream commit SHA")
    return {
        "sha": sha,
        "url": result.get("html_url", f"https://github.com/{UPSTREAM_REPO}/commit/{sha}"),
        "message": str(commit.get("message", "")).splitlines()[0][:200],
        "date": commit.get("committer", {}).get("date"),
    }


def download_upstream_archive(commit_sha: str, destination: Path) -> Path:
    url = ARCHIVE_URL + commit_sha
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "THLanguage-Bedrock-Effect-Text-Fix-Actions"},
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(request, timeout=180) as response, destination.open("wb") as output:
            shutil.copyfileobj(response, output)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Could not download upstream source archive: {exc}") from exc
    if destination.stat().st_size < 1024:
        raise ValueError("Upstream source archive is unexpectedly small")
    return destination


def extract_pack_from_archive(archive_path: Path, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive_path, "r") as archive:
        members = archive.infolist()
        pack_roots: set[str] = set()
        for info in members:
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Unsafe path found in upstream archive: {info.filename}")
            if len(path.parts) >= 2 and path.parts[-2:] == (PACK_FOLDER, "manifest.json"):
                pack_roots.add("/".join(path.parts[:-1]))
        if len(pack_roots) != 1:
            raise ValueError(f"Expected exactly one {PACK_FOLDER}/manifest.json in upstream archive; found {len(pack_roots)}")

        pack_root = next(iter(pack_roots))
        for info in members:
            path = PurePosixPath(info.filename)
            if not path.as_posix().startswith(pack_root + "/") and path.as_posix() != pack_root:
                continue
            relative_parts = path.parts[len(PurePosixPath(pack_root).parts):]
            if not relative_parts:
                continue
            target = destination.joinpath(*relative_parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)

    manifest = destination / "manifest.json"
    if not manifest.is_file():
        raise ValueError("Could not extract THLanguage_RP/manifest.json from upstream archive")
    return destination


def parse_version(value: Any, context: str) -> tuple[int, int, int]:
    if not (isinstance(value, list) and len(value) == 3 and all(type(item) is int and item >= 0 for item in value)):
        raise ValueError(f"{context} must be a three-number version array")
    return tuple(value)  # type: ignore[return-value]


def next_custom_version(upstream_version: tuple[int, int, int], previous: Any) -> tuple[int, int, int]:
    if previous is None:
        return upstream_version
    previous_version = parse_version(previous, "previous custom_version")
    if upstream_version > previous_version:
        return upstream_version
    return previous_version[0], previous_version[1], previous_version[2] + 1


def set_manifest_version(pack_dir: Path, version: tuple[int, int, int]) -> bool:
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    header_version = parse_version(manifest.get("header", {}).get("version"), "manifest header.version")
    module_versions = [parse_version(module.get("version"), f"manifest modules[{index}].version") for index, module in enumerate(manifest.get("modules", []))]
    if header_version == version and all(module_version == version for module_version in module_versions):
        return False

    manifest["header"]["version"] = list(version)
    for module in manifest["modules"]:
        module["version"] = list(version)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    return True


def set_output(values: dict[str, str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as file:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise ValueError(f"GitHub output {key} must be a single line")
            file.write(f"{key}={value}\n")


def verify_only_expected_pack_changes(source_dir: Path, built_dir: Path) -> list[str]:
    source_files = {path.relative_to(source_dir).as_posix(): path for path in source_dir.rglob("*") if path.is_file()}
    built_files = {path.relative_to(built_dir).as_posix(): path for path in built_dir.rglob("*") if path.is_file()}
    if source_files.keys() != built_files.keys():
        missing = sorted(source_files.keys() - built_files.keys())
        added = sorted(built_files.keys() - source_files.keys())
        raise ValueError(f"Build changed the Resource Pack file set (missing={missing[:10]}, added={added[:10]})")

    changed = [name for name in sorted(source_files) if sha256_file(source_files[name]) != sha256_file(built_files[name])]
    unexpected = sorted(set(changed) - {"texts/th_TH.lang", "manifest.json"})
    if unexpected:
        raise ValueError(f"Unexpected Resource Pack files changed: {unexpected}")

    source_manifest = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))
    built_manifest = json.loads((built_dir / "manifest.json").read_text(encoding="utf-8"))
    if source_manifest.get("header", {}).get("uuid") != built_manifest.get("header", {}).get("uuid"):
        raise ValueError("The Resource Pack header UUID changed unexpectedly")
    source_modules = source_manifest.get("modules", [])
    built_modules = built_manifest.get("modules", [])
    if len(source_modules) != len(built_modules):
        raise ValueError("The Resource Pack module list changed unexpectedly")
    for index, (source_module, built_module) in enumerate(zip(source_modules, built_modules)):
        if source_module.get("uuid") != built_module.get("uuid"):
            raise ValueError(f"Resource Pack module UUID changed unexpectedly at modules[{index}]")

    # Apart from version arrays, every manifest field must stay identical.
    source_compare = json.loads(json.dumps(source_manifest))
    built_compare = json.loads(json.dumps(built_manifest))
    source_compare.get("header", {}).pop("version", None)
    built_compare.get("header", {}).pop("version", None)
    for module in source_compare.get("modules", []):
        module.pop("version", None)
    for module in built_compare.get("modules", []):
        module.pop("version", None)
    if source_compare != built_compare:
        raise ValueError("Fields other than manifest version arrays changed unexpectedly")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", type=Path, default=ROOT / "mapping.json")
    args = parser.parse_args()

    try:
        from update_lang import apply_mapping, load_mapping
        from build_mcpack import build_mcpack
        from check_pack import validate_folder, validate_mcpack

        mapping = load_mapping(args.mapping)
        mapping_hash = sha256_file(args.mapping)
        script_hash = logic_sha256()
        previous = read_state()
        upstream = get_upstream_commit()

        same_commit = previous.get("upstream_commit") == upstream["sha"]
        same_mapping = previous.get("mapping_sha256") == mapping_hash
        same_logic = previous.get("build_logic_sha256") == script_hash
        if same_commit and same_mapping and same_logic:
            print("[SKIP] Upstream commit, mapping and build scripts have not changed.")
            set_output({"should_build": "false", "state_updated": "false"})
            return 0

        WORK_DIR.mkdir(parents=True, exist_ok=True)
        DIST_DIR.mkdir(parents=True, exist_ok=True)
        upstream_zip = WORK_DIR / "upstream-source.zip"
        source_pack_dir = WORK_DIR / "upstream-pack"
        build_pack_dir = WORK_DIR / "build-pack"
        download_upstream_archive(upstream["sha"], upstream_zip)
        extract_pack_from_archive(upstream_zip, source_pack_dir)

        source_validation = validate_folder(source_pack_dir)
        source_pack_hash = tree_sha256(source_pack_dir)
        source_manifest = json.loads((source_pack_dir / "manifest.json").read_text(encoding="utf-8"))
        upstream_version = parse_version(source_manifest["header"]["version"], "upstream manifest header.version")

        previous_pack_hash = previous.get("upstream_pack_sha256")
        pack_changed = previous_pack_hash != source_pack_hash
        mapping_changed = not same_mapping
        logic_changed = not same_logic

        should_build = not previous or pack_changed or mapping_changed or logic_changed
        new_state: dict[str, Any] = dict(previous)
        new_state.update({
            "schema_version": 1,
            "upstream_repository": UPSTREAM_REPO,
            "upstream_branch": UPSTREAM_BRANCH,
            "upstream_commit": upstream["sha"],
            "upstream_commit_url": upstream["url"],
            "upstream_commit_message": upstream["message"],
            "upstream_commit_date": upstream["date"],
            "upstream_version": list(upstream_version),
            "upstream_pack_sha256": source_pack_hash,
            "mapping_sha256": mapping_hash,
            "build_logic_sha256": script_hash,
            "last_checked_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

        if not should_build:
            print("[SKIP] Upstream commit changed, but the Resource Pack files are identical and local mapping/build logic did not change.")
            write_json(WORK_DIR / "new-state.json", new_state)
            set_output({"should_build": "false", "state_updated": "true"})
            return 0

        if build_pack_dir.exists():
            shutil.rmtree(build_pack_dir)
        shutil.copytree(source_pack_dir, build_pack_dir)

        source_lang = build_pack_dir / "texts" / "th_TH.lang"
        mapping_report = apply_mapping(source_lang, source_lang, mapping)
        if mapping_report.matched_lines == 0:
            raise ValueError("None of the mapping keys matched upstream th_TH.lang; refusing to publish an unchanged translation pack")

        custom_version = next_custom_version(upstream_version, previous.get("custom_version"))
        manifest_changed = set_manifest_version(build_pack_dir, custom_version)
        changed_pack_files = verify_only_expected_pack_changes(source_pack_dir, build_pack_dir)
        custom_validation = validate_folder(build_pack_dir)

        version_string = ".".join(str(number) for number in custom_version)
        upstream_short = upstream["sha"][:7]
        mapping_short = mapping_hash[:7]
        logic_short = script_hash[:7]
        asset_name = (
            f"THLanguage_Bedrock_Effect_Text_Fix-v{version_string}-"
            f"src-{upstream_short}-map-{mapping_short}.mcpack"
        )
        asset_path = DIST_DIR / asset_name
        build_mcpack(build_pack_dir, asset_path)
        archive_validation = validate_mcpack(asset_path)

        release_tag = f"effect-text-fix-v{version_string}-{upstream_short}-{mapping_short}-{logic_short}"
        release_title = f"THLanguage Bedrock Effect Text Fix v{version_string}"
        missing_list = mapping_report.missing_keys
        notes = [
            f"## THLanguage Bedrock Effect Text Fix v{version_string}",
            "",
            "Build created automatically from the latest MineGarp upstream Resource Pack.",
            "",
            f"- **Upstream:** [{UPSTREAM_REPO}]({upstream['url']})",
            f"- **Upstream commit:** `{upstream['sha']}`",
            f"- **Upstream pack version:** `{'.'.join(str(x) for x in upstream_version)}`",
            f"- **Custom pack version:** `{version_string}`",
            f"- **Mapping entries:** {mapping_report.mapping_entries}",
            f"- **Matched mapping keys:** {mapping_report.matched_keys}",
            f"- **Replaced lines:** {mapping_report.matched_lines}",
            f"- **Changed lines:** {mapping_report.changed_lines}",
            f"- **Missing mapping keys:** {len(missing_list)} (not added to the language file)",
            "",
            "### What changed",
            "- Applied the custom mapping to `THLanguage_RP/texts/th_TH.lang` only.",
            "- Updated only the `header.version` and `modules[].version` fields in `manifest.json` when needed so Minecraft can recognize a newer custom pack build.",
            "- Preserved the upstream UUIDs and all other Resource Pack files.",
            "- Did not generate `en_GB.lang` or `en_US.lang`.",
            "",
            f"### Validation\n- Resource Pack files: {custom_validation['file_count']}\n- MCPACK entries: {archive_validation['archive_entries']}\n- MCPACK ZIP integrity: passed",
            "",
            "### Attribution",
            f"This is a community modification of [{UPSTREAM_REPO}](https://github.com/{UPSTREAM_REPO}). Please retain upstream credits and follow the upstream CC BY-NC-SA 4.0 license and any applicable third-party notices.",
        ]
        if missing_list:
            notes.extend(["", "### Mapping keys not found in this upstream version", "The following keys were not present, so no new lines were added:"])
            notes.extend(f"- `{key}`" for key in missing_list[:100])
            if len(missing_list) > 100:
                notes.append(f"- ... and {len(missing_list) - 100} more (see build report)")
        notes_path = WORK_DIR / "release-notes.md"
        notes_path.write_text("\n".join(notes) + "\n", encoding="utf-8")

        report = {
            "upstream_repository": UPSTREAM_REPO,
            "upstream_commit": upstream["sha"],
            "upstream_commit_url": upstream["url"],
            "upstream_version": list(upstream_version),
            "custom_version": list(custom_version),
            "mapping": {
                "entries": mapping_report.mapping_entries,
                "matched_keys": mapping_report.matched_keys,
                "missing_keys": missing_list,
                "matched_lines": mapping_report.matched_lines,
                "changed_lines": mapping_report.changed_lines,
                "unchanged_lines": mapping_report.unchanged_lines,
            },
            "manifest_version_adjusted": manifest_changed,
            "changed_pack_files": changed_pack_files,
            "source_pack_sha256": source_pack_hash,
            "mapping_sha256": mapping_hash,
            "build_logic_sha256": script_hash,
            "validation": {
                "source_pack": source_validation,
                "custom_pack": custom_validation,
                "mcpack": archive_validation,
            },
            "asset": asset_name,
            "release_tag": release_tag,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        write_json(WORK_DIR / "build-report.json", report)

        new_state.update({
            "custom_version": list(custom_version),
            "last_release_tag": release_tag,
            "last_asset": asset_name,
            "last_build_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        write_json(WORK_DIR / "new-state.json", new_state)

        print(json.dumps(report, ensure_ascii=False, indent=2))
        set_output({
            "should_build": "true",
            "state_updated": "true",
            "asset_path": str(asset_path.relative_to(ROOT)).replace("\\", "/"),
            "asset_name": asset_name,
            "release_tag": release_tag,
            "release_title": release_title,
            "notes_path": str(notes_path.relative_to(ROOT)).replace("\\", "/"),
            "version": version_string,
        })
        return 0

    except (OSError, UnicodeError, ValueError, RuntimeError, KeyError, TypeError, zipfile.BadZipFile, urllib.error.URLError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
