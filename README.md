# rocq-live

Live [Rocq](https://rocq-prover.org) (Coq) proof sessions for coding agents and
humans, in any project, with no per-project setup.

Without a live session, an agent that wants the goal at line 300 recompiles
lines 1–299 every time it looks. With `rocq-live`, each file gets one
long-running `coqtop`, so a step costs only that step:

```
$ rocq-live goto theories/Foo.v:42
1 goal
  n : nat
  ============================
  double n = 2 * n
rocq-live: checked up to theories/Foo.v line 41 (asked for line 42)

$ rocq-live run theories/Foo.v 'unfold double. lia.'
No more goals.
```

Edit the file and call `goto` again: only the text from the first changed
character onwards is re-checked.

It is one Python file with no dependencies. It works as a **CLI** (for any
agent with a shell) and as an **MCP server** (Claude Code, Codex, Cursor,
Gemini CLI, …).

## Install

```sh
uv tool install rocq-live            # or: pipx install rocq-live
```

Or run it without installing (`uvx rocq-live …`), or take the single file:

```sh
curl -fsSL -o ~/.local/bin/rocq-live https://github.com/kylezh/rocq-live/releases/latest/download/rocq-live
chmod +x ~/.local/bin/rocq-live      # any directory on PATH works
```

Requirements: Python ≥ 3.9 and `coqtop` (Rocq 9.x; see
[Compatibility](https://github.com/kylezh/rocq-live#compatibility)). opam and dune are optional. macOS or Linux.

## Connect your agent

The server is the same for every client: the command `rocq-live mcp` over stdio.

**Claude Code**

```sh
claude mcp add -s user rocq-live -- rocq-live mcp
```

**Codex** — `~/.codex/config.toml`:

```toml
[mcp_servers.rocq-live]
command = "rocq-live"
args = ["mcp"]
tool_timeout_sec = 600     # the first goto may build the file's imports
```

**Cursor, Gemini CLI, Claude Desktop, other MCP clients** — the usual JSON entry:

```json
{ "mcpServers": { "rocq-live": { "command": "rocq-live", "args": ["mcp"] } } }
```

**Agents without MCP** — put this in your `AGENTS.md` / `CLAUDE.md`:

```markdown
To see a proof state, use `rocq-live goto FILE:LINE` (state before LINE);
try tactics with `rocq-live run FILE 'tac.'`, undo with `rocq-live back FILE`,
query with `rocq-live query FILE 'Search ...'`. After editing FILE, goto again.
```

All tools are marked read-only (`readOnlyHint`): they never edit files. A
`goto` may build the file's imports, as a build would.

## Commands

| CLI                              | MCP tool        | does                                                    |
|----------------------------------|-----------------|---------------------------------------------------------|
| `goto FILE[:LINE] [--full]`      | `rocq_goto`     | state just before LINE (default: end, checking every proof); starts a session |
| `run FILE[:LINE] 'tac1. tac2.'`  | `rocq_run`      | run on the current state (or on the state before LINE); an error changes nothing |
| `query FILE 'Search …'`          | `rocq_query`    | run, print, undo (Search, Check, Print, About, …)       |
| `try FILE 'lia' 'auto' …`        | `rocq_try`      | each tactic alone on the current state: which work, goals left (best first); state unchanged |
| `audit FILE [NAME…] [--allow AX,…]` | `rocq_audit` | `Admitted`/`admit`/axioms, and `Print Assumptions` of each declaration against the allowed axioms: PASS/FAIL |
| `back FILE [N]`                  | `rocq_back`     | undo the last N runs                                    |
| `goals FILE`                     | `rocq_goals`    | current goals                                           |
| `start FILE[:LINE]`              | `rocq_goto` with `fresh` | kill any session for FILE, start a new one     |
| `stop FILE` / `stop --all`       | `rocq_stop`     | kill the session(s)                                     |
| `list`                           | `rocq_sessions` | running sessions: memory, idle time, limits             |
| `gc`                             | —               | apply the limits now (this also happens automatically) |
| `info FILE`                      | `rocq_info`     | detected root, toolchain and load path                  |

Exit status: 0 on success, 1 on an error (Rocq or setup), 2 on bad usage.

**`try`** is for the moment an agent would otherwise guess one tactic per turn:

```
$ rocq-live try Foo.v 'lia' 'auto' 'intros; split' 'reflexivity'
rocq-live: 3 of 4 tactics work; state unchanged; best: [1] lia (no goals left)
[1] lia -> works (no goals left)
[2] auto -> works (no goals left)
[3] intros; split -> works (2 goals left)
    ...
[4] reflexivity -> FAILS: Error: The relation and is not a declared reflexive relation. ...
```

Each tactic gets `ROCQ_LIVE_TRY_TIMEOUT` seconds (default 30). `query` and
`try` also accept `FILE:LINE`.

**Fast replay.** `goto FILE:LINE` checks the statement of each finished `Qed`
proof before LINE, but takes the proof itself as `Admitted`, as `coqc -vos`
does. Everything else in the file (imports, scopes, notations, options,
`Defined` bodies) is replayed in place, so the goal at LINE is the same; only
the tactic work in earlier proofs is skipped. The reply says how many proofs
were taken as Admitted. Proofs are always checked in full when:
- you edited them since the session last read the file;
- the proof is `Defined`, `Program`, `Proof term.`, or inside a `Section`
  without `Proof using` (Admitted would generalize it differently);
- you pass `--full`, or give no LINE at all (the whole file).

`audit` always checks in full. Turn fast replay off with `ROCQ_LIVE_FAST=0`.

Measured on a file with 12 proofs that each take tactic time, followed by the
lemma being worked on (Rocq 9.0, M-series Mac):

| | time |
|---|---|
| fresh session, `goto --full` | 3.36 s |
| fresh session, `goto` (fast replay) | 0.25 s |
| one step in a live session | 0.08 s |
| restoring a stopped session from its recipe | 0.33 s |
| one call in no-daemon mode | 0.19 s |

**`audit`** fails on any `Admitted`, `admit` or `give_up`, on an `Axiom` or
`Parameter` that is not allowed, and on any declaration whose `Print
Assumptions` lists an axiom that is not allowed. Admitted lemmas count, since
Rocq lists them as axioms. Allowed axioms come from `axioms` in `.rocq-live`,
or from `--allow` / `allow`. With NAMEs, only those declarations are judged.
The audit checks the whole file, then returns the session to where it was.

**Hints.** For an error whose fix isn't in the message, a `rocq-live hint:`
line follows it. This covers a missing load-path binding, a library that is
not built, a `.vo` from another Rocq version, a stale `.vo`, a step stopped for
time or memory, and a proof command given outside a proof.

## Project detection

Detection starts at the file and goes upward; the nearest marker wins:

| found                        | load path                                                          |
|------------------------------|--------------------------------------------------------------------|
| `dune-project`               | `dune rocq top` / `dune coq top`; also rebuilds the file's imports  |
| `_CoqProject`                | its `-Q`, `-R`, `-I` and `-arg` lines (you keep the `.vo`s built)  |
| neither                      | no flags (root: the git root)                                      |

**Toolchain:** the current opam switch if it has `coqtop`, otherwise `coqtop`
(or `rocq repl`) on PATH. `rocq-live info FILE` shows what was picked.

### `.rocq-live` (optional)

Put this file at the project root to override detection. All keys are optional,
and relative paths are resolved from the file's directory:

```
switch   rocq-9.0                      # opam switch to use (`path`: coqtop on PATH)
flags    -Q theories MyLib             # load path, verbatim
loadpath scripts/loadpath.sh {file}    # or: a command that prints the flags
build    make -j8                      # command run before each start/goto
axioms   classic functional_extensionality_dep   # axioms `audit` accepts
```

Use `flags` for projects built by a plain `coqc` script, `loadpath` for custom
build trees (for example one built with `coqc -vos` for speed), and `build`
when `_CoqProject` `.vo`s should be rebuilt automatically.

### Environment

| variable            | meaning                                                   |
|---------------------|-----------------------------------------------------------|
| `ROCQ_LIVE_FLAGS`   | load path flags; overrides detection                      |
| `ROCQ_LIVE_SWITCH`  | opam switch (`path` = coqtop on PATH)                     |
| `ROCQ_LIVE_TIMEOUT` | seconds per step (default 600 for goto, 120 otherwise)    |
| `ROCQ_LIVE_TRY_TIMEOUT` | seconds per tactic in `try` (default 30)             |
| `ROCQ_LIVE_FAST`    | `0`: `goto` checks every proof (as `--full`)              |
| `ROCQ_LIVE_DAEMON`  | `0`: no background process (see [Recipes](https://github.com/kylezh/rocq-live#recipes-and-the-no-daemon-mode)) |

The session limits below can also be set in the environment.

## Sessions, memory and GC

Each `.v` file being worked on has one session: a background daemon with its
own `coqtop`, in its own process group, reachable through a Unix socket in
`/tmp/rocq-live-$UID/`. Within a session, going back (`goto` to an earlier line,
`back`) drops the later states. A session ends when you `stop` it, when it
has been idle for `idle` seconds, or when the GC stops it.

**Limits** apply to the whole machine. Each is read from the environment, else
from `~/.config/rocq-live/config` (`key value` lines). They are read again at
each use, so editing the file changes running sessions too.

| key            | environment              | default            | meaning                     |
|----------------|--------------------------|--------------------|-----------------------------|
| `max_mb`       | `ROCQ_LIVE_MAX_MB`       | half the RAM       | all sessions together       |
| `session_mb`   | `ROCQ_LIVE_SESSION_MB`   | `max_mb`           | one session                 |
| `max_sessions` | `ROCQ_LIVE_MAX_SESSIONS` | 16                 | how many sessions (soft, see below) |
| `grace`        | `ROCQ_LIVE_GRACE`        | 600                | seconds a session is spared from the count limit after its last use |
| `idle`         | `ROCQ_LIVE_IDLE`         | 1200               | seconds before an idle session exits (its next use restores it from the recipe) |

```
# ~/.config/rocq-live/config
max_mb       12000
session_mb   6000
max_sessions 12     # about 2 per concurrent agent: its proof file and a scratch file
grace        600
```

**GC.** When a session starts, after every request, and every second while a
step runs, rocq-live compares the sessions with the limits.
- **Too much memory in total:** the least recently used idle sessions are
  stopped until the total fits. A busy session is never stopped from outside.
- **Too many sessions:** the same, but sessions used within the last `grace`
  seconds are spared. To rocq-live, an agent thinking between two tool calls
  looks idle, and stopping its session would cost a full re-check on its next
  call. An extra idle `coqtop` costs only memory, and `max_mb` already bounds
  that. So the count is soft: it can be exceeded by sessions in recent use,
  and memory is the hard limit.
- **A step pushes its session over `session_mb`:** the step is interrupted.
  The same happens when the total is over `max_mb`, the step itself is growing,
  and no idle session is left to stop. Its `coqtop` is then restarted and the
  file replayed, since an OCaml process does not return a big heap to the
  system, and the reply says so. If `coqtop` ignores the interrupt (Rocq 9.3
  does inside `vm_compute`), it is killed after 5 s and the same recovery runs.
- **A file whose fresh `coqtop` is already above `session_mb`:** that file's
  limit becomes its fresh size plus 25%, with a note, so it isn't restarted on
  every request.

`rocq-live list` shows every session's memory and idle time:

```
$ rocq-live list
2.1 GB   busy     /work/proj/theories/Big.v
140 MB   12m idle /work/proj/theories/Small.v
2 session(s), 2.2 GB; limits: 16 sessions (sparing those used in the last 10m), 8.0 GB total, 8.0 GB per session, 20m idle
```

### Recipes and the no-daemon mode

Rocq cannot save a live session's state to a file (`Write State` was removed
in 2021), so rocq-live saves a **recipe** instead: the session's last `goto`
line and the runs made on top of it. Recipes live in `~/.cache/rocq-live/recipes/`
and are kept for 7 days. When a session was stopped by GC, by the idle
timeout, or by a reboot, its next use restores it: fast replay to the line,
then the runs. The reply says so, and how long it took. An explicit `stop`
forgets the recipe.

With `ROCQ_LIVE_DAEMON=0`, nothing runs in the background. Each call starts a
`coqtop`, rebuilds the state from the recipe, answers, saves the recipe and
exits. Each call is slower, since it replays the file from the start, but the
commands and output are the same. Use it where background processes are not
allowed (some CI runners and sandboxes).

**Robustness.**
- **Crash:** if `coqtop` crashes during a step (for example a stack overflow
  in Rocq itself), the session restarts it, replays to where it was, and
  reports that the step was not applied.
- **Upgrade:** when rocq-live is updated, each session started by the old
  version is restored from its recipe on its next use.
- **Cleanup:** files left by dead sessions are cleaned up, and their logs are
  kept for a day.

## Caching

- **Proof state.** The checked prefix of each file stays in its session. When
  `goto` moves backward it jumps to a saved state (`BackTo`), which is instant.
  When it moves forward or after an edit, it checks only the new text.
- **Sessions are shared.** A session is a background daemon keyed by the file's
  path, not owned by the agent. The CLI, the MCP server, several agents, and an
  MCP server restarted by its client all use the same warm session.
- **Imports.** In dune projects, imports are built by dune, so they use dune's
  cache. dune's default (`enabled-except-user-rules`) does not cache Rocq
  rules; to share checked files across worktrees and clones, enable it fully
  with `(cache enabled)` in `dune-workspace` or `DUNE_CACHE=enabled`.
- **Freshness.** A session watches the `.vo`/`.vos` files of the project modules
  it has loaded. When one is rebuilt, the session restarts `coqtop` and replays
  the file to the last `goto` line and the runs on top of it. It reports this,
  so it never answers from a stale import.

## Compatibility

| tested                                        | result |
|-----------------------------------------------|--------|
| Rocq 9.0.0 (opam), dune 3.21 with `(using coq …)`     | ✓ |
| Rocq 9.3.0 (Homebrew), dune 3.24 with `(using rocq …)` | ✓ |
| `_CoqProject` with Rocq 9.0 and 9.3                   | ✓ |
| Python 3.9 and 3.12, macOS                            | ✓ |
| MCP: Claude Code, Codex CLI 0.169                     | ✓ |
| memory watchdog / GC on Rocq 9.0 and 9.3              | ✓ |

- **Rocq/Coq versions.** rocq-live talks to `coqtop -emacs` using only commands
  that have been stable for years (`Fail Check`, `BackTo`, `Show`, `Print Libraries`).
  It parses both `Rocq <` and `Coq <` prompts, so Coq 8.x should work, but this
  is not tested yet.
- **dune versions.** It uses `dune rocq top` for `(using rocq …)` projects and
  `dune coq top` for `(using coq …)` projects, and falls back to the other if a
  dune version lacks one. dune ≥ 3.24 has dropped `coq top`.
- **Linux.** It uses only POSIX features (Unix sockets, process groups) but has
  not been tested there yet. Windows is not supported.

## How it works

The file is split into sentences: a `.` followed by white space, outside
comments and strings, with bullets and braces handled. Each sentence is sent to
`coqtop -emacs` followed by a marker command; the marker's output shows the
sentence has finished, and the state id after each sentence is recorded. A step
that runs too long is interrupted (SIGINT) and undone. Sessions live under
`/tmp/rocq-live-$UID/`, and a client talks to a session over a Unix socket.

## Tests

```sh
python3 tests/test_rocq_live.py                     # default toolchain
ROCQ_LIVE_SWITCH=path python3 tests/test_rocq_live.py
```

## Releasing

The version is written in one place: the git tag. To release X.Y.Z:

```sh
git tag vX.Y.Z && git push origin vX.Y.Z
```

The [Release workflow](https://github.com/kylezh/rocq-live/blob/master/.github/workflows/release.yml) then:
- writes X.Y.Z into the script and `server.json`;
- builds the package and checks the wheel's CLI and MCP handshake;
- publishes to PyPI (Trusted Publishing, no token) and to the MCP Registry;
- creates a GitHub release with the single-file script attached.

An untagged checkout reports version `0.0.0+dev`, which PyPI refuses, so a
local build can't be uploaded by mistake.

One-time setup: on PyPI, add a trusted publisher to the `rocq-live` project
(owner `kylezh`, repository `rocq-live`, workflow `release.yml`, environment
`pypi`), and create a GitHub environment named `pypi` in the repository.

## Related

[rocq-mcp](https://github.com/LLM4Rocq/rocq-mcp) is a larger MCP server built
on coq-lsp's `pet`. Its extras include proof-statement verification, a file
outline, notation lookup, and several errors per compile. rocq-live aims for no
dependencies beyond `coqtop`, a CLI as well as MCP, sessions shared per file
across agents, re-checking only what an edit changed, and machine-wide memory
limits.

## License

[MIT](https://github.com/kylezh/rocq-live/blob/master/LICENSE)

<!-- mcp-name: io.github.kylezh/rocq-live -->
