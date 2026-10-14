"""Run with python3 -B -m unittest discover -s tests -p 'test_*.py' -v."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("board_server", Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class LiveReadTests(unittest.TestCase):
    def test_utf8_json_is_independent_of_windows_code_page_and_stderr(self):
        issues = [{"id": "DEMO-1", "title": "Исследование связей — готово 🧭", "description": "Ёж и пример задачи"}]
        payload = json.dumps(issues, ensure_ascii=False).encode("utf-8")
        self.assertIn(b"\x98", payload)  # This byte cannot be decoded using cp1251.
        run = subprocess.run

        def run_fixture(command, **kwargs):
            self.assertEqual(command[1:], ["--readonly", "list", "--all", "--json", "--limit", "0"])
            self.assertNotIn("text", kwargs)
            self.assertNotIn("encoding", kwargs)
            self.assertEqual(kwargs["env"]["DOLT_DISABLE_EVENT_FLUSH"], "1")
            self.assertNotIn("BEADS_DIR", kwargs["env"])
            code = f"import sys; sys.stdout.buffer.write({payload!r}); sys.stderr.buffer.write(b'\\xff\\x98')"
            return run([sys.executable, "-c", code], **kwargs)

        with patch.dict(server.os.environ, {"BEADS_DIR": "unrelated-demo-database"}), patch.object(server.subprocess, "run", side_effect=run_fixture):
            result = server.read_live(Path.cwd())
        self.assertEqual(result[0]["title"], issues[0]["title"])
        self.assertEqual(result[0]["description"], issues[0]["description"])

    def test_utf8_bom_and_explicit_database(self):
        payload = b"\xef\xbb\xbf" + json.dumps([{"id": "DEMO-2", "title": "Проверка"}], ensure_ascii=False).encode("utf-8")
        with patch.object(server.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, payload, b"")) as run:
            self.assertEqual(server.read_live(Path.cwd(), "demo-beads")[0]["title"], "Проверка")
        self.assertEqual(run.call_args.kwargs["env"]["BEADS_DIR"], "demo-beads")

    def test_invalid_stdout_becomes_project_error_instead_of_request_crash(self):
        for payload in (b'["\x98"]', b"not JSON", b"{}", b""):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / ".beads").mkdir()
                source = {"slug": "demo", "root": str(root), "workdir": root, "beads_dir": None, "snapshot_slug": None, "prefix": "DEMO"}
                with patch.object(server.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, payload, b"")), patch.object(server, "project_sources", return_value=[source]):
                    data = server.load_projects()
                project = data["projects"][0]
                self.assertEqual(project["source"], "error")
                self.assertEqual(project["issues"], [])
                self.assertTrue(project["error"])
                json.dumps(data, ensure_ascii=False).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
