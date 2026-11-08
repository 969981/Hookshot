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
# Or publish checksum-addressed parts, including the supported setup list:
python -I scripts/make_setup_parts.py build/HookshotBuilder RELEASE_TAG build/setup-parts
```

For the parts route, upload **both** `hookshot-setup.txt` and `hookshot-setup-space.txt` alongside the thin bootstrap in the
same official release. Upload the referenced ZIPs to their declared part URLs (the `--base`/builder-parts location). The space
companion belongs beside the setup list, **not** at that part base. It contains the SHA256 of the exact setup-list bytes;
publish the pair together without modifying either file or reusing a companion from a different release. The new native
bootstrap requires the revision-aware list and matching companion before offering an installation. It does not install older
releases that lack this metadata. Older bootstrap/updater parsers keep their existing setup-list grammar; no unknown records
or comments are added to that list. See [the installation contract](../builder/setup/PLAN.md) for destination and space limits.

When retaining earlier tool parts with `--reuse previous-hookshot-setup.txt`, also pass
`--reuse-space previous-hookshot-setup-space.txt`. The companion must bind to the exact previous list; staged ZIPs are measured
again. The Windows workflow fetches the pair and uploads both new text assets. For the first release using this contract,
the maintainer must generate and upload the previous release's companion from existing SHA-verified ZIPs using
`scripts/release_parts.py space <previous list> <existing ZIP directory> <out companion>`, or fully stage all current parts
without reuse. Missing facts are an explicit workflow/publisher failure, with no automatic payload download to estimate sizes.

The publish target writes `builder-revision.txt`. The packer runs on Windows and verifies that marker against the source revision
embedded in both GUI files' Win32 ProductVersion at compilation, as well as the clean checkout, tag target and staged
build sources before writing `tools/version.txt`, `tools/VERSION` (the displayed release name) and `tools/revision.txt` and creating the ZIP. A stale staged renderer or an old
GUI publish is rejected rather than stamped with today's revision. Refreshing the sidecar with `--no-build` cannot bless an old
EXE or managed DLL. Upload that ZIP for the same tag. `revision.txt` identifies
the source; it is not the digest of the ZIP containing it. Both packaging routes use the same staged-source and compiled-GUI guard.
They also write `builder-identity.json`, containing the tag/revision and SHA256 of the EXE and managed DLL. Its own hash is excluded.
These GUI hashes are checked before launch and whenever an installed builder is reused. Refreshing version/revision text files cannot
make a stale GUI match the verified package. Reuse also compares the local GUI manifest with the manifest in the verified release archive;
rewriting both local GUI bytes and local hash metadata cannot make them the published builder. When GitHub supplies an asset SHA256,
the downloader verifies that digest too.

The managed updater keeps the released five-cycle retry schedule (3, 6, 12 and 20 seconds), GitHub API asset fallback and early-EOF
retries. Metadata requests and asset response headers have a 30-second bound. Each asset body read has a fresh 60-second inactivity
bound, so a large download that keeps making progress has no overall time limit. User cancellation, HTTP 403/404, invalid release
identity, declared-size mismatches and checksum failures do not trigger an alternate download or retry. Failed attempts discard only
their own partial file; a completed cached file is reused only after checking its size and SHA-256.

The setup list includes `identity 1 <full source revision>` alongside its existing tag, parts and run fields. Older standalone setups
ignore that line. The consent-based setup validates it and the separate size companion. The managed updater reads the list itself, downloads only missing
or invalid SHA-addressed ZIP parts, and applies the declared LLVM top-directory relocation inside a fresh owned staging folder. It never
downloads or executes the thin setup EXE. Archive paths, links, duplicate files, destinations and renames are checked before installation.
The complete ZIP remains a fallback when available; a legacy setup list can use that path only if the ZIP has supported package identity.

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

Releases without revision-aware setup metadata and without a supported full ZIP cannot be accepted automatically. This includes legacy
installer-only packages: install/rebuild manually with a revision-aware package once available. The updater does not infer the source of
downloaded bytes from the current tag or run an older bootstrap to bypass missing metadata.

Skipping an offer skips one tag/revision pair. A corrected source revision can be offered again. Downloaded builders use separate
identity-specific directories, so a corrected same-tag package does not replace the directory of the running builder.

## Limits and checks

This is **source identity**, not a complete toolchain/content manifest. Repacking changed compiler or library binaries under the
same tag and source commit does not trigger an offer. Publish such a change under a new tag if it should be offered to existing
users. The Linux runtime's automatic update offer remains out of scope.

Game-free checks are `tests/test_release_identity.py`, `tests/test_update_identity_c.py` and
`tests/update_identity/UpdateIdentityTests.csproj`. Their inputs are authored metadata, source files and ZIPs; they do not read games
or run the GUI. The native negative control retains the previous tag-only decision and must fail the corrected-source case.
Managed tests exercise actual download/preparation code against an offline HTTP handler, including manifest-only releases, ZIP fallback,
hash caches, truncation, release changes, malformed handoffs, LLVM relocation, archive boundaries and stale GUI bytes. A separate local
check keeps a real old GUI binary immutable and confirms it is rejected against fresh markers and the new GUI manifest without execution.
Authored stalled responses and streams exercise header/read inactivity, continuing progress, cancellation and the actual new-builder
command-line handoff. These checks do not demonstrate a real network update, installer handoff or subsequent game rebuild.
