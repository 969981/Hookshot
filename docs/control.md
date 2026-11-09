# The control bridge

A local socket inside the game process that tools, tests and coding agents use to drive a running game and read its
state. Today they write presses into a file the game polls (`HOS_INPUT_LIVE`), take screenshots with `RX_VK_SHOTS` and
read the game's state from the log (the `vk: N presents, F fps; a frame: D draws` line). The bridge does the same things
through one connection, with replies. Presses are timed in frames, and every reply carries the frame it was made at.

- The game side is `host/control.c`. Hooks: `host/nn_hid.c` (the controller's read), `gpu/vk/vk_replay.cpp` (shots at a
  present), `gpu/vk/vk_menu.cpp` (the Insert menu's settings).
- The client is `scripts/hookshot_ctl.py` (standard library only).
- Tests: `tests/test_control.py` (the bridge against a fake game) and `tests/test_hookshot_ctl.py` (the client against a
  fake bridge).

## Turning it on

```
HOS_CONTROL=/run/user/1000/hookshot/control.sock ./hookshot
```

It is off unless `HOS_CONTROL` is set. When it is on, the log says so once:
`control: listening on <path> (HOS_CONTROL: ...)`. On Windows the value is a named pipe, `\\.\pipe\<name>`; a bare name
gets that prefix.

`hookshot_ctl.py launch <game folder>` starts a game folder this way. It makes a run folder of its own (mode 0700) and
puts the socket (`control.sock`), the log (`log.txt`) and the screenshots (`shots/`) there. It waits until the bridge
answers, then prints the socket's path.

## Security

- **Opt-in.** There is no socket unless `HOS_CONTROL` names one.
- **Local only.** There is a Unix domain socket on Linux and a named pipe on Windows, and never TCP. The pipe refuses
  remote clients.
- **This user only.** The socket is created with mode 0600 (the umask is set around `bind` too), and a client whose
  `SO_PEERCRED` user differs from the game's is turned away. The pipe's DACL grants the current user alone.
  `FILE_FLAG_FIRST_PIPE_INSTANCE` keeps another process from owning the name first.
- **Nothing in the way is replaced.** A file at the path that is not a socket is left alone and the bridge stays off. A
  socket that a live game answers on is left alone too. A stale one, left by a game that ended, is removed.
- **Guest memory is a second opt-in.** It is off unless `HOS_CONTROL_MEM` is set, apart from `HOS_CONTROL`:
  `HOS_CONTROL_MEM=ro` allows reads, `rw` allows reads and writes (aliases: `r`/`read` for `ro`, `readwrite` for `rw`;
  case does not matter; any other value logs once and leaves it off). See [Guest memory](#guest-memory).
- **What a client can do.** Anything the player can do with a controller and the Insert menu. It can also write a PNG to
  any path the game's user can write, and it can end the game.
- **The game never waits for a client.** The bridge's own thread does all the socket I/O. The controller's read takes
  one short lock that the bridge never holds over I/O. A client that sends nonsense gets errors. A client that stops
  reading is dropped once 8 MB waits for it, and one that goes away mid-command has its presses let go.

Under a Landlock sandbox of the game's process, the bridge has to start before the restriction (it starts with the
game). The socket's folder must stay writable for shots taken there.

## Protocol

The protocol is newline-delimited JSON. Each request is one JSON object on one line, at most 64 KB. Each reply is one
line, and it gives back the request's `id` (any JSON value; `null` when there is none) and the current `frame` (the
game's presents so far):

```
> {"id": 1, "cmd": "status"}
< {"id":1,"ok":true,"frame":5321,"protocol":1,"fps":59.8,"state":"play","stalled":false,"draws":2410.3,...}
```

Requests on one connection may be answered out of order: a `status` sent while a `wait` is pending is answered first.
Match replies by `id`. Lines with an `"event"` key and no `"ok"` key are subscription events (see `subscribe`).

### Errors

```
< {"id":7,"ok":false,"frame":5400,"error":"bad_request","message":"frames: 1 to 216000"}
```

| error | when |
|---|---|
| `bad_json` | the line is not JSON (`id` is `null`) |
| `too_long` | a line over 64 KB; the rest of it is skipped and the connection goes on |
| `bad_request` | not an object, no `cmd`, or a bad argument |
| `unknown_cmd` | no such command |
| `timeout` | a `wait`, `shot` or input did not finish before its `timeout` (seconds) |
| `busy` | too many requests waiting (64), inputs queued (256) or clients (8) |
| `unavailable` | the renderer is not up yet, or is not the Vulkan one (shots, settings); or a memory command that is not served |
| `forbidden` | a memory command while `HOS_CONTROL_MEM` does not allow it |
| `out_of_range` | a memory address or range outside mapped guest memory |
| `not_found` | no such setting |
| `failed` | a shot could not be written |
| `cancelled` | a press that was waited on was let go by `release` |

### Frames and inputs

A frame is one present of the game's own; frames made by frame generation do not count. An input starts at the first
controller read after its request arrives, plus `at` frames, and is held for `frames` frames. The game reads the
controller once a frame, so `"frames": 8` is 8 game frames. The bridge's buttons are added to the player's. Its sticks
replace the player's on the axes they set. `"exclusive": true` keeps the player's own pad, keyboard and mouse out from
the request until the input ends (compare `HOS_INPUT_EXCLUSIVE`).

Buttons use the names `HOS_INPUT_SCRIPT` uses: `A B X Y L R ZL ZR PLUS MINUS LS RS LEFT UP RIGHT DOWN`. The stick
directions are `LUP LDOWN LLEFT LRIGHT RUP RDOWN RLEFT RRIGHT`, and `LX= LY= RX= RY=` set an axis from -32767 to 32767
(positive y is up). Names are given as `"A+B"` or `["A", "B"]`.

The reply comes once the input is over, with the frames it covered: `{"start": 5400, "end": 5408}` (`end` is the first
frame without it). With `"wait": false` the reply is immediate: `{"queued": 1, "frames": 8}`.

### Commands

**status**: the frame, the frame rate over the last second, the draws a frame over the last ~300 ms, the state, the
picture's size, the version.

```
> {"id":1,"cmd":"status"}
< {"id":1,"ok":true,"frame":5321,"protocol":1,"fps":59.8,"state":"play","stalled":false,"draws":2410.3,
   "window":{"width":1280,"height":720},"uptime":95.2,"clients":1,"input":{"queued":0,"exclusive":false},
   "version":"1.4.0-abc123","renderer":"Vulkan: ..."}
```

`state` is a guess from the draws a frame, the way test scripts read the log:

| state | meaning |
|---|---|
| `boot` | nothing presented yet |
| `intro` | under 11 draws, before any menu was seen |
| `menu` | 11 to 149 draws |
| `loading` | under 11 draws, after a menu or play |
| `play` | 150 or more |
| `unknown` | no Vulkan renderer to count draws |

A new state has to hold for 300 ms before it is taken. `stalled` is true when no frame has come for a second.

**press**: `buttons`, `frames` (default 8), `at`, `exclusive`, `wait`, `timeout`.

```
> {"id":2,"cmd":"press","buttons":"A","frames":8}
< {"id":2,"ok":true,"frame":5410,"start":5401,"end":5409}
```

**stick**: `stick` (`L` default, or `R`), `x` and `y` from -1 to 1, then as press. Buttons can go with it.

```
> {"id":3,"cmd":"stick","stick":"L","x":0,"y":1,"frames":120,"exclusive":true}
```

**sequence**: `steps`, a list of press or stick objects each with its own `at`. Or `script`, `HOS_INPUT_SCRIPT`'s syntax
in frames, `<at>:<names>[/<frames held>],...` (8 frames held unless given). With `"units": "hos"` the script's times are
in seconds and its holds in milliseconds, as `HOS_INPUT_SCRIPT` and `HOS_INPUT_LIVE` take them (60 frames a second; 200
ms held unless given). The reply comes when the last step is over: `start` is the first step's start and `end` the last
step's end.

```
> {"id":4,"cmd":"sequence","script":"0:A/8,60:A/8"}
> {"id":5,"cmd":"sequence","script":"0:LUP/2000,2:A","units":"hos"}
> {"id":6,"cmd":"sequence","steps":[{"at":0,"buttons":"ZL","frames":30},{"at":10,"lx":-1,"frames":20}]}
```

**release**: every queued input is let go: `{"released": 3}`. Presses that were waited on reply `cancelled`.

**shot**: a PNG of the first present after the request, or after frame `after`. It is 1280x720, as `RX_VK_SHOTS`
writes them. `path` defaults to `shots/shot_<n>_f<frame>.png` in the socket's folder (made 0700). A relative path is
taken from the socket's folder, not the game's (on Windows, where a pipe has no folder, from `%TEMP%\hookshot-control-<pipe name>\`, which also holds `shots\`). A shot is the game's own picture: the Insert menu and the FPS counter, drawn over it on the way to the window, are not in it. The file is written whole under another name, then renamed, so the
path in the reply is complete. The `timeout` default is 30 s. Shots need the Vulkan renderer.

```
> {"id":7,"cmd":"shot","path":"/tmp/run/kokiri.png"}
< {"id":7,"ok":true,"frame":5440,"present":5432,"path":"/tmp/run/kokiri.png","width":1280,"height":720}
```

**wait**: `event`, plus a `timeout` in seconds (default 60). The reply has the state and the frames waited:
`{"state": "play", "waited": 1203}`. A timeout replies `timeout`, with the state at that time.

| event | until |
|---|---|
| `present` | `frames` more frames (default 1) |
| `frame` | frame number `frame` |
| `in_play` | the state is `play` (at once if it is) |
| `state` | the state is `state` |
| `loading_start` | the next change into `loading` |
| `loading_end` | the next change out of `loading` |
| `change` | the next change of state |

**option_get**: the Insert menu's settings, or one of them by `name`. A name is the setting's key in
`hookshot_options.ini` (`text_speed`) or its row's name (`"Text speed"`), in any case, with `-` taken as `_`.

```
> {"id":8,"cmd":"option_get","name":"text_speed"}
< {"id":8,"ok":true,"frame":5500,"option":{"key":"text_speed","name":"Text speed","tab":"gameplay","value":0,
   "min":0,"max":3,"step":1,"text":"Normal"}}
```

**option_set**: `name` and `value` (a number, or `true`/`false`). The value is set as the menu's Left and Right set it:
cut to the row's range, and the quality preset turned to Custom when the menu would do that. It is kept in
`hookshot_options.ini`. The reply has the value it had before and the setting as it is now. Rows marked "(restart)" in
the menu take effect at the next start.

```
> {"id":9,"cmd":"option_set","name":"FPS counter","value":true}
< {"id":9,"ok":true,"frame":5510,"was":0,"option":{"key":"fps_counter",...,"value":1,"text":"On"}}
```

**subscribe**: a stream of events on this connection until `unsubscribe` or a disconnect; another `subscribe` replaces
it. Arguments:

- `events`: any of `present`, `state` and `log`. The default is `["present", "state"]`.
- `every`: frames between present events (default 60).
- `log`: only log lines that contain this text (it also turns `log` on).

The events carry `sub`, the subscribe request's `id`:

```
< {"event":"present","sub":10,"frame":5600,"fps":59.9,"state":"play","draws":2405.0}
< {"event":"state","sub":10,"frame":5720,"state":"loading","from":"play","draws":5.0}
< {"event":"log","sub":10,"frame":5721,"line":"[hos sead::MainThread] ..."}
< {"event":"log_dropped","sub":10,"lines":40}
```

**quit**: the reply, then the game exits as when its window is closed (the log flushed, exit code 0). The socket is
removed.

## Guest memory

Off unless `HOS_CONTROL_MEM` is set (apart from `HOS_CONTROL`):

| `HOS_CONTROL_MEM` | commands | aliases |
|---|---|---|
| unset / empty | none (`mem_*` reply `forbidden` with how to turn it on) | |
| `ro` | `mem_read`, `mem_read_many` | `r`, `read` |
| `rw` | the reads, and `mem_write` | `readwrite` |

Case does not matter. Any other value logs a line once and leaves memory off (it never falls back to a more permissive
level).

**This is a debugging tool, not a safe sandbox.**

- `ro` exposes all of the game's memory to a client. That includes values the runtime keeps for itself, not only the
  game's own.
- `rw` lets a client change that memory. A write can corrupt or crash the game and the runtime, not only move the game
  along, and a corrupted state can be saved to disk. **Use a throwaway save.**
- The socket is already this user's only (0600, `SO_PEERCRED`), so a client of the bridge can reach no memory that any
  other process of the same user could not already reach. The opt-in is about what the bridge itself will do, not about
  who can connect.

Addresses are guest addresses (the image is at `0x80000000`, the heap at `0x1000000000`). An address or range outside
the memory the runtime has mapped — the image, the system region in use, the heap, or an address region — is an error
(`out_of_range`), never a fault. Each request reads or writes at most 1 MB.

Reads and writes are done on the game thread, at a controller read, so a read is one frame's memory and a write lands
between frames. `mem_read_many` reads all its ranges in that one pass, so they are consistent with each other. Every
write is logged in `hookshot_log.txt` (`control: wrote N bytes to guest 0x...`).

A write to memory the GPU reads goes through the same soft write-watch the game's own CPU writes use (the page is marked
and synced into the renderer's VRAM mirror on the next frame's memory sync), so it reaches the GPU like any other CPU
write; this has not been checked against the GPU's output in a scene.

**mem_read**: `addr` and `size` (a number, or a `"0x..."` string for a large address). `format` is `hex` (default) or
`base64`.

```
> {"id":30,"cmd":"mem_read","addr":2147483648,"size":8}
< {"id":30,"ok":true,"frame":5000,"format":"hex","addr":2147483648,"size":8,"data":"030000141c300000"}
```

**mem_read_many**: `ranges`, a list of `{addr, size}`, read in one pass. The reply has a `ranges` array, each with its
`addr`, `size` and `data`.

**mem_write** (`rw` only): `addr`, and the bytes as `hex` or `base64`.

```
> {"id":31,"cmd":"mem_write","addr":4300000000,"hex":"2a000000"}
< {"id":31,"ok":true,"frame":5001,"wrote":4,"addr":4300000000}
```

TODO: named game values (scene and room id, Link's position, health, rupees, ...) through `mem get NAME` and a
`symbols` command: planned, once addresses are located and verified for this build. `symbols` replies `unavailable` for
now.

## The client

```
export HOS_CONTROL=$(python -I scripts/hookshot_ctl.py launch ~/Games/Hookshot --data /tmp/oot-data --headless)
# (a first start prepares the shaders before the first frame: the game reads no controller until then)
python -I scripts/hookshot_ctl.py wait frame --frame 1 --timeout 900
# (the title and the file select need A: press it until the save is loaded)
until [ "$(python -I scripts/hookshot_ctl.py --json status | jq -r .state)" = play ]; do
    python -I scripts/hookshot_ctl.py press A --frames 8; python -I scripts/hookshot_ctl.py wait present --frames 60
done
python -I scripts/hookshot_ctl.py status
python -I scripts/hookshot_ctl.py stick L 0 1 --frames 90 --exclusive
python -I scripts/hookshot_ctl.py press A --frames 8
python -I scripts/hookshot_ctl.py sequence '0:A/8,60:A/8'
python -I scripts/hookshot_ctl.py sequence '0:LUP/2000,2:A' --hos       # HOS_INPUT_LIVE's line, as it is
python -I scripts/hookshot_ctl.py shot out.png
python -I scripts/hookshot_ctl.py option get
python -I scripts/hookshot_ctl.py option set text_speed 3
python -I scripts/hookshot_ctl.py watch --events present,state --log "hid:"
python -I scripts/hookshot_ctl.py raw '{"cmd": "wait", "event": "frame", "frame": 9000}'
python -I scripts/hookshot_ctl.py mem read 0x80000000 8               # needs HOS_CONTROL_MEM=ro or rw
python -I scripts/hookshot_ctl.py mem read 0x1000000000 16 --format u32
python -I scripts/hookshot_ctl.py mem write 0x1000000000 2a000000      # needs HOS_CONTROL_MEM=rw
python -I scripts/hookshot_ctl.py quit
```

Each command prints plain lines; `--json` prints the replies. `--sock` overrides `$HOS_CONTROL`. The exit status is 0
when done, 1 for an error reply, 2 when there is no game or the usage is bad, and 3 for a timeout. `launch --data`
sets `XDG_DATA_HOME` for the run, so it plays from saves of its own. `--env NAME=VALUE` adds switches.

## Tests and what is not tested

`tests/test_control.py` builds the bridge with a fake game and drives it over its real transport: the Unix socket on
Linux, the named pipe on Windows (the CI's windows-2022 runner). On Windows it also checks the pipe's access control:
another first instance of the name and a second bridge on it are refused, and the DACL, read back through a client's
handle, is protected with a single ACE for the current user. `tests/test_hookshot_ctl.py` drives the client against a
fake bridge on the same transports.

Not tested: refusing remote clients (`PIPE_REJECT_REMOTE_CLIENTS`; that needs a second machine), `hookshot_ctl.py
launch` on Windows, and the bridge inside a Windows game build (only the Linux game was run).
