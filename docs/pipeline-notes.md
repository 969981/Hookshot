# Pipeline notes (`hookshot_pipelines.bin`)

Every graphics pipeline the game asks for is written once to `hookshot_pipelines.bin` beside the executable
(`PipeNote` in `gpu/vk/vk_draw.cpp`). A release build reads it before the game starts and builds all of them on every
CPU (`precompile_run`), so the second start has fewer first-time builds during play. The first start, and every new
area, still builds pipelines while you play: see issues 334 and 384.

## The file

Two `u64` (the translator's stamp, the size of a record), then records. A record starts with its pipeline's key
(`u64`) and holds the program key and variant, topology, target formats and a copy of the draw state (`NvnState`).
The stamp is `program_cache_stamp()`: a file made by another translator, or with a different record size, is
ignored by the game and written again.

## Tool

```
python -I scripts/pipenotes.py info  hookshot_pipelines.bin
python -I scripts/pipenotes.py merge merged.bin mine.bin theirs.bin
```

`merge` keeps the first record of every key and refuses files with another stamp or record size. A record cut short at
the end of a file (the game closed while writing) is dropped. The output is written whole and renamed over, and cannot
be one of the inputs. Tested without the game by `tests/test_pipenotes.py`.

## Before you give a file to someone else

Not checked, and not shown to be safe:

- The file holds no game files, but each record carries the draw state as it was, and `NvnState` has 64-bit values
  from that run (buffer addresses, texture and sampler handles). Whether `build_graphics` ever reads them is not
  known. If it does, a record from another player's run can build a wrong pipeline.
- A bad list stops the game in the precompile step. The game guards against that (`hookshot_pipelines.bin.running`
  makes the next start drop the notes) but it costs the start it happens on.
- A merged file is only useful for a build with the same stamp, that is the same release.

A list meant to be shared should hold only the fields `build_graphics` reads, in a format that does not depend on the
compiler. That needs the runtime changed and tested on a GPU, and is not done here.
