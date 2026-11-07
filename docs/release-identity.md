# Release source identity

The updater identifies an official build by its release tag **and the full source commit**. A tag can be corrected without being
renamed: during `foliage-linux-updater`, the tag and builder archive changed from `e900faa8` to `170ce53` while the published time
stayed the same. Tag-only comparisons missed that correction.

## Publishing the Windows builder

Use a clean checkout with the release tag pointing at `HEAD`. Publish the GUI from that checkout, stage its tools as usual, and
include the current tracked sources under `host`, `nx2`, `gpu` (including `gpu/third_party`) and `third_party`. Tests and docs may
be omitted. Do not stage a built game, dumps, generated game code or saves.

For example, in PowerShell:

```powershell
$revision = git rev-parse HEAD
dotnet publish builder/HookshotBuilder/HookshotBuilder.csproj -c Release -o build/HookshotBuilder -p:SourceRevisionId=$revision
# Stage dependencies and the current tools/source files in build/HookshotBuilder/tools.
python -I scripts/package_builder.py --staged build/HookshotBuilder --tag RELEASE_TAG --out build/HookshotBuilder.zip
```

The publish target writes `builder-revision.txt`. The packer runs on Windows and verifies that marker against the source revision
embedded in both GUI files' Win32 ProductVersion at compilation, as well as the clean checkout, tag target and staged
build sources before writing `tools/version.txt`, `tools/VERSION` (the displayed release name) and `tools/revision.txt` and creating the ZIP. A stale staged renderer or an old
GUI publish is rejected rather than stamped with today's revision. Refreshing the sidecar with `--no-build` cannot bless an old
EXE or managed DLL. Upload that ZIP for the same tag. `revision.txt` identifies
the source; it is not the digest of the ZIP containing it. When GitHub supplies a ZIP SHA256, the downloader verifies that external
digest before extracting.

## Installation and compatibility

`make_game.py` copies the packaged tag/revision into `hookshot_version.txt`; it never fetches a revision to label local files.
A source build with no release markers removes a stale release stamp and keeps update offers disabled. The revised GUI marks
its builder path as update protocol2. An older GUI is not silently treated as protocol2.

Already shipped tag-only games cannot learn revision-aware checks by themselves when the tag stays the same. Rebuild once with
a current revision-aware builder, or install a subsequent new-tag release. If a revised executable encounters tag-only metadata,
it logs that manual bootstrap requirement without repeatedly displaying a dialog or claiming the folder is current.

An older updater's `--fetched --tag` handoff is accepted only when the downloaded package has its own valid revision marker and
that complete identity matches a fresh GitHub result. Missing package identity, a mismatched explicit revision, an offline
verification, or a release changed during download cannot bypass validation. Existing game files are not rebuilt until validation
succeeds. Both lightweight and annotated Git tags are resolved, with a bounded annotated-tag chain.

Skipping an offer skips one tag/revision pair. A corrected source revision can be offered again. Downloaded builders use separate
identity-specific directories, so a corrected same-tag package does not replace the directory of the running builder.

## Limits and checks

This is **source identity**, not a complete toolchain/content manifest. Repacking changed compiler or library binaries under the
same tag and source commit does not trigger an offer. Publish such a change under a new tag if it should be offered to existing
users. The Linux runtime's automatic update offer remains out of scope.

Game-free checks are `tests/test_release_identity.py`, `tests/check_update_identity_c.py` and
`tests/update_identity/UpdateIdentityTests.csproj`. Their inputs are authored metadata, source files and ZIPs; they do not read games
or run the GUI. The native negative control retains the previous tag-only decision and must fail the corrected-source case.
