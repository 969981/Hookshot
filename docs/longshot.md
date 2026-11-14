# Fixes from Longshot

[Longshot](https://git.magicbean.dev/MagicBeanDev/Longshot) is the magicbean.dev team's continuation of upstream
Hookshot (GPL-3.0, like this tree). It forked from upstream at `4dd972a8` (upstream #2888, 2026-11-12). Its fixes are
taken here as cherry-picks (`git cherry-pick -x`), each with its Longshot PR number and author. Its history is not merged:
release-please reads every commit a merge brings in, so a merge would put Longshot's rename, its Forgejo tooling and its
`feat(release)!:` in this changelog and version.

**Taken through:** Longshot `main` at `514591a3` (2026-11-13). The next pull starts at:

```bash
git fetch longshot
git log --reverse --oneline 514591a3..longshot/main
```

When a pull is done, the hash above moves to the new tip, in the same pull request.

## Adapting a pick

Longshot renamed the project, so its later commits carry its names. A pick turns them back:

- comment tags `(longshot)` and `(longshot: ...)` become `(hookshot)` and `(hookshot: ...)`;
- file names `longshot_*` (`longshot_env.h`, `longshot_options.ini`, `longshot_load_start.txt`, ...) become `hookshot_*`;
- environment names `LONGSHOT_*` become `HOOKSHOT_*`;
- "Longshot" in a message to the player becomes "Hookshot".

Watch for option ids too: two trees can number a new row alike (Longshot's Damage taken is 55; here it is 56, after Auto
run). `tests/test_option_ids.py` catches it.

## Up to 514591a3

Taken (Longshot PR): #7, #8, #39, #54, #55, #78, #94, #108, #110, #138, #176, #177, #178, #187, #202, #233, #247,
#265, #266. Also, in a pull request of their own because each needs its own measurement: #41, #197, #198, #260.

Already here from upstream, under other hashes: #49 (= upstream #2929), #77 (= #2972), #131 (= #3038), #147 (= #2732),
#4 (`third_party/zbic` is carried whole), #63 (`host/rx_backend.c` already steps over 256 clear marks at a time), #174
(this tree compiles the Linux back end itself, and the AppImage carries `third_party/fidelityfx`).

Not taken yet, each worth its own pull request:

| Longshot | What | Why not yet |
|---|---|---|
| #234 | Quick pipelines for draws whose full build is not done | A feature with its own performance measurement |
| #107 | OoT's clustered lights sample their own shadow maps | Changes the shader translator, so every cache is prepared again; needs a visual check |
| #15 | The segment heap, so the driver's compiles run side by side (Windows) | Windows only: measure on Windows |
| #158, #164, #167 | Compile shaders All / Most / None | In the Builders, which differ here |
| #172 | A new install starts at its screen's size | Overlaps upstream #2566's choice of the output by tier, which this tree has |
| #159 | Fog and exposure docs | Check against this tree's renderer first |

Left out on purpose: the rename (`e2a4d453`), Discord as Longshot's (#10, #194), its README, website and docs (#9,
#11, #104, #114, #168, #192, #193, #210, #214, #257, #258, #274), its nightly version display (#170), its Forgejo forge
tools, templates, CI and releases (#3, #5, #6, #13, #18, #21, #48, #72, #76, #79, #99, #105, #106, #113, #115, #116,
#117, #121, #126, #128, #129, #132, #133, #135, #140, #143, #146, #148, #149, #154, #160, #166, #254), the removal of
builders this tree still ships (#161, #163, #171, #175, #179, #181), and importing Hookshot saves into Longshot (#20).
