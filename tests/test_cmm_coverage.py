"""The code map finds every function in a real file, except the known gap: a function defined inside another function.

Measured 2026-10-31 on v0.8.1, v0.11.0 and v0.12.1: 232 of the 243 functions in server.py; the 11 missing are all nested,
and methods such as __init__ are found (label Method). This fails if a release loses anything else. It also fails when a
release closes the gap: then the rule in claude/rules/cmm-rules.md is out of date and can go.

Indexes a copy in a temp HOME, so the real graph is not touched. Skipped without the binary.
Run: python3 tests/test_cmm_coverage.py  (stdlib only)."""
import ast
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = shutil.which("codebase-memory-mcp")


def definitions(source):
    """(line of the def or its first decorator, is it nested in a function) for each function."""
    out = []

    def walk(node, in_function):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lines = {child.lineno} | {d.lineno for d in child.decorator_list}
                out.append((lines, child.name, in_function))
                walk(child, True)
            else:
                walk(child, in_function)

    walk(ast.parse(source), False)
    return out


@unittest.skipUnless(EXE, "codebase-memory-mcp is not installed")
class CodeMapCoverage(unittest.TestCase):
    def test_only_nested_functions_are_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            os.makedirs(repo)
            shutil.copy(os.path.join(ROOT, "server.py"), repo)
            env = dict(os.environ, HOME=tmp, XDG_CACHE_HOME=os.path.join(tmp, ".cache"))
            for args in (["init", "-q"], ["add", "-A"],
                         ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"]):
                subprocess.run(["git", "-C", repo, *args], check=True, env=env)

            def cli(tool, args):
                r = subprocess.run([EXE, "cli", tool, json.dumps(args)], capture_output=True, text=True, env=env,
                                   timeout=300)
                self.assertEqual(r.returncode, 0, r.stderr)
                return r.stdout

            index = cli("index_repository", {"repo_path": repo})
            project = re.search(r'"project"\s*:\s*"([^"]+)"', index).group(1)
            out = cli("query_graph", {"project": project, "query": "MATCH (f) WHERE (f:Function OR f:Method) "
                                                                   "AND f.file_path='server.py' RETURN f.start_line"})
            # v0.8 prints JSON; later versions print one value per row.
            try:
                found = {int(row[0]) for row in json.loads(out)["rows"]}
            except ValueError:
                found = {int(v) for v in re.findall(r'^\s+"?(\d+)"?\s*$', out, re.M)}
            with open(os.path.join(repo, "server.py"), encoding="utf-8") as f:
                defs = definitions(f.read())
            missing = [(name, nested) for lines, name, nested in defs if not lines & found]
            self.assertGreater(len(defs), 100)
            self.assertEqual([m for m in missing if not m[1]], [], "the code map lost functions it used to find")
            nested_total = sum(1 for _, _, nested in defs if nested)
            self.assertEqual(len(missing), nested_total, "a release closed the nested-function gap: update cmm-rules.md")


if __name__ == "__main__":
    unittest.main()
