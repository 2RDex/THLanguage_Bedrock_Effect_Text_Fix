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
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "THLanguage-Bedrock-Effect-Text-Fix-Actions",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
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
    tree_sha = commit.get("tree", {}).get("sha") if isinstance(commit, dict) else None
    if not isinstance(sha, str) or len(sha) < 12:
        raise ValueError("GitHub API did not return a valid upstream commit SHA")
    if not isinstance(tree_sha, str) or len(tree_sha) < 12:
        raise ValueError("GitHub API did not return a valid upstream Git tree SHA")
    return {
        "sha": sha,
        "tree_sha": tree_sha,
        "url": result.get("html_url", f"https://github.com/{UPSTREAM_REPO}/commit/{sha}"),
        "message": str(commit.get("message", "")).splitlines()[0][:200],
        "date": commit.get("committer", {}).get("date"),
    }


def hash_resource_pack_tree_entries(entries: list[dict[str, Any]]) -> str:
    """Hash only file paths and Git blob SHAs inside the THLanguage_RP subtree."""
    files = [entry for entry in entries if entry.get("type") == "blob"]
    if not files:
        raise ValueError(f"Upstream {PACK_FOLDER} Git tree is empty")

    digest = hashlib.sha256()
    for entry in sorted(files, key=lambda item: str(item.get("path", ""))):
        path = entry.get("path")
        blob_sha = entry.get("sha")
        if not isinstance(path, str) or not path or path.startswith("/") or ".." in PurePosixPath(path).parts:
            raise ValueError(f"Invalid path in upstream {PACK_FOLDER} Git tree: {path!r}")
        if not isinstance(blob_sha, str) or len(blob_sha) < 12:
            raise ValueError(f"Invalid Git blob SHA for {PACK_FOLDER}/{path}")
        encoded_path = path.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(4, "big"))
        digest.update(encoded_path)
        digest.update(blob_sha.encode("ascii"))
    return digest.hexdigest()


def get_upstream_pack_tree_hash(commit_tree_sha: str) -> str:
    """Read only the THLanguage_RP subtree when deciding whether upstream changed the pack."""
    api_root = f"https://api.github.com/repos/{UPSTREAM_REPO}/git/trees/"
    root_tree = request_json(api_root + commit_tree_sha)
    root_entries = root_tree.get("tree", [])
    if not isinstance(root_entries, list):
        raise ValueError("GitHub API returned an invalid upstream root tree")

    pack_entry = next(
        (entry for entry in root_entries if entry.get("path") == PACK_FOLDER and entry.get("type") == "tree"),
        None,
    )
    if not isinstance(pack_entry, dict) or not isinstance(pack_entry.get("sha"), str):
        raise ValueError(f"Upstream repository does not contain the expected {PACK_FOLDER}/ directory")

    pack_tree = request_json(api_root + pack_entry["sha"] + "?recursive=1")
    if pack_tree.get("truncated") is True:
        raise RuntimeError(f"GitHub truncated the {PACK_FOLDER} tree response; refusing an incomplete comparison")
    pack_entries = pack_tree.get("tree", [])
    if not isinstance(pack_entries, list):
        raise ValueError(f"GitHub API returned an invalid {PACK_FOLDER} tree")
    return hash_resource_pack_tree_entries(pack_entries)


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


def generate_contents_json(pack_dir: Path) -> list[str]:
    """Regenerate contents.json from files present in a temporary pack copy.

    All paths are relative to the Resource Pack root and use POSIX separators.
    The contents.json index includes its own path, matching the upstream pack
    convention. The workflow calls this only on .work/build-pack, never on the checked-in
    THLanguage_RP directory or the untouched upstream extraction.
    """
    if not pack_dir.is_dir():
        raise ValueError(f"Resource pack directory does not exist: {pack_dir}")

    file_paths: set[str] = set()
    for path in pack_dir.rglob("*"):
        if path.is_symlink():
            raise ValueError(
                f"Symbolic links are not allowed in the Resource Pack: "
                f"{path.relative_to(pack_dir).as_posix()}"
            )
        if not path.is_file():
            continue

        relative = path.relative_to(pack_dir).as_posix()
        parsed = PurePosixPath(relative)
        if parsed.is_absolute() or ".." in parsed.parts or not relative:
            raise ValueError(f"Unsafe file path in Resource Pack: {relative!r}")
        file_paths.add(relative)

    if "manifest.json" not in file_paths:
        raise ValueError("Required pack file is missing: manifest.json")

    # contents.json is part of the pack and lists itself, as the upstream
    # contents.json format does. Add it explicitly because it is rewritten.
    file_paths.add("contents.json")
    ordered_paths = sorted(file_paths)
    contents = {
        "content": [{"path": relative} for relative in ordered_paths],
        "version": 1,
    }
    write_json(pack_dir / "contents.json", contents)
    print(
        "[INFO] Regenerated contents.json from "
        f"{len(ordered_paths)} existing Resource Pack files."
    )
    return ordered_paths


