# Builder installation destination and preflight

The native bootstrap reads bounded setup/size metadata into memory, then offers
Install, Change folder and Cancel before creating an installation directory,
downloading a ZIP, extracting, pruning or writing a location preference. The
folder picker chooses an existing parent; setup uses its dedicated
`HookshotBuilder` child. It does not create folders in the picker.

An insufficient-space plan still displays its truthful totals and offers Change
or Cancel. Install is disabled, and the coordinator rejects a forced Install.
The same applies to a validated root whose resulting file paths are too long.
An owned source used for Update/Repair cannot redirect to another root.

## Publishing the size companion

`hookshot-setup.txt` retains its grammar and publisher source identity. The
revision-aware managed updater rejects unknown records, including comments, so
size hints are a separate `hookshot-setup-space.txt` release asset:

```text
HookshotSetupSpace 1
manifest <SHA-256 of the exact setup-list bytes>
space <part SHA-256> <unpacked file bytes> <regular files> <ZIP entries> <maximum final relative UTF-16 path units>
```

Upload both text files beside the thin bootstrap in the same official release.
The companion is not served from the ZIP part `--base` URL. Publish the exact
pair generated together; changing the list invalidates the companion. The new
bootstrap requires this metadata and cannot install an older release missing it.
Older consumers remain compatible with the unchanged list grammar.

There is exactly one space record per part SHA. Internal facts come from every
just-written ZIP, including the .NET runtime. LLVM facts are pinned to its exact
SHA, compressed size and declared archive root, from the verified archive's ZIP
directory. A changed external pin without verified facts stops publication.
Nothing is downloaded just to estimate its size.

The Windows publisher preserves `--reuse` for tool folders absent from the staged
package. It requires `--reuse-space` bound to the exact prior list and inherits
only that selected part's facts. Every newly written ZIP is measured again. A
missing, stale or incomplete companion fails before the new list is published.
The workflow fetches the prior list/companion as a pair and uploads the new pair.

First adoption requires maintainer action because older releases lack this
companion. With all of that prior release's ZIPs already available locally, run:

```text
python -I scripts/release_parts.py space previous-hookshot-setup.txt existing-zips previous-hookshot-setup-space.txt
```

This verifies every ZIP's full SHA and compressed length and reads its directory,
including empty directories and final UTF-16 path lengths after the declared
destination/root rename. It never downloads or extracts. Upload the generated
file as `hookshot-setup-space.txt` beside the unchanged prior list before enabling
workflow reuse, or fully stage the current tool parts without reuse. The workflow
deliberately fails if it selects a legacy list without verified facts; incomplete
historical inventories do not substitute for archive metadata. No real legacy
companion was generated as part of this change.

The UI distinguishes full compressed download bytes (before cache reuse), exact
unpacked logical file bytes, a conservative logical budget of compressed plus
unpacked bytes, and free bytes on the selected volume. Filesystem overhead can
need more; this is not allocated disk space. Compiler work and completed games
have separate destination choices and are not included. This change does not
implement the game-work picker/cleanup in PR623.

## Ownership and transaction

Roots must be a new or empty dedicated `HookshotBuilder` child, or carry the
exact `.hookshot-install-root` bytes `HookshotInstallRoot 1\n`. Supported paths
are normal absolute Windows drive paths with no reparse ancestors. All planned
final, unique pending and SHA-download paths must fit the native 260 UTF-16-unit
buffers. Long paths are rejected without truncation, elevation, registry or PATH
changes.

Version folders additionally need the exact `.hookshot-install-version` bytes
`HookshotInstallVersion 1\n`. A root marker does not authorize deleting unknown
subdirectories. Native cleanup keeps the released current-only policy for owned
versions, renaming before deletion; unknown/unmarked folders and rename-denied
busy versions stay. Its download cleanup applies only to a marked cache and
unreferenced SHA-named ZIPs. Unknown cache filenames stay untouched.

After explicit Install, a shared exclusive root lock serializes participating
native/managed attempts. Each attempt atomically claims `.setup-<32 hex>` and
uses its own payload and downloads. Preparation/hash/GUI validation finishes
before a unique `v-<revision prefix>-<nonce>` version is published. Repair never
deletes or overwrites the previous same-tag payload before the replacement is
ready. Failure or cancellation cleans only the attempt's new artifacts; failure
to replace `current.txt` rolls back only the newly published version. The current
pointer uses same-volume atomic replacement after readiness.

The GUI's source identity and EXE/DLL digests are checked without loading a PE.
A per-version `.hookshot-published-gui-sha` receipt hashes the GUI manifest that
was verified during preparation. Current-version reuse checks this receipt as
well as tag/revision/file hashes, so substituting local GUI bytes and their local
hash manifest cannot bless an old GUI. This is local installation bookkeeping,
not a signature scheme against an attacker who controls all installation files.

## Source root, legacy installs and location

The native bootstrap recognizes its own executable only under a marked
`versions/<leaf>/HookshotBuilder` source. The managed updater uses the actual
executing GUI directory in that layout. A cache path is not source ownership.
Invalid marked source layouts fail without silently selecting AppData or a
saved preference.

Unmarked legacy managed installations keep the existing verified updater route.
Native legacy Repair offers explicit migration to a dedicated root and leaves
the old unmarked root untouched. The current GUI's Repair button shares managed
verified preparation with updates and forces a fresh unique target; it does not
execute a cached or legacy bootstrap. Owned managed updates/Repair put versions
and their marked download cache under the chosen source root, with no AppData
fallback on permission, space or I/O failure.

Only a successful native installation writes
`%LOCALAPPDATA%\HookshotBuilder\install-location.txt`, using UTF-8
`HookshotInstallLocation 1\n<canonical root>\n` and atomic replacement. A fresh
bootstrap/game launcher may read a valid owned saved root; an owned Update/Repair
source takes precedence. Locator failure preserves the previous locator and
reports the installed path. No unrelated settings or system-wide policy change.

Managed preparation retains earlier versions, as the existing managed updater
already did; it does not add a cleanup/lease mechanism. Native installation alone
applies current-only pruning. This difference can consume extra disk space until
native maintenance or deliberate user cleanup; it is not a configurable
retention policy or proof that every concurrent legacy installer/worker is safe.

## Checks and limits

Publisher fixtures use authored source/package/ZIP bytes. Win32 plan,
transaction and integration fixtures use only checked authored temporary roots,
actual production headers/coordinator/preparation/prune bodies, real sharing
denials, digest failures, low-space Change/Cancel gates, and byte/attribute
preservation assertions. Native integration substitutes transport/extraction;
it does not run Windows tar, the real installer, compiler tools or a payload GUI.
Managed owned-root fixtures call the actual shared downloader/archive validation
with an offline HTTP handler, fresh targets, LLVM rename, cancellation and real
denied atomic handoff. Existing identity/transport/archive fixtures stay intact.

The separately compiled TEST preview uses the actual consent loop, dialog and
folder picker with authored size/free-space facts and temporary parents. Install
records consent only; transport, preparation, persistence, pruning and program
launches are disabled. UI execution requires the designated review phase.

Real installer/network/Repair execution, native tar extraction and a real game
build are not demonstrated by these fixtures. Selected invalid folders currently
produce a bounded error and require rerunning setup; repeated chooser recovery
is a remaining UX refinement. Snapshot/rename checks do not prove all arbitrary
concurrent legacy processes safe. Missing source/space metadata fails closed.
