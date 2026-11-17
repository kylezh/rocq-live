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


# ROCQ_LIVE_CMD: test an installed command (e.g. a wheel's `rocq-live`)
# instead of the script in this repository
CMD = os.environ["ROCQ_LIVE_CMD"].split() if os.environ.get("ROCQ_LIVE_CMD") else [sys.executable, TOOL]


def sh(*args, check=None, env=None):
    e = dict(os.environ, **(env or {}))
    r = subprocess.run(CMD + list(args), capture_output=True, text=True, env=e)
    if check is not None:
        assert (r.returncode == 0) == check, "rocq-live %s -> %d\n%s%s" % (args, r.returncode, r.stdout, r.stderr)
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
    def compile_a(cls):
        # compile with the same toolchain rocq-live uses: its coqtop's coqc
        info = sh("info", os.path.join(cls.dir, "theories", "A.v"))
        env = os.environ.copy()
        if "opam switch" in info:
            sw = info.split("opam switch ")[1].split(" ")[0]
            cmd = ["opam", "exec", "--switch=" + sw, "--", "coqc"]
        else:
            cmd = ["coqc"]
        subprocess.run(cmd + ["-Q", "theories", "My", "theories/A.v"], cwd=cls.dir, env=env, check=True)

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
        self.assertIn("rocq_goto", [t["name"] for t in got[2]["result"]["tools"]])
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
        self.write("F.v", open(f).read().replace("exact I.", "exact I. "))
        out = sh("goto", f + ":7", check=False)
        self.assertIn("error in the sentence at theories/F.v line 1", out)
        sh("stop", f)

    def test_c_restore_from_recipe(self):
        sh("goto", self.b + ":7", check=True)
        sh("run", self.b, "unfold double.", check=True)
        d = "/tmp/rocq-live-%d" % os.getuid()

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
        self.assertIn("0 session(s)", sh("list", check=True))                         # nothing left running
        sh("stop", self.b, env=env)

    def test_5_stop_and_list(self):
        sh("goto", self.b, check=True)
        self.assertIn(os.path.realpath(self.b), sh("list", check=True))
        sh("stop", self.b, check=True)
        self.assertIn("no session", sh("goals", self.b, check=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
