"""Devices and their state, one JSON file per device

File <data dir>/laempli_<device ID>.json:

    {
        "info": {"id": ..., "since": ..., "firmware": ..., "ssid": ..., "rssi": ..., "leds": ..., ...},
        "config": {"leds": [{"path": [...], "name": ...}, null, ...]},   (or project rows, see leds.py)
        "state": {"leds": [{"label": ..., "path": [...], "status": ..., "result": ..., ...}, null, ...]},
        "settings": {"values": {"name": ..., "dim_timeout": ..., ...}, "options": {...}, "time": ...},
        "pending": {"seq": n, "values": {...}}
    }

"info" is the last known device info (from the stream query parameters and the reports). "config" is the LED
configuration (web page, see leds.py; old GitLab entries are converted at load). "state" is the state that the
device gets on its stream (made from the config and the jobs, see leds.py). "settings" and "pending" are the device
settings (name, display, LEDs) and the changes from the web page, see settings.py. The file is written on connect,
disconnect, config change, state change, settings change and pending change (not on each report). The housekeeping
removes devices that were not connected for a long time, see expire(). Changes go to a Changes object (web page live
updates, topics "devices" and "device:<ID>", see changes.py).

Firmware update (OTA) state, only in memory (device info "ota", None if no update): {"state", "version", "done",
"size", "error", "time"}. States: "starting" (update button, see start_ota()), "running" (the device gets the file on
its stream, see wsserver.py), "done" (device restarts), "failed" (with error). The device sends its state with each
"ota" message and each report, see set_ota() and report(). An old firmware (download over HTTP) sends no state: the
backend shows "starting" until the device connects again. Without connection, "starting" and "done" stay for
OTA_OFFLINE_TIME.

LED test (buttons on the device page, see web.py): the device reports the running test, device info "led_test"
{"mode", "first", "count"} (see clean_led_test()), None if none. Only in memory, None without connection.
"""

import json
import logging
import os
import re
import tempfile
import threading
import time

from .leds import convert_config
from .settings import clean_options, clean_report

LOG = logging.getLogger(__name__)

# Device ID: <type>-<6 hex chars> (type of firmware, last 3 MAC bytes), also in the file name
ID_RE = re.compile(r"^[a-z][a-z0-9]*-[0-9a-f]{6}$")

# File name prefix and suffix
FILE_PREFIX = "laempli_"
FILE_SUFFIX = ".json"

# Report values, all int (heap_size: only in the first report of a stream)
REPORT_KEYS = ("rssi", "uptime", "heap", "heap_size")

# CPU name of the report (first report of a stream), max. length
CPU_MAX = 64

# LED test modes (without "normal": no test)
LED_TEST_MODES = ("red", "green", "blue", "white", "off", "demo", "find")

# OTA states from the device
OTA_STATES = ("running", "done", "failed")

# Time that "starting" stays without a device state [s]
OTA_START_TIME = 10.0

# Time that "starting" and "done" stay without connection [s]
OTA_OFFLINE_TIME = 300.0


def valid_id(dev_id):
    """Check device ID"""
    return isinstance(dev_id, str) and (ID_RE.match(dev_id) is not None)


def empty_state():
    """Get state for a new device"""
    return {"leds": []}


def empty_config():
    """Get config for a new device"""
    return {"leds": []}


def empty_settings():
    """Get settings for a new device (none from the device yet)"""
    return {"values": {}, "options": {}, "time": None}


def empty_pending():
    """Get pending settings for a new device"""
    return {"seq": 0, "values": {}}


def clean_led_test(data):
    """Get the LED test of a report: {"mode", "first", "count"}, None if none or bad"""
    if (not isinstance(data, dict)) or (data.get("mode") not in LED_TEST_MODES):
        return None
    numbers = [data.get(key, 0) for key in ("first", "count")]
    if not all((isinstance(num, int) and (not isinstance(num, bool)) and (0 <= num <= 1000)) for num in numbers):
        return None
    return {"mode": data["mode"], "first": numbers[0], "count": numbers[1]}


def clean_ota(data):
    """Get OTA state from device data ({"state", "version", "done", "size", "error"}), None if not valid"""
    if (not isinstance(data, dict)) or (data.get("state") not in OTA_STATES):
        return None

    def number(key):
        value = data.get(key)
        return (value if (isinstance(value, int) and (not isinstance(value, bool)) and (value >= 0)) else 0)

    def text(key, size):
        value = data.get(key)
        return (value[:size] if isinstance(value, str) else "")
    return {"state": data["state"], "version": text("version", 64), "done": number("done"), "size": number("size"),
            "error": text("error", 64), "time": time.time()}


