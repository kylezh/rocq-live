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

The same session also stands in for `coqc` while you edit. `rocq-live check
FILE` gives `coqc`'s verdict, with the same diagnostics and exit code, but it
re-runs only what an edit can affect. After an edit inside a proof, that is
just the proof:

```
$ rocq-live check theories/Big.v          # coqc takes 11 s on this file
rocq-live: ./theories/Big.v OK [41 of 163 sentences re-run, 19 proof(s) checked before kept, 0.3 s]
```

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
To know whether FILE compiles, run `rocq-live check FILE` instead of coqc
(same verdict, re-checks only what changed; --all-errors lists every failing
proof). After changing something other files use, `rocq-live impact FILE`
compiles the files that require it and reports what broke.
```

All tools except `rocq_compile` and `rocq_impact` are marked read-only
(`readOnlyHint`): they never write files, though a `goto` may build the
file's imports, as a build would. Those two exist to write `.vo` files.

## Commands

| CLI                              | MCP tool        | does                                                    |
|----------------------------------|-----------------|---------------------------------------------------------|
| `goto FILE[:LINE] [--full]`      | `rocq_goto`     | state just before LINE (default: end, checking every proof); starts a session |
| `run FILE[:LINE] 'tac1. tac2.'`  | `rocq_run`      | run on the current state (or on the state before LINE); an error changes nothing |
| `query FILE 'Search …'`          | `rocq_query`    | run, print, undo (Search, Check, Print, About, …)       |
| `try FILE 'lia' 'auto' …`        | `rocq_try`      | each tactic alone on the current state: which work, goals left (best first); state unchanged |
| `check FILE [--allow-stale] [--rebuild] [--all-errors]` | `rocq_check` | `coqc`'s verdict on the whole file, re-checking only what changed (see below) |
| `compile FILE [--deps-only]`     | `rocq_compile`  | write `.vo` files: FILE's out-of-date dependencies in order, then FILE |
| `impact FILE`                    | `rocq_impact`   | what an edit to FILE breaks in other files: compile FILE and every file that requires it |
| `audit FILE [NAME…] [--allow AX,…]` | `rocq_audit` | `Admitted`/`admit`/axioms, and `Print Assumptions` of each declaration against the allowed axioms: PASS/FAIL |
| `back FILE [N]`                  | `rocq_back`     | undo the last N runs                                    |
| `goals FILE`                     | `rocq_goals`    | current goals                                           |
| `start FILE[:LINE]`              | `rocq_goto` with `fresh` | kill any session for FILE, start a new one     |
| `stop FILE` / `stop --all [--force]` | `rocq_stop` | kill FILE's session (and forget its recipe), or every idle one (busy ones only with `--force`) |
| `list`                           | `rocq_sessions` | running sessions: memory, idle time, limits             |
| `gc`                             | —               | apply the limits now (this also happens automatically) |
| `stats [FILE] [--since 7d]`      | `rocq_stats`    | what rocq-live did and saved, from its local log (see below) |
| `info FILE`                      | `rocq_info`     | detected root, toolchain and load path                  |

Exit status: 0 on success, 1 on an error (Rocq or setup), 2 on bad usage.
`check` uses `coqc`'s codes: 0 when the file checks, 1 when it does not, and 2
when it was not checked (an out-of-date dependency, or no toolchain).

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

**`check`** answers the question "does this file compile?" the way `coqc`
does, but from the file's live session:
- every proof is checked: nothing is taken as Admitted, and proofs that an
  earlier `goto` skipped are checked now;
- a file that leaves a proof, `Section` or `Module` open fails with `coqc`'s
  message;
- the first error is printed exactly as `coqc` prints it (`File "…", line N,
  characters A-B:` and the message), so whatever parses `coqc` output still
  works;
- only what the last edit can affect is re-run (see below). After a failure
  the session waits just before the error, so the check after the fix starts
  there.

It writes no `.vo`. A check that compiles is not a build, and your build
stays in charge of `.vo` files.

**`--all-errors`** (MCP: `all_errors`) reports every proof that fails, not
just the first error. Each failing proof is closed with `Admitted` so that
checking can go on, and later proofs that use it still check, as they will
once it is fixed. After a change that breaks six proofs, an agent sees all
six in one call instead of fixing them one per round. Each error is exactly
the one `coqc` reports when that proof is the only broken one. An error
outside a proof (in a statement, a definition or a `Require`) still stops the
check, since what follows it would fail for that reason alone. The next check
re-runs the proofs that were closed this way.

**What an edit re-runs.** Rocq checks a file top to bottom, so in general
everything after an edit is re-checked. rocq-live re-runs less, by keeping
track of which proofs have already been checked and what each edit can reach:
- **An edit to a `Qed` proof's tactics.** What follows a `Qed` sees only the
  lemma's statement, never its tactics. So only the edited proof is re-run.
- **An edit to a lemma's statement, a definition (`Definition`, `Fixpoint`,
  `Let`, `Example`), a `Defined` proof or an `Ltac`**, including adding or
  deleting one. Only later proofs that can see it are re-run. Seeing it
  means mentioning its name, or the name of a definition, proof statement or
  `Ltac` that mentions it, and so on along the chain; computing with a
  definition requires reaching it this way too.
- **An edit to an `Instance`** of a class declared in the same file. Typeclass
  search can use an instance without naming it, so the class and its methods
  count as changed, followed from the class declaration on. That includes
  generic code written before the instance, such as a function that calls a
  method.
- **Comments and white space** between sentences re-run nothing.

If a changed name reaches something that later proofs can use *without*
naming it, such as a `Hint`, an instance of another class, a `Notation`,
`Arguments` or a coercion, everything after the edit is re-run instead. The
same goes for edits to an `Inductive`, a `Record`, a `Notation`, a `Hint`, a
`Section` or `Module` command, a `Program` definition, and an instance of a
class from a library.

A proof that is kept is replayed as `Admitted`: its statement is checked
again, its tactics are not. Definitions and other commands after the edit
always run again. A proof whose tactics include a command whose effect
outlives its `Qed` (`Set Implicit Arguments`, `Hint`, `Ltac`, any capitalized
command other than queries such as `Check` or `Search`) counts as changed
as a whole. So does a proof in a `Section` without `Proof using`, since its
tactics decide which section variables it takes.

**Limits of this reuse.**
- Tactics that search the whole environment for lemmas can use one without
  naming it. CoqHammer's `hammer` and Tactician's `synth` are recognized, and
  proofs using them are always re-run; set `ROCQ_LIVE_REUSE=0` if you use
  another tactic like them.
- The same gap as `coqc -vos`: tactics that add a universe constraint could
  make a later proof fail with a universe inconsistency, and `check` would not
  notice until that proof is re-run.

`audit` always runs every proof, and `ROCQ_LIVE_REUSE=0` turns this reuse
off.

Measured on a file of 40 proofs that `coqc` checks in 11 s (Rocq 9.0,
M-series Mac):

| after                              | `check` |
|------------------------------------|---------|
| nothing changed                    | 0.19 s  |
| a tactic edit near the end         | 0.40 s  |
| a tactic edit in the middle        | 0.44 s  |
| a tactic edit near the start       | 0.40 s  |
| breaking a proof (first error)     | 0.15 s  |
| fixing it                          | 0.40 s  |
| a comment added near the top       | 0.15 s  |
| a statement edit in the middle     | 0.44 s  |

The first check of a file costs what `coqc` costs (11 s here). In this file
no proof uses another; when one does, a statement edit also re-runs the
proofs that use the lemma. Every result matched `coqc`. The tests check this
over sequences of edits, including edits that reach later proofs through a
hint, an `Ltac`, a definition, another proof's statement, a rename, a
`Section`, a `Defined` body, a definition that shadows an imported one, and
typeclass search through a generic function.

**Out-of-date dependencies.** A session loads the `.vo` of each library the
file requires, so a library edited but not rebuilt would be checked against
its old version. That can make a correct proof fail, or a wrong one pass.
`check` therefore refuses, with exit code 2, when a library the file requires
(directly or not) is out of date by `make`'s rule: no `.vo`, a `.vo` older
than its `.v`, or a `.vo` older than one it requires. It lists them:

```
$ rocq-live check theories/G3.v
rocq-live: ./theories/G3.v NOT CHECKED: 2 of its dependencies are out of date (make would rebuild them):
  theories/G1.v is newer than its .vo
  theories/G2.v requires theories/G1.v, which is out of date
