# Setup preflight contract (phase 1)

This contract is not yet wired into `setup.c`. It supplies publisher facts and
read-only policy for the destination/consent integration. It does not claim that
the released installer already asks for consent or rolls an installation back.

## Space companion

`hookshot-setup.txt` keeps its existing grammar, including the publisher source
identity. The published managed updater rejects unknown lines, even comments,
so size hints belong in the optional `hookshot-setup-space.txt` asset:

```text
HookshotSetupSpace 1
manifest <SHA-256 of the exact setup-list bytes>
space <part SHA-256> <unpacked file bytes> <regular files> <ZIP entries> <maximum final relative UTF-16 path units>
```

There must be one record for every part, with no duplicate or extra SHA. Internal
facts come from each just-written ZIP directory. External LLVM facts are pinned
to its exact SHA, compressed size and declared archive root; changing that pin
without verified facts stops publication. No archive is downloaded for display.

Unpacked bytes are logical file bytes, not allocated disk space. A complete plan
reports exact compressed/unpacked totals and a conservative **logical reserve**
of full compressed plus unpacked bytes, before cache reuse. Filesystem overhead
may require more space, and verified cached parts may reduce new writes. The UI
must keep those labels distinct. Missing hints, a mismatched manifest digest,
invalid numbers or overflow cannot produce a ready plan or a guessed total.

Maximum paths include internal `HookshotBuilder/` paths and the external LLVM
destination/rename. The native contract rejects paths exceeding its supported
buffer limit instead of truncating them or changing system long-path policy.

## Root and handoff

The chooser selects a parent; the displayed destination is its dedicated
`HookshotBuilder` child. Read-only preflight accepts a new child, an empty child,
or an existing root carrying the exact `.hookshot-install-root` bytes:

```text
HookshotInstallRoot 1
```

Nonempty unmarked roots are not silently adopted. Absolute local-drive paths
are normalized lexically; unsupported namespaces and reparse components are
rejected. Spaces and Unicode are supported. Existing legacy-root migration is
not implemented by this phase and must not become implicit deletion authority.

Version folders live at `versions/<safe leaf>/`. A root marker alone never makes
unknown subdirectories prunable: the version must also carry the exact
`.hookshot-install-version` bytes `HookshotInstallVersion 1\n`. This ownership
guard is separate from current/previous retention and process-lifetime policy.

For Update/Repair, an owned source executable named `HookshotBuilder.exe` or
`HookshotBuilder-Setup.exe` under `versions/<leaf>/HookshotBuilder/` determines the root.
Failure to recognize it returns a bounded error; it must not silently select a
saved preference or AppData. For a fresh bootstrap only, a valid saved owned root
can be the default. Existing `--update` and `--repair` forwarding is unchanged in
this phase; there is no new bootstrap command-line flag.

The future single per-user locator is
`%LOCALAPPDATA%\HookshotBuilder\install-location.txt`, written only after success.
Its UTF-8 bytes are `HookshotInstallLocation 1\n<canonical root>\n`; the stored
path is revalidated as an owned root before use. This phase provides the
success-only persistence callback gate, not a settings writer. Registry, PATH,
elevation and unrelated settings are outside the contract.

## Consent and transaction boundary

Install, Change and Cancel must be presented before any filesystem effect,
payload request, extraction, pruning or preference write. `setup_plan_begin`
permits the first-effect callback only for a complete plan and explicit Install,
and rechecks root ownership facts. Change/Cancel invoke no effect callback.

The integration must use a unique `.setup-<32 hex>` attempt beneath the chosen
root, with `payload/` and `downloads/<SHA>.zip`. A valid existing version is not
removed before its replacement is ready. Cancellation/failure removes only the
attempt's own new artifacts, preserving previous versions, caches and settings.
Busy same-tag replacement must fail with the old files preserved; it must not
kill processes or invent a blanket retention policy. `setup_plan_remember`
permits persistence only after the caller reports a successful commit.

Snapshot rechecks do not prove all concurrent installers safe. Atomic commit,
busy-directory handling, cancellation cleanup and effect ordering still require
review and authored integration fixtures before this header is wired into the
installer. The managed updater will need the same owned-root policy without
weakening its existing identity, transport or archive guards.

## Checks

`python -I -B tests/test_setup_plan.py PublisherSpaceTests` uses authored package
bytes, never executable payloads. On Windows, pass `--native --cc <compiler>`
and `NativeSetupPlanTests` to compile the actual header with warnings as errors.
The native fixture uses authored temporary roots, verifies them before recursive
cleanup, records effect callbacks and checks that preflight leaves payload bytes
and Win32 attributes unchanged. No setup, GUI, tool payload or game is launched.
