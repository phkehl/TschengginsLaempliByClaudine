#!/usr/bin/env python3
"""Tests for tools/laempli-notify.py (against the backend app on a local HTTP server).

Run: python3 -m unittest discover -s tests
"""

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import threading
import unittest
import unittest.mock

try:
    import werkzeug.serving
except ImportError:
    sys.exit("werkzeug missing, please run: sudo apt install python3-werkzeug")

TOP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(TOP, "backend"))

from laempli.devices import Devices  # noqa: E402
from laempli.generic import Generic  # noqa: E402
from laempli.jobs import Jobs  # noqa: E402
from laempli.web import create_app  # noqa: E402

# The tool (file name with "-", not importable as module)
SPEC = importlib.util.spec_from_file_location("laempli_notify", os.path.join(TOP, "tools", "laempli-notify.py"))
notify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(notify)


class LaempliNotifyTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        # Not the real config file next to the tool
        patcher = unittest.mock.patch.object(notify, "DEFAULT_CONFIG", os.path.join(self.tmpdir.name, "none.yaml"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.generic = Generic(self.tmpdir.name, ["tok"])
        self.jobs = Jobs()
        app = create_app(Devices(self.tmpdir.name), None, 15, None, self.jobs, None, self.generic)
        self.auth = []

        # Note the basic auth header
        def wsgi(environ, start_response):
            self.auth.append(environ.get("HTTP_AUTHORIZATION"))
            return app(environ, start_response)
        self.server = werkzeug.serving.make_server("127.0.0.1", 0, wsgi)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://user:pa%20ss@127.0.0.1:{}/".format(self.server.port)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.tmpdir.cleanup()

    def main(self, *argv):
        """Run the tool, returns (exit code, stdout, stderr)"""
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = notify.main(["--url", self.url, "--token", "tok"] + list(argv))
            except SystemExit as ex:
                code = ex.code
        return (code, out.getvalue(), err.getvalue())

    def state(self, *names):
        job = self.jobs.get(["generic"] + list(names))
        return (job["status"], job["result"])

    def test_set_get_delete(self):
        code, out, _ = self.main("set", "nas/backup", "--status", "running", "--timeout", "7200")
        self.assertEqual(code, 0)
        self.assertIn("nas / backup: RUNNING UNKNOWN", out)
        self.assertEqual(self.state("nas", "backup"), ("RUNNING", "UNKNOWN"))
        self.assertEqual(self.auth[-1], "Basic dXNlcjpwYSBzcw==")  # user:pa ss

        code, out, _ = self.main("set", "nas/backup", "--exit", "0", "--info", "size=12 GB", "--link",
                                 "https://x.org/log")
        self.assertEqual(code, 0)
        self.assertEqual(self.state("nas", "backup"), ("STOPPED", "PASS"))
        self.assertIn("    size: 12 GB", out)
        self.assertIn("    link: https://x.org/log", out)

        code, out, _ = self.main("get", "nas/backup", "--json")
        self.assertEqual(code, 0)
        self.assertIn('"result": "PASS"', out)
        code, out, _ = self.main("list")
        self.assertEqual(code, 0)
        self.assertIn("nas / backup: STOPPED PASS", out)

        code, out, _ = self.main("delete", "nas/backup")
        self.assertEqual(code, 0)
        self.assertIsNone(self.generic.job(["nas", "backup"]))
        code, _, err = self.main("get", "nas/backup")
        self.assertEqual(code, 1)
        self.assertIn("404", err)

    def test_errors(self):
        code, _, err = self.main("set", "a", "--status", "bogus")
        self.assertEqual(code, 1)
        self.assertIn("status must be one of", err)
        code, _, err = self.main("--token", "bad", "set", "a", "--status", "running")
        self.assertEqual(code, 1)
        self.assertIn("401", err)
        self.assertEqual(self.main("set", "a//b")[0], 2)
        self.assertEqual(self.main("run", "a")[0], 2)
        self.assertEqual(self.main("get", "a", "--", "x")[0], 2)

    def test_run(self):
        code, out, _ = self.main("run", "cron/daily", "--timeout", "90000", "--info", "host=server", "--",
                                 sys.executable, "-c", "import sys; sys.exit(3)")
        self.assertEqual(code, 3)
        self.assertEqual(self.state("cron", "daily"), ("STOPPED", "FAIL"))
        job = self.generic.job(["cron", "daily"])
        self.assertEqual(job["info"]["exit code"], "3")
        self.assertEqual(job["info"]["host"], "server")
        self.assertIn("duration", job["info"])
        self.assertIn("cron / daily: STOPPED FAIL", out)
        self.assertEqual(self.generic.get()["jobs"]['["cron", "daily"]']["timeout"], 90000)
        events = self.generic.events()
        self.assertEqual([ev["status"] for ev in events[:2]], ["STOPPED", "RUNNING"])

        code, _, err = self.main("run", "cron/x", "--", os.path.join(self.tmpdir.name, "none"))
        self.assertEqual(code, 127)
        self.assertEqual(self.state("cron", "x"), ("STOPPED", "FAIL"))
        self.assertIn("No such file", err)

    def test_url_token_order(self):
        cfg = os.path.join(self.tmpdir.name, "notify.yaml")
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write("url: {}\ntoken: tok\n".format(self.url))
        bad = "http://127.0.0.1:1"
        args = ["set", "order/a", "--status", "running"]

        def run(argv, env, default=os.path.join(self.tmpdir.name, "none.yaml")):
            with unittest.mock.patch.dict(os.environ, env, clear=True), \
                    unittest.mock.patch.object(notify, "DEFAULT_CONFIG", default), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                try:
                    code = notify.main(argv + args)
                except SystemExit as ex:
                    code = ex.code
            return (code, err.getvalue())

        env = {"LAEMPLI_NOTIFY_URL": bad, "LAEMPLI_NOTIFY_TOKEN": "bad"}
        # Config file before environment
        self.assertEqual(run(["--config", cfg], env)[0], 0)
        # Options before config file
        self.assertIn("401", run(["--config", cfg, "--token", "bad"], env)[1])
        self.assertEqual(run(["--config", cfg, "--url", self.url], env)[0], 0)
        # Environment alone, no default config file
        self.assertEqual(run([], {"LAEMPLI_NOTIFY_URL": self.url, "LAEMPLI_NOTIFY_TOKEN": "tok"})[0], 0)
        self.assertIn("no backend URL", run([], {"HOME": self.tmpdir.name})[1])
        # Default config file (next to the script) before environment, after --config
        self.assertEqual(run([], env, default=cfg)[0], 0)
        self.assertIn("no such file", run(["--config", cfg + ".x"], env, default=cfg)[1])
        # Given config file must exist
        self.assertIn("no such file", run(["--config", cfg + ".x"], env)[1])

    def test_run_no_backend(self):
        self.url = "http://127.0.0.1:1"
        code, _, err = self.main("run", "cron/y", "--", sys.executable, "-c", "pass")
        self.assertEqual(code, 0)
        self.assertIn("start not reported", err)
        self.assertIn("end not reported", err)


if __name__ == "__main__":
    unittest.main()