```

Then either rebuild them (`check --rebuild`, or `compile FILE --deps-only`),
or check against the old `.vo` files anyway with `--allow-stale`. `goto`
prints the same list as a warning but goes on. The dependencies come from
`coqdep`; its results are cached and refreshed only for files whose `Require`
lines changed. dune projects don't need this check, since `dune rocq top`
rebuilds the imports before each `goto` or `check`.

**`impact FILE`** answers "what does my edit break in other files?". It
finds every project file that requires FILE, directly or not, from the same
`coqdep` graph. It then compiles FILE and those files in dependency order,
`ROCQ_LIVE_JOBS` at a time (default: half the cores, at most 4), rebuilding
any out-of-date file they need along the way. A file that fails is reported
with `coqc`'s errors, and the files that require it are skipped, while
independent branches go on:

```
$ rocq-live impact theories/A.v
rocq-live impact theories/A.v: 4 file(s) require it
ok       theories/A.v (0.0 s)
FAILED   theories/B.v
File "./theories/B.v", line 3, characters 13-17:
Error:
The term "a_eq" has type "a = 2" while it is expected to have type "a = 1".
skipped  theories/C.v (requires theories/B.v, which failed)
FAILED   theories/E.v
File "./theories/E.v", line 3, characters 7-18:
Error: Unable to unify "2" with "a + a".
skipped  theories/F.v (requires theories/E.v, which failed)
rocq-live: 1 compiled, 2 failed, 2 not checked [0.1 s, 4 job(s)]
```

Files that don't require FILE are not touched. In a dune project, `impact`
runs one `dune build` of the same targets, and dune handles order and
parallelism. Use `check --all-errors` on a failed file to see all of its
errors.

**`compile`** builds `.vo` files the way the project does: `coqc` with the
load path (`_CoqProject`, `flags`), `dune build` in dune projects, or the
`compile` command in `.rocq-live`. It builds FILE's out-of-date dependencies in
dependency order, then FILE (unless `--deps-only`). Sessions that loaded a
rebuilt `.vo` reload it by themselves.

**`audit`** fails on any `Admitted`, `admit` or `give_up`, on an `Axiom` or
`Parameter` that is not allowed, and on any declaration whose `Print
Assumptions` lists an axiom that is not allowed. Admitted lemmas count, since
Rocq lists them as axioms. Allowed axioms come from `axioms` in `.rocq-live`,
or from `--allow` / `allow`. With NAMEs, only those declarations are judged.
The audit checks the whole file, then returns the session to where it was.

**Compact goals.** In a large proof, most hypotheses stay the same from one
step to the next, and re-reading them costs an agent tokens on every step.
After a `run`, hypotheses unchanged since the goals last shown are replaced
by one line naming them, and the ones that disappeared are listed. `try`
shows each tactic's result the same way:

```
$ rocq-live run Foo.v 'rewrite H1.'
1 goal (ID 11)
  (11 unchanged hypotheses not shown: A, l, l2, x, y, z, w, H1, H2, H3, H4)
  ============================
  length (l ++ l2) + y = length l + length l2 + w