class Devices:
    """Devices and their state"""

    def __init__(self, dirname, changes=None):
        self.dirname = dirname
        self.changes = changes  # Changes (None: no notifications)
        self.cond = threading.Condition()
        self.devices = {}   # ID -> {"info", "config", "state", "settings", "pending", "version": n, "streams": n}
        self.clients = 0    # Open streams of all devices
        os.makedirs(dirname, exist_ok=True)
        for name in sorted(os.listdir(dirname)):
            if (not name.startswith(FILE_PREFIX)) or (not name.endswith(FILE_SUFFIX)):
                continue
            dev_id = name[len(FILE_PREFIX):-len(FILE_SUFFIX)]
            if not valid_id(dev_id):
                continue
            path = os.path.join(dirname, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if not isinstance(data, dict):
                    raise ValueError("not an object")
            except (OSError, ValueError) as ex:
                LOG.warning("Ignore %s: %s", path, ex)
                continue
            config = data.get("config")
            if isinstance(config, dict) and (convert_config(config.get("leds")) != config.get("leds")):
                config["leds"] = convert_config(config["leds"])
                LOG.info("Converted LED config of %s", dev_id)
                self.devices[dev_id] = self._new(dev_id, data.get("info"), data.get("state"), config,
                                                 data.get("settings"), data.get("pending"))
                self._save(dev_id)
                continue
            self.devices[dev_id] = self._new(dev_id, data.get("info"), data.get("state"), config,
                                             data.get("settings"), data.get("pending"))
        LOG.info("Loaded %d devices from %s", len(self.devices), dirname)

    @staticmethod
    def _new(dev_id, info=None, state=None, config=None, settings=None, pending=None):
        settings = (settings if isinstance(settings, dict) else {})
        pending = (pending if isinstance(pending, dict) else {})
        dev = {"info": (dict(info) if isinstance(info, dict) else {}),
               "state": (state if isinstance(state, dict) else empty_state()),
               "config": (config if isinstance(config, dict) else empty_config()),
               "settings": dict(empty_settings(), values=clean_report(settings.get("values")),
                                options=clean_options(settings.get("options")), time=settings.get("time")),
               "pending": {"seq": (pending["seq"] if isinstance(pending.get("seq"), int) else 0),
                           "values": clean_report(pending.get("values"))},
               "version": 0, "streams": 0, "commands": [], "ota": None}
        dev["info"]["id"] = dev_id
        dev["info"]["connected"] = False
        dev["info"]["led_test"] = None
        dev["info"].setdefault("since", time.time())
        return dev

    def _get(self, dev_id):
        # Get device, create if new. Lock held.
        if dev_id not in self.devices:
            self.devices[dev_id] = self._new(dev_id)
            LOG.info("New device %s", dev_id)
        return self.devices[dev_id]

    def _changed(self, dev_id, listed=True):
        # Notify web pages: device page, device list
        if self.changes is not None:
            self.changes.notify(*((["devices"] if listed else []) + ["device:" + dev_id]))

    def _save(self, dev_id):
        # Atomic: write temp file, then rename. Lock held.
        dev = self.devices[dev_id]
        info = {key: value for key, value in dev["info"].items() if key != "connected"}
        path = os.path.join(self.dirname, FILE_PREFIX + dev_id + FILE_SUFFIX)
        fd, tmp = tempfile.mkstemp(prefix=".{}-".format(dev_id), suffix=FILE_SUFFIX, dir=self.dirname)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"info": info, "config": dev["config"], "state": dev["state"], "settings": dev["settings"],
                           "pending": dev["pending"]}, fh, indent=4)
                fh.write("\n")
            os.replace(tmp, path)
        except BaseException:
            os.unlink(tmp)
            raise

    def connect(self, info):
        """Register device stream, info from device_info(). Returns number of open streams."""
        with self.cond:
            dev = self._get(info["id"])
            if dev["streams"] == 0:
                dev["info"]["since"] = time.time()
            dev["info"].update(info)
            dev["info"]["connected"] = True
            dev["ota"] = None
            dev["streams"] += 1
            self.clients += 1
            self._save(info["id"])
            self._changed(info["id"])
            return self.clients

    def disconnect(self, dev_id):
        """Unregister device stream. Returns number of open streams."""
        with self.cond:
            dev = self.devices[dev_id]
            dev["streams"] -= 1
            if dev["streams"] == 0:
                dev["info"]["connected"] = False
                dev["info"]["since"] = time.time()
                dev["info"]["led_test"] = None
                dev["commands"] = []
                if (dev["ota"] is not None) and (dev["ota"]["state"] == "running"):
                    ota = dict(dev["ota"], state="failed", error="connection lost", time=time.time())
                    self._log_ota(dev_id, dev["ota"], ota)
                    dev["ota"] = ota
            self.clients -= 1
            self._save(dev_id)
            self._changed(dev_id)
            return self.clients

    def report(self, dev_id, data):
        """Update device info with report data (in memory), and the settings (saved if changed, see settings.py)"""
        with self.cond:
            dev = self._get(dev_id)
            for key in REPORT_KEYS:
                if isinstance(data.get(key), int) and (not isinstance(data.get(key), bool)):
                    dev["info"][key] = data[key]
            if isinstance(data.get("cpu"), str) and data["cpu"].isprintable():
                dev["info"]["cpu"] = data["cpu"][:CPU_MAX]
            dev["info"]["report"] = time.time()
            dev["info"]["led_test"] = clean_led_test(data.get("led_test"))
            changed = False
            settings = dev["settings"]
            if "settings" in data:
                values = clean_report(data["settings"])
                changed = (values != settings["values"])
                settings["values"] = values
                settings["time"] = time.time()
            if "options" in data:
                options = clean_options(data["options"])
                changed = (changed or (options != settings["options"]))
                settings["options"] = options
            self._report_ota(dev_id, dev, data.get("ota"))
            pending = dev["pending"]
            ack = data.get("ack")
            if pending["values"] and isinstance(ack, int) and (ack >= pending["seq"]):
                LOG.info("Settings of %s: device has %s", dev_id, ", ".join(sorted(pending["values"])))
                pending["values"] = {}
                changed = True
            if changed:
                self._save(dev_id)
            self._changed(dev_id)

    def _report_ota(self, dev_id, dev, data):
        # OTA state of a report. Lock held.
        ota = clean_ota(data)
        old = dev["ota"]
        if ota is None:
            # Keep a recent start and "done" (device restarts)
            if (old is not None) and (((old["state"] == "starting") and
                                       ((time.time() - old["time"]) < OTA_START_TIME)) or (old["state"] == "done")):
                return
        elif (old is not None) and all((ota[key] == old[key]) for key in ("state", "version", "done", "error")):
            return
        self._log_ota(dev_id, old, ota)
        dev["ota"] = ota

    @staticmethod
    def _log_ota(dev_id, old, ota):
        # Log state changes of an update
        if (ota is not None) and ((old is None) or (old["state"] != ota["state"])):
            if ota["state"] == "failed":
                LOG.warning("Firmware update of %s: failed: %s", dev_id, ota["error"])
            else:
                LOG.info("Firmware update of %s: %s %s", dev_id, ota["state"], ota["version"])

    def start_ota(self, dev_id, version):
        """Mark the update as started (update button)"""
        with self.cond:
            dev = self._get(dev_id)
            dev["ota"] = {"state": "starting", "version": version, "done": 0, "size": 0, "error": "",
                          "time": time.time()}
            self._changed(dev_id)

    def set_ota(self, dev_id, data):
        """Set the OTA state from an "ota" message of the device"""
        ota = clean_ota(data)
        if ota is None:
            return
        with self.cond:
            dev = self._get(dev_id)
            old = dev["ota"]
            self._log_ota(dev_id, old, ota)
            dev["ota"] = ota
            # Device list only for a new state
            self._changed(dev_id, ((old is None) or (old["state"] != ota["state"])))

    @staticmethod
    def _ota_view(dev):
        # Copy of the OTA state, None if none or old. Lock held.
        ota = dev["ota"]
        if (ota is None) or ((dev["streams"] == 0) and (ota["state"] in ("starting", "done")) and
                             ((time.time() - ota["time"]) > OTA_OFFLINE_TIME)):
            return None
        return dict(ota)

    def get_state(self, dev_id):
        """Get a copy of the device state and its version, (None, None) if unknown device"""
        with self.cond:
            dev = self.devices.get(dev_id)
            if dev is None:
                return None, None
            return json.loads(json.dumps(dev["state"])), dev["version"]

    def set_state(self, dev_id, state):
        """Replace the device state, save it and notify the stream"""
        with self.cond:
            dev = self._get(dev_id)
            dev["state"] = state
            dev["version"] += 1
            self._save(dev_id)
            self.cond.notify_all()
            self._changed(dev_id, False)

    def update_state(self, dev_id, state):
        """Set the device state if it differs (save, notify the stream). Returns True if changed.

        The key order counts (info lines are in display order).
        """
        with self.cond:
            dev = self._get(dev_id)
            if json.dumps(dev["state"]) == json.dumps(state):
                return False
            dev["state"] = json.loads(json.dumps(state))
            dev["version"] += 1
            self._save(dev_id)
            self.cond.notify_all()
            self._changed(dev_id, False)
            return True

    def get_config(self, dev_id):
        """Get a copy of the device config, None if unknown device"""
        with self.cond:
            dev = self.devices.get(dev_id)
            if dev is None:
                return None
            return json.loads(json.dumps(dev["config"]))

    def set_config(self, dev_id, config):
        """Set the device config (creates the device if new) and save it"""
        with self.cond:
            dev = self._get(dev_id)
            dev["config"] = json.loads(json.dumps(config))
            self._save(dev_id)
            self._changed(dev_id)

    def get_settings(self, dev_id):
        """Get a copy of the device settings: {"values", "options", "time", "pending", "seq"}, None if unknown device"""
        with self.cond:
            dev = self.devices.get(dev_id)
            if dev is None:
                return None
            return json.loads(json.dumps(dict(dev["settings"], pending=dev["pending"]["values"],
                                              seq=dev["pending"]["seq"])))

    def set_pending(self, dev_id, values):
        """Add changes from the web page ({key: value}, checked) to the pending settings, notify the stream

        A value equal to the device value removes the pending one. Returns the pending values.
        """
        with self.cond:
            dev = self.devices[dev_id]
            pending = dict(dev["pending"]["values"])
            for key, value in values.items():
                if dev["settings"]["values"].get(key) == value:
                    pending.pop(key, None)
                else:
                    pending[key] = value
            if pending != dev["pending"]["values"]:
                dev["pending"] = {"seq": dev["pending"]["seq"] + 1, "values": pending}
                self._save(dev_id)
                self.cond.notify_all()
                self._changed(dev_id, False)
            return dict(pending)

    def send_command(self, dev_id, event, data):
        """Send a command event to the stream of the device (only in memory), returns False if not connected"""
        with self.cond:
            dev = self.devices.get(dev_id)
            if (dev is None) or (dev["streams"] <= 0):
                return False
            dev["commands"].append((event, data))
            self.cond.notify_all()
            return True

    def pop_commands(self, dev_id):
        """Get and remove the command events of the device stream: [(event, data), ...]"""
        with self.cond:
            commands = self.devices[dev_id]["commands"]
            self.devices[dev_id]["commands"] = []
            return commands

    def get_pending(self, dev_id):
        """Get seq and a copy of the pending settings"""
        with self.cond:
            pending = self.devices[dev_id]["pending"]
            return pending["seq"], dict(pending["values"])

    def expire(self, days, now=None):
        """Remove devices not connected for days (0: none), with their file. Returns the removed IDs."""
        if days <= 0:
            return []
        limit = (time.time() if now is None else now) - (days * 86400)
        removed = []
        with self.cond:
            for dev_id, dev in list(self.devices.items()):
                if (dev["streams"] > 0) or (dev["info"].get("since", 0) >= limit):
                    continue
                try:
                    os.remove(os.path.join(self.dirname, FILE_PREFIX + dev_id + FILE_SUFFIX))
                except FileNotFoundError:
                    pass
                del self.devices[dev_id]
                LOG.info("Removed old device %s", dev_id)
                removed.append(dev_id)
        if removed and (self.changes is not None):
            self.changes.notify("devices")
        return removed

    def ids(self):
        """Get list of device IDs, sorted"""
        with self.cond:
            return sorted(self.devices)

    def wait_change(self, dev_id, version, timeout, seq=None):
        """Wait until the state version differs from version, new pending settings (seq differs), a command or timeout

        Returns the current state version and True if there are new pending settings or commands.
        """
        with self.cond:
            dev = self.devices[dev_id]

            def pending_due():
                return (bool(dev["commands"]) or
                        ((seq is not None) and bool(dev["pending"]["values"]) and (dev["pending"]["seq"] != seq)))
            self.cond.wait_for(lambda: ((dev["version"] != version) or pending_due()), timeout=timeout)
            return dev["version"], pending_due()

    def get(self):
        """Get list of device infos (with number of configured LEDs and OTA state), sorted by ID"""
        with self.cond:
            return [dict(self.devices[key]["info"], ota=self._ota_view(self.devices[key]),
                         configured=sum(1 for led in (self.devices[key]["state"].get("leds", [])
                                                      if ("rows" in self.devices[key]["config"]) else
                                                      self.devices[key]["config"].get("leds", [])) if led),
                         name=self.devices[key]["settings"]["values"].get("name", ""))
                    for key in sorted(self.devices)]
