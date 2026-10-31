"""memory_cleanup.py removes a memory note only when every condition holds, against a real git project and a fake Muninn.

Run: python3 tests/test_memory_cleanup.py  (stdlib only)."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import memory_cleanup as mc

DAY = mc.DAY
NOW = 1_800_000_000
OLD = NOW - 200 * DAY


class FakeMuninn:
    """The two doors memory_cleanup uses: the note list (which, like the real one, keeps closed notes) and the tools."""

    def __init__(self, notes):
        self.notes = {n["id"]: dict(n) for n in notes}
        self.closed = set()
        self.calls = []
        self.short_read = False
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, data):
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                notes = list(fake.notes.values())
                total = len(notes) + (1 if fake.short_read else 0)
                offset = int(self.path.split("offset=")[1])
                self.reply({"engrams": notes[offset:offset + 200], "total": total})

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                name, args = req["params"]["name"], req["params"]["arguments"]
                if name != "muninn_read":
                    fake.calls.append((name, args))
                if name == "muninn_read":
                    n = fake.notes[args["id"]]
                    out = {**n, "state": "active"}
                    if args["id"] in fake.closed:
                        out["valid_until"] = "2020-01-01T00:00:00Z"
                elif name == "muninn_forget":
                    fake.closed.add(args["id"])
                    out = {"ok": True}
                else:  # evolve: a new id, the old one retired
                    new_id = args["id"] + "-new"
                    old = fake.notes.pop(args["id"])
                    fake.notes[new_id] = {**old, "id": new_id, "content": args["new_content"]}
                    fake.closed.discard(args["id"])
                    out = {"id": new_id}
                self.reply({"result": {"content": [{"text": json.dumps(out)}]}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tool_calls(self, name):
        return [a for n, a in self.calls if n == name]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "demo")
        os.makedirs(os.path.join(self.root, "tools"))
        self.git("init", "-q", "-b", "main")
        for name in ("tools/kept.py", "tools/gone.py", "tools/old_name.py"):
            self.write(name, f"# {name}\nsome text so the rename is detected\nmore lines\nand more\n")
        self.commit("start")
        self.git("rm", "-q", "tools/gone.py")
        self.git("mv", "tools/old_name.py", "tools/new_name.py")
        self.commit("tidy")
        self.git("checkout", "-q", "-b", "keeper")  # tools/on_branch.py lives only on a branch
        self.write("tools/on_branch.py", "on a branch\n")
        self.commit("branch work")
        self.git("checkout", "-q", "main")
        self.registry = os.path.join(self.tmp.name, "projects.json")
        with open(self.registry, "w", encoding="utf-8") as f:
            json.dump({"projects": [{"path": self.root}, {"path": os.path.join(self.tmp.name, "missing")}]}, f)
        self.transcripts = os.path.join(self.tmp.name, "transcripts")
        os.makedirs(self.transcripts)
        self.state = os.path.join(self.tmp.name, "state.json")
        self.actions = os.path.join(self.tmp.name, "actions.json")

    def git(self, *args):
        subprocess.run(["git", "-C", self.root, *args], check=True, capture_output=True)

    def write(self, name, text):
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as f:
            f.write(text)

    def commit(self, msg):
        self.git("add", "-A")
        self.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", msg)

    def note(self, nid, content, tags=("demo",), created=OLD):
        return {"id": nid, "concept": nid, "content": content, "tags": list(tags), "created_at": created}

    def judge(self, content, tags=("demo",)):
        return mc.judge(self.note("x", content, tags), mc.load_projects(self.registry))

    def run_check(self, fake, now, covered=True):
        if covered:  # pretend the board has watched for long enough, unless the test says otherwise
            st = mc.read_json(self.state)
            st.setdefault("covered_from", now - 100 * DAY)
            st.setdefault("scanned_to", now - 30 * DAY)
            mc.write_json(self.state, st)
        return mc.run(now=now, rest=fake.url, mcp=fake.url + "/mcp", registry=self.registry, state_path=self.state,
                      actions_path=self.actions, transcripts=self.transcripts)


class Names(unittest.TestCase):
    def test_finds_paths_and_files_not_urls_or_words(self):
        found = mc.names_in("See tools/task_board/server.py, res://scenes/main.tscn and `foo.gd`. Also and/or 1/2 "
                            "http://example.com/a/b ~/claudeapps/x/y.py and /Users/me/z.")
        self.assertIn("tools/task_board/server.py", found)
        self.assertIn("scenes/main.tscn", found)
        self.assertIn("foo.gd", found)
        self.assertIn("~/claudeapps/x/y.py", found)
        self.assertNotIn("1/2", found)
        self.assertFalse([n for n in found if "example.com" in n])


class Judging(Base):
    def test_a_file_still_here_keeps_the_note(self):
        self.assertEqual(self.judge("Look in tools/kept.py")["kind"], "keep")

    def test_a_deleted_file_is_gone(self):
        v = self.judge("Look in tools/gone.py for the trick")
        self.assertEqual(v["kind"], "gone")
        self.assertEqual(v["names"], ["tools/gone.py"])

    def test_a_name_history_never_knew_is_not_evidence(self):
        self.assertEqual(self.judge("Use and/or the docs/made_up.py thing")["kind"], "keep")

    def test_a_file_alive_on_a_local_branch_is_not_gone(self):
        self.assertEqual(self.judge("Look in tools/on_branch.py")["kind"], "keep")

    def test_a_file_alive_in_a_card_copy_is_not_gone(self):
        wt = os.path.join(self.tmp.name, "wt")
        self.git("worktree", "add", "-q", "-b", "card", wt)
        with open(os.path.join(wt, "tools", "gone.py"), "w", encoding="utf-8") as f:
            f.write("back\n")
        self.assertEqual(self.judge("Look in tools/gone.py")["kind"], "keep")

    def test_a_rename_gives_the_new_name(self):
        v = self.judge("Look in tools/old_name.py")
        self.assertEqual(v["kind"], "renamed")
        self.assertEqual(v["renames"], {"tools/old_name.py": "tools/new_name.py"})

    def test_a_note_already_naming_both_is_left_alone(self):
        self.assertEqual(self.judge("Look in tools/old_name.py or tools/new_name.py")["kind"], "aware")

    def test_a_note_that_knows_the_file_went_is_left_alone(self):
        self.assertEqual(self.judge("We deleted tools/gone.py on purpose.")["kind"], "aware")

    def test_one_live_name_saves_the_note(self):
        self.assertEqual(self.judge("See tools/gone.py and tools/kept.py")["kind"], "partial")

    def test_no_clear_project_means_no_verdict(self):
        self.assertEqual(self.judge("Look in tools/gone.py", tags=())["kind"], "keep")

    def test_a_project_that_cannot_be_read_is_skipped(self):
        self.assertEqual([p.name for p in mc.load_projects(self.registry)], ["demo"])


class Removing(Base):
    def test_one_check_never_acts(self):
        fake = FakeMuninn([self.note("a", "Look in tools/gone.py")])
        self.run_check(fake, NOW)
        self.assertEqual(fake.calls, [])

    def test_two_checks_a_day_apart_remove_the_note_softly(self):
        fake = FakeMuninn([self.note("a", "Look in tools/gone.py")])
        self.run_check(fake, NOW)
        self.run_check(fake, NOW + DAY - 1)
        self.assertEqual(fake.calls, [])
        self.run_check(fake, NOW + DAY)
        forgets = fake.tool_calls("muninn_forget")
        self.assertEqual([f["id"] for f in forgets], ["a"])
        self.assertTrue(forgets[0]["not_true_since"])
        self.assertNotIn("hard", forgets[0])
        self.run_check(fake, NOW + 2 * DAY)  # the store still lists it: not removed twice
        self.assertEqual(len(fake.tool_calls("muninn_forget")), 1)

    def test_a_file_coming_back_restarts_the_wait(self):
        fake = FakeMuninn([self.note("a", "Look in tools/gone.py")])
        self.run_check(fake, NOW)
        self.write("tools/gone.py", "back\n")
        self.run_check(fake, NOW + DAY)
        os.remove(os.path.join(self.root, "tools", "gone.py"))
        self.run_check(fake, NOW + DAY + 1)
        self.assertEqual(fake.calls, [])

    def test_a_note_younger_than_90_days_stays(self):
        fake = FakeMuninn([self.note("a", "Look in tools/gone.py", created=NOW - 10 * DAY)])
        self.run_check(fake, NOW)
        st = self.run_check(fake, NOW + DAY)
        self.assertEqual(fake.calls, [])
        self.assertEqual(st["waiting"], 1)

    def test_a_note_used_in_a_session_stays(self):
        fake = FakeMuninn([self.note("01ARZ3NDEKTSV4RRFFQ69G5FAV", "Look in tools/gone.py")])
        path = os.path.join(self.transcripts, "s.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"id":"01ARZ3NDEKTSV4RRFFQ69G5FAV"}\n')
        os.utime(path, (NOW - 5 * DAY, NOW - 5 * DAY))
        older = os.path.join(self.transcripts, "older.jsonl")  # keeps the watched window whole
        with open(older, "w", encoding="utf-8") as f:
            f.write("{}\n")
        os.utime(older, (NOW - 40 * DAY, NOW - 40 * DAY))
        self.run_check(fake, NOW)
        st = self.run_check(fake, NOW + DAY)
        self.assertEqual(fake.calls, [])
        self.assertEqual(st["used"]["01ARZ3NDEKTSV4RRFFQ69G5FAV"], NOW - 5 * DAY)
        self.assertEqual(st["covered_from"], NOW - 100 * DAY)
        self.assertEqual(st["waiting"], 1)

    def test_nothing_goes_until_90_days_are_covered(self):
        fake = FakeMuninn([self.note("a", "Look in tools/gone.py")])
        path = os.path.join(self.transcripts, "s.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{}\n")
        os.utime(path, (NOW - 30 * DAY, NOW - 30 * DAY))
        self.run_check(fake, NOW, covered=False)
        st = self.run_check(fake, NOW + DAY, covered=False)
        self.assertEqual(fake.calls, [])
        self.assertEqual(st["covered_from"], NOW - 30 * DAY)
        self.assertEqual(st["waiting"], 1)

    def test_a_key_decision_waits_for_a_person(self):
        fake = FakeMuninn([self.note("a", "Look in tools/gone.py", tags=("demo", "key-decision"))])
        self.run_check(fake, NOW)
        st = self.run_check(fake, NOW + DAY)
        self.assertEqual(fake.calls, [])
        self.assertIn("a", st["review"])

    def test_a_renamed_file_updates_the_note_keeping_a_key_decision(self):
        fake = FakeMuninn([self.note("a", "Look in tools/old_name.py", tags=("demo", "key-decision"))])
        self.run_check(fake, NOW)
        self.run_check(fake, NOW + DAY)
        self.assertEqual(fake.tool_calls("muninn_forget"), [])
        self.assertEqual(fake.tool_calls("muninn_evolve")[0]["new_content"], "Look in tools/new_name.py")

    def test_a_short_read_of_the_store_stops_everything(self):
        fake = FakeMuninn([self.note("a", "Look in tools/gone.py")])
        self.run_check(fake, NOW)
        fake.short_read = True
        with self.assertRaises(RuntimeError):
            self.run_check(fake, NOW + DAY)
        self.assertEqual(fake.calls, [])

    def test_no_readable_project_stops_everything(self):
        fake = FakeMuninn([self.note("a", "Look in tools/gone.py")])
        with open(self.registry, "w", encoding="utf-8") as f:
            json.dump({"projects": []}, f)
        with self.assertRaises(RuntimeError):
            self.run_check(fake, NOW)

    def test_a_single_run_removes_a_limited_number(self):
        fake = FakeMuninn([self.note(f"n{i}", "Look in tools/gone.py") for i in range(mc.MAX_PER_RUN + 5)])
        self.run_check(fake, NOW)
        st = self.run_check(fake, NOW + DAY)
        self.assertEqual(len(fake.tool_calls("muninn_forget")), mc.MAX_PER_RUN)
        self.assertEqual(st["waiting"], 5)

    def test_dry_run_changes_nothing(self):
        fake = FakeMuninn([self.note("a", "Look in tools/gone.py")])
        self.run_check(fake, NOW)
        before = mc.read_json(self.state)
        st = mc.run(now=NOW + DAY, dry=True, rest=fake.url, mcp=fake.url + "/mcp", registry=self.registry,
                    state_path=self.state, actions_path=self.actions, transcripts=self.transcripts)
        self.assertEqual(fake.calls, [])
        self.assertEqual([e["id"] for e in st["would_remove"]], ["a"])
        self.assertEqual(mc.read_json(self.state), before)


class TheBoard(Base):
    def act(self, fake, nid, action, now):
        return mc.act(nid, action, now=now, mcp=fake.url + "/mcp", state_path=self.state, actions_path=self.actions)

    def view(self, now):
        return mc.view(now, self.state, self.actions)

    def removed_note(self):
        fake = FakeMuninn([self.note("a", "Look in tools/gone.py")])
        self.run_check(fake, NOW)
        self.run_check(fake, NOW + DAY)
        return fake

    def test_the_week_list_shows_a_removal_then_lets_it_go(self):
        self.removed_note()
        self.assertEqual([e["id"] for e in self.view(NOW + DAY)["removed"]], ["a"])
        later = self.view(NOW + 9 * DAY)
        self.assertEqual(later["removed"], [])
        self.assertEqual([e["id"] for e in later["earlier"]], ["a"])  # still there to bring back

    def test_bring_back_reopens_the_note_and_it_stays(self):
        fake = self.removed_note()
        v = self.act(fake, "a", "restore", NOW + 2 * DAY)
        self.assertEqual(v["removed"], [])
        self.assertIn("a-new", fake.notes)
        self.run_check(fake, NOW + 3 * DAY)
        self.run_check(fake, NOW + 4 * DAY)
        self.assertEqual(len(fake.tool_calls("muninn_forget")), 1)

    def test_keep_takes_a_key_decision_off_the_list_for_good(self):
        fake = FakeMuninn([self.note("a", "Look in tools/gone.py", tags=("demo", "key-decision"))])
        self.run_check(fake, NOW)
        self.run_check(fake, NOW + DAY)
        self.assertEqual(self.view(NOW + DAY)["review"][0]["id"], "a")
        self.assertEqual(self.act(fake, "a", "keep", NOW + DAY)["review"], [])
        st = self.run_check(fake, NOW + 2 * DAY)
        self.assertEqual(st["review"], {})

    def test_remove_from_the_list_closes_a_key_decision(self):
        fake = FakeMuninn([self.note("a", "Look in tools/gone.py", tags=("demo", "key-decision"))])
        self.run_check(fake, NOW)
        self.run_check(fake, NOW + DAY)
        v = self.act(fake, "a", "remove", NOW + DAY)
        self.assertEqual(v["review"], [])
        self.assertEqual([e["id"] for e in v["removed"]], ["a"])
        self.assertEqual(self.run_check(fake, NOW + 2 * DAY)["review"], {})

    def test_remove_is_refused_for_a_note_not_on_the_list(self):
        fake = FakeMuninn([self.note("a", "Look in tools/kept.py")])
        with self.assertRaises(ValueError):
            self.act(fake, "a", "remove", NOW)
        self.assertEqual(fake.calls, [])

    def test_checks_are_a_full_day_apart(self):
        self.assertTrue(mc.due(NOW, self.state))
        mc.note_failure("down", NOW, self.state)
        self.assertFalse(mc.due(NOW + DAY - 1, self.state))
        self.assertTrue(mc.due(NOW + DAY, self.state))


if __name__ == "__main__":
    unittest.main()
