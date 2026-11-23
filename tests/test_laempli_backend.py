#!/usr/bin/env python3
"""Tests for the Tschenggins Lämpli backend.

Run: python3 -m unittest discover -s tests
"""

import base64
import hashlib
import hmac
import io
import html
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from laempli.config import ConfigError, load_config  # noqa: E402
from laempli.changes import Changes  # noqa: E402
from laempli.dashboard import DashboardConfig, DashboardError, check_sticky  # noqa: E402
from laempli.devices import Devices, clean_led_test  # noqa: E402
from laempli import docs as docs_module  # noqa: E402
from laempli.docs import Docs, github_slug  # noqa: E402
from laempli.eventdb import EventStore, entry_text, search_words  # noqa: E402
from laempli.firmware import Firmware  # noqa: E402
from laempli.generic import Generic, check_path, parse_update  # noqa: E402
from laempli.github import Github  # noqa: E402
from laempli.gitlab import NO_COMMIT, RUNNING, WAITING, Gitlab  # noqa: E402
from laempli.leds import ConfigError as LedConfigError  # noqa: E402
from laempli.source import KEEP_DAYS, EventError, unix_time  # noqa: E402
from laempli.testjobs import TestJobs  # noqa: E402
from laempli.housekeeping import Housekeeping  # noqa: E402
from laempli.jobs import Jobs  # noqa: E402
from laempli.leds import (check_config, check_rows, convert_config, device_state, fmt_age, led_label,  # noqa: E402
                          parse_duration, update_states)
from laempli.logs import JournalFormatter, is_journal  # noqa: E402
from laempli.settings import SettingsError, check_settings, clean_options, clean_report  # noqa: E402
from laempli.stream import device_events, device_info  # noqa: E402
from laempli.web import (ProxyMiddleware, auth_user, create_app, events_query, fmt_dhms, led_view,  # noqa: E402
                         link_text)

DEV = "chlotz-b2c3d4"
DEV2 = "gitta-87e94c"


class Req:
    """Request with headers (for device_info())"""

    def __init__(self, headers=None):
        self.headers = (headers or {})


def open_stream(devices, query="device=" + DEV, heartbeat=15, firmware=None, headers=None):
    """Device stream (events of /ws/device, without the WebSocket) as generator of (event, data)"""
    args = {key: values[0] for key, values in urllib.parse.parse_qs(query).items()}
    info = device_info(args, Req(headers), "127.0.0.1")
    return device_events(devices, (firmware or Firmware(None)), info, heartbeat)


class EventStoreTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store = EventStore(self.tmpdir.name)

    def tearDown(self):
        self.store.close()
        self.tmpdir.cleanup()

    def test_words_text(self):
        self.assertEqual(search_words(' Foo "a b"  x_y "" '), ["foo", "a b", "x_y"])
        self.assertEqual(search_words(None), [])
        text = entry_text({"time": 0, "project": "G/P", "pipeline": 12, "user": None})
        self.assertTrue(text.endswith(" g/p 12"))
        self.assertRegex(text, r"^19(69|70)-")

    def test_events_log(self):
        self.assertEqual(self.store.add_events("a", [(1.0, "e1", {"x": 1}), (2.0, "e2", None)]), 2)
        self.store.add_event("b", 3.0, "e3", [1])
        self.assertEqual(list(self.store.iter_events("a")), [(1.0, "e1", {"x": 1}), (2.0, "e2", None)])
        self.assertEqual(self.store.count_events("b"), 1)
        for num in range(5):
            self.store.add_log("a", {"time": 10.0 + num, "name": ("odd" if (num % 2) else None), "num": num})
        self.store.add_log("b", {"time": "bad", "name": "b"})
        total, filtered, entries = self.store.query_log("a", 1, 2)
        self.assertEqual((total, filtered, [entry["num"] for entry in entries]), (5, 5, [3, 2]))
        # Missing values sort first, same values by insert order
        entries = self.store.query_log("a", sort=[("name", False)])[2]
        self.assertEqual([entry["num"] for entry in entries], [0, 2, 4, 1, 3])
        self.assertEqual(self.store.query_log("a", q="odd", until=13.0)[1:], (1, [{"time": 11.0, "name": "odd",
                                                                                     "num": 1}]))
        self.assertEqual(self.store.last_time("a"), 14.0)
        self.assertEqual(self.store.last_time("c"), None)
        self.assertEqual(self.store.query_log("b")[2][0]["time"], "bad")
        # Expire: raw events and log
        self.assertEqual(self.store.expire("a", 12.0), 2)
        self.assertEqual(self.store.query_log("a")[0], 3)
        self.store.clear_log("a")
        self.assertEqual(self.store.query_log("a")[0], 0)
        self.assertEqual(self.store.count_events("b"), 1)

    def test_transaction(self):
        with self.assertRaises(KeyError):
            with self.store.transaction():
                self.store.add_event("a", 1.0, "e", {})
                with self.store.transaction():
                    self.store.add_event("a", 2.0, "e", {})
                raise KeyError("x")
        self.assertEqual(self.store.count_events("a"), 0)
        with self.store.transaction():
            self.store.add_event("a", 1.0, "e", {})
        self.store.checkpoint()
        self.assertEqual(os.path.getsize(self.store.path + "-wal"), 0)
        # Other connection sees it, schema version checked
        other = EventStore(self.tmpdir.name)
        self.assertEqual(other.count_events("a"), 1)
        other.db.execute("UPDATE meta SET value = '99' WHERE key = 'schema'")
        other.close()
        with self.assertRaises(RuntimeError):
            EventStore(self.tmpdir.name)


class BackendTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.datadir = os.path.join(self.tmpdir.name, "data")
        self.devices = Devices(self.datadir)
        self.fwdir = os.path.join(self.tmpdir.name, "fw")
        os.mkdir(self.fwdir)
        self.make_client()

    def make_client(self, heartbeat=15):
        self.gitlab = Gitlab(self.datadir)
        app = create_app(self.devices, Firmware(self.fwdir), heartbeat, self.gitlab, Jobs())
        app.wsgi_app = ProxyMiddleware(app.wsgi_app)
        # Reverse proxy: passes the full path with the prefix
        self.client = app.test_client()
        self.client.environ_base["HTTP_X_FORWARDED_PREFIX"] = "/laempli"
        self.direct = app.test_client()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_redirect(self):
        # Without proxy: no prefix
        res = self.direct.get("/")
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.headers["Location"], "/web")
        self.assertEqual(self.direct.get("/laempli/web/devices").status_code, 404)
        # Proxy passes the path with or without the prefix
        for path in ("/laempli/", "/"):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 302)
            self.assertEqual(res.headers["Location"], "/laempli/web")

    def test_web(self):
        # Tabs, also below the prefix
        for path in ("/web/devices", "/laempli/web/devices", "/web/jobs", "/web/firmwares", "/web/gitlab",
                     "/web/sources"):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200, path)
            for tab in ("Devices", "Jobs", "Firmwares", "Sources"):
                self.assertIn(">{}</a>".format(tab).encode(), res.data, path)
            self.assertEqual(res.data.split(b"</nav>")[0].count(b"nav-link active"), 1, path)
            self.assertEqual(res.data.count(b"<main "), 1, path)
            self.assertIn(b"data-view=", res.data, path)
        # Version of scripts and styles: in the page and the URLs (reload after a deploy)
        page = self.client.get("/web/devices").data.decode()
        version = re.search(r'data-version="([0-9a-f]{12})"', page).group(1)
        self.assertIn("/laempli/static/laempli.js?v=" + version, page)
        self.assertIn("/laempli/static/laempli.css?v=" + version, page)
        # Sources: tab in the main navbar, second navbar with the sources, overview table
        for path in ("/web/gitlab", "/web/sources"):
            page = self.client.get(path).data.decode()
            nav = page.split("</nav>")[0]
            self.assertIn('nav-link active" aria-current="page"\n                           href="/laempli/web/sources"', nav)
            self.assertIn('class="navbar-nav sub-tabs source-tabs"', page)
            self.assertIn(">GitLab</a>", page)
        page = self.client.get("/web/sources").data.decode()
        self.assertRegex(page, r'<td><a href="/laempli/web/gitlab">GitLab</a></td>\s*<td class="text-end">0</td>')
        self.assertNotIn("Failing", page)
        # No events: table head without DataTables, then the time the events are kept
        page = self.client.get("/web/gitlab").data.decode()
        self.assertRegex(page, r'<table class="table table-sm table-hover">\s*<thead>')
        self.assertIn("No data for the past {} days.".format(self.gitlab.keep_days), page)
        # App page: no view, the script gets it
        for path in ("/web", "/laempli/web"):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200, path)
            self.assertNotIn(b"data-view=", res.data, path)
            self.assertIn(b"/static/laempli.js", res.data, path)

    def test_static(self):
        # Vendored Bootstrap and DataTables, also below the prefix, no external resources
        page = self.client.get("/laempli/web/devices").data.decode()
        self.assertIn("/laempli/static/datatables/dataTables.min.js", page)
        self.assertIn("/laempli/static/logo.png", page)
        for path in re.findall(r'(?:src|href)="(/laempli/static/[^"]+)"', page) + [
                "/static/bootstrap/bootstrap.min.css"]:
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200, path)
            res.close()
        for url in ("/web/devices", "/web/jobs", "/web/firmwares", "/web/gitlab", "/web/device/" + DEV):
            res = self.client.get(url)
            self.assertEqual(res.status_code, 200, url)
            page = res.data.decode()
            self.assertEqual(re.findall(r'<(?:script|link|img)[^>]*(?:src|href)="(?!/|data:)[^"]*"', page), [], url)
            # Favicon inlined
            self.assertIn('<link rel="icon" type="image/png" href="data:image/png;base64,iVBORw0KGgo', page)

    def test_ago(self):
        # Relative time in the tables, absolute time in the tooltip
        for seconds, text in ((0, "0:00"), (-5, "0:00"), (10.7, "0:10"), (60, "1:00"), (488, "8:08"),
                              (3599, "59:59"), (3600, "1:00:00"), (27488, "7:38:08"), (86399, "23:59:59"),
                              (86400, "1:00:00:00"), (113888, "1:07:38:08"), (1234567, "14:06:56:07")):
            self.assertEqual(fmt_dhms(seconds), text, seconds)
        self.devices.connect({"id": DEV})
        page = self.client.get("/web/devices").data.decode()
        self.assertIn('<body data-now="', page)
        self.assertRegex(page, r'<span class="reltime" data-time="[0-9.]+" data-bs-toggle="tooltip" '
                               r'data-bs-title="\d{4}-\d\d-\d\d \d\d:\d\d:\d\d">\d+:\d\d</span>')

    def test_not_found(self):
        self.assertEqual(self.client.get("/laemplix/web").status_code, 404)
        self.assertEqual(self.client.get("/api/state/" + DEV).status_code, 404)

    def test_bad_device(self):
        for args in ({}, {"device": "../x"}, {"device": "a1b2c3d4"}):
            self.assertIsNone(device_info(args, Req(), "127.0.0.1"), args)

    def test_state_file(self):
        self.devices.set_state(DEV, {"projects": {"foo": {}}})
        with open(os.path.join(self.datadir, "laempli_" + DEV + ".json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["state"], {"projects": {"foo": {}}})
        self.assertEqual(data["info"]["id"], DEV)
        loaded = Devices(self.datadir)
        self.assertEqual(loaded.get_state(DEV)[0], {"projects": {"foo": {}}})
        self.assertEqual(self.client.get("/laempli/api/state/" + DEV).get_json(),
                         {"version": 1, "state": {"projects": {"foo": {}}}})

    def test_events(self):
        stream = open_stream(self.devices)
        event, info = next(stream)
        self.assertEqual(event, "hello")
        self.assertEqual(set(info), {"server", "time", "utc_offset", "heartbeat", "firmware"})
        self.assertIsNone(info["firmware"])
        self.assertEqual(next(stream), ("state", {"version": 0, "state": {"leds": []}}))
        self.assertEqual(self.devices.clients, 1)

        # Change wakes the stream
        self.devices.set_state(DEV, {"projects": {"bar": {}}})
        self.assertEqual(next(stream), ("state", {"version": 1, "state": {"projects": {"bar": {}}}}))
        stream.close()
        self.assertEqual(self.devices.clients, 0)

    def test_heartbeat(self):
        stream = open_stream(self.devices, heartbeat=0.1)
        next(stream)
        next(stream)
        event, data = next(stream)
        self.assertEqual(event, "time")
        self.assertEqual(set(data), {"time", "utc_offset"})
        stream.close()

    def write_fw(self, file, data):
        with open(os.path.join(self.fwdir, file), "wb") as fh:
            fh.write(data)

    def test_firmware(self):
        self.assertEqual(self.client.get("/api/firmware").get_json(), {})
        self.assertEqual(self.client.get("/api/firmware/laempli-chlotz_20261023010203-abc1234.bin").status_code, 404)
        image = b"\xe9" + bytes(range(256)) * 10
        self.write_fw("laempli-chlotz_20261023010203-abc1234.bin", image)
        self.write_fw("laempli-chlotz_20261022010203-abc1233-dirty.bin", b"old")
        self.write_fw("laempli-xyz_20261024010203-abc1239.bin", b"other")
        self.write_fw("firmware.bin", b"bad name")
        self.write_fw("laempli-chlotz_abc1235-20261025010203.bin", b"old name format")
        latest = self.client.get("/laempli/api/firmware").get_json()
        self.assertEqual(set(latest), {"laempli-chlotz", "laempli-xyz"})
        info = latest["laempli-chlotz"]
        self.assertEqual(info["version"], "20261023010203-abc1234")
        self.assertEqual((info["hash"], info["time"]), ("abc1234", "20261023010203"))
        self.assertEqual(info["file"], "laempli-chlotz_20261023010203-abc1234.bin")
        self.assertEqual(info["size"], len(image))
        self.assertEqual(info["md5"], hashlib.md5(image).hexdigest())
        res = self.client.get("/laempli/api/firmware/" + info["file"])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, image)
        res.close()
        self.assertEqual(self.client.get("/api/firmware/firmware.bin").status_code, 404)
        self.assertEqual(self.client.get("/api/firmware/..%2Fx.bin").status_code, 404)
        stream = open_stream(self.devices, firmware=Firmware(self.fwdir))
        self.assertEqual(next(stream)[1]["firmware"]["md5"], info["md5"])
        stream.close()
        self.assertIn("20261024010203-abc1239", self.client.get("/web/firmwares").data.decode())

    def test_firmware_update(self):
        # Old firmware: red, update button works only for a connected device
        url = "/api/device/{}/update".format(DEV)
        name = "laempli-chlotz_20261024010203-abc1239.bin"
        with open(os.path.join(self.fwdir, name), "wb") as fh:
            fh.write(b"image")
        self.assertEqual(self.client.post(url).status_code, 409)
        stream = open_stream(self.devices, "device={}&fw=20261024010203-abc1239".format(DEV),
                             firmware=Firmware(self.fwdir))
        self.assertEqual(next(stream)[0], "hello")
        self.assertEqual(next(stream)[0], "state")
        # Same version
        self.assertEqual(self.client.post(url).get_json()["error"], "no newer firmware")
        self.assertIn("Firmware is up to date", self.client.get("/web/device/" + DEV).data.decode())
        self.assertNotIn("text-danger", self.client.get("/web/devices").data.decode())
        stream.close()
        stream = open_stream(self.devices, "device={}&fw=20261001000000-0000000".format(DEV),
                             firmware=Firmware(self.fwdir))
        self.assertEqual(next(stream)[0], "hello")
        self.assertEqual(next(stream)[0], "state")
        self.assertIn('class="text-danger fw-bold"', self.client.get("/web/devices").data.decode())
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn('id="firmware_update">Update firmware</button>\n'
                      '                <span class="small">Available: 20261024010203-abc1239</span>', page)
        self.assertNotIn('id="firmware"', page)
        self.assertNotIn("up to date", page)
        self.assertEqual(self.client.post(url).get_json()["version"], "20261024010203-abc1239")
        event, data = next(stream)
        self.assertEqual((event, data["firmware"]["file"]), ("update", name))
        # Progress bar instead of the button, no second start
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn('aria-valuenow="0"', page)
        self.assertIn("Updating to\n                    20261024010203-abc1239", page)
        self.assertNotIn('id="firmware_update"', page)
        self.assertEqual(self.client.post(url).get_json()["error"], "update running")
        self.devices.set_ota(DEV, {"state": "running", "version": "20261024010203-abc1239", "done": 2, "size": 5})
        self.assertIn('style="width: 40%">40%</span>', self.client.get("/web/device/" + DEV).data.decode())
        self.assertEqual(self.client.get("/api/devices").get_json()[0]["ota"]["done"], 2)
        self.devices.set_ota(DEV, {"state": "done", "version": "20261024010203-abc1239", "done": 5, "size": 5})
        self.assertIn("Restarting with", self.client.get("/web/device/" + DEV).data.decode())
        # Failed: button again, with the error
        self.devices.set_ota(DEV, {"state": "failed", "version": "20261024010203-abc1239", "error": "timeout"})
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn('id="firmware_update">Update firmware</button>', page)
        self.assertIn("Update failed: timeout", page)
        self.assertEqual(self.client.post(url).get_json()["version"], "20261024010203-abc1239")
        stream.close()
        # Not connected: old firmware downloads, progress bar for some minutes
        self.assertEqual(self.client.post(url).get_json()["error"], "device not connected")
        self.assertIn('id="firmware_progress"', self.client.get("/web/device/" + DEV).data.decode())
        self.devices.devices[DEV]["ota"]["time"] -= 301
        self.assertIn('id="firmware_update" disabled>', self.client.get("/web/device/" + DEV).data.decode())

    def test_firmware_usb_only(self):
        # Device without OTA (option no_ota, e.g. Gitta): no button, no update
        url = "/api/device/{}/update".format(DEV)
        with open(os.path.join(self.fwdir, "laempli-chlotz_20261024010203-abc1239.bin"), "wb") as fh:
            fh.write(b"image")
        stream = open_stream(self.devices, "device={}&fw=20261001000000-0000000".format(DEV),
                             firmware=Firmware(self.fwdir))
        self.assertEqual(next(stream)[0], "hello")
        self.assertEqual(next(stream)[0], "state")
        self.devices.report(DEV, {"device": DEV, "rssi": -50, "options": {"no_ota": True, "led_test": "yes"}})
        self.assertEqual(self.devices.get_settings(DEV)["options"], {"no_ota": True})
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertNotIn('id="firmware_update"', page)
        self.assertIn("Firmware update only over USB", page)
        self.assertIn("Available: 20261024010203-abc1239", page)
        self.assertEqual(self.client.post(url).get_json()["error"], "device updates only over USB")
        self.assertEqual(self.client.post(url).status_code, 409)
        stream.close()

    def test_devices(self):
        self.assertEqual(self.client.get("/api/devices").get_json(), [])
        stream = open_stream(self.devices, "device=CHLOTZ-B2C3D4&fw=abc-123&ssid=My%20Net&ip=10.1.2.3&rssi=-55&leds=10",
                             headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"})
        next(stream)
        devs = self.client.get("/api/devices").get_json()
        self.assertEqual(len(devs), 1)
        self.assertEqual(devs[0]["id"], DEV)
        self.assertTrue(devs[0]["connected"])
        self.assertEqual(devs[0]["ssid"], "My Net")
        self.assertEqual(devs[0]["rssi"], -55)
        self.assertEqual(devs[0]["remote_ip"], "203.0.113.7")
        self.assertEqual((devs[0]["configured"], devs[0]["leds"]), (0, 10))
        page = self.client.get("/web/devices").data.decode()
        self.assertIn(DEV, page)
        self.assertIn("My Net", page)
        self.devices.report(DEV, {"device": DEV, "rssi": -60, "uptime": 90061, "heap": 150000})
        dev = self.client.get("/api/devices").get_json()[0]
        self.assertEqual((dev["rssi"], dev["uptime"], dev["heap"]), (-60, 90061, 150000))
        self.assertIn("1:01:01:01", self.client.get("/web/devices").data.decode())
        self.assertIn(">146 KiB</dd>", self.client.get("/web/device/" + DEV).data.decode())
        # First report: CPU and heap size; they stay with the next reports
        self.devices.report(DEV, {"device": DEV, "heap": 150000, "heap_size": 307200,
                                  "cpu": "ESP32-D0WD-V3 rev 3, 2 cores, 240 MHz"})
        self.devices.report(DEV, {"device": DEV, "heap": 151000, "cpu": "bad\ncpu"})
        page = self.client.get("/web/devices").data.decode()
        self.assertIn("<td>ESP32-D0WD-V3 rev 3, 2 cores, 240 MHz</td>", page)
        self.assertIn("147 of 300 KiB", page)
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn("<dt>CPU</dt><dd>ESP32-D0WD-V3 rev 3, 2 cores, 240 MHz</dd>", page)
        self.assertIn("<dt>Free heap</dt><dd>147 of 300 KiB</dd>", page)
        stream.close()
        self.assertFalse(self.client.get("/api/devices").get_json()[0]["connected"])

        # Device info is saved, not connected after load
        loaded = Devices(self.datadir).get()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["ssid"], "My Net")
        self.assertFalse(loaded[0]["connected"])

    def test_bad_file(self):
        with open(os.path.join(self.datadir, "laempli_chlotz-deadbe.json"), "w", encoding="utf-8") as fh:
            fh.write("{bad")
        with open(os.path.join(self.datadir, "deadbeef.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        with open(os.path.join(self.datadir, "other.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        with self.assertLogs("laempli.devices", "WARNING"):
            self.assertEqual(Devices(self.datadir).get(), [])

    def test_auth_user(self):
        class Req:
            headers = {"Authorization": "Basic " + base64.b64encode(b"chlotz:secret").decode()}
        self.assertEqual(auth_user(Req), "chlotz")


def source(project_id, pipeline_id, job_id):
    return {"project": {"id": project_id}, "pipeline_id": pipeline_id, "job_id": job_id}


def pipeline_event(pipeline_id, ref, status, builds, bridges=(), parent=None, project_id=42):
    ev = {"object_kind": "pipeline",
          "object_attributes": {"id": pipeline_id, "iid": 1, "name": "Commit", "ref": ref, "tag": False, "sha": "abc",
                                "status": status, "source": "push",
                                "url": "https://gl/g/foo/-/pipelines/{}".format(pipeline_id)},
          "project": {"id": project_id, "name": "Foo", "path_with_namespace": "g/p{}".format(project_id),
                      "web_url": "https://gl/g/foo"},
          "user": {"username": "flip"},
          "builds": [{"id": job_id, "name": name, "stage": "test", "status": job_status}
                     for job_id, name, job_status in builds],
          "bridges": [{"id": job_id, "name": name, "stage": "x", "status": "running", "bridge": True}
                      for job_id, name in bridges]}
    if parent is not None:
        ev["source_pipeline"] = parent
    return ev


def job_event(pipeline_id, ref, job_id, name, status, parent=None, commit_status="running"):
    ev = {"object_kind": "build", "ref": ref, "tag": False, "sha": "abc", "pipeline_id": pipeline_id,
          "build_id": job_id, "build_name": name, "build_stage": "test", "build_status": status,
          "project_id": 42, "project": {"id": 42, "path_with_namespace": "g/p42"}, "user": {"username": "flip"},
          "commit": {"id": pipeline_id, "status": commit_status}}
    if parent is not None:
        ev["source_pipeline"] = parent
    return ev


# Signing token and its key
KEY = b"0123456789abcdef0123456789abcdef"
TOKEN = "whsec_" + base64.b64encode(KEY).decode()
OTHER_TOKEN = "whsec_" + base64.b64encode(b"other key").decode()
PIPELINE = {"X-Gitlab-Event": "Pipeline Hook"}

# Housekeeping config
CONFIG = {"housekeeping_interval": 3600, "firmware_keep": 10, "devices_keep_days": 365}


class GitlabRetryTest(unittest.TestCase):
    CREATED = 1800000000

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.gitlab = Gitlab(self.tmpdir.name, [])

    def tearDown(self):
        self.tmpdir.cleanup()

    def pipeline(self, age, pipeline_id, builds, bridges=(), parent=None):
        event = pipeline_event(pipeline_id, "main", "running", builds, bridges, parent)
        event["object_attributes"]["created_at"] = "2027-01-15 08:00:00 UTC"
        self.gitlab.handle("Pipeline Hook", event, now=(self.CREATED + age), raw=False)

    def job(self, age, pipeline_id, job_id, name, status, parent=None):
        self.gitlab.handle("Job Hook", job_event(pipeline_id, "main", job_id, name, status, parent),
                           now=(self.CREATED + age), raw=False)

    def started(self, *names):
        path = ["gitlab", "g/p42", "main"] + list(names)
        return [entry for entry in self.gitlab.jobs() if (entry["path"] == path)][0]["started"] - self.CREATED

    def test_retry_moves(self):
        self.pipeline(10, 100, [(1, "build", "running"), (2, "test", "running")])
        self.assertEqual(self.started(), 0)
        # Normal events do not move the pipeline
        self.job(20, 100, 1, "build", "success")
        self.job(30, 100, 3, "deploy", "manual")
        self.job(40, 100, 3, "deploy", "running")
        self.assertEqual(self.started(), 0)
        # Late event of the old job: no retry
        self.job(50, 100, 2, "test", "failed")
        self.assertEqual(self.started(), 0)
        # Job event of a retry
        self.job(60, 100, 4, "test", "pending")
        self.assertEqual(self.started(), 60)
        self.job(70, 100, 4, "test", "running")
        self.assertEqual(self.started(), 60)
        # Older ID after the retry: no change
        self.job(80, 100, 2, "test", "failed")
        self.assertEqual(self.started(), 60)
        # Retry seen first in the pipeline event
        self.pipeline(90, 100, [(5, "test", "pending")])
        self.assertEqual(self.started(), 90)

    def test_retry_downstream(self):
        parent = source(42, 100, 300)
        self.pipeline(10, 100, [], [(300, "yocto")])
        self.pipeline(20, 101, [(1, "image", "failed")], parent=parent)
        self.assertEqual(self.started(), 0)
        # Job in the child: moves the child and the parent
        self.job(30, 101, 2, "image", "pending", parent)
        self.assertEqual(self.started("yocto"), 30)
        self.assertEqual(self.started(), 30)
        # Retry of the trigger job
        self.pipeline(40, 100, [], [(301, "yocto")])
        self.assertEqual(self.started(), 40)

    def test_retry_replay(self):
        self.gitlab.handle("Pipeline Hook", pipeline_event(100, "main", "running", [(1, "test", "failed")]),
                           now=(self.CREATED + 10))
        self.gitlab.handle("Job Hook", job_event(100, "main", 2, "test", "pending"), now=(self.CREATED + 20))
        gitlab = Gitlab(self.tmpdir.name, [])
        self.assertEqual(gitlab.load(), 2)
        self.gitlab = gitlab
        self.assertEqual(self.started(), 20)


class GitlabMergeRequestTest(unittest.TestCase):
    """Merge request pipelines: job events have the MR ref, the pipeline event the source branch"""

    MR = "refs/merge-requests/231/head"

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.gitlab = Gitlab(self.tmpdir.name, [])

    def tearDown(self):
        self.tmpdir.cleanup()

    def handle(self, event):
        self.gitlab.handle("Hook", event, raw=False)

    def paths(self):
        return sorted(entry["path"] for entry in self.gitlab.jobs())

    def result(self, *path):
        return [entry for entry in self.gitlab.jobs() if (entry["path"] == list(path))][0]["result"]

    def test_job_first(self):
        self.handle(job_event(100, self.MR, 1, "validate", "pending"))
        self.assertEqual(self.paths(), [["gitlab", "g/p42", self.MR], ["gitlab", "g/p42", self.MR, "validate"]])
        self.handle(pipeline_event(100, "feature/x", "success", [(1, "validate", "success")]))
        self.assertEqual(self.paths(), [["gitlab", "g/p42", "feature/x"], ["gitlab", "g/p42", "feature/x", "validate"]])
        self.assertEqual(self.result("gitlab", "g/p42", "feature/x"), "PASS")
        # Late job event: stays on the source branch
        self.handle(job_event(100, self.MR, 1, "validate", "success"))
        self.assertEqual(self.paths(), [["gitlab", "g/p42", "feature/x"], ["gitlab", "g/p42", "feature/x", "validate"]])

    def test_pipeline_first(self):
        self.handle(pipeline_event(100, "feature/x", "running", [(1, "validate", "running")]))
        self.handle(job_event(100, self.MR, 1, "validate", "success"))
        self.handle(pipeline_event(100, "feature/x", "success", [(1, "validate", "success")]))
        self.assertEqual(self.paths(), [["gitlab", "g/p42", "feature/x"], ["gitlab", "g/p42", "feature/x", "validate"]])
        self.assertEqual(self.result("gitlab", "g/p42", "feature/x", "validate"), "PASS")

    def test_branch_kept(self):
        # Push pipeline of the branch, then a MR pipeline with a job event first
        self.handle(pipeline_event(90, "feature/x", "failed", [(1, "validate", "failed")]))
        self.handle(job_event(100, self.MR, 2, "validate", "running"))
        self.handle(pipeline_event(100, "feature/x", "success", [(2, "validate", "success")]))
        self.assertEqual(self.paths(), [["gitlab", "g/p42", "feature/x"], ["gitlab", "g/p42", "feature/x", "validate"]])
        self.assertEqual(self.gitlab.get()["projects"]["42"]["refs"]["feature/x"]["main"]["pipeline"], 100)
        self.assertEqual(self.result("gitlab", "g/p42", "feature/x"), "PASS")

    def test_downstream(self):
        parent = source(42, 100, 300)
        self.handle(pipeline_event(100, "feature/x", "running", [], [(300, "yocto")]))
        self.handle(job_event(101, self.MR, 1, "image", "running", parent))
        self.handle(pipeline_event(101, "feature/x", "success", [(1, "image", "success")], parent=parent))
        self.assertEqual(self.paths(), [["gitlab", "g/p42", "feature/x"], ["gitlab", "g/p42", "feature/x", "yocto"],
                                        ["gitlab", "g/p42", "feature/x", "yocto", "image"]])


class GitlabManualTest(unittest.TestCase):
    """Pipelines that wait for manual jobs: WAITING"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.gitlab = Gitlab(self.tmpdir.name, [])

    def tearDown(self):
        self.tmpdir.cleanup()

    def handle(self, event):
        self.gitlab.handle("Hook", event, raw=False)

    def entry(self, *names):
        path = ["gitlab", "g/p42", "main"] + list(names)
        return [entry for entry in self.gitlab.jobs() if (entry["path"] == path)][0]

    def state(self, *names):
        entry = self.entry(*names)
        return entry["status"]

    def test_main_pipeline(self):
        self.handle(pipeline_event(100, "main", "running", [(1, "build", "running"), (2, "deploy", "manual")]))
        self.assertEqual(self.state(), "RUNNING")
        self.assertEqual(self.state("deploy"), "WAITING")
        # Later stage stays created: not active
        self.handle(pipeline_event(100, "main", "running", [(1, "build", "success"), (2, "deploy", "manual"),
                                                            (3, "test", "created")]))
        self.assertEqual(self.state(), "WAITING")
        self.assertEqual(self.state("deploy"), "WAITING")
        self.assertEqual(self.state("build"), "STOPPED")
        self.handle(job_event(100, "main", 2, "deploy", "pending"))
        self.assertEqual(self.state(), "RUNNING")

    def test_pipeline_status_manual(self):
        self.handle(pipeline_event(100, "main", "manual", [(1, "build", "success"), (2, "deploy", "manual")]))
        self.assertEqual(self.state(), "WAITING")

    def test_downstream(self):
        # As core/release-config: parent running, child waits for manual jobs
        parent = source(42, 100, 300)
        self.handle(pipeline_event(100, "main", "running", [(1, "validate", "success")], [(300, "yocto")]))
        self.assertEqual(self.state(), "RUNNING")  # bridge running, child not known yet
        self.handle(pipeline_event(101, "main", "running", [(2, "image-a", "running"), (3, "image-b", "manual")],
                                   parent=parent))
        self.assertEqual(self.state(), "RUNNING")
        self.assertEqual(self.state("yocto"), "RUNNING")
        self.handle(job_event(101, "main", 2, "image-a", "success", parent))
        self.assertEqual(self.state("yocto"), "WAITING")
        self.assertEqual(self.state(), "WAITING")
        # Child status manual
        self.handle(pipeline_event(101, "main", "manual", [(2, "image-a", "success"), (3, "image-b", "manual")],
                                   parent=parent))
        self.assertEqual(self.state(), "WAITING")
        # Someone starts the manual job
        self.handle(job_event(101, "main", 3, "image-b", "pending", parent))
        self.assertEqual(self.state(), "RUNNING")

    def test_optional_manual(self):
        # Finished pipeline with an optional manual job: job waits
        self.handle(pipeline_event(100, "main", "success", [(1, "build", "success"), (2, "deploy", "manual")]))
        self.assertEqual(self.state(), "STOPPED")
        self.assertEqual(self.state("deploy"), "WAITING")

    def test_no_manual(self):
        # Nothing active, nothing manual (events lost): stays running
        self.handle(pipeline_event(100, "main", "running", [(1, "build", "success")]))
        self.assertEqual(self.state(), "RUNNING")


def push_event(ref, deleted=True, project_id=42, kind="push"):
    return {"object_kind": kind, "ref": ref, "before": "abc", "after": (NO_COMMIT if deleted else "def"),
            "project_id": project_id, "project": {"id": project_id, "path_with_namespace": "g/p{}".format(project_id)},
            "user_username": "flip", "commits": [], "total_commits_count": 0}


class GitlabDeleteTest(unittest.TestCase):
    """Deleted branches and tags, removal by hand"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.gitlab = Gitlab(self.tmpdir.name, [])

    def tearDown(self):
        self.tmpdir.cleanup()

    def handle(self, event, now=None, raw=True):
        hook = {"push": "Push Hook", "tag_push": "Tag Push Hook", "pipeline": "Pipeline Hook"}.get(
            event.get("object_kind"), "Job Hook")
        return self.gitlab.handle(hook, event, now=now, raw=raw)

    def refs(self):
        return sorted(self.gitlab.get()["projects"]["42"]["refs"])

    def setup_refs(self):
        parent = source(42, 100, 300)
        self.handle(pipeline_event(90, "main", "success", [(1, "build", "success")]))
        self.handle(pipeline_event(100, "feature/x", "running", [(2, "build", "running")], [(300, "yocto")]))
        self.handle(pipeline_event(101, "feature/x", "running", [(3, "image", "running")], parent=parent))

    def test_branch_deleted(self):
        self.setup_refs()
        self.assertEqual(self.refs(), ["feature/x", "main"])
        self.assertIn("slots removed", self.handle(push_event("refs/heads/feature/x")))
        self.assertEqual(self.refs(), ["main"])
        self.assertEqual([entry["path"][2] for entry in self.gitlab.jobs()], ["main", "main"])
        log = self.gitlab.events()[0]
        self.assertEqual((log["kind"], log["ref"], log["user"]), ("deleted", "feature/x", "flip"))
        # Late events of the running pipelines: ignored
        self.assertIn("ignored", self.handle(job_event(100, "feature/x", 2, "build", "success")))
        self.assertIn("ignored", self.handle(pipeline_event(100, "feature/x", "success", [(2, "build", "success")])))
        self.assertIn("ignored", self.handle(pipeline_event(102, "feature/x", "running", [(4, "image", "running")],
                                                            parent=source(42, 100, 300))))
        self.assertEqual(self.refs(), ["main"])
        # New pipeline: back
        self.handle(pipeline_event(110, "feature/x", "running", [(5, "build", "running")]))
        self.assertEqual(self.refs(), ["feature/x", "main"])
        self.assertNotIn("deleted", self.gitlab.get()["projects"]["42"])

    def test_replay(self):
        self.setup_refs()
        self.handle(push_event("refs/heads/feature/x"))
        self.handle(job_event(100, "feature/x", 2, "build", "success"))
        gitlab = Gitlab(self.tmpdir.name, [])
        self.assertEqual(gitlab.load(), 5)
        self.assertEqual(sorted(gitlab.get()["projects"]["42"]["refs"]), ["main"])

    def test_tag_deleted(self):
        self.handle(pipeline_event(100, "v1.0", "success", [(1, "build", "success")]))
        self.handle(push_event("refs/tags/v1.0", kind="tag_push"))
        self.assertEqual(self.refs(), [])

    def test_other_pushes(self):
        self.setup_refs()
        self.assertIsNone(self.handle(push_event("refs/heads/feature/x", deleted=False)))
        self.assertEqual(self.refs(), ["feature/x", "main"])
        self.assertEqual(len(self.gitlab.raw_events()), 3)
        # Unknown project, bad ref
        self.assertIn("unknown project", self.handle(push_event("refs/heads/x", project_id=7)))
        with self.assertRaises(EventError):
            self.handle(push_event("refs/remotes/x"))

    def test_remove(self):
        self.setup_refs()
        self.assertFalse(self.gitlab.remove("g/p42", "nope", "editor"))
        self.assertFalse(self.gitlab.remove("g/other", "main", "editor"))
        self.assertTrue(self.gitlab.remove("g/p42", "feature/x", "editor"))
        self.assertEqual(self.refs(), ["main"])
        self.assertEqual(self.gitlab.events()[0]["kind"], "removed")
        gitlab = Gitlab(self.tmpdir.name, [])
        gitlab.load()
        self.assertEqual(sorted(gitlab.get()["projects"]["42"]["refs"]), ["main"])

    def test_expire_marker(self):
        now = time.time()
        self.handle(pipeline_event(100, "feature/x", "success", [(1, "build", "success")]), now=now)
        self.handle(push_event("refs/heads/feature/x"), now=now)
        # Project stays while it has the marker
        self.gitlab.expire_jobs(now + 60)
        self.assertIn("feature/x", self.gitlab.get()["projects"]["42"]["deleted"])
        self.gitlab.expire_jobs(now + (KEEP_DAYS * 86400) + 60)
        self.assertEqual(self.gitlab.get()["projects"], {})


class GitlabTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.datadir = self.tmpdir.name
        self.make_client([OTHER_TOKEN, TOKEN])

    def make_client(self, token):
        self.gitlab = Gitlab(self.datadir, token)
        self.jobs = Jobs()
        self.devices = Devices(self.datadir)
        self.client = create_app(self.devices, None, 15, self.gitlab, self.jobs).test_client()

    def tearDown(self):
        self.tmpdir.cleanup()

    def post(self, data, event="Pipeline Hook", key=KEY, age=0, body=None):
        body = (json.dumps(data).encode() if body is None else body)
        stamp = str(int(time.time()) - age)
        sig = base64.b64encode(hmac.new(key, b"msg-1." + stamp.encode() + b"." + body, hashlib.sha256).digest())
        headers = {"X-Gitlab-Event": event, "webhook-id": "msg-1", "webhook-timestamp": stamp,
                   "webhook-signature": "v1,xxxx v1," + sig.decode(), "Content-Type": "application/json"}
        return self.client.post("/api/gitlab", data=body, headers=headers)

    def pipelines(self):
        return self.gitlab.get()["projects"]["42"]["pipelines"]

    def refs(self, ref, project="42"):
        # Newest pipeline of the slots of a ref
        slots = self.gitlab.get()["projects"][project]["refs"][ref]
        result = {"downstream": {key: slot["pipeline"] for key, slot in slots["downstream"].items()}}
        if "main" in slots:
            result["main"] = slots["main"]["pipeline"]
        return result

    def led(self, *names, ref="main"):
        # Result, GitLab status and status of a job
        job = self.jobs.get(["gitlab", "g/p42", ref] + list(names))
        return (job["result"], job["info"].get("status"), job["status"])

    def raw_lines(self):
        # Raw events in the store
        return self.gitlab.raw_events()

    def reloaded(self):
        # Gitlab made again from the raw events
        gitlab = Gitlab(self.datadir)
        gitlab.load()
        return gitlab

    def test_signature(self):
        ev = pipeline_event(100, "main", "running", [])
        self.assertEqual(self.post(ev, key=b"bad").status_code, 401)
        self.assertEqual(self.post(ev, age=600).status_code, 401)
        self.assertEqual(self.client.post("/api/gitlab", json=ev, headers=PIPELINE).status_code, 401)
        self.assertEqual(self.gitlab.get()["projects"], {})
        self.assertEqual(self.post(ev, age=60).status_code, 200)
        self.make_client([TOKEN])
        self.assertEqual(self.post(ev, age=60).status_code, 200)
        self.make_client([OTHER_TOKEN])
        self.assertEqual(self.post(ev).status_code, 401)
        self.make_client([])
        self.assertEqual(self.client.post("/api/gitlab", json=ev, headers=PIPELINE).status_code, 200)
        for token in ("abc", "whsec_", "whsec_!!"):
            with self.assertRaises(ValueError, msg=token):
                Gitlab(self.datadir, [token])

    def test_bad_events(self):
        self.assertEqual(self.post(None, body=b"{bad").status_code, 400)
        self.assertEqual(self.post({"object_kind": "pipeline"}).status_code, 400)
        self.assertEqual(self.post({"object_kind": "push"}).status_code, 200)
        self.assertEqual(self.gitlab.get()["projects"], {})

    def test_other_events(self):
        # Ignored at once: no signature check, nothing stored
        for event in ("Merge Request Hook", "Note Hook", "Laempli Remove", ""):
            res = self.client.post("/api/gitlab", json=pipeline_event(100, "main", "running", []),
                                   headers={"X-Gitlab-Event": event})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.get_json(), {"ok": True, "ignored": True})
        self.assertEqual(self.gitlab.get()["projects"], {})
        self.assertEqual(self.raw_lines(), [])
        self.assertEqual(self.gitlab.events(), [])

    def test_pipeline_jobs(self):
        self.assertEqual(self.post(pipeline_event(100, "main", "running", [(1, "build", "running")])).status_code,
                         200)
        self.post(pipeline_event(100, "main", "running", [(1, "build", "success"), (2, "test", "pending")]))
        # Late "running" does not undo a finished job
        self.post(pipeline_event(100, "main", "running", [(1, "build", "running"), (2, "test", "pending")]))
        self.post(pipeline_event(101, "dev", "failed", [(5, "build", "failed")]))
        data = self.client.get("/api/gitlab").get_json()
        proj = data["projects"]["42"]
        self.assertEqual((proj["path"], proj["url"]), ("g/p42", "https://gl/g/foo"))
        self.assertEqual(self.refs("main"), {"main": 100, "downstream": {}})
        main = proj["pipelines"]["100"]
        self.assertEqual((main["id"], main["status"], main["user"], main["name"]), (100, "running", "flip", "Commit"))
        self.assertEqual({key: job["status"] for key, job in main["jobs"].items()}, {"1": "success", "2": "pending"})
        self.assertEqual(main["jobs"]["2"]["name"], "test")
        self.assertEqual(proj["pipelines"][str(self.refs("dev")["main"])]["status"], "failed")

        # Newer pipeline replaces, the older one stays for late events
        self.post(pipeline_event(110, "main", "pending", []))
        self.post(pipeline_event(100, "main", "success", []))
        proj = self.gitlab.get()["projects"]["42"]
        self.assertEqual(self.refs("main")["main"], 110)
        self.assertEqual((proj["pipelines"]["110"]["status"], proj["pipelines"]["110"]["jobs"]), ("pending", {}))
        self.assertEqual(proj["pipelines"]["100"]["status"], "success")

        # Raw events stored, the same data again from them, no other files
        self.assertEqual(len(self.raw_lines()), 6)
        self.assertEqual(self.raw_lines()[1]["event"], "Pipeline Hook")
        self.assertEqual(self.reloaded().get(), self.gitlab.get())
        self.assertEqual([name for name in os.listdir(self.datadir) if name.startswith("gitlab")], [])

        page = self.client.get("/web/jobs").data.decode()
        self.assertIn("gitlab → g/p42 → main → build", page)
        self.assertIn("gitlab → g/p42 → dev", page)

        # Old replaced pipeline goes after PRUNE_AGE, events for it are then ignored
        self.gitlab.handle("Pipeline Hook", pipeline_event(110, "main", "running", []), time.time() + 4000, raw=False)
        self.assertEqual(sorted(self.pipelines()), ["101", "110"])
        self.gitlab.handle("Pipeline Hook", pipeline_event(100, "main", "failed", []), raw=False)
        self.assertEqual(sorted(self.pipelines()), ["101", "110"])
        self.assertTrue(self.gitlab.events()[0]["ignored"])

    def test_downstream(self):
        # Parent with trigger job (bridge) 300, child pipeline 201
        self.post(pipeline_event(200, "main", "running", [(1, "check", "success")], bridges=[(300, "yocto")]))
        self.post(pipeline_event(201, "main", "running", [(2, "image", "running")], parent=source(42, 200, 300)))
        proj = self.gitlab.get()["projects"]["42"]
        self.assertEqual(self.refs("main"), {"main": 200, "downstream": {"yocto": 201}})
        child = proj["pipelines"]["201"]
        self.assertEqual((child["trigger"], child["parent"]), ("yocto", {"project": 42, "pipeline": 200, "job": 300}))
        self.assertEqual(child["jobs"]["2"]["status"], "running")
        self.assertEqual(proj["pipelines"]["200"]["bridges"]["300"]["name"], "yocto")

        # Parent events do not change the child, and the other way round
        self.post(pipeline_event(200, "main", "success", []))
        self.post(pipeline_event(201, "main", "failed", [], parent=source(42, 200, 300)))
        self.assertEqual((self.pipelines()["200"]["status"], self.pipelines()["201"]["status"]), ("success", "failed"))

        # New parent: child slot keeps the old child until the new one starts
        self.post(pipeline_event(210, "main", "running", [], bridges=[(310, "yocto")]))
        self.assertEqual(self.refs("main"), {"main": 210, "downstream": {"yocto": 201}})
        self.post(pipeline_event(211, "main", "pending", [], parent=source(42, 210, 310)))
        self.assertEqual(self.refs("main"), {"main": 210, "downstream": {"yocto": 211}})

        # Unknown parent, other project (multi-project pipeline)
        self.post(pipeline_event(220, "dev", "running", [], parent=source(42, 999, 998)))
        self.post(pipeline_event(230, "main", "running", [], parent=source(42, 210, 310), project_id=43))
        self.assertEqual(self.refs("dev"), {"downstream": {"downstream": 220}})
        self.assertEqual(self.refs("main", "43"), {"downstream": {"g/p42:yocto": 230}})
        paths = [tuple(job["path"][1:]) for job in self.gitlab.jobs()]
        self.assertEqual(sorted(paths), [("g/p42", "dev", "downstream"), ("g/p42", "main"), ("g/p42", "main", "check"),
                                         ("g/p42", "main", "yocto"), ("g/p43", "main", "g/p42:yocto")])

    def test_event_log(self):
        self.post(pipeline_event(200, "main", "running", [(1, "a", "success"), (2, "b", "failed")],
                                 bridges=[(300, "yocto")]))
        self.post(pipeline_event(201, "main", "pending", [(3, "c", "pending")], parent=source(42, 200, 300)))
        res = self.client.get("/api/gitlab/log").get_json()
        self.assertEqual((res["total"], res["filtered"]), (2, 2))
        events = res["events"]
        self.assertEqual(len(events), 2)
        self.assertEqual((events[0]["pipeline"], events[0]["trigger"], events[0]["status"]), (201, "yocto", "pending"))
        self.assertEqual((events[1]["pipeline"], events[1]["trigger"]), (200, None))
        self.assertEqual((events[1]["jobs"], events[1]["jobs_ok"], events[1]["jobs_failed"]), (2, 1, 1))
        self.assertEqual((events[1]["project"], events[1]["user"], events[1]["source"]), ("g/p42", "flip", "push"))
        page = self.client.get("/web/gitlab").data.decode()
        self.assertIn("2\n                events in the last 90 days", page)
        self.assertIn('data-server="/web/gitlab/rows"', page)
        rows = self.client.get("/web/gitlab/rows?start=0&length=10").get_json()
        self.assertEqual((rows["total"], rows["filtered"], len(rows["rows"])), (2, 2, 2))
        self.assertIn("1/1/2", rows["rows"][1])
        self.assertIn("yocto", rows["rows"][0])

        # Made again from the raw events, all entries
        self.assertEqual(self.reloaded().events(), events)
        for ix in range(30):
            self.gitlab.handle("Pipeline Hook", pipeline_event(1000 + ix, "x" if (ix % 3) else "y_1", "running", []),
                               now=(time.time() + ix))
        self.assertEqual(len(self.reloaded().events()), 32)

        # Pages, search (all words, quoted, LIKE characters), sort, time range
        res = self.client.get("/api/gitlab/log?start=5&length=10").get_json()
        self.assertEqual((res["total"], res["filtered"]), (32, 32))
        self.assertEqual([ev["pipeline"] for ev in res["events"]], list(range(1024, 1014, -1)))
        res = self.client.get("/api/gitlab/log?q=y_1&length=1000").get_json()
        self.assertEqual((res["total"], res["filtered"]), (32, 10))
        self.assertEqual(self.client.get("/api/gitlab/log?q=y%251").get_json()["filtered"], 0)
        self.assertEqual(self.client.get("/api/gitlab/log?q=%22main g/p42%22").get_json()["filtered"], 0)
        self.assertEqual(self.client.get("/api/gitlab/log?q=g/p42 main").get_json()["filtered"], 2)
        self.assertEqual(self.client.get("/api/gitlab/log?q=G/P42 1001").get_json()["filtered"], 1)
        res = self.client.get("/api/gitlab/log?sort=5.asc&length=3").get_json()
        self.assertEqual([ev["pipeline"] for ev in res["events"]], [200, 201, 1000])
        res = self.client.get("/api/gitlab/log?sort=ref.desc,0.asc&length=2").get_json()
        self.assertEqual([ev["pipeline"] for ev in res["events"]], [1000, 1003])
        since = events[0]["time"] + 20.5
        res = self.client.get("/api/gitlab/log?since={}&length=1000".format(since)).get_json()
        self.assertEqual((res["filtered"], res["events"][-1]["pipeline"]), (9, 1021))
        for query in ("length=1001", "start=-1", "length=x", "sort=99.asc", "sort=0.up", "sort=url", "since=x"):
            self.assertEqual(self.client.get("/api/gitlab/log?" + query).status_code, 400, query)
            self.assertEqual(self.client.get("/web/gitlab/rows?" + query).status_code, 400, query)

    def test_expire_events(self):
        # Old raw event file: imported once
        raw = os.path.join(self.datadir, "gitlab_raw.jsonl")
        now = time.time()
        self.assertEqual(self.gitlab.expire_events(), 0)
        with open(raw, "w", encoding="utf-8") as fh:
            fh.write("{bad\n")
            for age, pipeline_id, status in ((100, 100, "failed"), (91, 101, "failed"), (89, 102, "running"),
                                             (10, 102, "success")):
                fh.write(json.dumps({"time": now - (age * 86400), "event": "Pipeline Hook",
                                     "data": pipeline_event(pipeline_id, "main", status, [])}) + "\n")
        with self.assertLogs("laempli.gitlab", "INFO") as logs:
            gitlab = Gitlab(self.datadir)
            self.assertEqual(gitlab.load(), 2)
        output = "\n".join(logs.output)
        self.assertIn("gitlab_raw.jsonl line 1 skipped", output)
        self.assertIn("Imported 4 raw GitLab events", output)
        self.assertIn("Removed 2 old raw GitLab events", output)
        self.assertEqual([line["data"]["object_attributes"]["id"] for line in self.raw_lines()], [102, 102])
        self.assertEqual(sorted(gitlab.get()["projects"]["42"]["pipelines"]), ["102"])
        self.assertEqual((os.path.exists(raw), os.path.exists(raw + ".imported")), (False, True))
        # A raw file with events in the store: not imported
        with open(raw, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"time": now, "event": "Pipeline Hook",
                                 "data": pipeline_event(104, "main", "running", [])}) + "\n")
        with self.assertLogs("laempli.gitlab", "WARNING"):
            self.assertEqual(Gitlab(self.datadir).load(), 2)
        os.unlink(raw)

        # Nothing expired
        self.assertEqual(gitlab.expire_events(), 0)
        # New events are added at the end, all expired later, the log too
        self.post(pipeline_event(103, "main", "running", []))
        self.assertEqual(len(self.raw_lines()), 3)
        self.assertEqual(len(gitlab.events()), 3)
        self.assertEqual(gitlab.expire_events(now + (100 * 86400)), 3)
        self.assertEqual(self.raw_lines(), [])
        self.assertEqual(gitlab.events(), [])

    def test_plots(self):
        job = "Job Hook"
        self.post(pipeline_event(200, "main", "running", []))
        self.post(pipeline_event(200, "main", "running", []))
        self.post(job_event(200, "main", 1, "a", "running"), event=job)
        self.post(job_event(200, "main", 1, "a", "failed"), event=job)
        self.post(pipeline_event(200, "main", "failed", []))
        self.post(pipeline_event(210, "dev", "success", []))
        res = self.client.get("/api/plots").get_json()
        self.assertEqual(res["states"][:4], ["running", "waiting", "success", "failed"])
        names = {name: ix for ix, name in enumerate(res["states"])}
        # One series per job, sorted by path, a point per change (same state again: no point)
        self.assertEqual([(item["path"], item["kind"]) for item in res["series"]],
                         [(["gitlab", "g/p42", "dev"], "pipeline"), (["gitlab", "g/p42", "main"], "pipeline"),
                          (["gitlab", "g/p42", "main", "a"], "job")])
        self.assertEqual([[point[1] for point in item["points"]] for item in res["series"]],
                         [[names["success"]], [names["running"], names["failed"]],
                          [names["running"], names["failed"]]])
        self.assertEqual((res["points"], res["truncated"]), (5, False))
        # The cause of a point: its event log entry
        entry = self.client.get("/api/log/{}".format(res["series"][1]["points"][1][2])).get_json()
        self.assertEqual((entry["source"], entry["entry"]["pipeline"], entry["entry"]["status"]),
                         ("gitlab", 200, "failed"))
        self.assertEqual(self.client.get("/api/log/999999").status_code, 404)
        # Filters
        for query, count in (("kind=pipelines", 2), ("kind=jobs", 1), ("q=dev", 1), ("q=%22a main%22", 0), ("q=%22main a%22", 1), ("q=g/p42 dev", 1),
                             ("failed=1", 2), ("src=gitlab", 3),
                             ("since={}&until={}".format(time.time() + 10, time.time() + 20), 0)):
            self.assertEqual(len(self.client.get("/api/plots?" + query).get_json()["series"]), count, query)
        for query in ("kind=x", "since=x", "src=github", "since=10&until=5"):
            self.assertEqual(self.client.get("/api/plots?" + query).status_code, 400, query)
        # Ignored event (old pipeline): no point
        self.gitlab.handle("Pipeline Hook", pipeline_event(220, "main", "running", []), time.time() + 4000, raw=False)
        self.post(pipeline_event(200, "main", "canceled", []))
        main = self.client.get("/api/plots?kind=pipelines&q=main").get_json()["series"][0]
        self.assertEqual(len(main["points"]), 2)
        query = "/api/plots?kind=pipelines&q=main&until={}".format(time.time() + 5000)
        main = self.client.get(query).get_json()["series"][0]
        self.assertEqual([point[1] for point in main["points"]], [names["running"], names["failed"], names["running"]])
        # Pages: Jobs and Plots tabs
        page = self.client.get("/web/plots").data.decode()
        self.assertIn('data-view="plots"', page)
        self.assertIn('data-keep-days="90"', page)
        self.assertIn('href="/web/jobs">Jobs</a>', page)
        self.assertIn('href="/api/plots"', page)
        self.assertIn('id="plot_panel"', page)
        self.assertIn('id="plot_scroll" tabindex="0"', page)
        self.assertIn('data-act="all"', page)
        self.assertRegex(page, r'src="/static/plots\.js\?v=\w+"></script>\s*<script src="/static/laempli\.js')
        self.assertIn('aria-current="page"\n                   href="/web/plots">Plots</a>', page)
        page = self.client.get("/web/jobs").data.decode()
        self.assertIn('href="/web/plots">Plots</a>', page)
        # Jobs table: filters, rows with result and status
        self.assertIn('data-filters="jobs"', page)
        self.assertRegex(page, r'data-filter="result" data-mode="many"\s+data-value="FAIL">FAIL<')
        self.assertRegex(page, r'data-filter="status" data-mode="many"\s+data-value="RUNNING">RUNNING<')
        self.assertIn('<tr data-result="FAIL" data-status="STOPPED">', page)

    def test_ignored_projects(self):
        self.gitlab.ignored = ["g/p4[3-9]"]
        self.post(pipeline_event(100, "main", "running", [(1, "build", "running")]))
        self.post(pipeline_event(200, "main", "failed", [(2, "build", "failed")], project_id=43))
        ev = job_event(200, "main", 3, "test", "failed")
        ev.update(project_id=43, project={"id": 43, "path_with_namespace": "g/p43"})
        self.post(ev, event="Job Hook")
        # Stored, logged as ignored, no data, no jobs
        self.assertEqual(len(self.raw_lines()), 3)
        self.assertEqual(sorted(self.gitlab.get()["projects"]), ["42"])
        self.assertEqual({job["path"][1] for job in self.gitlab.jobs()}, {"g/p42"})
        log = self.gitlab.events()
        self.assertEqual((log[0]["kind"], log[0]["project"], log[0]["job"], log[0]["status"], log[0]["reason"],
                          log[0]["ignored"]), ("job", "g/p43", "test", "failed", "project ignored", True))
        self.assertEqual((log[1]["pipeline"], log[1]["ref"], log[1]["status"], log[1]["reason"]),
                         (200, "main", "failed", "project ignored"))
        # Event log API: ignored entries hidden by default, kind and state filters
        for query, filtered in (("", 1), ("ignored=1", 3), ("kind=jobs&ignored=1", 1), ("kind=pipelines", 1),
                                ("kind=pipelines&ignored=1", 2), ("state=failed", 0), ("state=failed&ignored=1", 2),
                                ("state=running", 1), ("state=running,failed&ignored=1", 3), ("q=g/p43", 0)):
            res = self.client.get("/api/gitlab/log?" + query).get_json()
            self.assertEqual((res["total"], res["filtered"]), (3, filtered), query)
        for query in ("kind=runs", "state=x", "state=failed,bad"):
            self.assertEqual(self.client.get("/api/gitlab/log?" + query).status_code, 400, query)
        rows = "".join(self.client.get("/web/gitlab/rows?ignored=1").get_json()["rows"])
        self.assertIn("(project ignored)", rows)
        # Pages: filters, ignored counts
        page = self.client.get("/web/gitlab").data.decode()
        self.assertIn('data-filters="gitlab_log"', page)
        self.assertRegex(page, r'data-filter="kind" data-mode="one"\s+data-value="pipelines">Pipelines<')
        self.assertRegex(page, r'data-filter="state" data-mode="many"\s+data-value="failed">failed<')
        self.assertRegex(page, r'data-filter="ignored" data-mode="toggle"\s+data-value="1"')
        self.assertIn("in the last 90 days, 2 ignored, newest first", page)
        self.assertRegex(page, r'ignored projects\)">Ignored\s*</button>')
        self.assertRegex(self.client.get("/web/sources").data.decode(), r'<td class="text-end">3</td>\s*'
                         r'<td class="text-end">2</td>')
        # No plot points
        plots = self.client.get("/api/plots").get_json()
        self.assertEqual({item["path"][1] for item in plots["series"]}, {"g/p42"})
        # Replay: the list applies again; without it the events make jobs
        again = Gitlab(self.datadir, ignored=["g/p43"])
        again.load()
        self.assertEqual(sorted(again.get()["projects"]), ["42"])
        again = Gitlab(self.datadir)
        again.load()
        self.assertEqual(sorted(again.get()["projects"]), ["42", "43"])
        self.assertIn("g/p43", {job["path"][1] for job in again.jobs()})

    def test_expire_jobs(self):
        self.post(pipeline_event(200, "main", "running", [(1, "a", "success")], bridges=[(300, "yocto")]))
        self.post(pipeline_event(201, "main", "running", [], parent=source(42, 200, 300)))
        self.post(pipeline_event(210, "dev", "success", []))
        self.client.post("/api/leds/" + DEV, json=[{"path": ["gitlab", "g/p42", "main"]}])
        self.assertEqual(self.led(), ("UNKNOWN", "running", "RUNNING"))
        self.assertEqual(self.gitlab.expire_jobs(), 0)
        self.assertEqual(len(self.gitlab.jobs()), 4)

        # Newer event for dev: only main goes
        later = time.time() + (91 * 86400)
        self.gitlab.handle("Pipeline Hook", pipeline_event(220, "dev", "running", []), later - 86400, raw=False)
        housekeeping = Housekeeping(CONFIG, [self.gitlab], self.jobs, self.devices, Firmware(None))
        self.assertEqual(housekeeping.run_once(later), {"gitlab events": 3, "gitlab jobs": 2, "firmware": 0,
                                                        "devices": 0})
        self.assertEqual(sorted(self.refs("dev")), ["downstream", "main"])
        self.assertNotIn("main", self.gitlab.get()["projects"]["42"]["refs"])
        self.assertEqual(sorted(self.gitlab.get()["projects"]["42"]["pipelines"]), ["220"])
        self.assertEqual([job["path"][2] for job in self.jobs.all()], ["dev"])
        self.assertEqual(self.devices.get_state(DEV)[0]["leds"][0]["status"], "UNKNOWN")

        # All gone: no project
        self.assertEqual(self.gitlab.expire_jobs(later + (91 * 86400)), 1)
        self.assertEqual(self.gitlab.get()["projects"], {})

    def test_housekeeping(self):
        # Firmware: newest 2 of each name stay
        fwdir = os.path.join(self.datadir, "fw")
        os.mkdir(fwdir)
        for name in ("laempli-chlotz", "laempli-xyz"):
            for ix in range(4):
                with open(os.path.join(fwdir, "{}_2026102300000{}-abc.bin".format(name, ix)), "wb") as fh:
                    fh.write(b"x")
        with open(os.path.join(fwdir, "other.bin"), "wb") as fh:
            fh.write(b"x")
        firmware = Firmware(fwdir)

        # Devices: one connected, one seen 10 days ago, one 400 days ago
        now = time.time()
        for dev_id in ("chlotz-000001", "chlotz-000002", "chlotz-000003"):
            self.devices.connect({"id": dev_id})
        self.devices.disconnect("chlotz-000002")
        self.devices.disconnect("chlotz-000003")
        with self.devices.cond:
            self.devices.devices["chlotz-000002"]["info"]["since"] = now - (10 * 86400)
            self.devices.devices["chlotz-000003"]["info"]["since"] = now - (400 * 86400)

        cfg = dict(CONFIG, firmware_keep=2)
        housekeeping = Housekeeping(cfg, [self.gitlab], self.jobs, self.devices, firmware)
        with self.assertLogs("laempli.housekeeping", "INFO"):
            self.assertEqual(housekeeping.run_once(now), {"gitlab events": 0, "gitlab jobs": 0, "firmware": 4,
                                                          "devices": 1})
        self.assertEqual(sorted(os.listdir(fwdir)), ["laempli-chlotz_20261023000002-abc.bin",
                                                     "laempli-chlotz_20261023000003-abc.bin",
                                                     "laempli-xyz_20261023000002-abc.bin",
                                                     "laempli-xyz_20261023000003-abc.bin", "other.bin"])
        self.assertEqual(self.devices.ids(), ["chlotz-000001", "chlotz-000002"])
        self.assertFalse(os.path.exists(os.path.join(self.datadir, "laempli_chlotz-000003.json")))
        self.assertEqual(self.devices.expire(0, now + (1000 * 86400)), [])

        # An error in one step: the others run
        with unittest.mock.patch.object(self.gitlab, "expire_events", side_effect=OSError("disk")):
            with self.assertLogs("laempli.housekeeping", "ERROR"):
                result = housekeeping.run_once(now)
        self.assertEqual(result, {"gitlab events": None, "gitlab jobs": 0, "firmware": 0, "devices": 0})

        # Thread: runs each interval, stops
        housekeeping.interval = 0.05
        with unittest.mock.patch.object(housekeeping, "run_once") as run_once:
            housekeeping.start()
            time.sleep(0.3)
            housekeeping.stop()
        self.assertGreater(run_once.call_count, 1)
        self.assertIsNone(housekeeping.thread)

    def test_results(self):
        self.assertEqual(self.led(), ("UNKNOWN", None, "UNKNOWN"))
        self.post(pipeline_event(100, "main", "running", []))
        self.assertEqual(self.led(), ("UNKNOWN", "running", "RUNNING"))
        self.post(pipeline_event(100, "main", "success", []))
        self.assertEqual(self.led(), ("PASS", "success", "STOPPED"))
        job = self.jobs.get(["gitlab", "g/p42", "main"])
        self.assertEqual((job["info"]["pipeline"], job["link"]), ("100 (#1)", "https://gl/g/foo/-/pipelines/100"))

        # New pipeline: previous result, pulsing
        self.post(pipeline_event(110, "main", "pending", []))
        self.assertEqual(self.led(), ("PASS", "pending", "WAITING"))
        self.post(pipeline_event(110, "main", "running", []))
        self.assertEqual(self.led(), ("PASS", "running", "RUNNING"))

        # Older pipeline finishes after a newer one started: it is the newest result
        self.post(pipeline_event(105, "main", "failed", []))
        self.assertEqual(self.led(), ("FAIL", "running", "RUNNING"))
        self.post(pipeline_event(120, "main", "running", []))
        self.post(pipeline_event(110, "main", "success", []))
        self.assertEqual(self.led(), ("PASS", "running", "RUNNING"))
        # Even older result does not change it
        self.post(pipeline_event(108, "main", "failed", []))
        self.assertEqual(self.led(), ("PASS", "running", "RUNNING"))
        self.assertTrue(self.jobs.get(["gitlab", "g/p42", "main"])["info"]["pipeline"].startswith("120 "))

        # Canceled: result unknown, retry of the same pipeline
        self.post(pipeline_event(120, "main", "canceling", []))
        self.assertEqual(self.led(), ("PASS", "canceling", "WAITING"))
        self.post(pipeline_event(120, "main", "canceled", []))
        self.assertEqual(self.led(), ("UNKNOWN", "canceled", "STOPPED"))
        self.post(pipeline_event(120, "main", "running", []))
        self.assertEqual(self.led(), ("UNKNOWN", "running", "RUNNING"))
        self.post(pipeline_event(120, "main", "failed", []))
        self.assertEqual(self.led(), ("FAIL", "failed", "STOPPED"))

        # Waiting status pulse fast (also manual), other status are steady and keep the result
        for status in WAITING:
            self.post(pipeline_event(130, "main", status, []))
            self.assertEqual(self.led(), ("FAIL", status, "WAITING"), status)
        for status in RUNNING:
            self.post(pipeline_event(130, "main", status, []))
            self.assertEqual(self.led(), ("FAIL", status, "RUNNING"), status)
        for status in ("scheduled", "skipped", "unknown"):
            self.post(pipeline_event(130, "main", status, []))
            self.assertEqual(self.led(), ("FAIL", status, "STOPPED"), status)

        # Success with warnings (failed jobs with allow_failure)
        event = pipeline_event(140, "main", "success", [])
        event["object_attributes"]["detailed_status"] = "passed with warnings"
        self.post(event)
        self.assertEqual(self.led(), ("WARN", "success", "STOPPED"))

        # The same again from the raw events
        self.assertEqual(self.reloaded().jobs(), self.gitlab.jobs())

    def test_results_downstream(self):
        self.post(pipeline_event(200, "main", "running", [], bridges=[(300, "yocto")]))
        self.post(pipeline_event(201, "main", "failed", [], parent=source(42, 200, 300)))
        self.post(pipeline_event(200, "main", "success", []))
        self.assertEqual(self.led(), ("PASS", "success", "STOPPED"))
        self.assertEqual(self.led("yocto"), ("FAIL", "failed", "STOPPED"))
        self.post(pipeline_event(210, "main", "running", [], bridges=[(310, "yocto")]))
        self.assertEqual(self.led(), ("PASS", "running", "RUNNING"))
        self.assertEqual(self.led("yocto"), ("FAIL", "failed", "STOPPED"))
        self.post(pipeline_event(211, "main", "pending", [], parent=source(42, 210, 310)))
        self.assertEqual(self.led("yocto"), ("FAIL", "pending", "WAITING"))
        self.assertEqual(self.led("other"), ("UNKNOWN", None, "UNKNOWN"))
        self.assertEqual(self.jobs.tree(), {"gitlab": {"job": False, "children": {"g/p42": {"job": False, "children": {
            "main": {"job": True, "children": {"yocto": {"job": True, "children": {}}}}}}}}})
        self.assertIn('<span class="badge state-badge text-bg-danger">FAIL</span>', self.client.get("/web/jobs").data.decode())

    def test_job_results(self):
        job = "Job Hook"
        self.assertEqual(self.led("test"), ("UNKNOWN", None, "UNKNOWN"))
        self.post(pipeline_event(100, "main", "running", [(1, "build", "success"), (2, "test", "running")]))
        self.assertEqual(self.led("build"), ("PASS", "success", "STOPPED"))
        self.assertEqual(self.led("test"), ("UNKNOWN", "running", "RUNNING"))
        self.post(job_event(100, "main", 2, "test", "failed"), event=job)
        self.assertEqual(self.led("test"), ("FAIL", "failed", "STOPPED"))
        # The pipeline is separate
        self.assertEqual(self.led(), ("UNKNOWN", "running", "RUNNING"))

        # Retry: new job ID, same name
        self.post(job_event(100, "main", 3, "test", "pending"), event=job)
        self.assertEqual(self.led("test"), ("FAIL", "pending", "WAITING"))
        self.post(job_event(100, "main", 3, "test", "success"), event=job)
        self.assertEqual(self.led("test"), ("PASS", "success", "STOPPED"))
        self.post(pipeline_event(100, "main", "success", []))

        # New pipeline: job not there yet, then waits, manual job pulses fast
        self.post(pipeline_event(110, "main", "pending", []))
        self.assertEqual(self.led("test"), ("PASS", "not in pipeline", "STOPPED"))
        self.post(job_event(110, "main", 11, "test", "created"), event=job)
        self.assertEqual(self.led("test"), ("PASS", "created", "WAITING"))
        self.post(job_event(110, "main", 12, "deploy", "manual"), event=job)
        self.assertEqual(self.led("deploy"), ("UNKNOWN", "manual", "WAITING"))
        # Late result of an older run does not win, canceled: result unknown
        self.post(job_event(110, "main", 11, "test", "canceled"), event=job)
        self.post(job_event(100, "main", 2, "test", "failed"), event=job)
        self.assertEqual(self.led("test"), ("UNKNOWN", "canceled", "STOPPED"))
        self.assertEqual(self.jobs.get(["gitlab", "g/p42", "main", "test"])["link"], "https://gl/g/foo/-/jobs/11")
        # Failed with allow_failure: warn
        event = job_event(110, "main", 13, "lint", "failed")
        event["build_allow_failure"] = True
        self.post(event, event=job)
        self.assertEqual(self.led("lint"), ("WARN", "failed", "STOPPED"))

        # Downstream jobs, tree for the web page
        self.post(pipeline_event(110, "main", "running", [], bridges=[(300, "yocto")]))
        self.post(job_event(111, "main", 20, "image", "running", parent=source(42, 110, 300)), event=job)
        self.assertEqual(self.led("yocto", "image"), ("UNKNOWN", "running", "RUNNING"))
        main = self.jobs.tree()["gitlab"]["children"]["g/p42"]["children"]["main"]
        self.assertEqual(sorted(main["children"]), ["build", "deploy", "lint", "test", "yocto"])
        self.assertEqual(list(main["children"]["yocto"]["children"]), ["image"])

        # LED config and state with job
        self.client.post("/api/leds/" + DEV, json=[{"path": ["gitlab", "g/p42", "main", "test"]}])
        state = self.devices.get_state(DEV)[0]["leds"][0]
        self.assertEqual((state["label"], state["path"][-1], state["result"], state["status"], state["info"]["status"],
                          state["link"]), ("p42 main test", "test", "UNKNOWN", "STOPPED", "canceled",
                                           "https://gl/g/foo/-/jobs/11"))
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn('id="chain_0"', page)
        self.assertIn('<span class="badge state-badge text-bg-light border">UNKNOWN</span>'
                      '<span class="badge state-badge text-bg-secondary">STOPPED</span>', page)
        # Tile: label without tooltip, info icon (link) top right; fields: no state
        self.assertIn('<span class="led-tile-label">p42 main test</span>', page)
        self.assertIn('data-live="tile-info-0"><button type="button" class="badge narrow-badge info-icon has-link"', page)
        # Popover: path, badges, source info, link
        tip = html.unescape(re.search(r'data-job-tip="([^"]*)"', page).group(1))
        self.assertIn('<span class="tip-path">gitlab → g/p42 → main → test</span>', tip)
        self.assertIn('<div class="tip-title">GitLab info</div>', tip)
        self.assertIn('<dt>status</dt><dd>canceled</dd>', tip)
        self.assertIn('<a class="tip-link" href="https://gl/g/foo/-/jobs/11" target="_blank" rel="noopener">'
                      'Open GitLab job</a>', tip)
        self.assertNotIn('data-live="state-0"', page)

    def test_downstream_without_parent(self):
        # GitLab sends the last event of a canceled child pipeline without source_pipeline
        self.post(pipeline_event(200, "main", "running", [(1, "check", "success")], bridges=[(300, "yocto")]))
        self.post(pipeline_event(201, "main", "running", [(2, "image", "running")], parent=source(42, 200, 300)))
        self.post(pipeline_event(200, "main", "canceled", []))
        event = pipeline_event(201, "main", "canceled", [(2, "image", "canceled")])
        event["object_attributes"]["source"] = "parent_pipeline"
        self.post(event)
        self.post(job_event(201, "main", 2, "image", "canceled"), event="Job Hook")
        self.assertEqual(self.refs("main"), {"main": 200, "downstream": {"yocto": 201}})
        self.assertEqual(self.led("yocto"), ("UNKNOWN", "canceled", "STOPPED"))
        self.assertEqual(self.led("yocto", "image"), ("UNKNOWN", "canceled", "STOPPED"))
        self.assertEqual(self.led("image"), ("UNKNOWN", None, "UNKNOWN"))
        self.assertEqual(self.led(), ("UNKNOWN", "canceled", "STOPPED"))
        self.assertEqual(self.gitlab.get()["projects"]["42"]["refs"]["main"]["main"]["result_pipeline"], 200)

        # Unknown downstream pipeline without parent: ignored, not a main pipeline
        event = pipeline_event(202, "main", "failed", [])
        event["object_attributes"]["source"] = "parent_pipeline"
        self.post(event)
        self.assertEqual(self.led(), ("UNKNOWN", "canceled", "STOPPED"))
        self.assertNotIn("202", self.pipelines())
        self.assertTrue(self.gitlab.events()[0]["ignored"])

    def test_jobs_api(self):
        self.post(pipeline_event(100, "main", "running", [(1, "build", "success")]))
        jobs = self.client.get("/api/jobs").get_json()["jobs"]
        self.assertEqual([job["path"] for job in jobs], [["gitlab", "g/p42", "main"],
                                                         ["gitlab", "g/p42", "main", "build"]])
        self.assertEqual(jobs[0]["info"], {"pipeline": "100 (#1)", "name": "Commit", "status": "running",
                                           "source": "push", "commit": "abc", "user": "flip"})
        self.assertEqual(jobs[1]["info"], {"job": "1", "status": "success", "stage": "test", "pipeline": "100 (#1)",
                                           "commit": "abc", "user": "flip"})
        self.assertFalse(os.path.exists(os.path.join(self.datadir, "jobs.json")))

    def test_device_page(self):
        self.post(pipeline_event(200, "main", "running", [], bridges=[(300, "yocto")]))
        self.post(pipeline_event(201, "main", "success", [], parent=source(42, 200, 300)))
        self.assertEqual(self.client.get("/web/device/x").status_code, 404)
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertEqual(page.count('id="chain_'), 10)
        self.assertIn("g/p42", page)
        self.assertIn("yocto", page)

        form = {"rows": "10", "path_0": json.dumps([" gitlab", "g/p42", "main "]), "path_3": "[]",
                "path_2": json.dumps(["gitlab", "g/p42", "main", "yocto"]), "name_2": "Yocto"}
        res = self.client.post("/web/device/" + DEV, data=form)
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.headers["Location"], "/web/device/" + DEV)
        config = self.devices.get_config(DEV)["leds"]
        self.assertEqual(config, [{"path": ["gitlab", "g/p42", "main"], "name": ""}, None,
                                  {"path": ["gitlab", "g/p42", "main", "yocto"], "name": "Yocto"}])
        state = self.devices.get_state(DEV)[0]["leds"]
        self.assertEqual((state[0]["label"], state[0]["result"], state[0]["status"]),
                         ("p42 main", "UNKNOWN", "RUNNING"))
        self.assertIsNone(state[1])
        self.assertEqual((state[2]["label"], state[2]["result"], state[2]["status"]), ("Yocto", "PASS", "STOPPED"))
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn('value="Yocto"', page)
        self.assertIn('data-path=\'["gitlab", "g/p42", "main", "yocto"]\'', page)
        self.assertIn("2/?", self.client.get("/web/devices").data.decode())

        # Bad config: not saved, form shows the input
        for path, error in ((["gitlab"], "path must have"), ("{bad", "list of strings")):
            res = self.client.post("/web/device/" + DEV, data={"rows": "3", "path_1": json.dumps(path)
                                                               if isinstance(path, list) else path, "name_1": "Bad"})
            self.assertEqual(res.status_code, 400)
            self.assertIn(error, res.data.decode())
            self.assertIn('value="Bad"', res.data.decode())
        self.assertEqual(self.devices.get_config(DEV)["leds"], config)

        # Config and state saved in the device file
        loaded = Devices(self.datadir)
        self.assertEqual(loaded.get_config(DEV)["leds"], config)
        self.assertEqual(loaded.get_state(DEV)[0]["leds"], state)

    def test_old_config(self):
        # GitLab config of older versions: converted and saved at load
        old = {"info": {}, "state": {"leds": []}, "config": {"leds": [
            {"project": "g/p42", "ref": "main", "downstream": "yocto", "job": "", "name": "Y"}, None,
            {"project": "", "ref": ""}, {"project": "g/p42", "ref": "dev", "downstream": "", "job": "build"}]}}
        path = os.path.join(self.datadir, "laempli_{}.json".format(DEV))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(old, fh)
        new = [{"path": ["gitlab", "g/p42", "main", "yocto"], "name": "Y"}, None, None,
               {"path": ["gitlab", "g/p42", "dev", "build"], "name": ""}]
        with self.assertLogs("laempli.devices", "INFO"):
            self.assertEqual(Devices(self.datadir).get_config(DEV)["leds"], new)
        with open(path, "r", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["config"]["leds"], new)
        self.assertEqual(check_config(new), new)

    def test_api_leds(self):
        self.assertEqual(self.client.get("/api/leds/" + DEV).status_code, 404)
        self.assertEqual(self.client.get("/api/leds/x").status_code, 404)
        res = self.client.post("/api/leds/" + DEV, json=[None, {"path": ["gitlab", "g/p42", "dev"]}])
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["config"], [None, {"path": ["gitlab", "g/p42", "dev"], "name": ""}])
        self.assertEqual((data["state"][1]["label"], data["state"][1]["result"], data["state"][1]["status"]),
                         ("p42 dev", "UNKNOWN", "UNKNOWN"))
        res = self.client.post("/api/leds/" + DEV, json={"path": ["x"]})
        self.assertEqual(res.status_code, 400)
        self.assertIn("list", res.get_json()["error"])
        self.assertEqual(self.client.get("/api/leds/" + DEV).get_json(), data)

    def test_state_push(self):
        # Device stream gets a new state after a GitLab event
        self.client.post("/api/leds/" + DEV, json=[{"path": ["gitlab", "g/p42", "main"]}])
        stream = open_stream(self.devices)
        next(stream)
        first = next(stream)[1]
        self.assertEqual(first["state"]["leds"][0]["status"], "UNKNOWN")
        self.post(pipeline_event(100, "main", "running", []))
        change = next(stream)[1]
        self.assertEqual(change["version"], first["version"] + 1)
        self.assertEqual((change["state"]["leds"][0]["status"], change["state"]["leds"][0]["result"]),
                         ("RUNNING", "UNKNOWN"))
        # Event without change for this device: no new state
        version = self.devices.get_state(DEV)[1]
        self.post(pipeline_event(500, "other", "running", []))
        self.assertEqual(self.devices.get_state(DEV)[1], version)
        stream.close()

    def test_jobs(self):
        job = "Job Hook"
        # Job event before the pipeline event: pipeline with the commit status
        self.assertEqual(self.post(job_event(100, "main", 1, "build", "pending", commit_status="pending"),
                                   event=job).status_code, 200)
        self.assertEqual(self.refs("main"), {"main": 100, "downstream": {}})
        self.assertEqual(self.led(), ("UNKNOWN", "pending", "WAITING"))
        self.post(pipeline_event(100, "main", "running", [(1, "build", "running")]))
        self.post(job_event(100, "main", 2, "test", "created"), event=job)
        self.post(job_event(100, "main", 1, "build", "success"), event=job)
        # Late "running" does not undo a finished job
        self.post(job_event(100, "main", 1, "build", "running"), event=job)
        jobs = self.pipelines()["100"]["jobs"]
        self.assertEqual({key: value["status"] for key, value in jobs.items()}, {"1": "success", "2": "created"})
        self.assertEqual((jobs["2"]["name"], jobs["2"]["stage"]), ("test", "test"))
        # Job events do not change the pipeline status from the pipeline event
        self.assertEqual(self.led(), ("UNKNOWN", "running", "RUNNING"))

        # Child pipeline jobs, also before the child pipeline event
        self.post(pipeline_event(110, "main", "running", [], bridges=[(300, "yocto")]))
        self.post(job_event(111, "main", 5, "image", "pending", parent=source(42, 110, 300)), event=job)
        self.assertEqual(self.refs("main"), {"main": 110, "downstream": {"yocto": 111}})
        self.assertEqual(self.pipelines()["111"]["trigger"], "yocto")

        # Job of an old, removed pipeline is ignored
        self.gitlab.handle("Pipeline Hook", pipeline_event(110, "main", "running", []), time.time() + 4000, raw=False)
        self.post(job_event(100, "main", 1, "build", "failed"), event=job)
        self.assertNotIn("100", self.pipelines())

        # Event log
        events = self.gitlab.events()
        self.assertTrue(events[0]["ignored"])
        self.assertEqual((events[0]["kind"], events[0]["job"]), ("job", "build"))
        self.assertEqual((events[2]["kind"], events[2]["job"], events[2]["trigger"]), ("job", "image", "yocto"))
        rows = self.client.get("/web/gitlab/rows").get_json()["rows"]
        self.assertIn("test: image", "".join(rows))
        self.assertEqual(self.client.post("/api/gitlab", json={"object_kind": "build"}, headers={"X-Gitlab-Event": job,
                         "webhook-id": "x"}).status_code, 401)
        self.assertEqual(self.post({"object_kind": "build", "ref": "main", "pipeline_id": 1}, event=job).status_code,
                         400)

    def test_replay(self):
        self.post(pipeline_event(200, "main", "running", [], bridges=[(300, "yocto")]))
        self.post(pipeline_event(201, "main", "running", [], parent=source(42, 200, 300)))
        events = os.path.join(self.datadir, "events.jsonl")
        with open(events, "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(entry) + "\n" for entry in self.gitlab.raw_events())
        other = os.path.join(self.datadir, "other")
        os.mkdir(other)
        rebuilt = Gitlab(other)
        self.assertEqual(rebuilt.replay(events), 2)
        self.assertEqual(rebuilt.get(), self.gitlab.get())
        self.assertEqual(len(rebuilt.events()), 2)
        self.assertEqual(rebuilt.raw_events(), [])


GH_SECRET = "s3cret"
GH_REPO = {"id": 77, "full_name": "o/r", "html_url": "https://github.com/o/r"}


def gh_run(run_id, status, conclusion=None, branch="main", workflow="ci", attempt=1):
    # workflow_run event
    return {"action": ("completed" if (status == "completed") else status),
            "workflow_run": {"id": run_id, "name": workflow, "head_branch": branch, "head_sha": "abcdef1234567890",
                             "display_title": "Fix foo", "run_number": run_id - 88, "event": "push",
                             "status": status, "conclusion": conclusion, "run_attempt": attempt,
                             "html_url": "https://github.com/o/r/actions/runs/{}".format(run_id),
                             "created_at": "2026-10-24T10:00:00Z", "run_started_at": "2026-10-24T10:00:00Z",
                             "updated_at": "2026-10-24T10:05:30Z", "actor": {"login": "flip"},
                             "head_commit": {"message": "Fix foo\n\nMore text"}},
            "repository": GH_REPO, "sender": {"login": "flip"}}


def gh_job(job_id, run_id, name, status, conclusion=None, branch="main", workflow="ci", attempt=1, steps=()):
    # workflow_job event
    return {"action": status,
            "workflow_job": {"id": job_id, "run_id": run_id, "workflow_name": workflow, "head_branch": branch,
                             "run_attempt": attempt, "head_sha": "abcdef1234567890", "name": name,
                             "status": status, "conclusion": conclusion,
                             "html_url": "https://github.com/o/r/actions/runs/{}/job/{}".format(run_id, job_id),
                             "started_at": "2026-10-24T10:01:00Z",
                             "completed_at": ("2026-10-24T10:03:05Z" if (status == "completed") else None),
                             "runner_name": "GitHub Actions 7", "steps": list(steps)},
            "repository": GH_REPO, "sender": {"login": "flip"}}


def gh_delete(ref, ref_type="branch"):
    return {"ref": ref, "ref_type": ref_type, "pusher_type": "user", "repository": GH_REPO, "sender": {"login": "flip"}}


class GithubDeleteTest(unittest.TestCase):
    """Deleted branches and tags, removal by hand"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.github = Github(self.tmpdir.name, [])

    def tearDown(self):
        self.tmpdir.cleanup()

    def handle(self, data, event=None):
        if event is None:
            event = ("workflow_run" if ("workflow_run" in data) else "workflow_job")
        return self.github.handle(event, data)

    def branches(self):
        return sorted(self.github.get()["repos"][str(GH_REPO["id"])]["slots"])

    def test_branch_deleted(self):
        self.handle(gh_run(100, "completed", "success"))
        self.handle(gh_run(110, "in_progress", branch="feature/x"))
        self.assertEqual(self.branches(), ["feature/x", "main"])
        self.assertIn("slots removed", self.handle(gh_delete("feature/x"), "delete"))
        self.assertEqual(self.branches(), ["main"])
        self.assertEqual(self.github.events()[0]["kind"], "deleted")
        # Late events: ignored
        self.assertIn("ignored", self.handle(gh_job(5, 110, "build", "completed", "success", branch="feature/x")))
        self.assertIn("ignored", self.handle(gh_run(110, "completed", "success", branch="feature/x")))
        self.assertEqual(self.branches(), ["main"])
        # Replay
        github = Github(self.tmpdir.name, [])
        github.load()
        self.assertEqual(sorted(github.get()["repos"][str(GH_REPO["id"])]["slots"]), ["main"])
        # New run: back
        self.handle(gh_run(120, "queued", branch="feature/x"))
        self.assertEqual(self.branches(), ["feature/x", "main"])

    def test_tag_deleted(self):
        self.handle(gh_run(100, "completed", "success", branch="v1.0"))
        self.handle(gh_delete("v1.0", "tag"), "delete")
        self.assertEqual(self.branches(), [])

    def test_remove(self):
        self.handle(gh_run(100, "completed", "success", branch="feature/x"))
        self.assertFalse(self.github.remove("o/r", "nope", "editor"))
        self.assertTrue(self.github.remove("o/r", "feature/x", "editor"))
        self.assertEqual(self.branches(), [])
        self.assertEqual(self.github.events()[0]["kind"], "removed")
        github = Github(self.tmpdir.name, [])
        github.load()
        self.assertEqual(sorted(github.get()["repos"][str(GH_REPO["id"])]["slots"]), [])


class JobsRemoveApiTest(unittest.TestCase):
    """POST /api/jobs/remove"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.gitlab = Gitlab(self.tmpdir.name, [])
        self.github = Github(self.tmpdir.name, [])
        self.jobs = Jobs()
        app = create_app(Devices(self.tmpdir.name), None, 15, self.gitlab, self.jobs, self.github,
                         editors=["editor"])
        self.client = app.test_client()
        self.gitlab.handle("Pipeline Hook", pipeline_event(100, "feature/x", "success", [(1, "build", "success")]))
        self.github.handle("workflow_run", gh_run(100, "completed", "success", branch="feature/y"))
        self.jobs.update("gitlab", self.gitlab.jobs())
        self.jobs.update("github", self.github.jobs())

    def tearDown(self):
        self.tmpdir.cleanup()

    def remove(self, path, user="editor"):
        return self.client.post("/api/jobs/remove", json={"path": path}, headers={"X-Forwarded-User": user})

    def test_remove(self):
        self.assertEqual(self.remove(["gitlab", "g/p42", "feature/x"], user="other").status_code, 403)
        self.assertEqual(self.remove(["gitlab", "g/p42"]).status_code, 400)
        self.assertEqual(self.remove(["generic", "a", "b"]).status_code, 400)
        self.assertEqual(self.remove(["gitlab", "g/p42", "nope"]).status_code, 404)
        self.assertEqual(len(self.jobs.find(["gitlab"], 3)), 1)
        self.assertEqual(self.remove(["gitlab", "g/p42", "feature/x"]).get_json(), {"ok": True})
        self.assertEqual(self.jobs.find(["gitlab"], 3), [])
        self.assertEqual(self.gitlab.events()[0]["user"], "editor")
        self.assertEqual(self.remove(["github", "o/r", "feature/y"]).status_code, 200)
        self.assertEqual(self.jobs.find(["github"], 4), [])


class GithubTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.datadir = self.tmpdir.name
        self.github = Github(self.datadir, ["other", GH_SECRET])
        self.jobs = Jobs()
        self.devices = Devices(self.datadir)
        self.client = create_app(self.devices, None, 15, None, self.jobs, self.github).test_client()

    def tearDown(self):
        self.tmpdir.cleanup()

    def post(self, data, event=None, secret=GH_SECRET, body=None, form=False):
        if event is None:
            event = ("workflow_run" if ("workflow_run" in data) else "workflow_job")
        body = (json.dumps(data).encode() if body is None else body)
        content_type = "application/json"
        if form:
            body = urllib.parse.urlencode({"payload": body.decode()}).encode()
            content_type = "application/x-www-form-urlencoded"
        headers = {"X-GitHub-Event": event, "X-GitHub-Delivery": "d-1", "Content-Type": content_type}
        if secret is not None:
            headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return self.client.post("/api/github", data=body, headers=headers)

    def led(self, *names, branch="main"):
        # Result, info status and status of a job
        job = self.jobs.get(["github", "o/r", branch, "ci"] + list(names))
        return (job["result"], job["info"].get("status"), job["status"])

    def raw_lines(self):
        return self.github.raw_events()

    def test_signature(self):
        body = b'{"a": 1}'
        good = "sha256=" + hmac.new(GH_SECRET.encode(), body, hashlib.sha256).hexdigest()
        self.assertIsNone(self.github.check_signature(good, body))
        self.assertEqual(self.github.check_signature(None, body), "no signature")
        self.assertEqual(self.github.check_signature(good, body + b" "), "bad signature")
        self.assertEqual(self.github.check_signature("sha1=abc", body), "bad signature")
        self.assertIsNone(Github(self.datadir).check_signature(None, body))
        with self.assertLogs("laempli.web", "WARNING"):
            self.assertEqual(self.post(gh_run(100, "queued"), secret=None).status_code, 401)
            self.assertEqual(self.post(gh_run(100, "queued"), secret="wrong").status_code, 401)
        self.assertEqual(self.raw_lines(), [])
        self.assertEqual(self.post(gh_run(100, "queued")).status_code, 200)
        self.assertEqual(len(self.raw_lines()), 1)

    def test_ping_ignored_bad(self):
        res = self.post({"zen": "Keep it simple.", "hook_id": 5, "repository": GH_REPO}, event="ping")
        self.assertEqual(res.get_json(), {"ok": True, "ping": True})
        res = self.post({"ref": "refs/heads/main"}, event="push")
        self.assertEqual(res.get_json(), {"ok": True, "ignored": True})
        self.assertEqual(self.raw_lines(), [])
        self.assertEqual(self.jobs.all(), [])
        with self.assertLogs("laempli.web", "WARNING"):
            self.assertEqual(self.post({}, event="workflow_run", body=b"{bad").status_code, 400)
            self.assertEqual(self.post({"workflow_run": {"id": 1}}, event="workflow_run").status_code, 400)
            self.assertEqual(self.post({"workflow_job": {"id": 1, "run_id": 2, "name": "x"}, "repository": GH_REPO},
                                       event="workflow_job").status_code, 400)
        # Form content type works too
        self.assertEqual(self.post(gh_run(100, "queued"), form=True).status_code, 200)
        self.assertEqual(self.led(), ("UNKNOWN", "queued", "WAITING"))
        self.assertEqual(self.client.get("/api/github").get_json()["repos"]["77"]["name"], "o/r")

    def test_run_jobs(self):
        self.post(gh_run(100, "queued"))
        self.assertEqual(self.led(), ("UNKNOWN", "queued", "WAITING"))
        self.post(gh_job(1, 100, "build", "queued"))
        self.assertEqual(self.led("build"), ("UNKNOWN", "queued", "WAITING"))
        self.post(gh_run(100, "in_progress"))
        self.post(gh_job(1, 100, "build", "in_progress"))
        self.post(gh_job(2, 100, "test", "in_progress"))
        self.assertEqual(self.led(), ("UNKNOWN", "in_progress", "RUNNING"))
        self.assertEqual(self.led("build"), ("UNKNOWN", "in_progress", "RUNNING"))
        self.post(gh_job(1, 100, "build", "completed", "success"))
        self.assertEqual(self.led("build"), ("PASS", "success", "STOPPED"))
        # A late "in_progress" does not undo a completed job
        self.post(gh_job(1, 100, "build", "in_progress"))
        self.assertEqual(self.led("build"), ("PASS", "success", "STOPPED"))
        steps = [{"name": "Checkout", "conclusion": "success"}, {"name": "Build", "conclusion": "failure"}]
        self.post(gh_job(2, 100, "test", "completed", "failure", steps=steps))
        self.post(gh_run(100, "completed", "failure"))
        self.assertEqual(self.led(), ("FAIL", "failure", "STOPPED"))
        self.assertEqual(self.led("test"), ("FAIL", "failure", "STOPPED"))

        run = self.jobs.get(["github", "o/r", "main", "ci"])
        self.assertEqual(run["link"], "https://github.com/o/r/actions/runs/100")
        self.assertEqual({key: run["info"][key] for key in ("run", "title", "event", "commit", "user", "duration")},
                         {"run": "#12", "title": "Fix foo", "event": "push", "commit": "abcdef12 Fix foo",
                          "user": "flip", "duration": "5:30"})
        job = self.jobs.get(["github", "o/r", "main", "ci", "test"])
        self.assertEqual(job["link"], "https://github.com/o/r/actions/runs/100/job/2")
        self.assertEqual({key: job["info"][key] for key in ("job", "failed step", "run", "runner", "duration")},
                         {"job": "2", "failed step": "Build", "run": "#12", "runner": "GitHub Actions 7",
                          "duration": "2:05"})
        self.assertEqual([entry["path"][3:] for entry in self.jobs.all()], [["ci"], ["ci", "build"], ["ci", "test"]])

        # Event log, newest first
        log = self.client.get("/api/github/log").get_json()["events"]
        self.assertEqual(len(log), 9)
        self.assertEqual((log[0]["repo"], log[0]["branch"], log[0]["workflow"], log[0]["run"], log[0]["status"]),
                         ("o/r", "main", "ci", "#12", "failure"))
        self.assertEqual((log[1]["kind"], log[1]["job"]), ("job", "test"))

    def test_job_before_run(self):
        self.post(gh_job(1, 100, "build", "in_progress"))
        self.assertEqual(self.led(), ("UNKNOWN", "in_progress", "RUNNING"))
        self.assertEqual(self.led("build"), ("UNKNOWN", "in_progress", "RUNNING"))
        self.assertEqual(self.jobs.get(["github", "o/r", "main", "ci"])["info"]["run"], "100")
        self.post(gh_run(100, "in_progress"))
        self.assertEqual(self.jobs.get(["github", "o/r", "main", "ci"])["info"]["run"], "#12")

    def test_rerun(self):
        self.post(gh_job(1, 100, "build", "completed", "failure"))
        self.post(gh_run(100, "completed", "failure"))
        self.assertEqual(self.led(), ("FAIL", "failure", "STOPPED"))
        # Re-run: same run ID, attempt 2, new job ID
        self.post(gh_run(100, "queued", attempt=2))
        self.assertEqual(self.led(), ("FAIL", "queued", "WAITING"))
        self.assertEqual(self.jobs.get(["github", "o/r", "main", "ci"])["info"]["run"], "#12 attempt 2")
        self.post(gh_job(5, 100, "build", "in_progress", attempt=2))
        self.assertEqual(self.led("build"), ("FAIL", "in_progress", "RUNNING"))
        self.post(gh_job(5, 100, "build", "completed", "success", attempt=2))
        self.post(gh_run(100, "completed", "success", attempt=2))
        self.assertEqual(self.led(), ("PASS", "success", "STOPPED"))
        self.assertEqual(self.led("build"), ("PASS", "success", "STOPPED"))
        # Late events of attempt 1: no change
        self.post(gh_run(100, "completed", "failure", attempt=1))
        self.post(gh_job(1, 100, "build", "completed", "failure"))
        self.assertEqual(self.led(), ("PASS", "success", "STOPPED"))
        self.assertEqual(self.led("build"), ("PASS", "success", "STOPPED"))

    def test_newer_run(self):
        self.post(gh_run(100, "in_progress"))
        self.post(gh_job(1, 100, "build", "in_progress"))
        self.post(gh_run(101, "queued"))
        self.assertEqual(self.led(), ("UNKNOWN", "queued", "WAITING"))
        # Job of the old run without result: not a job of the slot
        self.assertEqual(self.led("build"), ("UNKNOWN", None, "UNKNOWN"))
        # Late events of the old run: results only
        self.post(gh_job(1, 100, "build", "completed", "success"))
        self.post(gh_run(100, "completed", "success"))
        self.assertEqual(self.led(), ("PASS", "queued", "WAITING"))
        self.assertEqual(self.led("build"), ("PASS", "not in run", "STOPPED"))
        self.post(gh_run(101, "completed", "failure"))
        self.assertEqual(self.led(), ("FAIL", "failure", "STOPPED"))
        # Old run removed after PRUNE_AGE, then its events are ignored
        self.github.handle("workflow_run", gh_run(101, "completed", "failure"), time.time() + 7200)
        self.assertEqual(sorted(self.github.get()["repos"]["77"]["runs"]), ["101"])
        self.post(gh_run(100, "completed", "success"))
        self.assertTrue(self.client.get("/api/github/log?ignored=1").get_json()["events"][0]["ignored"])
        self.assertEqual(self.led(), ("FAIL", "failure", "STOPPED"))
        # Other branch and other workflow: own slots
        self.post(gh_run(102, "in_progress", branch="dev"))
        self.post(gh_run(103, "in_progress", workflow="docker"))
        self.assertEqual(self.led(branch="dev"), ("UNKNOWN", "in_progress", "RUNNING"))
        self.assertEqual(self.jobs.get(["github", "o/r", "main", "docker"])["status"], "RUNNING")
        self.assertEqual(self.led(), ("FAIL", "failure", "STOPPED"))

    def test_conclusions(self):
        for conclusion, result in (("success", "PASS"), ("skipped", "PASS"), ("neutral", "PASS"),
                                   ("timed_out", "FAIL"), ("startup_failure", "FAIL"), ("cancelled", "UNKNOWN"),
                                   ("failure", "FAIL"), ("action_required", "UNKNOWN"), ("stale", "UNKNOWN")):
            self.post(gh_run(100, "completed", conclusion))
            self.assertEqual(self.led()[0], result, conclusion)
        self.post(gh_run(101, "waiting"))
        self.assertEqual(self.led(), ("UNKNOWN", "waiting", "WAITING"))

    def test_ignored_repos(self):
        self.github.ignored = ["o/*"]
        self.post(gh_run(100, "in_progress"))
        self.post(gh_job(1, 100, "build", "completed", "failure"))
        self.assertEqual(len(self.raw_lines()), 2)
        self.assertEqual((self.github.get()["repos"], self.jobs.all()), ({}, []))
        log = self.client.get("/api/github/log?ignored=1").get_json()["events"]
        self.assertEqual((log[0]["kind"], log[0]["job"], log[0]["status"], log[0]["reason"]),
                         ("job", "build", "failure", "repository ignored"))
        self.assertEqual((log[1]["run"], log[1]["branch"], log[1]["workflow"], log[1]["status"], log[1]["user"]),
                         ("#12", "main", "ci", "in_progress", "flip"))
        self.assertEqual(self.client.get("/api/github/log").get_json()["filtered"], 0)
        self.assertEqual(self.client.get("/api/github/log?kind=runs&ignored=1").get_json()["filtered"], 1)
        self.assertEqual(self.client.get("/api/github/log?kind=pipelines").status_code, 400)
        self.assertIn('data-value="runs">Runs<', self.client.get("/web/github").data.decode())

    def test_replay_expire(self):
        self.post(gh_run(100, "in_progress"))
        self.post(gh_job(1, 100, "build", "completed", "success"))
        self.post(gh_run(100, "completed", "success"))
        github = Github(self.datadir)
        self.assertEqual(github.load(), 3)
        self.assertEqual(github.get(), self.github.get())
        self.assertEqual(github.jobs(), self.github.jobs())
        self.assertEqual(len(github.events()), 3)
        # Expire: nothing now, all after keep_days
        self.assertEqual((self.github.expire_jobs(), self.github.expire_events()), (0, 0))
        later = time.time() + (91 * 86400)
        self.assertEqual(self.github.expire_jobs(later), 1)
        self.assertEqual(self.github.get()["repos"], {})
        self.assertEqual(self.github.expire_events(later), 3)
        self.assertEqual(self.raw_lines(), [])
        housekeeping = Housekeeping(CONFIG, [self.github], self.jobs, self.devices, Firmware(None))
        self.assertEqual(github.expire_jobs(later), 1)
        self.assertEqual(housekeeping.run_once(later), {"github events": 0, "github jobs": 0, "firmware": 0,
                                                        "devices": 0})

    def test_web_leds(self):
        self.post(gh_run(100, "in_progress"))
        self.post(gh_job(1, 100, "ci-fpsdk-trixie", "completed", "success"))
        page = self.client.get("/web/github").data.decode()
        self.assertIn("ci-fpsdk-trixie", "".join(self.client.get("/web/github/rows").get_json()["rows"]))
        self.assertIn(">GitHub</a>", page)
        self.assertIn("nav-link active", page)
        self.assertIn("ci → ci-fpsdk-trixie", self.client.get("/web/jobs").data.decode())
        # LED config with GitHub paths, device state
        self.devices.connect({"id": DEV, "leds": 3})
        path = ["github", "o/r", "main", "ci", "ci-fpsdk-trixie"]
        res = self.client.post("/api/leds/" + DEV, json=[{"path": path[:4]}, {"path": path, "name": "Trixie"}])
        self.assertEqual(res.status_code, 200)
        state = self.devices.get_state(DEV)[0]["leds"]
        self.assertEqual([(led["label"], led["status"], led["result"]) for led in state],
                         [("r main ci", "RUNNING", "UNKNOWN"), ("Trixie", "STOPPED", "PASS")])
        self.post(gh_run(100, "completed", "success"))
        state = self.devices.get_state(DEV)[0]["leds"]
        self.assertEqual((state[0]["status"], state[0]["result"]), ("STOPPED", "PASS"))
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn("(this workflow)", page)
        self.assertIn('"o/r"', page)


class GenericTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.datadir = self.tmpdir.name
        self.generic = Generic(self.datadir, ["other", "tok"])
        self.jobs = Jobs()
        self.devices = Devices(self.datadir)
        self.client = create_app(self.devices, None, 15, None, self.jobs, None, self.generic).test_client()

    def tearDown(self):
        self.tmpdir.cleanup()

    def set(self, url, token="tok", **kwargs):
        headers = ({"X-Laempli-Token": token} if token else {})
        return self.client.post("/api/generic/" + url, headers=headers, **kwargs)

    def state(self, *names):
        job = self.jobs.get(["generic"] + list(names))
        return (job["status"], job["result"])

    def test_parse_update(self):
        self.assertEqual(parse_update({}), {})
        self.assertEqual(parse_update({"status": "running", "result": " Pass "}),
                         {"status": "RUNNING", "result": "PASS"})
        self.assertEqual(parse_update({"exit": "0"}), {"status": "STOPPED", "result": "PASS"})
        self.assertEqual(parse_update({"exit": 3, "status": "waiting"}), {"status": "WAITING", "result": "FAIL"})
        self.assertEqual(parse_update({"info.size": "12 GB", "info": {"host": "nas"}, "timeout": "90"}),
                         {"info": {"host": "nas", "size": "12 GB"}, "timeout": 90})
        self.assertEqual(parse_update({"link": "https://x.org/a"}), {"link": "https://x.org/a"})
        self.assertEqual(parse_update({"link": ""}), {"link": None})
        for fields in ({"status": "done"}, {"result": "ok"}, {"exit": "x"}, {"exit": True}, {"timeout": -1},
                       {"timeout": "1h"}, {"link": "ftp://x"}, {"info": "x"}, {"info.status": "x"}, {"info.": "x"},
                       {"foo": 1}, {"info.x": "y" * 201}, {"info": {str(ix): "" for ix in range(21)}}):
            with self.assertRaises(EventError, msg=fields):
                parse_update(fields)
        self.assertEqual(check_path([" nas ", "backup"]), ["nas", "backup"])
        for path in ([], ["a", ""], ["a"] * 8, ["x" * 201], "a", [1]):
            with self.assertRaises(EventError, msg=path):
                check_path(path)

    def test_set(self):
        # Query string, token
        with self.assertLogs("laempli.web", "WARNING"):
            self.assertEqual(self.set("nas/backup?status=running", token=None).status_code, 401)
            self.assertEqual(self.set("nas/backup?status=running", token="bad").status_code, 401)
        res = self.set("nas/backup?status=running&timeout=90000")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["job"]["path"], ["generic", "nas", "backup"])
        self.assertEqual(self.state("nas", "backup"), ("RUNNING", "UNKNOWN"))
        # Form, partial update: status stays
        self.set("nas/backup", data={"result": "pass", "info.size": "12 GB"})
        self.assertEqual(self.state("nas", "backup"), ("RUNNING", "PASS"))
        # JSON, exit code
        self.set("nas/backup", json={"exit": 1, "link": "https://nas/log", "info": {"error": "disk full"}})
        job = self.jobs.get(["generic", "nas", "backup"])
        self.assertEqual((job["status"], job["result"], job["link"]), ("STOPPED", "FAIL", "https://nas/log"))
        self.assertEqual(list(job["info"]), ["updated", "timeout", "error"])
        self.assertEqual(job["info"]["timeout"], "25:00:00")
        # GET of a job
        self.assertEqual(self.client.get("/api/generic/nas/backup").get_json()["job"]["result"], "FAIL")
        self.assertEqual(self.client.get("/api/generic/nas/other").status_code, 404)
        self.assertEqual(list(self.client.get("/api/generic").get_json()["jobs"]), ['["nas", "backup"]'])
        # Errors with a text
        with self.assertLogs("laempli.web", "WARNING"):
            res = self.set("nas/backup?status=done")
        self.assertEqual(res.status_code, 400)
        self.assertIn("status must be one of", res.get_json()["error"])
        self.assertEqual(self.set("a/b/c/d/e/f/g/h?status=running").status_code, 400)
        self.assertEqual(self.set("a//b?status=running").status_code, 400)
        self.assertEqual(self.set("nas/backup", data="[1]", content_type="application/json").status_code, 400)
        # Delete
        with self.assertLogs("laempli.web", "WARNING"):
            self.assertEqual(self.client.delete("/api/generic/nas/backup").status_code, 401)
        self.assertEqual(self.client.delete("/api/generic/nas/backup", headers={"X-Laempli-Token": "tok"})
                         .status_code, 200)
        self.assertEqual(self.client.delete("/api/generic/nas/backup", headers={"X-Laempli-Token": "tok"})
                         .status_code, 404)
        self.assertEqual(self.jobs.all(), [])
        # No tokens: no check
        generic = Generic(os.path.join(self.datadir, "x"))
        client = create_app(self.devices, None, 15, None, Jobs(), None, generic).test_client()
        self.assertEqual(client.post("/api/generic/a?exit=0").status_code, 200)

    def test_timeout(self):
        now = time.time()
        self.generic.handle("set", {"path": ["cron", "daily"], "exit": 0, "timeout": 60}, now)
        self.assertEqual(self.generic.check_late(now + 30), False)
        job = self.generic.job(["cron", "daily"], now + 61)
        self.assertEqual((job["status"], job["result"], job["info"]["status"]), ("UNKNOWN", "PASS", "late"))
        self.assertEqual(self.generic.check_late(now + 61), True)
        self.assertEqual(self.generic.check_late(now + 62), False)
        # Next update: not late; timeout 0: never late
        self.generic.handle("set", {"path": ["cron", "daily"], "exit": 0}, now + 70)
        self.assertEqual(self.generic.job(["cron", "daily"], now + 100)["status"], "STOPPED")
        self.assertEqual(self.generic.check_late(now + 100), True)
        self.generic.handle("set", {"path": ["cron", "daily"], "timeout": 0}, now + 101)
        self.assertEqual(self.generic.job(["cron", "daily"], now + 1e6)["status"], "STOPPED")
        # Thread calls the function when the late jobs change
        self.generic.handle("set", {"path": ["cron", "fast"], "timeout": 1}, time.time() - 2)
        called = threading.Event()
        self.generic.start(called.set, interval=0.05)
        self.assertTrue(called.wait(2))
        self.generic.stop()
        self.assertIsNone(self.generic.thread)

    def test_replay_expire(self):
        self.set("nas/backup?status=running")
        self.set("nas/backup?exit=0&info.size=1")
        self.set("cron/x?exit=1")
        self.client.delete("/api/generic/cron/x", headers={"X-Laempli-Token": "tok"})
        generic = Generic(self.datadir)
        self.assertEqual(generic.load(), 4)
        self.assertEqual(generic.get(), self.generic.get())
        self.assertEqual([entry["action"] for entry in generic.events()], ["delete", "set", "set", "set"])
        self.assertEqual(generic.events()[1]["client"], "127.0.0.1")
        later = time.time() + (91 * 86400)
        self.assertEqual(self.generic.expire_jobs(), 0)
        self.assertEqual(self.generic.expire_jobs(later), 1)
        self.assertEqual(self.generic.get()["jobs"], {})
        self.assertEqual(self.generic.expire_events(later), 4)

    def test_plots(self):
        self.set("nas/backup?status=running")
        self.set("nas/backup?exit=1")
        self.set("nas/backup?timeout=60")
        self.client.delete("/api/generic/nas/backup", headers={"X-Laempli-Token": "tok"})
        res = self.client.get("/api/plots").get_json()
        self.assertEqual([(item["path"], item["kind"]) for item in res["series"]],
                         [(["generic", "nas", "backup"], "job")])
        self.assertEqual([res["states"][point[1]] for point in res["series"][0]["points"]], ["running", "failed"])
        self.assertEqual(self.client.get("/api/plots?kind=pipelines").get_json()["series"], [])

    def test_web_leds(self):
        self.set("nas/backup?status=running&timeout=5400")
        page = self.client.get("/web/generic").data.decode()
        rows = "".join(self.client.get("/web/generic/rows").get_json()["rows"])
        self.assertNotIn("Scripts set the jobs", page)
        self.assertIn("nas / backup", rows)
        self.assertIn('<td class="text-end">1:30:00</td>', rows)
        self.assertIn(">Generic</a>", page)
        self.assertIn("generic → nas → backup", self.client.get("/web/jobs").data.decode())
        self.devices.connect({"id": DEV, "leds": 2})
        res = self.client.post("/api/leds/" + DEV, json=[{"path": ["generic", "nas", "backup"], "name": "Backup"}])
        self.assertEqual(res.status_code, 200)
        self.set("nas/backup?exit=0")
        led = self.devices.get_state(DEV)[0]["leds"][0]
        self.assertEqual((led["label"], led["status"], led["result"]), ("Backup", "STOPPED", "PASS"))
        self.assertIn('"nas"', self.client.get("/web/device/" + DEV).data.decode())