def parse_version(value: Any, context: str) -> tuple[int, int, int]:
    if not (isinstance(value, list) and len(value) == 3 and all(type(item) is int and item >= 0 for item in value)):
        raise ValueError(f"{context} must be a three-number version array")
    return tuple(value)  # type: ignore[return-value]


def resolve_pack_version(upstream_version: tuple[int, int, int]) -> tuple[int, int, int]:
    """Keep the custom pack version exactly equal to the upstream manifest version.

    Mapping changes, workflow test runs, and build-logic changes must not bump
    the Minecraft pack version. Build uniqueness is handled separately by the
    upstream commit, mapping hash, and build-logic hash in asset names and tags.
    """
    return upstream_version


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
    missing = sorted(source_files.keys() - built_files.keys())
    added = sorted(built_files.keys() - source_files.keys())
    unexpected_added = [name for name in added if name != "contents.json"]
    if missing or unexpected_added:
        raise ValueError(
            "Build changed the Resource Pack file set "
            f"(missing={missing[:10]}, added={unexpected_added[:10]})"
        )

    changed = [
        name for name in sorted(source_files)
        if name in built_files and sha256_file(source_files[name]) != sha256_file(built_files[name])
    ]
    if "contents.json" in added:
        changed.append("contents.json")
    changed.sort()
    unexpected = sorted(set(changed) - {"texts/th_TH.lang", "manifest.json", "contents.json"})
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
        has_pack_tree_state = isinstance(previous.get("upstream_pack_git_tree_sha256"), str)
        if same_commit and same_mapping and same_logic and has_pack_tree_state:
            print("[SKIP] Upstream commit, THLanguage_RP files, mapping and build scripts have not changed.")
            set_output({"should_build": "false", "state_updated": "false"})
            return 0

        # Compare only the Git subtree for THLanguage_RP. Changes to README.md,
        # LICENSE or any other file outside this folder must not cause a pack build.
        pack_git_tree_hash = get_upstream_pack_tree_hash(upstream["tree_sha"])
        pack_tree_changed = previous.get("upstream_pack_git_tree_sha256") != pack_git_tree_hash
        mapping_changed = not same_mapping
        logic_changed = not same_logic

        # If upstream changed outside THLanguage_RP, remember the new commit but
        # do not download the repository archive or create a new MCPACK/Release.
        if previous and not pack_tree_changed and not mapping_changed and not logic_changed:
            WORK_DIR.mkdir(parents=True, exist_ok=True)
            new_state: dict[str, Any] = dict(previous)
            new_state.update({
                "schema_version": 1,
                "upstream_repository": UPSTREAM_REPO,
                "upstream_branch": UPSTREAM_BRANCH,
                "upstream_commit": upstream["sha"],
                "upstream_commit_url": upstream["url"],
                "upstream_commit_message": upstream["message"],
                "upstream_commit_date": upstream["date"],
                "upstream_pack_git_tree_sha256": pack_git_tree_hash,
                "mapping_sha256": mapping_hash,
                "build_logic_sha256": script_hash,
                "last_checked_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
            print(f"[SKIP] Upstream commit changed, but no files inside {PACK_FOLDER}/ changed. No MCPACK or Release will be created.")
            write_json(WORK_DIR / "new-state.json", new_state)
            set_output({"should_build": "false", "state_updated": "true"})
            return 0

        WORK_DIR.mkdir(parents=True, exist_ok=True)
        DIST_DIR.mkdir(parents=True, exist_ok=True)
        upstream_zip = WORK_DIR / "upstream-source.zip"
        source_pack_dir = WORK_DIR / "upstream-pack"
        build_pack_dir = WORK_DIR / "build-pack"
        download_upstream_archive(upstream["sha"], upstream_zip)
        extract_pack_from_archive(upstream_zip, source_pack_dir)

        # Keep the extracted upstream pack untouched. Its contents.json may be
        # outdated, so strict validation happens only after a fresh index is
        # generated in the temporary build copy below.
        source_pack_hash = tree_sha256(source_pack_dir)
        source_manifest = json.loads((source_pack_dir / "manifest.json").read_text(encoding="utf-8"))
        upstream_version = parse_version(source_manifest["header"]["version"], "upstream manifest header.version")

        should_build = not previous or pack_tree_changed or mapping_changed or logic_changed
        new_state = dict(previous)
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
            "upstream_pack_git_tree_sha256": pack_git_tree_hash,
            "mapping_sha256": mapping_hash,
            "build_logic_sha256": script_hash,
            "last_checked_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

        if not should_build:
            print(f"[SKIP] No files inside {PACK_FOLDER}/, mapping or build scripts changed.")
            write_json(WORK_DIR / "new-state.json", new_state)
            set_output({"should_build": "false", "state_updated": "true"})
            return 0

        if build_pack_dir.exists():
            shutil.rmtree(build_pack_dir)
        shutil.copytree(source_pack_dir, build_pack_dir)

        # Rebuild the index from the actual files in the exact folder that will
        # be validated and packed. This fixes stale/misplaced Subpack paths.
        generated_contents_entries = generate_contents_json(build_pack_dir)

        source_lang = build_pack_dir / "texts" / "th_TH.lang"
        mapping_report = apply_mapping(source_lang, source_lang, mapping)
        if mapping_report.matched_lines == 0:
            raise ValueError("None of the mapping keys matched upstream th_TH.lang; refusing to publish an unchanged translation pack")

        custom_version = resolve_pack_version(upstream_version)
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

        release_tag = f"auto-effect-text-fix-v{version_string}-{upstream_short}-{mapping_short}-{logic_short}"
        release_title = f"[Auto] THLanguage Bedrock Effect Text Fix v{version_string}"
        missing_list = mapping_report.missing_keys
        notes = [
            f"## THLanguage Bedrock Effect Text Fix v{version_string}",
            "",
            "สร้างแพ็กโดยอัตโนมัติจาก Resource Pack เวอร์ชันล่าสุดของ MineGarp",
            "",
            f"- **ต้นฉบับ:** [{UPSTREAM_REPO}]({upstream['url']})",
            f"- **Upstream Commit:** `{upstream['sha']}`",
            f"- **เวอร์ชัน Resource Pack ต้นฉบับ:** `{'.'.join(str(x) for x in upstream_version)}`",
            f"- **เวอร์ชัน Resource Pack ที่ปรับแต่ง:** `{version_string}`",
            f"- **จำนวนรายการใน Mapping:** {mapping_report.mapping_entries}",
            f"- **Key ใน Mapping ที่ตรงกับต้นฉบับ:** {mapping_report.matched_keys}",
            f"- **จำนวนบรรทัดที่จับคู่และแทนที่:** {mapping_report.matched_lines}",
            f"- **จำนวนบรรทัดที่เปลี่ยนแปลง:** {mapping_report.changed_lines}",
            f"- **Key ใน Mapping ที่ไม่พบในต้นฉบับ:** {len(missing_list)} (ไม่มีการเพิ่มลงในไฟล์ภาษา)",
            "",
            "### ✨ สิ่งที่เปลี่ยนแปลง",
            "- ใช้ Mapping ที่กำหนดเองเฉพาะกับ `THLanguage_RP/texts/th_TH.lang` เท่านั้น",
            "- ปรับเฉพาะฟิลด์ `header.version` และ `modules[].version` ใน `manifest.json` เมื่อจำเป็น เพื่อให้ Minecraft ตรวจพบ Resource Pack เวอร์ชันใหม่",
            "- คง UUID ของต้นฉบับและไฟล์ Resource Pack อื่นๆไว้ตามเดิม ยกเว้น `contents.json` ที่สร้างใหม่จากไฟล์จริง",
            "",
            f"### 🔍 ผลการตรวจสอบ\n- จำนวนไฟล์ Resource Pack: {custom_validation['file_count']}\n- จำนวนรายการภายใน MCPACK: {archive_validation['archive_entries']}\n- ความสมบูรณ์ของไฟล์ MCPACK ZIP: ผ่านการตรวจสอบ",
            "",
            "### 📜 เครดิตและลิขสิทธิ์",
            f"โปรเจกต์นี้เป็นผลงานดัดแปลงโดยชุมชนจาก [{UPSTREAM_REPO}](https://github.com/{UPSTREAM_REPO}) โปรดคงเครดิตของผู้พัฒนาต้นฉบับ และปฏิบัติตามเงื่อนไขสัญญาอนุญาต CC BY-NC-SA 4.0 ของต้นฉบับ รวมถึงประกาศลิขสิทธิ์ของบุคคลที่สามที่เกี่ยวข้อง",
        ]
        if missing_list:
            notes.extend(["", "### คีย์ใน Mapping ที่ไม่พบในต้นฉบับเวอร์ชันนี้", "คีย์ต่อไปนี้ไม่มีอยู่ในไฟล์ภาษาของต้นฉบับ จึงไม่มีการเพิ่มบรรทัดใหม่ลงในไฟล์ภาษา:"])
            notes.extend(f"- `{key}`" for key in missing_list[:100])
            if len(missing_list) > 100:
                notes.append(f"- ... และอีก {len(missing_list) - 100} รายการ (ดูรายงานการ Build)")
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
            "contents_entries_generated": len(generated_contents_entries),
            "source_pack_sha256": source_pack_hash,
            "upstream_pack_git_tree_sha256": pack_git_tree_hash,
            "mapping_sha256": mapping_hash,
            "build_logic_sha256": script_hash,
            "validation": {
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
