#!/usr/bin/env python3
"""Tests for the WebSocket server (backend/laempli/wsserver.py) and the change notifications (changes.py).

Run: python3 -m unittest discover -s tests
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest

try:
    from websockets.exceptions import ConnectionClosed, InvalidStatus
    from websockets.sync.client import connect
except ImportError:
    sys.exit("websockets missing, please run: sudo apt install python3-websockets")

TOP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(TOP, "backend"))

import laempli.wsserver  # noqa: E402
from laempli.changes import Changes  # noqa: E402
from laempli.devices import Devices  # noqa: E402
from laempli.firmware import Firmware  # noqa: E402
from laempli.wsserver import WsServer  # noqa: E402

DEV = "chlotz-b2c3d4"

# Heartbeat for the tests [s]
HEARTBEAT = 0.3


class ChangesTest(unittest.TestCase):

    def test_wait(self):
        changes = Changes()
        rev = changes.current()
        self.assertEqual(changes.wait(rev, 0.01), (rev, []))
        changes.notify("devices", "device:x")
        changes.notify("jobs")
        new, topics = changes.wait(rev, 0.01)
        self.assertEqual(topics, ["device:x", "devices", "jobs"])
        self.assertEqual(changes.wait(new, 0.01), (new, []))
        # From another thread
        threading.Timer(0.05, changes.notify, ("gitlab",)).start()
        self.assertEqual(changes.wait(new, 2)[1], ["gitlab"])


class WsServerTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.changes = Changes()
        self.devices = Devices(self.tmpdir.name, self.changes)
        self.server = WsServer(self.devices, Firmware(None), HEARTBEAT, self.changes)
        self.port = self.server.start("127.0.0.1", 0)
        self.min_interval = laempli.wsserver.MIN_INTERVAL
        laempli.wsserver.MIN_INTERVAL = 0.05

    def tearDown(self):
        laempli.wsserver.MIN_INTERVAL = self.min_interval
        self.server.stop()
        self.tmpdir.cleanup()

    def url(self, path):
        return "ws://127.0.0.1:{}{}".format(self.port, path)

    def recv(self, conn, event):
        # Next message of an event (skip others)
        end = time.time() + 5
        while time.time() < end:
            msg = json.loads(conn.recv(timeout=5))
            if msg["event"] == event:
                return msg["data"]
        self.fail("no {} event".format(event))

    def wait_for(self, check):
        end = time.time() + 5
        while (time.time() < end) and (not check()):
            time.sleep(0.02)
        self.assertTrue(check())

    def info(self):
        return next(dev for dev in self.devices.get() if dev["id"] == DEV)

    def test_device(self):
        headers = {"X-Forwarded-User": "laempli", "X-Forwarded-For": "203.0.113.7"}
        with connect(self.url("/laempli/ws/device?device={}&leds=4&fw=x".format(DEV)),
                     additional_headers=headers) as conn:
            hello = self.recv(conn, "hello")
            self.assertEqual((hello["server"], hello["heartbeat"], hello["firmware"]), ("laempli", HEARTBEAT, None))
            self.assertEqual(self.recv(conn, "state"), {"version": 0, "state": {"leds": []}})
            info = self.info()
            self.assertEqual((info["connected"], info["leds"], info["user"], info["remote_ip"], info["firmware"]),
                             (True, 4, "laempli", "203.0.113.7", "x"))
            # Time event each heartbeat
            self.assertIn("utc_offset", self.recv(conn, "time"))

            # Report: settings and options
            report = {"rssi": -50, "uptime": 10, "settings": {"name": "Old", "dim_level": 20},
                      "options": {"anim_running": ["a", "b"]}, "ack": 0}
            conn.send(json.dumps({"event": "report", "data": report}))
            self.wait_for(lambda: self.devices.get_settings(DEV)["values"] == {"name": "Old", "dim_level": 20})
            self.assertEqual(self.info()["rssi"], -50)

            # Settings from the web page, then the ack clears them
            self.devices.set_pending(DEV, {"name": "New"})
            self.assertEqual(self.recv(conn, "settings"), {"seq": 1, "settings": {"name": "New"}})
            report = dict(report, settings={"name": "New", "dim_level": 20}, ack=1)
            conn.send(json.dumps({"event": "report", "data": report}))
            self.wait_for(lambda: self.devices.get_settings(DEV)["pending"] == {})

            # New state
            self.devices.set_state(DEV, {"leds": [None]})
            self.assertEqual(self.recv(conn, "state"), {"version": 1, "state": {"leds": [None]}})

            # Bad messages are ignored
            with self.assertLogs("laempli.wsserver", "WARNING"):
                conn.send("{bad")
                conn.send(json.dumps({"event": "other", "data": {}}))
                self.recv(conn, "time")
        # Disconnected at the next event (heartbeat)
        self.wait_for(lambda: not self.info()["connected"])

    def test_ota(self):
        # Firmware parts on the device connection, state to the device info
        fwdir = os.path.join(self.tmpdir.name, "fw")
        os.makedirs(fwdir)
        name = "laempli-chlotz_20261024010203-abc1239.bin"
        image = bytes(range(256)) * 40
        with open(os.path.join(fwdir, name), "wb") as fh:
            fh.write(image)
        self.server.firmware = Firmware(fwdir)
        with connect(self.url("/ws/device?device={}&fw=x".format(DEV))) as conn:
            self.recv(conn, "hello")
            self.recv(conn, "state")
            self.devices.start_ota(DEV, "20261024010203-abc1239")
            self.assertEqual(self.info()["ota"]["state"], "starting")
            req = {"state": "running", "version": "20261024010203-abc1239", "done": 0, "size": len(image),
                   "file": name, "offset": 0, "length": 4096}
            conn.send(json.dumps({"event": "ota", "data": req}))
            conn.send(json.dumps({"event": "ota", "data": dict(req, offset=8192, length=4096)}))
            for offset in (0, 8192):
                while True:
                    msg = conn.recv(timeout=5)
                    if isinstance(msg, bytes):
                        break
                self.assertEqual(msg[:4], offset.to_bytes(4, "big"))
                self.assertEqual(msg[4:], image[offset:offset + 4096])
            # Last part is short
            conn.send(json.dumps({"event": "ota", "data": dict(req, done=8192, offset=10000, length=4096)}))
            while True:
                msg = conn.recv(timeout=5)
                if isinstance(msg, bytes):
                    break
            self.assertEqual(msg[4:], image[10000:])
            self.wait_for(lambda: self.info()["ota"]["done"] == 8192)
            self.assertEqual(self.info()["ota"]["state"], "running")
            # Bad requests: error event
            with self.assertLogs("laempli.wsserver", "WARNING"):
                for bad in (dict(req, file="other.bin"), dict(req, offset=len(image)), dict(req, length=0),
                            dict(req, length=laempli.wsserver.MAX_CHUNK + 1), dict(req, offset="0")):
                    conn.send(json.dumps({"event": "ota", "data": bad}))
                    self.assertEqual(self.recv(conn, "ota"), {"error": "bad firmware part"})
            # A report keeps the state
            conn.send(json.dumps({"event": "report", "data": {"ota": dict(req, done=9000)}}))
            self.wait_for(lambda: self.info()["ota"]["done"] == 9000)
        # Connection lost while it runs
        self.wait_for(lambda: not self.info()["connected"])
        self.assertEqual((self.info()["ota"]["state"], self.info()["ota"]["error"]), ("failed", "connection lost"))
        # New connection: the device reports its state
        with connect(self.url("/ws/device?device={}&fw=x".format(DEV))) as conn:
            self.recv(conn, "hello")
            self.assertIsNone(self.info()["ota"])
            conn.send(json.dumps({"event": "report", "data": {"ota": {"state": "failed", "error": "timeout"}}}))
            self.wait_for(lambda: (self.info()["ota"] or {}).get("error") == "timeout")
            # Device restarted: no state
            conn.send(json.dumps({"event": "report", "data": {}}))
            self.wait_for(lambda: self.info()["ota"] is None)

    def test_paths(self):
        for path, status in (("/ws/other", 404), ("/ws/device?device=bad", 400), ("/ws/device", 400)):
            with self.assertRaises(InvalidStatus, msg=path) as ctx:
                with connect(self.url(path)):
                    pass
            self.assertEqual(ctx.exception.response.status_code, status, path)
        with connect(self.url("/ws/device?device=" + DEV)) as conn:
            self.recv(conn, "hello")

    def test_not_laempli(self):
        # Only Lämpli types and app=laempli (missing: old firmware); log once per device and reason
        paths = ("/ws/device?device=energycyd-04eb58&app=energymon", "/ws/device?device=touchy-04eb58",
                 "/ws/device?device={}&app=energymon".format(DEV))
        with self.assertLogs("laempli.wsserver", "WARNING") as logs:
            for path in (paths + paths):
                with self.assertRaises(InvalidStatus, msg=path) as ctx:
                    with connect(self.url(path)):
                        pass
                self.assertEqual(ctx.exception.response.status_code, 403, path)
        self.assertEqual(len(logs.output), 3)
        self.assertIn("rejected energycyd-04eb58", logs.output[0])
        self.assertIn("not a Lämpli (app energymon)", logs.output[0])
        self.assertIn("not a Lämpli (type touchy)", logs.output[1])
        self.assertIsNone(self.devices.get_settings("energycyd-04eb58"))
        with connect(self.url("/ws/device?device={}&app=laempli".format(DEV))) as conn:
            self.assertEqual(self.recv(conn, "hello")["server"], "laempli")

    def test_web(self):
        with connect(self.url("/laempli/ws/web")) as conn:
            # Time without changes
            self.assertIn("utc_offset", self.recv(conn, "time"))
            self.devices.connect({"id": DEV})
            self.assertEqual(self.recv(conn, "changed"), ["device:" + DEV, "devices"])
            self.devices.set_state(DEV, {"leds": []})
            self.changes.notify("jobs")
            changed = set()
            while changed != {"device:" + DEV, "jobs"}:
                changed.update(self.recv(conn, "changed"))
        # Thread ends at the next send
        with self.assertRaises(ConnectionClosed):
            conn.recv(timeout=1)


if __name__ == "__main__":
    unittest.main()