class TestJobsTest(unittest.TestCase):

    def test_jobs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = TestJobs(tmpdir).jobs()
            self.assertEqual([entry["path"] for entry in entries],
                             [["test", "test{:02d}".format(num)] for num in range(1, 11)])
            for entry in entries:
                self.assertEqual((entry["status"], entry["result"], entry["time"], entry["info"]),
                                 ("STOPPED", "UNKNOWN", None, {}))
            jobs = Jobs()
            self.assertTrue(jobs.update("test", entries))
            self.assertEqual(len(jobs.tree()["test"]["children"]), 10)

    def test_change(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test = TestJobs(tmpdir)
            jobs = Jobs()
            jobs.update("test", test.jobs())
            devices = Devices(tmpdir)
            devices.connect({"id": DEV, "leds": 2})
            client = create_app(devices, None, 15, None, jobs, testjobs=test).test_client()
            res = client.post("/api/leds/" + DEV, json=[{"path": ["test", "test01"]}])
            self.assertEqual(res.status_code, 200)

            def led():
                state = devices.get_state(DEV)[0]["leds"][0]
                return (state["label"], state["status"], state["result"])

            self.assertEqual(led(), ("test01", "STOPPED", "UNKNOWN"))
            # Each field alone, the other one stays
            res = client.post("/web/test/test01", data={"status": "RUNNING"})
            self.assertEqual(res.status_code, 302)
            self.assertEqual(led(), ("test01", "RUNNING", "UNKNOWN"))
            client.post("/web/test/test01", data={"result": "FAIL"})
            self.assertEqual(led(), ("test01", "RUNNING", "FAIL"))
            self.assertEqual(jobs.get(["test", "test02"])["time"], None)
            self.assertIsNotNone(jobs.get(["test", "test01"])["time"])
            # Bad requests change nothing
            self.assertEqual(client.post("/web/test/test01", data={"status": "BAD"}).status_code, 400)
            self.assertEqual(client.post("/web/test/test01").status_code, 400)
            self.assertEqual(client.post("/web/test/nope", data={"status": "RUNNING"}).status_code, 404)
            self.assertEqual(led(), ("test01", "RUNNING", "FAIL"))
            # Both fields in one event (Send button of the page)
            client.post("/web/test/test01", data={"result": "PASS", "status": "WAITING"})
            self.assertEqual(led(), ("test01", "WAITING", "PASS"))
            self.assertEqual(test.raw_events()[-1]["data"],
                             {"name": "test01", "result": "PASS", "status": "WAITING", "user": None})
            # State stays after a restart
            again = TestJobs(tmpdir)
            self.assertEqual(again.load(), 3)
            self.assertEqual(again.jobs(), test.jobs())

    def test_max_age(self):
        # A changed job gets outdated as a real job
        with tempfile.TemporaryDirectory() as tmpdir:
            test = TestJobs(tmpdir)
            test.handle("set", {"name": "test01", "status": "RUNNING"}, now=(time.time() - 7200))
            jobs = Jobs()
            jobs.update("test", test.jobs())
            devices = Devices(tmpdir)
            devices.connect({"id": DEV, "leds": 1})
            devices.set_config(DEV, {"leds": check_config([{"path": ["test", "test01"], "max_age": "1h"}])})
            update_states(devices, jobs)
            self.assertEqual(devices.get_state(DEV)[0]["leds"][0]["status"], "UNKNOWN")

    def test_page(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test = TestJobs(tmpdir)
            test.handle("set", {"name": "test03", "result": "WARN"})
            jobs = Jobs()
            client = create_app(Devices(tmpdir), None, 15, None, jobs, testjobs=test).test_client()
            page = client.get("/web/test").data.decode()
            self.assertIn('data-view="test"', page)
            self.assertIn("test → test10", page)
            self.assertIn('action="/web/test/test03"', page)
            self.assertIn('class="btn btn-warning"', page)
            self.assertIn('<tr data-job="test03" data-result="WARN" data-status="STOPPED">', page)
            self.assertIn('<input type="hidden" name="result" value="WARN">', page)
            self.assertIn('<button type="submit" class="btn btn-sm btn-outline-secondary test-send" disabled', page)
            self.assertIn('data-field="status" data-value="RUNNING" data-cls="primary"', page)
            self.assertIn('table table-sm table-hover align-middle w-auto', page)
            self.assertIn('<th>Change</th>', page)
            self.assertIn('<button type="button" class="btn btn-sm btn-outline-secondary test-cancel" disabled', page)
            self.assertIn('>Test</a>', page)
            client = create_app(Devices(tmpdir), None, 15, None, jobs).test_client()
            self.assertEqual(client.get("/web/test").status_code, 404)
            self.assertEqual(client.post("/web/test/test01", data={"status": "RUNNING"}).status_code, 404)
            self.assertNotIn('>Test</a>', client.get("/web/devices").data.decode())


class LedConfigTest(unittest.TestCase):

    def test_check_config(self):
        self.assertEqual(check_config([]), [])
        self.assertEqual(check_config([None, {"path": [" gitlab ", "a/b", " main"]}, None, {"path": []},
                                       {"path": None, "name": "x"}]),
                         [None, {"path": ["gitlab", "a/b", "main"], "name": ""}])
        for bad in ({}, "x", [1], [{"path": ["a"]}], [{"path": "a/b"}], [{"path": ["a", "b"], "foo": 1}],
                    [{"path": ["a", 1]}], [{"path": ["a", " "]}], [{"path": ["a", "b"], "name": "x" * 41}],
                    [{"path": ["a"] * 9}], [{"path": ["a", "b" * 201]}], [{"path": ["a", "b"], "name": 1}],
                    [None] * 65):
            with self.assertRaises(LedConfigError, msg=repr(bad)[:40]):
                check_config(bad)

    def test_durations(self):
        for text, value in (("", None), (None, None), (" ", None), (0, 0), ("0", 0), ("90", 90), ("90s", 90),
                            ("30m", 1800), ("1h30m", 5400), (" 2H ", 7200), ("3d", 259200), ("1d 2h", 93600),
                            (3600, 3600)):
            self.assertEqual(parse_duration(text), value, repr(text))
        for bad in ("x", "1x", "h", "-5", -1, True, 1.5, "1.5h", "367d", 367 * 86400):
            with self.assertRaises(ValueError, msg=repr(bad)):
                parse_duration(bad)
        for value, text in ((0, "0"), (45, "45s"), (3600, "1h"), (5400, "1h30m"), (259200, "3d"), (93784, "1d2h3m4s")):
            self.assertEqual(fmt_age(value), text)

    def test_config_max_age(self):
        self.assertEqual(check_config([{"path": ["a", "b"], "max_age": "2h"}, {"path": ["a", "c"], "max_age": 0},
                                       {"path": ["a", "d"], "max_age": ""}, {"path": ["a", "e"], "max_age": None}]),
                         [{"path": ["a", "b"], "name": "", "max_age": 7200},
                          {"path": ["a", "c"], "name": ""},
                          {"path": ["a", "d"], "name": ""}, {"path": ["a", "e"], "name": ""}])
        with self.assertRaisesRegex(LedConfigError, "LED 2: max. age"):
            check_config([None, {"path": ["a", "b"], "max_age": "soon"}])
        # Old timeouts: result timeout becomes max. age
        self.assertEqual(check_config([{"path": ["a", "b"], "status_timeout": 60, "result_timeout": "2d"},
                                       {"path": ["a", "c"], "status_timeout": 60}]),
                         [{"path": ["a", "b"], "name": "", "max_age": 172800}, {"path": ["a", "c"], "name": ""}])

    def test_state_max_age(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jobs = Jobs()
            now = 1792800000.0
            entries = [{"path": ["s", name], "status": status, "result": "PASS", "link": "https://x", "time": now - age,
                        "info": {"x": "1"}}
                       for name, status, age in (("run", "RUNNING", 3700), ("days", "STOPPED", 6 * 86400),
                                                 ("week", "STOPPED", (7 * 86400) + 1), ("ancient", "RUNNING", 9e6))]
            entries.append({"path": ["s", "none"], "status": "RUNNING", "result": "FAIL", "link": None, "time": None,
                            "info": {}})
            jobs.update("s", entries)
            names = ("run", "days", "week", "ancient", "none")

            def state(**fields):
                leds = device_state(jobs, {"leds": [dict({"path": ["s", name]}, **fields) for name in names]},
                                    now)["leds"]
                return {name: (led["status"], led["result"], led["info"].get("outdated")) for name, led in
                        zip(names, leds)}
            # Default: no limit
            self.assertEqual(state()["ancient"], ("RUNNING", "PASS", None))
            self.assertEqual(state(max_age=7 * 86400), {
                "run": ("RUNNING", "PASS", None), "days": ("STOPPED", "PASS", None),
                "week": ("UNKNOWN", "UNKNOWN", "no change for 7d"),
                "ancient": ("UNKNOWN", "UNKNOWN", "no change for 7d"),
                "none": ("RUNNING", "FAIL", None)})
            self.assertEqual(state(max_age=3600)["run"], ("UNKNOWN", "UNKNOWN", "no change for 1h"))
            # Outdated: as a job without data
            led = device_state(jobs, {"leds": [{"path": ["s", "ancient"], "name": "A", "max_age": 604800}]},
                               now)["leds"][0]
            self.assertEqual(led, {"label": "A", "path": ["s", "ancient"], "status": "UNKNOWN", "result": "UNKNOWN",
                                   "link": None, "time": None, "info": {"outdated": "no change for 7d"}})
            self.assertEqual(jobs.get(["s", "ancient"])["status"], "RUNNING")

    def test_web_max_age(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            devices = Devices(tmpdir)
            client = create_app(devices, None, 15, None, Jobs()).test_client()
            devices.connect({"id": DEV, "leds": 2})
            form = {"rows": "2", "path_0": '["s", "a"]', "name_0": "A", "max_age_0": "2h", "path_1": '["s", "b"]',
                    "max_age_1": "0"}
            res = client.post("/web/device/" + DEV, data=form)
            self.assertEqual(res.status_code, 302)
            self.assertEqual(devices.get_config(DEV)["leds"], [{"path": ["s", "a"], "name": "A", "max_age": 7200},
                                                               {"path": ["s", "b"], "name": ""}])
            page = client.get("/web/device/" + DEV).data.decode()
            self.assertIn('name="max_age_0"', page)
            self.assertIn('value="2h"', page)
            self.assertIn('placeholder="(e.g. 1h, 7d. empty = forever)"', page)
            self.assertNotIn("timeout", page.replace("dim_timeout", ""))
            res = client.post("/web/device/" + DEV, data=dict(form, max_age_0="soon"))
            self.assertEqual(res.status_code, 400)
            page = res.data.decode()
            self.assertIn("LED 1: max. age", page)
            self.assertIn('value="soon"', page)

    def test_config_sounds(self):
        # Empty sounds left out, names stripped
        self.assertEqual(check_config([{"path": ["a", "b"], "sounds": {"fail": " Tetris1 ", "pass": "", "fixed": "fx",
                                                                       "broken": None}},
                                       {"path": ["a", "c"], "sounds": {"pass": " "}}, {"path": ["a", "d"]}]),
                         [{"path": ["a", "b"], "name": "", "sounds": {"fail": "Tetris1", "fixed": "fx"}},
                          {"path": ["a", "c"], "name": ""}, {"path": ["a", "d"], "name": ""}])
        for sounds, error in (("x", "sounds must be an object"), ({"stop": "x"}, "unknown sound events stop"),
                              ({"fail": 1}, "sound fail must be a string"), ({"pass": "a b"}, "sound pass: max."),
                              ({"pass": "x" * 25}, "sound pass: max.")):
            with self.assertRaisesRegex(LedConfigError, "LED 2: " + error):
                check_config([None, {"path": ["a", "b"], "sounds": sounds}])
        # State: sounds of the LED config, only if set
        with tempfile.TemporaryDirectory() as tmpdir:
            state = device_state(Jobs(), {"leds": [{"path": ["a", "b"], "sounds": {"fail": "Tetris1"}},
                                                         {"path": ["a", "c"]}]})["leds"]
        self.assertEqual(state[0]["sounds"], {"fail": "Tetris1"})
        self.assertNotIn("sounds", state[1])

    def test_convert_config(self):
        self.assertEqual(convert_config([{"project": "a/b", "ref": "main", "downstream": "", "job": "x", "name": "N"},
                                         {"project": ""}, None, {"path": ["s", "t"]}]),
                         [{"path": ["gitlab", "a/b", "main", "x"], "name": "N"}, None, None, {"path": ["s", "t"]}])
        self.assertEqual(convert_config("x"), "x")
        self.assertEqual(convert_config([{"path": ["s", "t"], "status_timeout": 60, "result_timeout": 0},
                                         {"path": ["s", "u"], "result_timeout": 60, "max_age": 120},
                                         {"path": ["s", "v"], "result_timeout": ""}]),
                         [{"path": ["s", "t"], "max_age": 0}, {"path": ["s", "u"], "max_age": 120},
                          {"path": ["s", "v"]}])

    def test_label(self):
        self.assertEqual(led_label({"path": ["gitlab", "core/release-config", "main"]}), "release-config main")
        self.assertEqual(led_label({"path": ["gitlab", "core/release-config", "main", "yocto"]}),
                         "release-config main yocto")
        self.assertEqual(led_label({"path": ["gitlab", "a", "b"], "name": "Short"}), "Short")
        self.assertEqual(led_label({"path": ["gitlab", "a/b", "main", "y", "build"]}), "b main y build")


class ProjectRowsTest(unittest.TestCase):
    """Project rows: one row per project, one LED per pipeline, oldest first"""

    NOW = 1792800000.0
    ROWS_DEV = "staegge-b2c3d4"

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.devices = Devices(self.tmpdir.name)
        self.jobs = Jobs()
        app = create_app(self.devices, None, 15, None, self.jobs)
        self.client = app.test_client()

    def tearDown(self):
        self.tmpdir.cleanup()

    def job(self, path, age, started=None):
        return {"path": path, "status": "STOPPED", "result": "PASS", "link": None, "time": self.NOW - age,
                "started": (None if (started is None) else (self.NOW - started)), "info": {}}

    def row(self, row=None, row_len=3):
        row = ({"path": ["gitlab", "p"]} if (row is None) else row)
        config = {"leds": [], "row_len": row_len, "rows": [row]}
        return device_state(self.jobs, config, self.NOW)["leds"]

    def test_pipelines_only(self):
        self.jobs.update("gitlab", [self.job(["gitlab", "p", "main"], 10), self.job(["gitlab", "p", "main", "x"], 1),
                                    self.job(["gitlab", "other", "main"], 1)])
        self.jobs.update("github", [self.job(["github", "o/r", "dev", "ci"], 10),
                                    self.job(["github", "o/r", "dev", "ci", "build"], 1)])
        self.assertEqual([led and led["path"] for led in self.row()], [["gitlab", "p", "main"], None, None])
        github = self.row({"path": ["github", "o/r"]})
        self.assertEqual(github[0]["path"], ["github", "o/r", "dev", "ci"])
        self.assertEqual(github[0]["label"], "dev ci")

    def test_order(self):
        # By start of the run, not by the last change: a late event does not move it
        self.jobs.update("gitlab", [self.job(["gitlab", "p", "new"], 50, 100), self.job(["gitlab", "p", "old"], 1, 300),
                                    self.job(["gitlab", "p", "mid"], 60, 200)])
        self.assertEqual([led["label"] for led in self.row()], ["old", "mid", "new"])
        # New run: moves to the new end
        self.jobs.update("gitlab", [self.job(["gitlab", "p", "new"], 50, 100), self.job(["gitlab", "p", "old"], 1, 5),
                                    self.job(["gitlab", "p", "mid"], 60, 200)])
        self.assertEqual([led["label"] for led in self.row()], ["mid", "new", "old"])
        # No start time: last change
        self.jobs.update("gitlab", [self.job(["gitlab", "p", "a"], 5), self.job(["gitlab", "p", "b"], 50)])
        self.assertEqual([led and led["label"] for led in self.row()], ["b", "a", None])

    def test_full_row(self):
        self.jobs.update("gitlab", [self.job(["gitlab", "p", str(num)], num) for num in range(5)])
        self.assertEqual([led["label"] for led in self.row()], ["2", "1", "0"])

    def test_max_age(self):
        self.jobs.update("gitlab", [self.job(["gitlab", "p", "young"], 3600),
                                    self.job(["gitlab", "p", "week"], 8 * 86400)])
        self.assertEqual(self.row()[0]["label"], "young")
        self.assertEqual(self.row()[1], None)
        self.assertEqual(self.row({"path": ["gitlab", "p"], "max_age": 60}), [None, None, None])
        self.assertEqual(len([led for led in self.row({"path": ["gitlab", "p"], "max_age": 0}) if led]), 2)

    def test_no_timeouts(self):
        # Only the max. age: an old run keeps its status and result
        job = dict(self.job(["gitlab", "p", "main"], 5 * 86400), status="RUNNING")
        self.jobs.update("gitlab", [job])
        led = self.row({"path": ["gitlab", "p"], "max_age": 0})[0]
        self.assertEqual((led["status"], led["result"]), ("RUNNING", "PASS"))

    def test_check(self):
        config = check_rows([{"path": ["gitlab", " a/b "], "max_age": "2d"}, {"path": [], "max_age": ""}],
                            (4, 15))
        self.assertEqual(config, {"leds": [], "row_len": 15, "rows": [{"path": ["gitlab", "a/b"], "max_age": 172800},
                                                                      None, None, None]})
        for rows in ({}, [None] * 5, [1], [{"path": ["test", "test01"]}], [{"path": ["gitlab"]}],
                     [{"path": ["gitlab", "a", "b"]}], [{"path": ["gitlab", " "]}], [{"path": ["gitlab", "a"], "x": 1}],
                     [{"path": ["gitlab", "a"], "max_age": "soon"}]):
            with self.assertRaises(LedConfigError, msg=rows):
                check_rows(rows, (4, 15))

    def test_web(self):
        # The page uses the current time
        self.jobs.update("gitlab", [dict(self.job(["gitlab", "g/p", "main"], 0), time=time.time())])
        self.jobs.update("test", TestJobs(self.tmpdir.name).jobs())
        self.devices.connect({"id": self.ROWS_DEV, "leds": 60})
        url = "/web/device/" + self.ROWS_DEV
        page = self.client.get(url).data.decode()
        self.assertIn('value="gitlab:g/p"', page)
        self.assertNotIn("test:", page)
        self.assertNotIn("led-sounds", page)
        res = self.client.post(url, data={"project_1": "gitlab:g/p", "max_age_1": "soon"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("row 2: max. age", res.data.decode())
        self.assertEqual(self.client.post(url, data={"project_1": "gitlab:g/p", "max_age_1": "2d"}).status_code, 302)
        page = self.client.get(url).data.decode()
        self.assertIn('value="gitlab:g/p" selected', page)
        self.assertIn('value="2d"', page)
        state, _ = self.devices.get_state(self.ROWS_DEV)
        self.assertEqual(len(state["leds"]), 60)
        self.assertEqual(state["leds"][15]["path"], ["gitlab", "g/p", "main"])
        self.assertEqual(self.devices.get()[0]["configured"], 1)
        # API: same rows, other devices keep the LED list
        data = self.client.get("/api/leds/" + self.ROWS_DEV).get_json()
        self.assertEqual(data["config"][1], {"path": ["gitlab", "g/p"], "max_age": 172800})
        res = self.client.post("/api/leds/" + self.ROWS_DEV, json=[{"path": ["gitlab", "g/p"]}])
        self.assertEqual(res.get_json()["state"][0]["label"], "main")
        res = self.client.post("/api/leds/" + self.ROWS_DEV, json=[{"path": ["test", "x"]}])
        self.assertEqual(res.status_code, 400)

    def test_sources(self):
        # Sources give the start of the run
        self.assertEqual(unix_time("2026-10-22 10:00:36 UTC"), unix_time("2026-10-22T10:00:36Z"))
        self.assertIsNone(unix_time(None))
        self.assertIsNone(unix_time("soon"))


class ConfigTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmpdir.name, "test.yaml")

    def tearDown(self):
        self.tmpdir.cleanup()

    def load(self, text):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return load_config(self.path)

    def test_defaults(self):
        cfg = self.load("")
        self.assertEqual((cfg["port"], cfg["events_keep_days"]), (8070, 90))
        self.assertEqual((cfg["housekeeping_interval"], cfg["firmware_keep"], cfg["devices_keep_days"]),
                         (3600, 10, 365))
        self.assertEqual(self.load("devices_keep_days: 0\n")["devices_keep_days"], 0)
        self.assertEqual(cfg["data"], os.path.normpath(os.path.join(self.tmpdir.name, "..", "data")))

    def test_values(self):
        cfg = self.load("listen: 0.0.0.0\nport: 1234\ndata: x\nlog_level: debug\neditors: [flip]\n")
        self.assertEqual((cfg["listen"], cfg["port"], cfg["editors"]), ("0.0.0.0", 1234, ["flip"]))
        self.assertEqual(cfg["data"], os.path.join(self.tmpdir.name, "x"))
        self.assertEqual(cfg["device_editors"], {})
        cfg = self.load("device_editors:\n    laempli: ['gitta-87e94c', 'chloetzli-1ee4d5', 'gitta-87e94c']\n"
                        "    nobody: []\n")
        self.assertEqual(cfg["device_editors"], {"laempli": ["chloetzli-1ee4d5", "gitta-87e94c"], "nobody": []})
        self.assertEqual(self.load("device_editors:\n")["device_editors"], {})
        # Old prefix key: warning, ignored
        with self.assertLogs("laempli.config", "WARNING"):
            self.assertNotIn("prefix", self.load("prefix: /foo\n"))

    def test_errors(self):
        for text in ("port: x\n", "port: 0\n", "foo: 1\n", "log_level: x\n", "- a\n",
                     "editors: flip\n", "editors: ['']\n", "proxies: []\n",
                     "heartbeat: 0\n", "data: ''\n", "{bad\n", "firmware: x\n",
                     "gitlab_token: x\n", "gitlab_signing_token: []\n",
                     "gitlab_signing_tokens: whsec_YWJj\n", "gitlab_signing_tokens: [abc]\n",
                     "gitlab_signing_tokens: [1]\n", "gitlab_signing_tokens: {a: 1}\n", "events_keep_days: 0\n",
                     "events_keep_days: x\n", "events_keep_days: true\n", "housekeeping_interval: 59\n",
                     "housekeeping_interval: 1.5\n", "firmware_keep: 0\n", "devices_keep_days: -1\n",
                     "github_webhook_secrets: abc\n", "github_webhook_secrets: ['']\n",
                     "github_webhook_secrets: [1]\n", "generic_tokens: abc\n", "generic_tokens: ['']\n",
                     "device_editors: [a]\n", "device_editors: {a: gitta-87e94c}\n", "device_editors: {a: [x]}\n",
                     "device_editors: {1: [gitta-87e94c]}\n", "device_editors: {'': [gitta-87e94c]}\n"):
            with self.assertRaises(ConfigError, msg=text):
                self.load(text)

    def test_signing_tokens(self):
        self.assertEqual(self.load("")["gitlab_signing_tokens"], [])
        self.assertEqual(self.load("gitlab_signing_tokens:\n")["gitlab_signing_tokens"], [])
        self.assertEqual(self.load("gitlab_signing_tokens:\n    - whsec_YWJj\n")["gitlab_signing_tokens"],
                         ["whsec_YWJj"])
        self.assertEqual(len(self.load("gitlab_signing_tokens: [whsec_YWJj, whsec_eHl6]\n")["gitlab_signing_tokens"]),
                         2)

    def test_github_secrets(self):
        self.assertEqual(self.load("")["github_webhook_secrets"], [])
        self.assertEqual(self.load("github_webhook_secrets:\n")["github_webhook_secrets"], [])
        self.assertEqual(self.load("github_webhook_secrets: [abc, xyz]\n")["github_webhook_secrets"], ["abc", "xyz"])

    def test_ignored(self):
        cfg = self.load("")
        self.assertEqual((cfg["gitlab_ignored_projects"], cfg["github_ignored_repos"]), ([], []))
        cfg = self.load("gitlab_ignored_projects: [' a/b ', 'c/*']\ngithub_ignored_repos:\n")
        self.assertEqual((cfg["gitlab_ignored_projects"], cfg["github_ignored_repos"]), (["a/b", "c/*"], []))
        for text in ("gitlab_ignored_projects: a/b\n", "github_ignored_repos: ['']\n", "github_ignored_repos: [1]\n"):
            with self.assertRaises(ConfigError, msg=text):
                self.load(text)

    def test_generic_tokens(self):
        self.assertEqual(self.load("")["generic_tokens"], [])
        self.assertEqual(self.load("generic_tokens: [abc]\n")["generic_tokens"], ["abc"])

    def test_repo_config(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend",
                            "tschenggins-laempli.yaml.example")
        cfg = load_config(path)
        self.assertTrue(cfg["data"].endswith(os.sep + "data"))


class LogsTest(unittest.TestCase):

    def test_journal_format(self):
        fmt = JournalFormatter("%(name)s: %(message)s")
        rec = logging.LogRecord("x", logging.WARNING, __file__, 1, "a\nb", None, None)
        self.assertEqual(fmt.format(rec), "<4>x: a\n<4>b")
        rec = logging.LogRecord("x", logging.DEBUG, __file__, 1, "c", None, None)
        self.assertEqual(fmt.format(rec), "<7>x: c")

    def test_is_journal(self):
        with tempfile.TemporaryFile() as fh:
            st = os.fstat(fh.fileno())
            old = os.environ.get("JOURNAL_STREAM")
            try:
                os.environ["JOURNAL_STREAM"] = "{}:{}".format(st.st_dev, st.st_ino)
                self.assertTrue(is_journal(fh))
                os.environ["JOURNAL_STREAM"] = "1:2"
                self.assertFalse(is_journal(fh))
                del os.environ["JOURNAL_STREAM"]
                self.assertFalse(is_journal(fh))
                self.assertFalse(is_journal(io.StringIO()))
            finally:
                if old is not None:
                    os.environ["JOURNAL_STREAM"] = old


# Device settings report (as the firmware sends it)
SETTINGS = {"name": "Office", "dim_timeout": 60, "dim_level": 20, "saver_timeout": 60, "led_brightness": 64,
            "anim_running": 2, "anim_waiting": 1}
OPTIONS = {"led_brightness": [[16, "very dim"], [64, "medium"], [160, "brightest"]],
           "anim_running": ["sine", "triangle", "breathe"], "anim_waiting": ["fast sine", "blink"]}


class SettingsTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.datadir = os.path.join(self.tmpdir.name, "data")
        self.devices = Devices(self.datadir)
        app = create_app(self.devices, None, 15, None, Jobs())
        self.client = app.test_client()

    def tearDown(self):
        self.tmpdir.cleanup()

    def report(self, **fields):
        self.devices.report(DEV, dict({"device": DEV, "rssi": -50}, **fields))

    def settings(self):
        return self.client.get("/api/settings/" + DEV).get_json()

    def test_check(self):
        self.assertEqual(check_settings({"name": "  Buro x ", "dim_level": "30"}),
                         {"name": "Buro x", "dim_level": 30})
        self.assertEqual(check_settings({"name": "Stägge"}), {"name": "Stägge"})
        self.assertEqual(check_settings({"name": "ä" * 12 + "x"}), {"name": "ä" * 12 + "x"})
        for values in ({"name": "ä" * 13}, {"name": "\ud800"}, {"name": "a\u0080b"}, {"name": "a/b"},
                       {"name": "x" * 26}, {"dim_level": 101},
                       {"dim_timeout": -1}, {"led_brightness": 0}, {"saver_timeout": "1m"}, {"dim_level": True},
                       {"bogus": 1}, ["name"]):
            with self.assertRaises(SettingsError, msg=values):
                check_settings(values)
        # Animation index must be one of the device options
        self.assertEqual(check_settings({"anim_waiting": 1}, OPTIONS), {"anim_waiting": 1})
        with self.assertRaises(SettingsError):
            check_settings({"anim_waiting": 2}, OPTIONS)
        self.assertEqual(clean_report({"name": "a/b", "dim_level": 5, "x": 1}), {"dim_level": 5})
        self.assertEqual(clean_options({"led_brightness": [[16, "x"], "bad"], "anim_running": ["a"]}),
                         {"led_brightness": [[16, "x"]], "anim_running": ["a"]})

    def test_no_settings(self):
        # Device without settings (old firmware): no form, changes refused
        self.report()
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn('class="text-body-secondary">(none)</span> ({})</h1>'.format(DEV), page)
        self.assertIn('class="text-body-secondary">(none)</span>', self.client.get("/web/devices").data.decode())
        self.assertIn("No settings from the device yet", page)
        self.assertNotIn('id="settings_save"', page)
        res = self.client.post("/api/settings/" + DEV, json={"dim_level": 10})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.client.get("/api/settings/chlotz-000000").status_code, 404)

    def test_sync(self):
        self.report(settings=SETTINGS, options=OPTIONS, ack=0)
        data = self.settings()
        self.assertEqual((data["values"], data["options"], data["pending"]), (SETTINGS, OPTIONS, {}))
        self.assertIn("Office", self.client.get("/web/devices").data.decode())
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn("Office ({})</h1>".format(DEV), page)
        self.assertIn("<title>Lämpli Office ({})</title>".format(DEV), page)
        self.assertIn('id="settings_save"', page)
        self.assertIn('<option value="64" selected>medium (64)</option>', page)
        self.assertIn('<option value="2" selected>2 breathe</option>', page)
        self.assertIn("synced with device", page)

        # Web change: pending, sent on the stream
        res = self.client.post("/web/device/{}/settings".format(DEV),
                               data=dict(SETTINGS, name=" Kitchen ", dim_level="35"))
        self.assertEqual(res.status_code, 302)
        data = self.settings()
        self.assertEqual((data["pending"], data["seq"]), ({"name": "Kitchen", "dim_level": 35}, 1))
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn("not synced", page)
        self.assertIn("2 changes pending", page)
        self.assertIn("sent when the device connects", page)
        self.assertIn('value="Kitchen"', page)
        stream = open_stream(self.devices)
        events = [next(stream) for _ in range(3)]
        self.assertEqual(events[2], ("settings", {"seq": 1, "settings": {"dim_level": 35, "name": "Kitchen"}}))

        # Change while connected: sent at once (all pending values)
        self.client.post("/api/settings/" + DEV, json={"anim_waiting": 0})
        event, data = next(stream)
        self.assertEqual((event, data["seq"], len(data["settings"])), ("settings", 2, 3))

        # Device applied seq 1 only: still pending; seq 2: done
        self.report(settings=dict(SETTINGS, name="Kitchen", dim_level=35), ack=1)
        self.assertEqual(len(self.settings()["pending"]), 3)
        self.report(settings=dict(SETTINGS, name="Kitchen", dim_level=35, anim_waiting=0), ack=2)
        data = self.settings()
        self.assertEqual((data["pending"], data["values"]["name"]), ({}, "Kitchen"))
        stream.close()

        # Saved, also after load
        loaded = Devices(self.datadir)
        self.assertEqual(loaded.get_settings(DEV)["values"]["anim_waiting"], 0)
        self.assertEqual(loaded.get_settings(DEV)["options"], OPTIONS)

    def test_change_back(self):
        # Web change back to the device value: not pending any more; no change: no new seq
        self.report(settings=SETTINGS, options=OPTIONS)
        self.client.post("/api/settings/" + DEV, json={"dim_level": 50, "name": "X"})
        self.client.post("/api/settings/" + DEV, json={"dim_level": 20})
        data = self.settings()
        self.assertEqual((data["pending"], data["seq"]), ({"name": "X"}, 2))
        self.client.post("/api/settings/" + DEV, json={"name": "X"})
        self.assertEqual(self.settings()["seq"], 2)
        # Device change of a key: shown, pending one stays (web wins)
        self.report(settings=dict(SETTINGS, name="Y", dim_level=10), ack=0)
        data = self.settings()
        self.assertEqual((data["values"]["dim_level"], data["pending"]), (10, {"name": "X"}))
        loaded = Devices(self.datadir)
        self.assertEqual(loaded.get_pending(DEV), (2, {"name": "X"}))

    def test_errors(self):
        self.report(settings=SETTINGS, options=OPTIONS)
        res = self.client.post("/web/device/{}/settings".format(DEV), data=dict(SETTINGS, name="a/b"))
        self.assertEqual(res.status_code, 400)
        page = res.data.decode()
        self.assertIn("Not saved: Name: max. 25 bytes", page)
        self.assertIn('value="a/b"', page)
        res = self.client.post("/api/settings/" + DEV, json={"anim_running": 3})
        self.assertEqual(res.status_code, 400)
        self.assertIn("0-2", res.get_json()["error"])
        self.assertEqual(self.client.post("/api/settings/" + DEV, json=[1]).status_code, 400)
        self.assertEqual(self.settings()["pending"], {})
        # Value not in the options (device has an other one): extra option
        self.report(settings=dict(SETTINGS, led_brightness=100))
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn('<option value="100" selected>100 (other)</option>', page)

    def test_hello(self):
        # Hello: name, ring and bow brightness with options
        bright = [[16, "very dim"], [128, "very bright"]]
        self.report(settings={"name": "Hello", "ring_brightness": 128, "bow_brightness": 50},
                    options={"ring_brightness": bright, "bow_brightness": bright, "anim_running": ["x"]})
        data = self.settings()
        self.assertEqual(data["values"], {"name": "Hello", "ring_brightness": 128, "bow_brightness": 50})
        self.assertEqual(data["options"], {"ring_brightness": bright, "bow_brightness": bright, "anim_running": ["x"]})
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn('<option value="128" selected>very bright (128)</option>', page)
        self.assertIn('<option value="50" selected>50 (other)</option>', page)
        self.assertNotIn('id="set_led_brightness"', page)
        res = self.client.post("/api/settings/" + DEV, json={"bow_brightness": 0})
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/api/settings/" + DEV, json={"bow_brightness": 16})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.settings()["pending"], {"bow_brightness": 16})

    def test_chloetzli(self):
        # Chlötzli: name and LED settings, melodies, no sound effect
        bright = [[16, "very dim"], [64, "medium"]]
        self.report(settings={"name": "Chlötzli", "led_brightness": 64, "anim_running": 3, "anim_waiting": 8},
                    options={"led_brightness": bright, "anim_running": ["a"] * 12, "anim_waiting": ["b"] * 12,
                             "melodies": ["Bond:7"]})
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn('id="set_led_brightness"', page)
        self.assertIn('id="set_anim_running"', page)
        self.assertNotIn('id="set_dim_level"', page)
        self.assertIn('<option value="Bond">Bond (7s)</option>', page)
        self.assertNotIn('<option value="fx">', page)

    def test_led_reverse(self):
        # Setting of all device types: select off, on; pending; only 0 or 1
        self.report(settings={"name": "x", "led_reverse": 0})
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn('id="set_led_reverse"', page)
        self.assertIn('<option value="1">on</option>', page)
        self.assertEqual(self.client.post("/api/settings/" + DEV, json={"led_reverse": 2}).status_code, 400)
        self.assertEqual(self.client.post("/api/settings/" + DEV, json={"led_reverse": 1}).status_code, 200)
        self.assertEqual(self.settings()["pending"], {"led_reverse": 1})
        self.report(settings={"name": "x", "led_reverse": 1}, ack=self.settings()["seq"])
        self.assertEqual(self.settings()["values"]["led_reverse"], 1)

    def test_sound_test(self):
        # Sound button: only sounds of the device, only with a stream
        self.report(settings={"name": "x"}, options={"melodies": ["Bond:7"]})
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn('class="btn btn-outline-secondary sound-test"', page)
        url = "/api/device/{}/sound".format(DEV)
        self.assertEqual(self.client.post(url, json={"sound": "Bond"}).status_code, 409)
        self.assertEqual(self.client.post(url, json={"sound": "Nope"}).status_code, 400)
        self.assertEqual(self.client.post(url, json={}).status_code, 400)
        stream = open_stream(self.devices)
        self.assertEqual(next(stream)[0], "hello")
        self.assertEqual(next(stream)[0], "state")
        self.assertEqual(self.client.post(url, json={"sound": "Bond"}).status_code, 200)
        self.assertEqual(next(stream), ("sound", {"sound": "Bond"}))
        stream.close()
        self.assertEqual(self.devices.pop_commands(DEV), [])

    def test_led_test(self):
        # LED test buttons: only with the firmware option, only with a stream; find: LED index; badge from the report
        url = "/api/device/{}/led".format(DEV)
        self.report(settings={"name": "x"})
        self.assertNotIn("data-led-test=", self.client.get("/web/device/" + DEV).data.decode())
        self.assertEqual(self.client.post(url, json={"mode": "red"}).status_code, 409)
        self.report(settings={"name": "x"}, options={"led_test": True})
        self.assertEqual(self.settings()["options"], {"led_test": True})
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn('data-led-test="find"', page)
        self.assertIn('data-led-test="red" disabled>', page)
        self.assertEqual(self.client.post(url, json={"mode": "red"}).status_code, 409)
        for data in ({"mode": "pink"}, {}, ["red"], {"mode": "find"}, {"mode": "find", "led": -1},
                     {"mode": "find", "led": True}, {"mode": "find", "led": 64}):
            self.assertEqual(self.client.post(url, json=data).status_code, 400, msg=data)
        stream = open_stream(self.devices, query="device={}&leds=4".format(DEV))
        self.assertEqual(next(stream)[0], "hello")
        self.assertEqual(next(stream)[0], "state")
        self.assertNotIn("data-led-test=\"red\" disabled", self.client.get("/web/device/" + DEV).data.decode())
        self.assertEqual(self.client.post(url, json={"mode": "find", "led": 4}).status_code, 400)
        self.assertEqual(self.client.post(url, json={"mode": "find", "led": 2}).status_code, 200)
        self.assertEqual(next(stream), ("led", {"mode": "find", "first": 2, "count": 1}))
        self.assertEqual(self.client.post(url, json={"mode": "demo", "led": 9}).status_code, 200)
        self.assertEqual(next(stream), ("led", {"mode": "demo"}))
        # Badge from the report, gone after the test and without connection
        self.report(led_test={"mode": "find", "first": 2, "count": 1})
        self.assertIn("LED test:\n                    LED 3</span>",
                      self.client.get("/web/device/" + DEV).data.decode())
        self.report(led_test={"mode": "demo"})
        self.assertIn("rainbow</span>", self.client.get("/web/device/" + DEV).data.decode())
        self.report(led_test={"mode": "bad"})
        self.assertNotIn("LED test:", self.client.get("/web/device/" + DEV).data.decode())
        self.report(led_test={"mode": "red"})
        stream.close()
        self.assertNotIn("LED test:", self.client.get("/web/device/" + DEV).data.decode())
        self.assertEqual(clean_led_test({"mode": "find", "first": 15, "count": 15}),
                         {"mode": "find", "first": 15, "count": 15})
        self.assertIsNone(clean_led_test({"mode": "red", "first": -1}))
        self.assertIsNone(clean_led_test("red"))
        # Project rows: find a row
        rows_dev = "staegge-b2c3d4"
        self.devices.report(rows_dev, {"device": rows_dev, "options": {"led_test": True}})
        stream = open_stream(self.devices, query="device={}&leds=60".format(rows_dev))
        self.assertEqual(next(stream)[0], "hello")
        self.assertEqual(next(stream)[0], "state")
        rows_url = "/api/device/{}/led".format(rows_dev)
        self.assertEqual(self.client.post(rows_url, json={"mode": "find", "row": 4}).status_code, 400)
        self.assertEqual(self.client.post(rows_url, json={"mode": "find", "row": 1}).status_code, 200)
        self.assertEqual(next(stream), ("led", {"mode": "find", "first": 15, "count": 15}))
        self.devices.report(rows_dev, {"device": rows_dev, "led_test": {"mode": "find", "first": 15, "count": 15}})
        self.assertIn("row 2</span>", self.client.get("/web/device/" + rows_dev).data.decode())
        stream.close()

    def test_sounds(self):
        # Sound options: melodies (bad names left out), sound module
        self.assertEqual(clean_options({"melodies": ["Tetris1", "a b", 1, "x" * 25, "Bad:x", "Bad:", "Bad:²"],
                                        "sound_fx": 1}),
                         {"melodies": ["Tetris1"]})
        self.assertEqual(clean_options({"melodies": ["Tetris1:40", "Bond"]}),
                         {"melodies": ["Tetris1", "Bond"], "melody_secs": {"Tetris1": 40}})
        self.report(settings={"name": "Hello"}, options={"melodies": ["Tetris1:40", "Bond"], "sound_fx": True})
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn('<select class="form-select" name="sound_broken_0"\n'
                      '                                    id="sound_broken_0">', page)
        self.assertIn('<option value="fx">Sound effect</option>', page)
        self.assertIn('<option value="Bond">Bond</option>', page)
        self.assertIn('<option value="Tetris1">Tetris1 (40s)</option>', page)
        # Form: sounds saved, state has them
        form = {"rows": "2", "path_0": json.dumps(["gitlab", "a/b", "main"]), "sound_fail_0": "Bond",
                "sound_pass_0": "", "sound_broken_0": "fx", "sound_fixed_0": "Tetris1", "sound_fail_1": "Bond"}
        self.assertEqual(self.client.post("/web/device/" + DEV, data=form).status_code, 302)
        sounds = {"fail": "Bond", "broken": "fx", "fixed": "Tetris1"}
        self.assertEqual(self.devices.get_config(DEV)["leds"], [{"path": ["gitlab", "a/b", "main"], "name": "",
                                                                 "sounds": sounds}])
        self.assertEqual(self.devices.get_state(DEV)[0]["leds"][0]["sounds"], sounds)
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn('<option value="Bond" selected>Bond</option>', page)
        self.assertIn('<option value="fx" selected>Sound effect</option>', page)
        # Device without the sound: extra option
        self.report(settings={"name": "Hello"}, options={"melodies": ["Bond"]})
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn('<option value="fx" selected>fx (not on device)</option>', page)
        self.assertNotIn('<option value="fx">Sound effect</option>', page)
        # Bad sound: not saved, error
        res = self.client.post("/web/device/" + DEV, data=dict(form, sound_fail_0="a b"))
        self.assertEqual(res.status_code, 400)
        self.assertIn("LED 1: sound fail: max.", res.data.decode())

    def test_name_only(self):
        # Device with only the name (hello): only this row, other keys refused
        self.report(settings={"name": "Hello"}, options={})
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertIn('id="set_name"', page)
        self.assertNotIn('id="set_dim_timeout"', page)
        self.assertNotIn("never dim", page)
        res = self.client.post("/api/settings/" + DEV, json={"dim_level": 10})
        self.assertEqual(res.status_code, 400)
        self.assertIn("no setting dim_level", res.get_json()["error"])
        res = self.client.post("/web/device/{}/settings".format(DEV), data={"name": "Hello 2"})
        self.assertEqual(res.status_code, 302)
        self.assertEqual(self.settings()["pending"], {"name": "Hello 2"})


class LedViewTest(unittest.TestCase):
    """Device page: LEDs as on the device, fields of one LED"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.devices = Devices(self.tmpdir.name)
        self.jobs = Jobs()
        self.jobs.update("test", TestJobs(self.tmpdir.name).jobs())
        self.client = create_app(self.devices, None, 15, None, self.jobs).test_client()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_arrangement(self):
        rev = (lambda value: {"values": {"led_reverse": value}})
        self.assertEqual(led_view("chlotz-1", None, 12), {"cols": 3, "order": list(range(12))})
        self.assertEqual(led_view("chlotz-1", rev(1), 3)["order"], [0, 1, 2])
        # Chlötzli: LED 1 at the top with led_reverse 1
        self.assertEqual(led_view("chloetzli-1", rev(1), 3), {"cols": 1, "order": [0, 1, 2]})
        self.assertEqual(led_view("chloetzli-1", rev(0), 3)["order"], [2, 1, 0])
        self.assertEqual(led_view("chloetzli-1", None, 3)["order"], [0, 1, 2])
        # Chischtli: as the Chlötzli
        self.assertEqual(led_view("chischtli-1", rev(1), 3), {"cols": 1, "order": [0, 1, 2]})
        self.assertEqual(led_view("chischtli-1", rev(0), 3)["order"], [2, 1, 0])
        self.assertEqual(led_view("staegge-1", rev(1), 3)["order"], [2, 1, 0])
        self.assertEqual(led_view("staegge-1", rev(0), 3)["order"], [0, 1, 2])
        # Gitta: 2 columns, turned by 180 deg with led_reverse 1
        self.assertEqual(led_view("gitta-1", rev(0), 6), {"cols": 2, "order": [0, 1, 2, 3, 4, 5]})
        self.assertEqual(led_view("gitta-1", rev(1), 6)["order"], [5, 4, 3, 2, 1, 0])
        self.assertEqual(led_view("hello-1", None, 1), {"cols": 1, "order": [0]})
        self.assertEqual(led_view("other-1", None, 2)["cols"], 4)

    def test_leds_page(self):
        dev = "chloetzli-a1b2c3"
        self.devices.connect({"id": dev, "leds": 3})
        self.devices.report(dev, {"device": dev, "rssi": -50, "settings": {"name": "x", "led_reverse": 0}})
        self.client.post("/api/leds/" + dev, json=[None, {"path": ["test", "test01"], "name": "Nightly"}])
        page = self.client.get("/web/device/" + dev).data.decode()
        self.assertIn('id="led_visual" style="--cols: 1"', page)
        tiles = re.findall(r'class="led-tile" data-ix="(\d+)"', page)
        self.assertEqual(tiles, ["2", "1", "0"])
        self.assertEqual(page.count('class="led-fields"'), 3)
        self.assertIn('<span class="led-tile-label">Nightly</span>', page)
        self.assertIn('data-live="tile-1"', page)
        self.assertIn('id="cancel"', page)

    def test_details(self):
        dev = "hello-a1b2c3"
        self.devices.connect({"id": dev, "leds": 1, "ssid": "Home", "rssi": -61})
        page = self.client.get("/web/device/" + dev).data.decode()
        details = page[page.index('id="details"'):page.index('id="ledform"')]
        for text in ('<dt>Status</dt><dd><span class="badge text-bg-success" data-dev-status="' + dev + '">connected</span></dd>',
                     "<dt>WiFi</dt><dd>Home</dd>", "<dt>Signal</dt><dd>-61 dBm</dd>", "<dt>LEDs</dt><dd>0/1</dd>"):
            self.assertIn(text, details)
        # Status only in the card, not in the title
        self.assertEqual(page.count(">connected</span>"), 1)

    def test_dashboard(self):
        now = time.time()
        self.jobs.update("gitlab", [{"path": ["gitlab", "g/p", "main"], "status": "RUNNING", "result": "FAIL",
                                     "link": "https://gl/g/p/-/pipelines/7", "time": now, "started": None, "info": {}},
                                    {"path": ["gitlab", "g/p", "old"], "status": "STOPPED", "result": "PASS",
                                     "link": None, "time": now - 100, "started": None, "info": {}}])
        page = self.client.get("/web/dashboard").data.decode()
        self.assertIn('data-view="dashboard"', page)
        self.assertIn(">Dashboard</a>", page.split("</nav>")[0])
        data = json.loads(re.search(r'<script type="application/json" id="dash_jobs">(.*?)</script>', page,
                                    re.S).group(1))
        self.assertEqual([job["path"][-1] for job in data[:2]], ["main", "old"])
        self.assertEqual(data[0]["linkText"], "Open GitLab pipeline")
        self.assertIsNone(data[1]["linkText"])
        # Pipelines have "started", the test jobs not
        self.assertTrue(data[0]["pipeline"])
        self.assertFalse(any(job["pipeline"] for job in data if (job["path"][0] == "test")))
        # Details of one job (zoomed tile)
        url = "/api/job?path=" + urllib.parse.quote(json.dumps(["gitlab", "g/p", "main"]))
        job = self.client.get(url).get_json()
        self.assertEqual((job["result"], job["info"], job["linkText"]), ("FAIL", {}, "Open GitLab pipeline"))
        self.assertEqual(self.client.get("/api/job?path=" + urllib.parse.quote('["gitlab", "g/p"]')).status_code, 404)
        for bad in ("", "x", "[]", "[1]", '{"a": 1}'):
            self.assertEqual(self.client.get("/api/job?path=" + urllib.parse.quote(bad)).status_code, 400, bad)
        # Jobs without time (test jobs) last
        self.assertTrue(all(job["time"] is None for job in data[2:]) or (len(data) == 2))

    def test_device_tabs(self):
        self.devices.connect({"id": "hello-a1b2c3", "leds": 1})
        self.devices.connect({"id": "chlotz-a1b2c3", "leds": 15})
        self.devices.report("hello-a1b2c3", {"device": "hello-a1b2c3", "settings": {"name": "Hi"}})
        tabs = r'<a class="nav-link( active)?"[^>]*\s+href="/web/(device/[^"]+|devices)"[^>]*>([^<]*)</a>'
        page = self.client.get("/web/devices").data.decode()
        self.assertEqual(re.findall(tabs, page), [(" active", "devices", "Devices"), (" active", "devices", "Overview"),
                                                  ("", "device/chlotz-a1b2c3", "chlotz-a1b2c3"),
                                                  ("", "device/hello-a1b2c3", "Hi")])
        page = self.client.get("/web/device/hello-a1b2c3").data.decode()
        self.assertEqual(re.findall(tabs, page)[2:], [("", "device/chlotz-a1b2c3", "chlotz-a1b2c3"),
                                                      (" active", "device/hello-a1b2c3", "Hi")])
        self.assertIn('"devices"', re.search(r"data-topics='([^']*)'", page).group(1))

    def test_rows_page(self):
        dev = "staegge-a1b2c3"
        self.jobs.update("gitlab", [{"path": ["gitlab", "g/p", "main"], "status": "RUNNING", "result": "PASS",
                                     "link": None, "time": time.time(), "started": None, "info": {}}])
        self.devices.connect({"id": dev, "leds": 60})
        self.client.post("/web/device/" + dev, data={"project_1": "gitlab:g/p"})
        page = self.client.get("/web/device/" + dev).data.decode()
        self.assertEqual(page.count('class="led-row"'), 4)
        self.assertEqual(len(re.findall(r'class="led-cell[ "]', page)), 60)
        self.assertIn('<span class="badge narrow-badge text-bg-success">P</span>'
                      '<span class="badge narrow-badge text-bg-primary">R</span>', page)
        self.assertIn('data-bs-title="LED 1: main\nPASS, RUNNING"', page)
        self.assertEqual(page.count('class="led-fields row-fields"'), 4)


class DashboardConfigTest(unittest.TestCase):
    """Dashboard: sticky jobs (config file, API, page)"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.changes = Changes()
        self.config = DashboardConfig(self.tmpdir.name, self.changes)
        app = create_app(Devices(self.tmpdir.name), jobs=Jobs(), editors=["editor"],
                         dashboard=self.config)
        self.client = app.test_client()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_check(self):
        # Paths alone (first format) or entries with name; duplicate paths removed, names stripped
        entries = [["a", "b"], {"path": ["a", "b"], "name": "x"}, {"path": ["c", "d"], "name": " N "}]
        self.assertEqual(check_sticky(entries), [{"path": ["a", "b"], "name": ""}, {"path": ["c", "d"], "name": "N"}])
        too_many = [["a", str(ix)] for ix in range(31)]
        for bad in (None, "x", [["a"]], [["a", ""]], [["a", 1]], too_many, [list("abcdefghij")], [{"name": "x"}],
                    [{"path": ["a", "b"], "name": "x" * 41}], [{"path": ["a", "b"], "name": 3}], [3]):
            with self.assertRaises(DashboardError, msg=repr(bad)[:40]):
                check_sticky(bad)

    def test_api(self):
        self.assertEqual(self.client.get("/api/dashboard").get_json(), {"sticky": []})
        body = {"sticky": [{"path": ["gitlab", "g/p", "main"], "name": "Main"},
                           {"path": ["generic", "nas", "backup"], "name": ""}]}
        self.assertEqual(self.client.post("/api/dashboard", json=body).status_code, 403)
        headers = {"X-Forwarded-User": "editor"}
        rev = self.changes.rev
        self.assertEqual(self.client.post("/api/dashboard", json=body, headers=headers).get_json(), body)
        self.assertGreater(self.changes.rev, rev)
        self.assertEqual(self.client.post("/api/dashboard", json={"sticky": [["x"]]}, headers=headers).status_code,
                         400)
        # Saved: a new config has it, the page too
        self.assertEqual(DashboardConfig(self.tmpdir.name).sticky(), body["sticky"])
        page = self.client.get("/web/dashboard").data.decode()
        self.assertEqual(json.loads(re.search(r'<script type="application/json" id="dash_sticky">(.*?)</script>', page,
                                              re.S).group(1)), body["sticky"])
        self.assertIn('id="dash_config_open"', page)
        self.assertIn('"dashboard"', re.search(r"data-topics='([^']*)'", page).group(1))

    def test_old_file(self):
        # First format: paths alone
        with open(os.path.join(self.tmpdir.name, "dashboard.json"), "w", encoding="utf-8") as fh:
            json.dump({"sticky": [["gitlab", "g/p", "main"]]}, fh)
        self.assertEqual(DashboardConfig(self.tmpdir.name).sticky(), [{"path": ["gitlab", "g/p", "main"], "name": ""}])

    def test_bad_file(self):
        with open(os.path.join(self.tmpdir.name, "dashboard.json"), "w", encoding="utf-8") as fh:
            fh.write("{bad")
        self.assertEqual(DashboardConfig(self.tmpdir.name).sticky(), [])


@unittest.skipIf(docs_module.markdown is None, "python3-markdown not installed")
class DocsTest(unittest.TestCase):
    """Docs tab: Markdown docs as HTML, links, images"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        top = self.tmpdir.name
        os.makedirs(os.path.join(top, "sub"))
        with open(os.path.join(top, "A.md"), "w", encoding="utf-8") as fh:
            fh.write("# Top\n\n## 3.3 V supply (old)\n\nSee [b](sub/b.md#more-stägge), [here](#33-v-supply-old), "
                     "[tools](tools/tools.md), [web](https://example.com).\n\n![pic](sub/pic.jpg) ![no](sub/x.jpg)\n\n"
                     "| a | b |\n|---|---|\n| 1 | 2 |\n")
        with open(os.path.join(top, "sub", "b.md"), "w", encoding="utf-8") as fh:
            fh.write("# B\n\n## More: Stägge\n\n[back](../A.md)\n")
        with open(os.path.join(top, "sub", "pic.jpg"), "wb") as fh:
            fh.write(b"jpg")
        self.docs = Docs(top, (("a", "A.md", "A"), ("b", "sub/b.md", "B"), ("c", "missing.md", "C")))
        self.client = create_app(Devices(os.path.join(top, "data")), docs=self.docs).test_client()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_slug(self):
        self.assertEqual(github_slug("3.3 V supply (old)"), "33-v-supply-old")
        self.assertEqual(github_slug("Pass → fail"), "pass--fail")
        self.assertEqual(github_slug("Stägge: <code>led</code>"), "stägge-led")

    def test_render(self):
        body = self.docs.html("a", "/laempli")
        self.assertIn('<h2 id="33-v-supply-old">', body)
        self.assertIn('<a href="/laempli/web#/docs/b?sec=more-stägge">b</a>', body)
        self.assertIn('<a href="/laempli/web#/docs/a?sec=33-v-supply-old">here</a>', body)
        self.assertIn('<a class="doc-nolink" title="Not in the docs">tools</a>', body)
        self.assertIn('<a target="_blank" rel="noopener" href="https://example.com">web</a>', body)
        self.assertIn('src="/laempli/web/docs/file/sub/pic.jpg"', body)
        self.assertIn('src="sub/x.jpg"', body)
        self.assertIn("<table>", body)
        self.assertIn('<a href="/web#/docs/a">back</a>', self.docs.html("b", ""))
        self.assertIsNone(self.docs.html("c", ""))
        self.assertEqual(self.docs.files, {"sub/pic.jpg"})

    def test_web(self):
        page = self.client.get("/web/docs").data.decode()
        self.assertIn('<h1 id="top">Top</h1>', page)
        self.assertIn('class="navbar-nav sub-tabs doc-tabs"', page)
        self.assertRegex(page, r'doc-tabs" data-live="sub-tabs">\s*<li class="nav-item">\s*<a class="nav-link active" '
                               r'aria-current="page"\s*href="/web/docs/a" title="A.md">A</a>')
        self.assertIn(">Docs</a>", page.split("</nav>")[0])
        self.assertIn("Not available: file not found.", self.client.get("/web/docs/c").data.decode())
        self.assertEqual(self.client.get("/web/docs/x").status_code, 404)
        self.assertEqual(self.client.get("/web/docs/file/sub/pic.jpg").data, b"jpg")
        for path in ("sub/x.jpg", "A.md", "../etc/passwd", "sub/../A.md"):
            self.assertEqual(self.client.get("/web/docs/file/" + path).status_code, 404, path)


class EventsLinkTest(unittest.TestCase):
    """Link from a job to its events"""

    def test_query(self):
        self.assertEqual(events_query(["gitlab", "a/b", "main", "my job"]), 'a/b main "my job"')

    def test_link_text(self):
        self.assertEqual(link_text(["gitlab", "a/b", "main"], "https://gl/a/b/-/pipelines/7"), "Open GitLab pipeline")
        self.assertEqual(link_text(["gitlab", "a/b", "main", "t"], "https://gl/a/b/-/jobs/8"), "Open GitLab job")
        self.assertEqual(link_text(["github", "o/r", "main", "CI"], "https://github.com/o/r/actions/runs/4"),
                         "Open GitHub workflow run")
        self.assertEqual(link_text(["github", "o/r", "main", "CI", "build"],
                                   "https://github.com/o/r/actions/runs/4/job/5"), "Open GitHub job")
        self.assertEqual(link_text(["generic", "nas", "backup"], "https://nas/log"), "Open Generic link")
        self.assertEqual(events_query(["generic"]), "")

    def test_jobs_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            generic = Generic(tmp)
            jobs = Jobs()
            generic.handle("set", {"path": ["nas", "my backup"], "status": "RUNNING"})
            jobs.update("generic", generic.jobs())
            app = create_app(Devices(tmp), generic=generic, jobs=jobs)
            page = app.test_client().get("/web/jobs").data.decode()
            self.assertIn('href="/web#/generic?q=nas%20%22my%20backup%22">(events)</a>', page)


class EditorsTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.devices = Devices(self.tmpdir.name)
        self.jobs = Jobs()
        self.test = TestJobs(self.tmpdir.name)
        self.jobs.update(TestJobs.SOURCE, self.test.jobs())
        self.devices.report(DEV, {"device": DEV, "settings": {"name": "Old"}})
        self.devices.report(DEV2, {"device": DEV2, "settings": {"name": "Other"}})
        app = create_app(self.devices, None, 15, None, self.jobs, editors=["flip"], testjobs=self.test,
                         device_editors={"laempli": [DEV], "guest": []})
        app.wsgi_app = ProxyMiddleware(app.wsgi_app)
        self.client = app.test_client()

    def tearDown(self):
        self.tmpdir.cleanup()

    def as_user(self, user):
        self.client.environ_base["HTTP_X_FORWARDED_USER"] = user

    def edits(self, dev_id=DEV):
        # Status codes of all edit requests
        leds = [{"path": ["test", "test01"]}]
        return [self.client.post("/api/leds/" + dev_id, json=leds).status_code,
                self.client.post("/api/settings/" + dev_id, json={"name": "New"}).status_code,
                self.client.post("/web/device/" + dev_id, data={"rows": "1", "path_0": json.dumps(leds[0]["path"])})
                .status_code,
                self.client.post("/web/device/{}/settings".format(dev_id), data={"name": "New"}).status_code,
                self.client.post("/web/test/test01", data={"status": "RUNNING"}).status_code]

    def device_edits(self, dev_id):
        # Status codes of the device commands (not connected: 409, no LED test: 409, no sound: 400)
        return [self.client.post("/api/device/{}/update".format(dev_id)).status_code,
                self.client.post("/api/device/{}/sound".format(dev_id), json={"sound": "x"}).status_code,
                self.client.post("/api/device/{}/led".format(dev_id), json={"mode": "red"}).status_code]

    def test_editor(self):
        self.as_user("flip")
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertNotIn("read only", page)
        self.assertIn('id="save">Save</button>', page)
        self.assertIn('data-can-edit="true"', page)
        self.assertIn(">flip", self.client.get("/web/devices").data.decode())
        self.assertEqual(self.edits(), [200, 200, 302, 302, 302])
        self.assertEqual(self.edits(DEV2), [200, 200, 302, 302, 302])

    def test_device_editor(self):
        # Edits only DEV, not DEV2, no test jobs
        self.as_user("laempli")
        page = self.client.get("/web/device/" + DEV).data.decode()
        self.assertNotIn("read only", page)
        self.assertIn('id="save">Save</button>', page)
        self.assertIn('id="settings_save">Save</button>', page)
        self.assertIn('data-can-edit="true"', page)
        with self.assertLogs("laempli.web", "WARNING"):
            self.assertEqual(self.edits(), [200, 200, 302, 302, 403])
        self.assertNotIn(403, self.device_edits(DEV))
        page = self.client.get("/web/device/" + DEV2).data.decode()
        self.assertEqual(page.count('>read only</span>'), 2)
        self.assertIn('id="save" disabled>Save</button>', page)
        self.assertIn('data-can-edit="false"', page)
        with self.assertLogs("laempli.web", "WARNING"):
            self.assertEqual(self.edits(DEV2), [403, 403, 403, 403, 403])
            self.assertEqual(self.device_edits(DEV2), [403, 403, 403])
        # Global edits: none
        self.assertIn('data-can-edit="false"', self.client.get("/web/dashboard").data.decode())
        with self.assertLogs("laempli.web", "WARNING"):
            self.assertEqual(self.client.post("/api/jobs/remove", json={"path": ["x"]}).status_code, 403)

    def test_other_users(self):
        # Other user, no user: read only
        for user in ("guest", "Flip", "", "(null)"):
            self.as_user(user)
            page = self.client.get("/web/device/" + DEV).data.decode()
            self.assertEqual(page.count('>read only</span>'), 2, user)
            self.assertNotIn("read only", page.split("<main")[0], user)
            self.assertIn('id="save" disabled>Save</button>', page, user)
            self.assertIn('id="settings_save" disabled>Save</button>', page, user)
            self.assertIn('data-can-edit="false"', page, user)
            with self.assertLogs("laempli.web", "WARNING"):
                self.assertEqual(self.edits(), [403, 403, 403, 403, 403], user)
                self.assertEqual(self.device_edits(DEV), [403, 403, 403], user)
            self.assertEqual(self.client.post("/api/leds/" + DEV, json=[]).get_json(), {"error": "no edit rights"})
        # Nothing changed, reading works
        self.assertEqual(self.client.get("/api/leds/" + DEV).get_json()["config"], [])
        self.assertEqual(self.client.get("/api/settings/" + DEV).get_json()["pending"], {})

    def test_prefix(self):
        # Trailing slash, bad prefix
        self.client.environ_base["HTTP_X_FORWARDED_PREFIX"] = "/tschenggins-laempli/"
        self.assertEqual(self.client.get("/").headers["Location"], "/tschenggins-laempli/web")
        self.client.environ_base["HTTP_X_FORWARDED_PREFIX"] = "tschenggins-laempli"
        self.assertEqual(self.client.get("/").headers["Location"], "/web")


if __name__ == "__main__":
    unittest.main()
