import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from update_lang import apply_mapping, load_mapping  # noqa: E402
from build_mcpack import build_mcpack  # noqa: E402
from check_pack import validate_folder, validate_mcpack  # noqa: E402
import prepare_build  # noqa: E402
from prepare_build import (
    extract_pack_from_archive,
    get_upstream_pack_tree_hash,
    hash_resource_pack_tree_entries,
    next_custom_version,
    set_manifest_version,
    verify_only_expected_pack_changes,
)  # noqa: E402


class UpdateLangTests(unittest.TestCase):
    def test_replaces_only_existing_exact_keys_and_preserves_crlf_and_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.lang"
            output = root / "output.lang"
            source.write_bytes(
                b"\xef\xbb\xbf"
                b"# comment\r\n"
                b"effect.speed=old value\r\n"
                b"effect.speedExtra=keep me\r\n"
                b"effect.jump=old jump"
            )
            report = apply_mapping(
                source,
                output,
                {"effect.speed": "§c(Speed)§r ความเร็ว", "effect.jump": "กระโดด", "missing.key": "not added"},
            )
            self.assertEqual(report.matched_keys, 2)
            self.assertEqual(report.matched_lines, 2)
            self.assertEqual(report.missing_keys, ["missing.key"])
            self.assertEqual(
                output.read_bytes(),
                b"\xef\xbb\xbf# comment\r\n"
                + "effect.speed=§c(Speed)§r ความเร็ว\r\n".encode("utf-8")
                + b"effect.speedExtra=keep me\r\n"
                + "effect.jump=กระโดด".encode("utf-8"),
            )

    def test_mapping_requires_string_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapping.json"
            path.write_text(json.dumps({"a": 1}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_mapping(path)

    def test_zero_matches_are_reported_not_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.lang"
            output = root / "output.lang"
            source.write_text("existing.key=value\n", encoding="utf-8")
            report = apply_mapping(source, output, {"not.present": "new"})
            self.assertEqual(report.matched_lines, 0)
            self.assertEqual(output.read_text(encoding="utf-8"), "existing.key=value\n")


class PackTests(unittest.TestCase):
    def create_pack(self, root: Path):
        (root / "texts").mkdir(parents=True)
        manifest = {
            "format_version": 2,
            "header": {
                "name": "Test Pack",
                "uuid": "3d11bf9c-a5a6-4d9a-a7ec-fbfeb12b3690",
                "version": [1, 2, 7],
            },
            "modules": [
                {
                    "type": "resources",
                    "uuid": "1269ef24-59c0-4804-803b-8ed501460932",
                    "version": [1, 2, 7],
                }
            ],
        }
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (root / "texts" / "th_TH.lang").write_text("effect.speed=ความเร็ว\n", encoding="utf-8")
        (root / "texts" / "languages.json").write_text('["th_TH"]\n', encoding="utf-8")
        (root / "texts" / "language_names.json").write_text('[["th_TH", "ภาษาไทย (ประเทศไทย)"]]\n', encoding="utf-8")
        (root / "contents.json").write_text(
            json.dumps({"content": [{"path": "manifest.json"}, {"path": "texts/th_TH.lang"}, {"path": "texts/languages.json"}, {"path": "texts/language_names.json"}]}),
            encoding="utf-8",
        )

    def test_pack_build_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "THLanguage_RP"
            pack.mkdir()
            self.create_pack(pack)
            result = validate_folder(pack)
            self.assertEqual(result["version"], [1, 2, 7])
            output = build_mcpack(pack, root / "dist" / "test.mcpack")
            archive_result = validate_mcpack(output)
            self.assertGreater(archive_result["archive_entries"], 0)
            with zipfile.ZipFile(output) as archive:
                self.assertIn("manifest.json", archive.namelist())
                self.assertIn("texts/th_TH.lang", archive.namelist())

    def test_extract_pack_from_upstream_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_pack = root / "repo" / "THLanguage_RP"
            source_pack.mkdir(parents=True)
            self.create_pack(source_pack)
            archive_path = root / "upstream.zip"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for file_path in source_pack.rglob("*"):
                    if file_path.is_file():
                        archive.write(file_path, file_path.relative_to(root).as_posix())
            extracted = extract_pack_from_archive(archive_path, root / "extracted")
            self.assertTrue((extracted / "manifest.json").is_file())
            self.assertTrue((extracted / "texts" / "th_TH.lang").is_file())

    def test_upstream_tree_hash_uses_only_files_in_resource_pack_subtree(self):
        pack_entries_a = [
            {"path": "manifest.json", "type": "blob", "sha": "a" * 40},
            {"path": "texts/th_TH.lang", "type": "blob", "sha": "b" * 40},
        ]
        pack_entries_b = [
            {"path": "texts/th_TH.lang", "type": "blob", "sha": "b" * 40},
            {"path": "manifest.json", "type": "blob", "sha": "a" * 40},
        ]
        self.assertEqual(hash_resource_pack_tree_entries(pack_entries_a), hash_resource_pack_tree_entries(pack_entries_b))
        changed_pack = [
            {"path": "manifest.json", "type": "blob", "sha": "a" * 40},
            {"path": "texts/th_TH.lang", "type": "blob", "sha": "c" * 40},
        ]
        self.assertNotEqual(hash_resource_pack_tree_entries(pack_entries_a), hash_resource_pack_tree_entries(changed_pack))

    def test_upstream_pack_detection_queries_only_the_THLanguage_RP_subtree(self):
        root_tree = {
            "tree": [
                {"path": "README.md", "type": "blob", "sha": "d" * 40},
                {"path": "LICENSE", "type": "blob", "sha": "e" * 40},
                {"path": "THLanguage_RP", "type": "tree", "sha": "f" * 40},
            ]
        }
        pack_tree = {
            "truncated": False,
            "tree": [
                {"path": "manifest.json", "type": "blob", "sha": "a" * 40},
                {"path": "texts/th_TH.lang", "type": "blob", "sha": "b" * 40},
            ],
        }
        with patch.object(prepare_build, "request_json", side_effect=[root_tree, pack_tree]) as request:
            result = get_upstream_pack_tree_hash("1" * 40)
        self.assertEqual(result, hash_resource_pack_tree_entries(pack_tree["tree"]))
        self.assertEqual(request.call_count, 2)
        self.assertTrue(request.call_args_list[0].args[0].endswith("/git/trees/" + "1" * 40))
        self.assertTrue(request.call_args_list[1].args[0].endswith("/git/trees/" + "f" * 40 + "?recursive=1"))

    def test_version_bumps_patch_when_upstream_version_is_not_newer(self):
        self.assertEqual(next_custom_version((1, 2, 7), [1, 2, 7]), (1, 2, 8))
        self.assertEqual(next_custom_version((1, 3, 0), [1, 2, 8]), (1, 3, 0))

    def test_manifest_version_change_preserves_uuids_and_other_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            built = root / "built"
            source.mkdir()
            self.create_pack(source)
            import shutil
            shutil.copytree(source, built)
            self.assertTrue(set_manifest_version(built, (1, 2, 8)))
            changed = verify_only_expected_pack_changes(source, built)
            self.assertEqual(changed, ["manifest.json"])
            manifest = json.loads((built / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["header"]["version"], [1, 2, 8])
            self.assertEqual(manifest["modules"][0]["version"], [1, 2, 8])


if __name__ == "__main__":
    unittest.main()