```

`goals` and `goto` always show everything. `ROCQ_LIVE_COMPACT=0` turns this
off.

**Similar names.** Models often misremember lemma names: a typo, the parts
in another order (`app_length` and `length_app`), or a missing qualifier.
When an error says a name does not exist, `rocq-live` searches the current
environment (Rocq's `Search` by name, at most 8 s) and lists up to five
similar names that do exist, with their types. The agent can then use the
right one directly instead of spending a round on `Search`:

```
$ rocq-live run Foo.v 'rewrite app_lenght.'
...
Error: The variable app_lenght was not found in the current environment.
rocq-live: error; state unchanged
rocq-live hint: app_lenght does not exist here; similar names:
  length_app : forall [A : Type] (l l' : list A), length (l ++ l') = length l + length l'
  ...
```

`Search` only sees loaded libraries. When no loaded name is close, rocq-live
looks for the name, or one containing it, in the `.glob` files of the
installed libraries and the project. It then confirms the find by requiring
that library and checking the name, in a step that is undone right after:

```
$ rocq-live run Foo.v 'rewrite Nat.add_com.'
...
rocq-live hint: Nat.add_com does not exist here; Nat.add_comm is in Stdlib.Arith.PeanoNat, which is not loaded; add `From Stdlib Require Import PeanoNat.`
  Nat.add_comm : forall n m : nat, n + m = m + n
