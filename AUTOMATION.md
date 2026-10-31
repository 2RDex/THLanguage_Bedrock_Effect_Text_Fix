# THLanguage Bedrock Effect Text Fix — automation

This repository overlay adds a scheduled GitHub Actions workflow for the fork
`2RDeX/THLanguage_Bedrock_Effect_Text_Fix`.

## What it does

- Checks `MineGarp/THLanguage_Bedrock` on `main` every 15 minutes.
- Downloads a pinned upstream commit only when the upstream commit or local
  mapping/build scripts have changed.
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
- Publishes the `.mcpack` to both GitHub Actions Artifacts and GitHub Releases.
- Updates `upstream-state.json` only after a successful build/release, or after
  confirming that an upstream commit changed files outside the Resource Pack.

## Install into the existing fork

1. Extract this overlay into the root of your existing fork checkout. Merge the
   files; do not delete or replace the existing `THLanguage_RP/`, `LICENSE`, or
   upstream `README.md` folders/files.
2. Confirm the default branch is `main`.
3. In GitHub, open **Settings → Actions → General → Workflow permissions**.
4. Select **Read and write permissions** and save.
5. Confirm GitHub Actions is enabled for this fork in the **Actions** tab.
6. Commit and push the added files to `main`.
7. The first scheduled run will create the initial custom `.mcpack`. Scheduled
   runs are not guaranteed to start at the exact minute, so check the Actions
   tab after the next scheduled interval.

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
- **Releases:** persistent download page with the `.mcpack` attached.

The workflow skips publishing when the upstream commit, Resource Pack content,
mapping, and build scripts have not changed. A README-only upstream commit does
not produce a new `.mcpack`.

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
