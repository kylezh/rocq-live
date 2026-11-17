#!/usr/bin/env python3
"""End-to-end tests: a scratch _CoqProject project, driven through the CLI and
the MCP server. Needs coqc/coqtop (whatever rocq-live would pick; set
ROCQ_LIVE_SWITCH to choose).  Run:  python3 tests/test_rocq_live.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rocq-live")

A = "Definition double (n : nat) := n + n.\n"
B = """From My Require Import A.
From Stdlib Require Import Lia.

Lemma double_twice : forall n, double n = 2 * n.
Proof.
  intros n.
  unfold double.
  lia.
Qed.
"""


# the tests keep their own sessions and stats log (sessions they start inherit
# this): their `stop --all` and gc limits never touch anyone else's sessions
SESSIONS = tempfile.mkdtemp(prefix="rlt-", dir="/tmp")
os.environ["ROCQ_LIVE_SESSION_DIR"] = SESSIONS
STATS = os.path.join(tempfile.mkdtemp(prefix="rocq-live-stats-"), "stats.jsonl")
os.environ["ROCQ_LIVE_STATS_FILE"] = STATS

# ROCQ_LIVE_CMD: test an installed command (e.g. a wheel's `rocq-live`)
# instead of the script in this repository
CMD = os.environ["ROCQ_LIVE_CMD"].split() if os.environ.get("ROCQ_LIVE_CMD") else [sys.executable, TOOL]


def sh(*args, check=None, env=None, cwd=None, code=None):
    e = dict(os.environ, **(env or {}))
    r = subprocess.run(CMD + list(args), capture_output=True, text=True, env=e, cwd=cwd)
    if check is not None:
        assert (r.returncode == 0) == check, "rocq-live %s -> %d\n%s%s" % (args, r.returncode, r.stdout, r.stderr)
    if code is not None:
        assert r.returncode == code, "rocq-live %s -> %d, not %d\n%s%s" % (
            args, r.returncode, code, r.stdout, r.stderr)
    return r.stdout + r.stderr


class Project(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="rocq-live-test-")
        os.makedirs(os.path.join(cls.dir, "theories"))
        with open(os.path.join(cls.dir, "_CoqProject"), "w") as f:
            f.write("-Q theories My\n")
        cls.write("A.v", A)
        cls.write("B.v", B)
        cls.compile_a()
        cls.b = os.path.join(cls.dir, "theories", "B.v")

    @classmethod
    def write(cls, name, text):
        with open(os.path.join(cls.dir, "theories", name), "w") as f:
            f.write(text)

    @classmethod
    def coqc(cls):
        """coqc of the toolchain rocq-live uses (its coqtop's switch)."""
        info = sh("info", os.path.join(cls.dir, "theories", "A.v"))
        if "opam switch" in info:
            sw = info.split("opam switch ")[1].split(" ")[0]
            return ["opam", "exec", "--switch=" + sw, "--", "coqc"]
        return ["coqc"]

    @classmethod
    def compile_a(cls):
        subprocess.run(cls.coqc() + ["-Q", "theories", "My", "theories/A.v"], cwd=cls.dir, check=True)

    @classmethod
    def tearDownClass(cls):
        sh("stop", cls.b)
        shutil.rmtree(cls.dir)

    def test_1_goto_run_query_back(self):
        out = sh("goto", self.b + ":7", check=True)
        self.assertIn("double n = 2 * n", out)
        self.assertIn("checked up to theories/B.v line 6", out)
        out = sh("run", self.b, "unfold double.", check=True)
        self.assertIn("n + n = 2 * n", out)
        out = sh("run", self.b, "no_such_tactic.", check=False)
        self.assertIn("state unchanged", out)
        self.assertIn("n + n = 2 * n", sh("goals", self.b, check=True))
        self.assertIn("nat -> nat", sh("query", self.b, "Check double.", check=True))
        self.assertIn("double n = 2 * n", sh("back", self.b, check=True))
        self.assertIn("give complete sentences", sh("run", self.b, "intros", check=False))

    def test_2_edit_then_goto(self):
        sh("goto", self.b, check=True)
        self.write("B.v", B.replace("  lia.\n", "  bogus.\n"))
        out = sh("goto", self.b, check=False)
        self.assertIn("error in the sentence at theories/B.v line 8", out)
        self.write("B.v", B)
        self.assertIn("(asked for line EOF)", sh("goto", self.b, check=True))

    def test_3_rebuilt_import_reloads(self):
        sh("goto", self.b, check=True)
        self.write("A.v", A + "Definition triple (n : nat) := n + n + n.\n")
        self.compile_a()
        out = sh("query", self.b, "Check triple.", check=True)
        self.assertIn("changed since it was loaded", out)
        self.assertIn("triple", out)

    def test_4_mcp(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "rocq_goto", "arguments": {"file": self.b, "line": 7}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "rocq_run", "arguments": {"file": self.b, "text": "unfold double."}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "rocq_run", "arguments": {"file": self.b, "text": "bogus."}}},
            {"jsonrpc": "2.0", "id": 6, "method": "nope"},
        ]
        p = subprocess.Popen(CMD + ["mcp"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
        for m in msgs:
            p.stdin.write(json.dumps(m) + "\n")
        p.stdin.flush()
        got = {}
        while len(got) < 6:
            m = json.loads(p.stdout.readline())
            got[m["id"]] = m
        p.stdin.close()
        p.wait(10)
        p.stdout.close()
        self.assertEqual(got[1]["result"]["serverInfo"]["name"], "rocq-live")
        tools = {t["name"]: t for t in got[2]["result"]["tools"]}
        self.assertIn("rocq_goto", tools)
        self.assertTrue(tools["rocq_check"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["rocq_compile"]["annotations"]["readOnlyHint"])   # writes .vo
        self.assertFalse(tools["rocq_impact"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["rocq_stats"]["annotations"]["readOnlyHint"])
        self.assertFalse(got[3]["result"]["isError"])
        self.assertIn("double n = 2 * n", got[3]["result"]["content"][0]["text"])
        self.assertIn("n + n = 2 * n", got[4]["result"]["content"][0]["text"])   # in order
        self.assertTrue(got[5]["result"]["isError"])
        self.assertEqual(got[6]["error"]["code"], -32601)

    def test_6_try(self):
        sh("goto", self.b + ":7", check=True)
        out = sh("try", self.b, "unfold double; lia", "reflexivity", "intros", "no_such", check=True)
        self.assertIn("2 of 4 tactics work; state unchanged; best: [1] unfold double; lia (no goals left)", out)
        self.assertIn("[4] no_such -> FAILS: Error: The reference no_such was not found", out)
        self.assertIn("double n = 2 * n", sh("goals", self.b, check=True))     # unchanged

    def test_7_audit(self):
        self.write("C.v", "Axiom ax : 1 = 1.\nLemma ok : True. Proof. exact I. Qed.\n"
                   "Lemma adm : 2 = 2. Admitted.\nLemma u : 1 = 1. Proof. exact ax. Qed.\n"
                   "Module M. Lemma inm : True. Proof. exact I. Qed. End M.\n")
        c = os.path.join(self.dir, "theories", "C.v")
        out = sh("audit", c, check=False)
        self.assertIn("audit theories/C.v: FAIL", out)
        self.assertIn("line 3: Admitted", out)
        self.assertIn("axiom not allowed: ax  (used by u)", out)
        self.assertIn("4 declaration(s) checked", out)   # ok, adm, u and M.inm (qualified)
        out = sh("audit", c, "u", "--allow", "ax", check=True)
        self.assertIn("audit theories/C.v: PASS", out)
        self.assertIn("(elsewhere in the file)", out)
        out = sh("audit", c, "ok", "M.inm", check=True)
        self.assertIn("2 closed", out)
        sh("stop", c)

    def test_8_hints(self):
        self.write("H.v", "From Nope Require Import X.\n")
        h = os.path.join(self.dir, "theories", "H.v")
        out = sh("goto", h, check=False)
        self.assertIn("rocq-live hint: no load-path binding covers Nope.X", out)
        sh("stop", h)

    def test_9_gc_session_limit(self):
        sh("stop", "--all")
        sh("goto", self.b, check=True)
        self.write("G.v", "Lemma g : True. Proof. exact I. Qed.\n")
        g = os.path.join(self.dir, "theories", "G.v")
        # used within the grace period: spared, the count limit is exceeded
        out = sh("goto", g, check=True, env={"ROCQ_LIVE_MAX_SESSIONS": "1"})
        self.assertNotIn("gc stopped", out)
        self.assertIn(os.path.realpath(self.b), sh("list", check=True))
        sh("stop", g)
        # past the grace period: the least recently used one is stopped
        out = sh("goto", g, check=True, env={"ROCQ_LIVE_MAX_SESSIONS": "1", "ROCQ_LIVE_GRACE": "0"})
        self.assertIn("gc stopped the least recently used session %s" % os.path.realpath(self.b), out)
        listing = sh("list", check=True)
        self.assertIn(os.path.realpath(g), listing)
        self.assertNotIn(os.path.realpath(self.b), listing)
        sh("stop", g)

    def test_a_memory_watchdog(self):
        # a limit below what coqtop needs: the session adopts its fresh-start
        # size (+25%) as the limit; a step that grows past it is interrupted
        sh("stop", "--all")
        env = {"ROCQ_LIVE_SESSION_MB": "1"}
        out = sh("goto", self.b + ":7", check=True, env=env)
        self.assertIn("even freshly started", out)
        out = sh("run", self.b, "Eval vm_compute in Nat.iter 300000000 (fun x => x) tt.", check=False, env=env)
        self.assertIn("over the per-session limit", out)
        self.assertIn("restarted", out)          # by an interrupt, or by killing coqtop
        self.assertIn("rocq-live hint: the step used too much memory", out)
        self.assertIn("double n = 2 * n", sh("goals", self.b, check=True))
        sh("stop", self.b)

    def test_b_fast_replay(self):
        f = os.path.join(self.dir, "theories", "F.v")
        self.write("F.v", "Lemma l1 : 1 = 1. Proof. exact I. Qed.\n"      # broken, but finished
                   "Section S. Variable k : nat.\nLemma s1 : k = k. Proof. reflexivity. Qed.\nEnd S.\n"
                   "Lemma l2 : 2 = 2.\nProof.\n  reflexivity.\nQed.\n")
        out = sh("goto", f + ":7", check=True)           # inside l2, before `reflexivity.`
        self.assertIn("2 = 2", out)
        self.assertIn("1 finished proof(s) before it taken as Admitted", out)   # l1; s1 is in a Section
        out = sh("goto", f + ":7", "--full", check=False)
        self.assertIn("error in the sentence at theories/F.v line 1", out)
        out = sh("goto", f, check=False)                  # no LINE: every proof
        self.assertIn("error in the sentence at theories/F.v line 1", out)
        # an edited proof is checked even without --full
        sh("start", f + ":7", check=True)
        with open(f) as fh:
            self.write("F.v", fh.read().replace("exact I.", "exact (I)."))
        out = sh("goto", f + ":7", check=False)
        self.assertIn("error in the sentence at theories/F.v line 1", out)
        sh("stop", f)

    def test_c_restore_from_recipe(self):
        sh("goto", self.b + ":7", check=True)
        sh("run", self.b, "unfold double.", check=True)
        d = SESSIONS

        def read(name):
            with open(os.path.join(d, name)) as fh:
                return fh.read()
        sock = [l for l in os.listdir(d) if l.endswith(".sock.file") and read(l) == os.path.realpath(self.b)][0]
        pid = int(read(sock[:-5] + ".pid"))
        os.killpg(pid, 9)                                   # as gc or a reboot would
        out = sh("goals", self.b, check=True)
        self.assertIn("restored the session from its recipe", out)
        self.assertIn("line 7 and 1 run(s)", out)
        self.assertIn("n + n = 2 * n", out)
        sh("stop", self.b)
        self.assertIn("no session", sh("goals", self.b, check=False))   # stop forgets it

    def test_d_no_daemon(self):
        sh("stop", "--all")
        env = {"ROCQ_LIVE_DAEMON": "0"}
        self.assertIn("double n = 2 * n", sh("goto", self.b + ":7", check=True, env=env))
        self.assertIn("n + n = 2 * n", sh("run", self.b, "unfold double.", check=True, env=env))
        self.assertIn("n + n = 2 * n", sh("goals", self.b, check=True, env=env))    # from the recipe
        self.assertIn("double n = 2 * n", sh("back", self.b, check=True, env=env))
        self.assertIn("1 of 2 tactics work", sh("try", self.b, "lia", "unfold double; lia", check=True, env=env))
        self.assertIn("./theories/B.v OK", sh("check", "theories/B.v", code=0, env=env, cwd=self.dir))
        self.assertIn("0 session(s)", sh("list", check=True))                         # nothing left running
        sh("stop", self.b, env=env)

    def test_e_check_matches_coqc(self):
        """check gives coqc's diagnostics (byte for byte) and exit code."""
        cases = {
            "CkOk.v": "From My Require Import A.\nLemma t : double 1 = 2.\nProof. reflexivity. Qed.\n",
            "CkTerm.v": "(* \u00e9t\u00e9 *) Lemma c : 1 = 1.\nProof. (* \u00fc *) exact (eq_refl\n   2).\nQed.\n",
            "CkSyntax.v": "Definition x := (1 + .\n",
            "CkQed.v": "Lemma d : 1 = 1.\nProof.\nQed.\n",
            "CkOpen.v": "Lemma a : 1 = 1.\nProof.\n",
            "CkBlocks.v": "Module M.\nSection S.\nModule Type T.\nEnd T.\nDefinition y := 1.\n",
            "CkNested.v": "Lemma a : 1 = 1.\nProof.\nLemma b : 2 = 2.\n",
        }
        coqc = self.coqc()
        for name, text in cases.items():
            self.write(name, text)
            rel = "theories/" + name
            r = subprocess.run(coqc + ["-Q", "theories", "My", rel], cwd=self.dir,
                               capture_output=True, text=True)
            want = [l for l in (r.stdout + r.stderr).splitlines() if l.strip()]
            for attempt in ("cold", "warm"):
                p = subprocess.run(CMD + ["check", rel], cwd=self.dir, capture_output=True, text=True)
                got = [l for l in p.stdout.splitlines() if l.strip() and not l.startswith("rocq-live")]
                self.assertEqual(got, want, "%s (%s)" % (name, attempt))
                self.assertEqual(p.returncode, r.returncode, "%s (%s)\n%s" % (name, attempt, p.stdout))
            sh("stop", os.path.join(self.dir, rel))

    def test_f_check_rechecks_only_the_change(self):
        f = os.path.join(self.dir, "theories", "Inc.v")
        lemmas = ["Lemma l%d : %d = %d.\nProof.\n  reflexivity.\nQed.\n" % (i, i, i) for i in range(30)]
        self.write("Inc.v", "".join(lemmas))
        self.assertIn("OK [all 120 sentences run", sh("check", f, code=0))
        self.write("Inc.v", "".join(lemmas[:-1]) + lemmas[-1].replace("reflexivity", "exact (eq_refl 3)"))
        out = sh("check", f, code=1)
        self.assertIn("line 119, characters 9-18", out)
        self.assertIn("FAILED [1 of 120 sentences re-run", out)       # only the edited proof
        self.write("Inc.v", "".join(lemmas))
        self.assertIn("OK [2 of 120 sentences re-run", sh("check", f, code=0))
        # proofs a goto took as Admitted are checked by check
        self.write("Inc.v", lemmas[0].replace("reflexivity", "exact I") + "".join(lemmas[1:]))
        sh("start", f + ":119", check=True)                 # l0 is taken as Admitted here
        out = sh("check", f, code=1)
        self.assertIn("line 3, characters 8-9", out)
        sh("stop", f)

    def test_g_out_of_date_dependencies_and_compile(self):
        d = os.path.join(self.dir, "theories")
        self.write("G1.v", "Definition g := 1.\n")
        self.write("G2.v", "From My Require Import G1.\nDefinition h := g.\n")
        self.write("G3.v", "From My Require Import G2.\nLemma k : h = 1.\nProof. reflexivity. Qed.\n")
        g3 = os.path.join(d, "G3.v")
        out = sh("check", g3, code=2)                                 # nothing built yet
        self.assertIn("theories/G1.v has no .vo", out)
        self.assertIn("theories/G2.v has no .vo", out)
        out = sh("compile", g3, "--deps-only", code=0)
        self.assertIn("compiled theories/G1.v, theories/G2.v", out)  # dependencies first
        self.assertFalse(os.path.exists(os.path.join(d, "G3.vo")))
        self.assertIn("OK", sh("check", g3, code=0))
        # edit G1 (its .v now newer than its .vo): G2 is out of date through it
        self.write("G1.v", "Definition g := 2.\n")
        earlier = os.path.getmtime(os.path.join(d, "G1.v")) - 5
        os.utime(os.path.join(d, "G1.vo"), (earlier, earlier))
        out = sh("check", g3, code=2)
        self.assertIn("theories/G1.v is newer than its .vo", out)
        self.assertIn("theories/G2.v requires theories/G1.v, which is out of date", out)
        self.assertIn("warning: 2 of this file's dependencies are out of date", sh("goto", g3, check=True))
        out = sh("check", g3, "--allow-stale", code=0)               # the old G1.vo: g = 1
        self.assertIn("checking against out-of-date dependencies", out)
        out = sh("check", g3, "--rebuild", code=1)                   # the new G1: g = 2
        self.assertIn("compiled theories/G1.v, theories/G2.v", out)
        self.assertIn("line 3, characters", out)
        self.write("G3.v", "From My Require Import G2.\nLemma k : h = 2.\nProof. reflexivity. Qed.\n")
        self.assertIn("OK", sh("check", g3, code=0))
        self.assertIn("compiled theories/G3.v", sh("compile", g3, code=0))
        self.assertTrue(os.path.exists(os.path.join(d, "G3.vo")))
        self.write("G3.v", "Lemma bad : 1 = 2.\nProof. reflexivity. Qed.\n")
        self.assertIn("compiling theories/G3.v failed", sh("compile", g3, code=1))
        sh("stop", g3)

    def test_h_check_agrees_with_coqc_over_edits(self):
        """Random edits, each followed by check and coqc: same verdict and
        diagnostics every time, including edits whose effects reach later
        proofs (a Section proof's variables, a Set inside a proof, a
        statement, a Defined body), which must not be answered from earlier
        checks."""
        import random
        toggles = [   # (text in the base file, the alternative)
            ("Lemma l1 : 1 = 1.\nProof.\n  reflexivity.", "Lemma l1 : 1 = 1.\nProof.\n  simpl. reflexivity."),
            ("Lemma l2 : 2 = 2.", "Lemma l2 : 3 = 3."),                    # l4 uses l2
            ("Lemma l3 : 3 = 3.\nProof.\n  reflexivity.", "Lemma l3 : 3 = 3.\nProof.\n  exact I."),
            ("(* top *)", "(* top, longer comment *)\n"),
            ("Lemma s1 : True.\nProof.\n  exact I.", "Lemma s1 : True.\nProof.\n  pose proof n as m. exact I."),
            ("Lemma ab : True.\nProof.\n  Set Implicit Arguments.", "Lemma ab : True.\nProof.\n  idtac."),
            ("Definition dd : nat.\nProof.\n  exact 1.", "Definition dd : nat.\nProof.\n  exact 2."),
            ("Lemma l5 : 5 = 5.\nProof.\n  reflexivity.", "Lemma l5 : 5 = 5.\nProof.\n    reflexivity."),
            ("Lemma l6 : 6 = 6.\nProof.\n  reflexivity.", "Lemma l6 : 6 = 6.\nProof.\n  idtac.\n  reflexivity."),
            ("Lemma l7 : 7 = 7.", "Lemma l7 : 8 = 8."),                    # u7 sees it through a hint
            ("Lemma l8 : 8 = 8.", "Lemma l8 : 9 = 9."),                    # u8 through an Ltac
            ("Lemma l9 : 9 = 9.", "Lemma l9 : 10 = 10."),                  # u9 through a Definition
            ("Lemma l10 : 10 = 10.", "Lemma l10 : 10 + 0 = 10."),          # nothing uses it
            ("Lemma l11 : 11 = 11.", "Lemma l11b : 11 = 11."),             # renamed: u11 loses it
            ("Lemma l13 : 13 = 13.", "Lemma l13 : 14 = 14."),              # w13 sees it through v13's type
            ("Definition dv := 5.", "Definition dv := 6."),                # udv computes with it
            ("Definition unused := 1.", "Definition unused := 2."),        # nothing uses it
            ("Ltac use8 := exact l8.", "Ltac use8 := exact I."),           # u8 runs it
            ("(* slot *)", "Definition double (n : nat) := n."),          # shadows My.A.double for ud
            ("Instance op_nat : Op nat := Nat.add.", "Instance op_nat : Op nat := Nat.mul."),
                                                     # ut reaches it by typeclass search, through twice
        ]
        base = "\n".join([
            "(* top *)",
            "From My Require Import A.",
            "Class Op (A : Type) := op : A -> A -> A.",
            "Definition twice {A} `{Op A} (x : A) := op x x.",     # generic, before the instance
            "Definition dv := 5.",
            "Definition unused := 1.",
            "(* slot *)",
            "Lemma l1 : 1 = 1.\nProof.\n  reflexivity.\nQed.",
            "Lemma l2 : 2 = 2.\nProof.\n  reflexivity.\nQed.",
            "Lemma l3 : 3 = 3.\nProof.\n  reflexivity.\nQed.",
            "Section S.\nVariable n : nat.",
            "Lemma s1 : True.\nProof.\n  exact I.\nQed.",
            "Lemma s2 : True.\nProof.\n  exact s1.\nQed.",               # takes n when s1 does
            "End S.",
            "Lemma use_s2 : True.\nProof.\n  exact s2.\nQed.",
            "Lemma use_s1 : True.\nProof.\n  exact s1.\nQed.",          # fails once s1 takes n
            "Lemma ab : True.\nProof.\n  Set Implicit Arguments.\n  exact I.\nQed.",   # outlives the Qed
            "Definition idf (A : Type) (x : A) := x.",
            "Lemma uses_ab : True.\nProof.\n  pose proof (idf 1) as h. exact I.\nQed.",   # needs A implicit
            "Definition dd : nat.\nProof.\n  exact 1.\nDefined.",
            "Lemma ddv : dd = 1.\nProof.\n  reflexivity.\nQed.",       # needs dd's body
            "Lemma l4 : 2 = 2.\nProof.\n  exact l2.\nQed.",            # needs l2's statement
            "Lemma l5 : 5 = 5.\nProof.\n  reflexivity.\nQed.",
            "Lemma l6 : 6 = 6.\nProof.\n  reflexivity.\nQed.",
            "Lemma l7 : 7 = 7.\nProof.\n  reflexivity.\nQed.",
            "Create HintDb db7.\n#[export] Hint Resolve l7 : db7.",
            "Lemma l8 : 8 = 8.\nProof.\n  reflexivity.\nQed.",
            "Ltac use8 := exact l8.",
            "Lemma l9 : 9 = 9.\nProof.\n  reflexivity.\nQed.",
            "Definition d9 := l9.",
            "Lemma l10 : 10 = 10.\nProof.\n  reflexivity.\nQed.",
            "Lemma l11 : 11 = 11.\nProof.\n  reflexivity.\nQed.",
            "Lemma u7 : 7 = 7.\nProof.\n  solve [eauto with nocore db7].\nQed.",
            "Lemma u8 : 8 = 8.\nProof.\n  use8.\nQed.",
            "Lemma u9 : 9 = 9.\nProof.\n  exact d9.\nQed.",
            "Lemma u11 : 11 = 11.\nProof.\n  exact l11.\nQed.",
            "Lemma u12 : 12 = 12.\nProof.\n  reflexivity.\nQed.",
            "Lemma l13 : 13 = 13.\nProof.\n  reflexivity.\nQed.",
            "Lemma v13 : l13 = l13.\nProof.\n  reflexivity.\nQed.",
            "Lemma w13 : True.\nProof.\n  match type of v13 with @eq (13 = 13) _ _ => exact I end.\nQed.",
            "#[export] Instance op_nat : Op nat := Nat.add.",
            "Lemma udv : dv = 5.\nProof.\n  reflexivity.\nQed.",
            "Lemma ud : double 2 = 4.\nProof.\n  reflexivity.\nQed.",
            "Lemma ut : twice 3 = 6.\nProof.\n  reflexivity.\nQed.",
            "Lemma u14 : 14 = 14.\nProof.\n  reflexivity.\nQed.", ""])
        rel = "theories/Rand.v"
        f = os.path.join(self.dir, rel)
        coqc = self.coqc()
        rng = random.Random(7)
        state = [False] * len(toggles)
        # each edit on and off again (from the base file, every proof
        # checked), then random sequences of them
        order = [i for i in range(len(toggles)) for _ in (0, 1)]
        order += [rng.randrange(len(toggles)) for _ in range(40)]
        for step, i in enumerate([None] + order):
            if i is not None:
                state[i] = not state[i]
            text = base
            for (a, b), on in zip(toggles, state):
                if on:
                    self.assertIn(a, text)
                    text = text.replace(a, b, 1)
            self.write("Rand.v", text)
            if rng.random() < 0.3:                   # sometimes a goto in between (fast replay)
                sh("goto", "%s:%d" % (f, rng.randint(1, text.count("\n"))))
            r = subprocess.run(coqc + ["-Q", "theories", "My", rel], cwd=self.dir,
                               capture_output=True, text=True)
            want = [l for l in (r.stdout + r.stderr).splitlines() if l.strip()]
            p = subprocess.run(CMD + ["check", rel], cwd=self.dir, capture_output=True, text=True)
            got = [l for l in p.stdout.splitlines() if l.strip() and not l.startswith("rocq-live")]
            ctx = "step %d, toggles %s\n%s" % (step, [k for k, on in enumerate(state) if on], p.stdout)
            self.assertEqual(got, want, ctx)
            self.assertEqual(p.returncode, r.returncode, ctx)
        sh("stop", f)

    def test_i_tactic_edit_keeps_later_proofs(self):
        f = os.path.join(self.dir, "theories", "Keep.v")
        lemmas = ["Lemma k%d : %d = %d.\nProof.\n  reflexivity.\nQed.\n" % (i, i, i) for i in range(10)]
        self.write("Keep.v", "".join(lemmas))
        sh("check", f, code=0)
        self.write("Keep.v", "".join(lemmas).replace("k3 : 3 = 3.\nProof.\n  reflexivity.",
                                                     "k3 : 3 = 3.\nProof.\n  idtac.\n  reflexivity."))
        out = sh("check", f, code=0)
        self.assertIn("6 proof(s) checked before kept", out)            # k4..k9
        with open(f) as fh:
            self.write("Keep.v", "(* a comment *)\n" + fh.read())
        self.assertIn("OK [0 of", sh("check", f, code=0))               # comments: nothing re-run
        out = sh("audit", f, check=True)                                # audit runs every proof
        self.assertIn("PASS", out)
        self.assertIn("10 declaration(s) checked with Print Assumptions, 10 closed", out)
        sh("stop", f)

    def test_j_statement_edit_keeps_proofs_that_cannot_see_it(self):
        f = os.path.join(self.dir, "theories", "Stm.v")
        text = ("Lemma a : 1 = 1.\nProof.\n  reflexivity.\nQed.\n"
                + "".join("Lemma b%d : %d = %d.\nProof.\n  reflexivity.\nQed.\n" % (i, i, i) for i in range(8))
                + "Lemma c : 1 = 1.\nProof.\n  exact a.\nQed.\n")
        self.write("Stm.v", text)
        sh("check", f, code=0)
        self.write("Stm.v", text.replace("Lemma a : 1 = 1.", "Lemma a : 2 = 2."))
        out = sh("check", f, code=1)                       # c uses a: re-run, and fails
        self.assertIn("8 proof(s) checked before kept", out)   # b0..b7 cannot see a
        self.assertIn('line 39, characters 8-9:', out)
        sh("stop", f)

    def test_k_definition_edit_keeps_proofs_that_cannot_see_it(self):
        f = os.path.join(self.dir, "theories", "Def.v")
        text = ("Definition k := 1.\n"
                + "".join("Lemma b%d : %d = %d.\nProof.\n  reflexivity.\nQed.\n" % (i, i, i) for i in range(6))
                + "Lemma c : k = 1.\nProof.\n  reflexivity.\nQed.\n")
        self.write("Def.v", text)
        sh("check", f, code=0)
        self.write("Def.v", text.replace("Definition k := 1.", "Definition k := 2."))
        out = sh("check", f, code=1)                       # c computes with k: re-run, fails
        self.assertIn("6 proof(s) checked before kept", out)
        sh("stop", f)

    def test_l_check_all_errors(self):
        """--all-errors reports every failing proof; each error is the one
        coqc reports when that proof is the only broken one."""
        def text(broken):
            out = ["Lemma p%d : %d = %d.\nProof.\n  %s\nQed.\n"
                   % (i, i, i, "exact (eq_refl %d)." % (i + 100) if i in broken else "reflexivity.")
                   for i in range(6)]
            return "".join(out) + "Lemma uses : 1 = 1.\nProof.\n  exact p1.\nQed.\n"   # p1 broken: fine
        rel = "theories/AllErr.v"
        coqc = self.coqc()
        want = []
        for i in (1, 3, 4):
            self.write("AllErr.v", text({i}))
            r = subprocess.run(coqc + ["-Q", "theories", "My", rel], cwd=self.dir, capture_output=True, text=True)
            want += [l for l in (r.stdout + r.stderr).splitlines() if l.strip()]
        self.write("AllErr.v", text({1, 3, 4}))
        first = sh("check", rel, cwd=self.dir, code=1)
        self.assertEqual(first.count("File "), 1)                     # coqc's: the first error only
        out = sh("check", rel, "--all-errors", cwd=self.dir, code=1)
        got = [l for l in out.splitlines() if l.strip() and not l.startswith("rocq-live")]
        self.assertEqual(got, want)
        self.assertIn("3 error(s); 3 failing proof(s) closed with Admitted to go on", out)
        self.write("AllErr.v", text(set()))
        self.assertIn("OK", sh("check", rel, "--all-errors", cwd=self.dir, code=0))
        # an error outside a proof stops, after the proof errors before it
        self.write("AllErr.v", text({1}) + "Definition bad := undefined_name.\nLemma z : True.\nProof.\n  exact 7.\nQed.\n")
        out = sh("check", rel, "--all-errors", cwd=self.dir, code=1)
        self.assertEqual(out.count("File "), 2)
        self.assertIn("stopped at an error outside a proof", out)
        sh("stop", os.path.join(self.dir, rel))

    def test_m_impact(self):
        d = os.path.join(self.dir, "theories")
        self.write("I1.v", "Definition i := 1.\nLemma i_eq : i = 1.\nProof. reflexivity. Qed.\n")
        self.write("IOther.v", "Definition other := 0.\n")                       # never built
        self.write("I2.v", "From My Require Import I1 IOther.\nLemma i2 : i = 1.\nProof. exact i_eq. Qed.\n")
        self.write("I3.v", "From My Require Import I2.\nLemma i3 : True.\nProof. exact I. Qed.\n")
        self.write("I4.v", "From My Require Import I1.\nLemma i4 : i + i = 2.\nProof. reflexivity. Qed.\n")
        self.write("I5.v", "From My Require Import I4.\nLemma i5 : True.\nProof. exact I. Qed.\n")
        self.write("I6.v", "Definition unrelated := 6.\n")
        out = sh("impact", os.path.join(d, "I1.v"), code=0)
        self.assertIn("4 file(s) require it", out)
        self.assertIn("ok       theories/IOther.v", out)                  # out of date, needed by I2
        self.assertIn("(out of date, needed)", out)
        self.assertLess(out.index("theories/I1.v"), out.index("theories/I2.v"))
        self.assertNotIn("I6.v", out)
        self.write("I1.v", "Definition i := 2.\nLemma i_eq : i = 2.\nProof. reflexivity. Qed.\n")
        out = sh("impact", os.path.join(d, "I1.v"), code=1)
        self.assertIn("FAILED   theories/I2.v", out)
        self.assertIn('File "./theories/I2.v", line 3', out)
        self.assertIn("skipped  theories/I3.v (requires theories/I2.v, which failed)", out)
        self.assertIn("FAILED   theories/I4.v", out)
        self.assertIn("skipped  theories/I5.v (requires theories/I4.v, which failed)", out)
        self.assertIn("1 compiled, 2 failed, 2 not checked", out)

    def test_n_compact_goals_and_name_hints(self):
        f = os.path.join(self.dir, "theories", "Hyp.v")
        self.write("Hyp.v", "Lemma my_special_lemma : forall n : nat, n + 0 = n.\n"
                   "Proof. intros n. rewrite <- plus_n_O. reflexivity. Qed.\n"
                   "Lemma t : forall (a b c d : nat) (H1 : a = b) (H2 : b = c) (H3 : c = d), a + 0 = d.\n"
                   "Proof.\n  intros.\n  rewrite H1.\nAdmitted.\n")
        out = sh("goto", f + ":6", check=True)                     # full
        self.assertIn("H3 : c = d", out)
        out = sh("run", f, "rewrite H1.", check=True)
        self.assertIn("(7 unchanged hypotheses not shown: a, b, c, d, H1, H2, H3)", out)
        self.assertIn("b + 0 = d", out)
        self.assertNotIn("H2 : b = c", out)
        out = sh("run", f, "clear H3.", check=True)
        self.assertIn("(no longer in context: H3)", out)
        self.assertIn("H2 : b = c", sh("goals", f, check=True))     # goals: everything
        self.assertIn("H2 : b = c", sh("run", f, "idtac.", check=True, env={"ROCQ_LIVE_COMPACT": "0"}))
        out = sh("run", f, "rewrite my_specail_lemma.", check=False)  # a typo
        self.assertIn("rocq-live hint: my_specail_lemma does not exist here; similar names:", out)
        self.assertIn("  my_special_lemma : forall n : nat, n + 0 = n", out)
        out = sh("run", f, "rewrite lemma_special_my.", check=False)  # parts in another order
        self.assertIn("  my_special_lemma :", out)
        # in a library that is not loaded: which one, and the Require line
        out = sh("run", f, "rewrite Nat.add_comm.", check=False)
        self.assertIn("rocq-live hint: Nat.add_comm is in Stdlib.Arith.PeanoNat, which is not loaded; "
                      "add `From Stdlib Require Import PeanoNat.`", out)
        self.assertIn("Nat.add_comm : forall n m : nat, n + m = m + n", out)
        out = sh("run", f, "rewrite Nat.add_com.", check=False)     # and a typo
        self.assertIn("Nat.add_com does not exist here; Nat.add_comm is in Stdlib.Arith.PeanoNat", out)
        out = sh("run", f, "rewrite no_such_name_at_all.", check=False)
        self.assertIn("no name like no_such_name_at_all is loaded", out)
        self.assertNotIn("similar names", out)
        self.assertIn("H2 : b = c", sh("goals", f, check=True))     # the lookups left no trace
        sh("stop", f)

    def test_o_stats(self):
        f = os.path.join(self.dir, "theories", "Stats.v")
        lemmas = ["Lemma s%d : %d = %d.\nProof.\n  reflexivity.\nQed.\n" % (i, i, i) for i in range(8)]
        self.write("Stats.v", "".join(lemmas))
        sh("stop", f)
        sh("check", f, code=0)                                          # a full run: the baseline
        self.write("Stats.v", "".join(lemmas).replace("s3 : 3 = 3.\nProof.\n  reflexivity.",
                                                      "s3 : 3 = 3.\nProof.\n  idtac. reflexivity."))
        sh("check", f, code=0)                                          # keeps s4..s7
        self.write("Stats.v", "".join(lemmas).replace("reflexivity", "exact I"))
        sh("check", f, "--all-errors", code=1)                          # 8 errors in one call
        out = sh("stats", f, check=True)
        self.assertIn("rocq-live stats: 3 request(s) on 1 file(s)", out)
        self.assertIn("2 whole-file check(s) took", out)
        self.assertIn("4 checked proof(s) kept instead of re-run", out)
        self.assertIn("check --all-errors: 1 call(s) found 8 error(s), 7 more than one per call", out)
        self.assertIn("calls   check 3", out)
        self.assertIn("via cli 3", out)
        n = len(open(STATS).readlines())
        sh("check", f, code=1, env={"ROCQ_LIVE_STATS": "0"})              # recording off: no line
        self.assertEqual(len(open(STATS).readlines()), n)
        self.assertIn("nothing recorded", sh("stats", f, "--since", "1m", env={"ROCQ_LIVE_STATS_FILE": STATS + ".none"}))
        sh("stop", f)
        # the usual order: a goto first, so the first check re-runs nearly,
        # not quite, everything; that still measures a full run
        g = os.path.join(self.dir, "theories", "Stats2.v")
        self.write("Stats2.v", "".join(lemmas))
        sh("goto", g + ":2", check=True)
        sh("check", g, code=0)                                          # 32 of 33 sentences
        self.write("Stats2.v", "".join(lemmas).replace("s5 : 5 = 5.\nProof.\n  reflexivity.",
                                                       "s5 : 5 = 5.\nProof.\n  idtac. reflexivity."))
        sh("check", g, code=0)
        out = sh("stats", g, check=True)
        self.assertIn("1 whole-file check(s) took", out)
        self.assertNotIn("no full run", out)
        sh("stop", g)

    def daemon_pid(self, f):
        for n in os.listdir(SESSIONS):
            if n.endswith(".sock.file") and open(os.path.join(SESSIONS, n)).read() == os.path.realpath(f):
                return int(open(os.path.join(SESSIONS, n[:-5] + ".pid")).read())
        return None

    def test_p_session_stopped_between_steps(self):
        """A session stopped under an agent (gc, someone's stop --all) is
        restored on its next request, which then succeeds."""
        sh("goto", self.b + ":7", check=True)
        sh("run", self.b, "unfold double.", check=True)
        os.killpg(self.daemon_pid(self.b), 9)
        out = sh("run", self.b, "lia.", check=True)
        self.assertIn("restored the session from its recipe", out)
        self.assertIn("No more goals", out)
        self.assertIn("n + n = 2 * n", sh("back", self.b, check=True))
        sh("stop", self.b)

    def test_q_stop_all_spares_busy_sessions(self):
        import threading
        f = os.path.join(self.dir, "theories", "Busy.v")
        self.write("Busy.v", "".join("Lemma p%d : Nat.pow 2 17 + %d = 131072 + %d.\nProof. reflexivity. Qed.\n"
                                     % (i, i, i) for i in range(12)))
        result = {}
        t = threading.Thread(target=lambda: result.update(out=sh("goto", f, code=0)))
        t.start()
        for _ in range(100):                                       # until it is busy
            if any(n.endswith(".busy") for n in os.listdir(SESSIONS)):
                break
            time.sleep(0.05)
        out = sh("stop", "--all", check=True)
        self.assertIn("left 1 busy one(s) running", out)
        t.join()
        self.assertIn("checked up to", result["out"])               # its request went through
        sh("stop", f)

    def test_r_concurrent_starts_and_stops(self):
        """Several agents at once, each stepping through its own file, while
        sessions are stopped and killed around them: every step succeeds."""
        import random, threading
        files = []
        for i in range(4):
            self.write("W%d.v" % i, "Lemma w : forall n : nat, n + 0 = n.\nProof.\n  intros n.\n  "
                       "rewrite <- plus_n_O.\n  reflexivity.\nQed.\n")
            files.append(os.path.join(self.dir, "theories", "W%d.v" % i))
        errors, stop = [], threading.Event()

        def agent(f):
            try:
                for _ in range(4):
                    out = sh("goto", f + ":4", code=0)
                    assert "n + 0 = n" in out, out
                    out = sh("run", f, "rewrite <- plus_n_O.", code=0)
                    assert "n = n" in out, out
                    sh("run", f, "exact I.", code=1)                    # a failing step
                    out = sh("back", f, code=0)
                    assert "n + 0 = n" in out, out
            except AssertionError as e:
                errors.append("%s: %s" % (os.path.basename(f), e))

        def disturb():
            rng = random.Random(1)
            while not stop.is_set():
                if rng.random() < 0.5:
                    sh("stop", "--all")                                # spares busy ones
                else:
                    pid = self.daemon_pid(rng.choice(files))
                    if pid:
                        try:
                            os.killpg(pid, 9)                           # as gc would, any time
                        except OSError:
                            pass
                time.sleep(0.2)
        both = [threading.Thread(target=agent, args=(f,)) for f in files]
        noise = threading.Thread(target=disturb)
        noise.start()
        for t in both:
            t.start()
        # two agents starting the same file at the same moment get one session
        same = [threading.Thread(target=lambda: sh("goto", files[0] + ":4", code=0)) for _ in range(2)]
        for t in both:
            t.join()
        stop.set()
        noise.join()
        for t in same:
            t.start()
        for t in same:
            t.join()
        self.assertEqual(sum(1 for n in os.listdir(SESSIONS) if n.endswith(".sock.file")
                             and open(os.path.join(SESSIONS, n)).read() == os.path.realpath(files[0])), 1)
        self.assertEqual(errors, [])
        sh("stop", "--all")

    def test_5_stop_and_list(self):
        sh("goto", self.b, check=True)
        self.assertIn(os.path.realpath(self.b), sh("list", check=True))
        sh("stop", self.b, check=True)
        self.assertIn("no session", sh("goals", self.b, check=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