```

If nothing similar exists anywhere, the hint says so instead of listing
unrelated names.

**Hints.** For an error whose fix isn't in the message, a `rocq-live hint:`
line follows it. This covers a missing load-path binding, a library that is
not built, a `.vo` from another Rocq version, a stale `.vo`, a step stopped for
time or memory, and a proof command given outside a proof.

**`stats`** shows what rocq-live has done and what it saved, from a log it
keeps on your machine (`~/.cache/rocq-live/stats.jsonl`). The log records one
line per request: the command, the file, how long it took, and what it
did. Nothing is ever sent anywhere, and `ROCQ_LIVE_STATS=0` turns the log
off.

```
$ rocq-live stats --since 7d
rocq-live stats: 21 request(s) on 4 file(s) since 2026-11-10 01:01
  time    8 whole-file check(s) took 1.4 s; full re-checks would have taken about 88.7 s (from each file's last full run) -> ~87.3 s saved; 3 more with no full run to compare
  work    402 sentence(s) run (whole-file checks: 397 of 1,551, 26%); 86 checked proof(s) kept instead of re-run; 0 proof(s) skipped by fast replay
  errors  check --all-errors: 2 call(s) found 3 error(s), 2 more than one per call
  tokens  compact goals left out 84 characters of unchanged hypotheses (~21 tokens)
  hints   3 name hint(s): 3 similar loaded name(s), 0 in a library to Require, 0 with nothing similar
  impact  2 run(s): 6 file(s) compiled, 2 failed, 2 skipped
  calls   check 12 (median 0.2 s, 3 errors), run 3 (median 0.1 s, 2 errors), impact 2 (median 0.1 s, 1 error), goto 1 (median 0.1 s), ...
          via cli 21; over 19.0 s; log: ~/.cache/rocq-live/stats.jsonl
```

The time saved is an estimate. Each whole-file check (`check`, or `goto`
with no line) is compared with the most recent full run of the same file,
scaled by the file's current size. A full run is one that re-ran at least 90%
of the sentences and kept or skipped no proof; its time is scaled up to all
of them. Agents usually `goto` a line before they check, so the first check
rarely re-runs every sentence itself. That full
run costs about what `coqc` does. The times are rocq-live's own work, without
the client's process start. "More than one per call" counts the extra
errors `--all-errors` found, each a round an agent did not need. The token
estimate assumes about four characters per token. Give a FILE to see one
file only.

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
compile  make {vo}                     # how `compile` builds one file ({vo}, {file})
axioms   classic functional_extensionality_dep   # axioms `audit` accepts
```

Use `flags` for projects built by a plain `coqc` script, `loadpath` for custom
build trees (for example one built with `coqc -vos` for speed), and `build`
when `_CoqProject` `.vo`s should be rebuilt automatically. `compile` is how
`rocq-live compile` builds one file; `{vo}` is the `.vo` path relative to the
root and `{file}` the `.v`. A project using `loadpath` needs it, since
rocq-live cannot know where its `.vo` files go.

### Environment

| variable            | meaning                                                   |
|---------------------|-----------------------------------------------------------|
| `ROCQ_LIVE_FLAGS`   | load path flags; overrides detection                      |
| `ROCQ_LIVE_SWITCH`  | opam switch (`path` = coqtop on PATH)                     |
| `ROCQ_LIVE_TIMEOUT` | seconds per step (default 600 for goto, 120 otherwise)    |
| `ROCQ_LIVE_TRY_TIMEOUT` | seconds per tactic in `try` (default 30)             |
| `ROCQ_LIVE_FAST`    | `0`: `goto` checks every proof (as `--full`)              |
| `ROCQ_LIVE_REUSE`   | `0`: after an edit inside a proof, re-run every proof after it    |
| `ROCQ_LIVE_JOBS`    | files `impact` compiles at once (default: half the cores, at most 4) |
| `ROCQ_LIVE_COMPACT` | `0`: `run` and `try` show every hypothesis, not just the changed ones |
| `ROCQ_LIVE_STATS`   | `0`: keep no stats log (`ROCQ_LIVE_STATS_FILE`: another path for it) |
| `ROCQ_LIVE_DAEMON`  | `0`: no background process (see [Recipes](https://github.com/kylezh/rocq-live#recipes-and-the-no-daemon-mode)) |
| `ROCQ_LIVE_SESSION_DIR` | a separate set of sessions (default `/tmp/rocq-live-$UID`); `stop --all` and GC see only the sessions in it. The tests use one |

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
- **Several agents at once:** a session that ends between or during an
  agent's requests (stopped by GC, by someone's `stop --all`, or killed) is
  restored from its recipe on the next request, and that request goes
  through. A `run` or `back` that took effect before the session ended is
  in the recipe already, so it is never applied twice. `stop --all` stops
  only idle sessions, and keeps their recipes, unless `--force`. A session
  that is starting counts as busy until it has answered its first request.
  Agents that need a session for the same file at the same time start only
  one between them.
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
  When it moves forward or after an edit, it checks only the new text, and
  keeps the proofs that edit cannot affect (see `check`). Which proofs have
  been checked is also kept when `coqtop` restarts (for example after hitting
  a memory limit), so the replay after a restart is fast too.
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

[rocq-warm](https://github.com/zeldovich/rocq-warm) keeps a warm `rocq repl`
per file to give `coqc`'s verdict after an edit, and refuses to check against
stale libraries. rocq-live's `check` answers the same question from the same
session that serves goals, tactics and queries.

## License

[MIT](https://github.com/kylezh/rocq-live/blob/master/LICENSE)

<!-- mcp-name: io.github.kylezh/rocq-live -->
