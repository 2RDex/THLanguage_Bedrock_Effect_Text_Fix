# THLanguage Bedrock Effect Text Fix — automation

This repository overlay adds a scheduled GitHub Actions workflow for the fork
`2RDeX/THLanguage_Bedrock_Effect_Text_Fix`.

## What it does

- Checks `MineGarp/THLanguage_Bedrock` on `main` every 15 minutes, and also
  supports a manual **Run workflow** from the Actions tab.
- Detects upstream Resource Pack changes by hashing only the Git tree below
  `THLanguage_RP/`. Updates to upstream `README.md`, `LICENSE`, or other files
  outside that directory do not trigger an MCPACK build or Release.
- Downloads the upstream archive only when files under `THLanguage_RP/`, the
  local `mapping.json`, or the build scripts have changed.
- Applies the existing root-level `mapping.json` to
  `THLanguage_RP/texts/th_TH.lang` only. Existing keys are replaced; missing
  keys are reported and are never added.
- Preserves all other upstream Resource Pack files. The only permitted pack
  changes are `texts/th_TH.lang` and, when necessary, the version fields in
  `manifest.json` (`header.version` and each `modules[].version`). UUIDs remain
  unchanged.
- Does not generate `en_GB.lang` or `en_US.lang`, and does not rewrite
  `contents.json`, `languages.json`, or `language_names.json`.
- Validates the Resource Pack and the resulting `.mcpack` before publishing.
- In the temporary upstream build copy only, removes the three known stale `contents.json` references under `subpacks/font/` when their PNG files are absent (`default8.png`, `glyph_00.png`, `glyph_0E.png`). It does not fabricate files, does not modify the checked-in `THLanguage_RP/`, and still fails on any other missing content reference. If upstream restores one of these PNGs, its `contents.json` entry is kept.
- Publishes the `.mcpack` to both GitHub Actions Artifacts and automation-owned GitHub Releases. Automation releases use the `auto-effect-text-fix-` tag prefix so they are separated from manually created releases.
- Updates `upstream-state.json` only after a successful build/release, or after
  recording a new upstream commit that changed files outside `THLanguage_RP/`.

## Install into the existing fork

1. Extract this overlay into the root of your existing fork checkout. Merge the
   files; do not delete or replace the existing `THLanguage_RP/`, `LICENSE`, or
   upstream `README.md` folders/files.
2. Confirm the default branch is `main`.
3. In GitHub, open **Settings → Actions → General → Workflow permissions**.
4. Select **Read and write permissions** and save.
5. Confirm GitHub Actions is enabled for this fork in the **Actions** tab.
6. Commit and push the added files to `main`.
7. The first scheduled run will create the initial custom `.mcpack`. You can also start a run manually from **Actions → Upstream Sync and MCPACK Build → Run workflow**. Scheduled runs are not guaranteed to start at the exact minute.

No Personal Access Token or third-party secret is required. The workflow uses
GitHub's automatically provided `GITHUB_TOKEN`.

## Updating translations

Edit only the root-level `mapping.json`, commit, and push to `main`. The next
scheduled run detects the changed mapping hash, downloads the current upstream
pack and rebuilds the `.mcpack`.

`mapping.json` must be a JSON object whose keys and values are strings. Values
must remain single-line strings. Minecraft formatting codes such as `§c` and
`§r` are preserved as literal mapping content.

## Versioning

The initial custom build uses the upstream `manifest.json` version. For each
later custom build, the workflow adopts a newer upstream version when it is
higher than the last custom version. If the source pack or mapping changes
without a higher upstream version, it increments the patch component so
Minecraft can recognize the custom pack update. Both the manifest header and
module versions are kept in sync. UUIDs are not changed.

## Downloads

- **Actions Artifacts:** available on the successful workflow run; retention is
  set to 90 days (subject to GitHub repository/account limits).
- **Releases:** persistent download page with the `.mcpack` attached. Automation creates a separate release using an `auto-effect-text-fix-` tag. Existing manually created releases are never edited, deleted, or reused. If an automation tag already exists without the expected asset, the workflow stops rather than overwriting anything.

The workflow checks only the `THLanguage_RP/` subtree for upstream Resource
Pack changes. A README-only or LICENSE-only upstream commit outside that folder
updates the saved upstream commit state but does not download the full archive,
build a `.mcpack`, or create a Release. A change to the local mapping or build
scripts still triggers a new build because those directly affect the output.

## Important GitHub Actions behavior

GitHub scheduled workflows run from the repository's default branch and can be
delayed during periods of high load. GitHub may automatically disable scheduled
workflows in public repositories after 60 days without repository activity.
This workflow checks every 15 minutes, but it cannot guarantee real-time
execution at an exact minute.

## Manual local test

Python 3.12+ is recommended. From the repository root:

```bash
python scripts/update_lang.py THLanguage_RP/texts/th_TH.lang mapping.json --backup
python scripts/check_pack.py --folder THLanguage_RP
python scripts/build_mcpack.py THLanguage_RP dist/THLanguage_Bedrock_Effect_Text_Fix.mcpack
python scripts/check_pack.py --mcpack dist/THLanguage_Bedrock_Effect_Text_Fix.mcpack
```

The local updater edits the chosen language file. The GitHub workflow does not
edit the checked-in upstream pack folder; it works on a temporary copy.

## Attribution and license

This is a community modification of
[MineGarp/THLanguage_Bedrock](https://github.com/MineGarp/THLanguage_Bedrock).
Keep upstream credits, the upstream `LICENSE`, and any applicable third-party
notices. Follow the license terms that apply to the source material.
