# Automated upstream synchronization and preview releases

The configured successor candidate is [Hookshot self-hosted Git](https://git.notrexed.dev/Hookshot/Hookshot), branch `main`. On 2026-11-24 the web endpoint returned HTTP 521; availability and shared Git ancestry must be verified by the workflow before any merge. The old GitHub addresses are historical only.

Every four hours (UTC cron `23 */4 * * *`) and on manual dispatch, [Sync upstream and publish](../.github/workflows/sync-upstream.yml) checks the upstream head. When there are new commits it **merges** upstream `main` into this repository's `main` and pushes without rewriting history. If the merge conflicts, the job fails without forcing an update.

The job creates one unique tag per upstream commit: `v<next-patch>-rc.sync.g<upstream-sha-prefix>`. Repeated checks do not produce duplicate tags. Auto-synced builds are **GitHub prereleases**, not upstream stable releases.

It then uses `workflow_dispatch` to run the existing [Linux AppImage](../.github/workflows/linux-appimage.yml) and [Windows Builder](../.github/workflows/windows-builder.yml) jobs at that exact tag. Both workflows publish their assets only when the build succeeds; the Windows publishing job is enabled for `-rc.sync.` tags. Normal tags retain the upstream opt-in `CI_RELEASE_WINDOWS` variable.

This explicit dispatch is necessary because code and tag pushes using the built-in `GITHUB_TOKEN` do **not** trigger new `push` workflows. The synchronization job requires only the `contents: write` and `actions: write` permissions granted in its workflow; no personal access token is required. The repository's Actions settings must permit write access for `GITHUB_TOKEN`.

## Operation

- **Unavailable or unrelated upstream:** fail closed before merging or publishing; retain all previous snapshots. Do not use an unrelated mirror without provenance review.

- **Manual sync / retry:** Actions → Sync upstream and publish → Run workflow. When a tag exists but assets are missing, a manual run redispatches both builders.
- **Build status:** See Actions → Linux AppImage / Windows builder. A release appears under Releases once assets are uploaded.
- **Merge conflicts:** Resolve the conflicting upstream changes on `main`, preserving the local sync workflow and the Windows auto-publish guard; then run the sync job again.
- **No game data:** The existing jobs publish only the game-free builder and AppImage components. Never include game images, keys or generated game executables.

GitHub Actions cron runs are best-effort and may be delayed; they do not guarantee execution at an exact minute.
