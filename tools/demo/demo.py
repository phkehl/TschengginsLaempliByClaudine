#!/usr/bin/env python3
"""Demo of the Tschenggins Lämpli web interface: a fake GitLab and 3 fake Lämplis

The demo starts a local backend (127.0.0.1, own data directory) and feeds it with fake data, for screenshots and
screencasts of the web pages (see demo-media.mjs and demo.md):

- Fake GitLab: a simulation of some fake projects (acme/firmware, acme/web-shop, ...) with pipelines on main, on
  feature branches (merge requests, then merge and branch deletion) and on tags. Jobs go created -> pending -> running
  -> success or failed, some fail, some get a retry, one project has a child pipeline (trigger job), one a manual
  deploy job. The simulation sends the same webhook events as GitLab ("Pipeline Hook", "Job Hook", "Push Hook") to
  /api/gitlab.
- History: before the backend starts, the simulation runs over the last days (--history) and writes its events with
  their time into the event store of the data directory. The backend replays them at start, so the Jobs, Plots and
  event log pages have data. More activity on work days 8-18 h (local time).
- 3 fake Lämplis (Chlotz, Hello, Stägge) connect on the WebSocket /ws/device as the firmware does, send reports with
  settings and options, and take the settings of the web page. The demo sets their LED configuration (API).
- Live: the simulation goes on in real time. --pace fast makes all times 60 times shorter (some changes each second,
  for screencasts) and jobs fail 3 times more often.

The web pages: http://127.0.0.1:<port>/web (read only, no X-Forwarded-User header). Edit rights: send the header
"X-Forwarded-User: demo" (e.g. with a browser extension, or demo-media.mjs does it). Ctrl-C stops the demo.

The directory: default data/demo (git-ignored). The demo writes <dir>/demo.yaml and makes <dir>/data new at each
start. It refuses a directory that has other files but no demo.yaml (protects real data).

Needs the Debian packages of the backend (python3-flask, python3-waitress, python3-websockets, python3-yaml) and
python3-coloredlogs (optional).

Examples:

    tools/demo/demo.py                          # data/demo, port 8099 (WebSocket 8100), 7 days history
    tools/demo/demo.py --pace fast --seed 2     # quick changes, other random data
    tools/demo/demo.py /tmp/demo --port 8200 --history 7
"""

import argparse
import asyncio
import datetime
import heapq
import itertools
import json
import logging
import os
import random
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

TOP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(TOP, "backend"))

from laempli.eventdb import EventStore  # noqa: E402 pylint: disable=wrong-import-position
from laempli.logs import is_journal, setup_logging  # noqa: E402 pylint: disable=wrong-import-position

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:
    sys.exit("websockets missing, please run: sudo apt install python3-websockets")

LOG = logging.getLogger("demo")

# Editor (X-Forwarded-User) of the demo backend
EDITOR = "demo"

# Fake GitLab server URL (links on the pages)
GITLAB_URL = "https://gitlab.example.com"

# Time factor and failure factor of --pace fast
FAST = 1.0 / 60.0
FAST_FAILS = 3.0

# Fake users: (username, name)
USERS = (("alice", "Alice Example"), ("bob", "Bob Example"), ("carol", "Carol Example"), ("dave", "Dave Example"))

# Fake commit titles
TITLES = (
    "Fix race in the OTA download", "Update CI image to Debian 13", "Add retry to the HTTP client",
    "Bump dependencies", "Refactor the config parser", "Speed up the unit tests", "Fix typo in the docs",
    "Add dark mode to the shop", "Handle empty carts", "Log the WiFi roaming events", "Cache the build tools",
    "Remove old feature flag", "Add integration test for the payment", "Fix flaky timeout test",
)


def job(name, duration, fail=0.0, **opts):
    """Job spec: name, mean duration [s], failure probability, options (only: main, tag, branch; manual; trigger)"""
    return dict(opts, name=name, duration=duration, fail=fail)


# Fake projects: stages (stage, [jobs]), child pipeline stages of a trigger job, mean time between pipelines on a
# work day [s], feature branch names
PROJECTS = (
    {"id": 101, "path": "acme/firmware", "name": "Firmware", "every": 2400,
     "stages": [("build", [job("build:esp32", 240), job("build:esp8266", 180)]),
                ("test", [job("test:unit", 120, 0.05), job("test:hil", 420, 0.2)]),
                ("package", [job("image", 0, trigger=True, main=True)])],
     "child": [("build", [job("rootfs", 600, 0.05), job("sdk", 480)]), ("test", [job("smoke", 180, 0.1)])],
     "branches": ["feature/wifi-roaming", "fix/ota-timeout", "feature/led-themes"]},
    {"id": 102, "path": "acme/web-shop", "name": "Web shop", "every": 1800,
     "stages": [("build", [job("build", 150)]), ("test", [job("lint", 40, 0.05), job("test:e2e", 360, 0.15)]),
                ("deploy", [job("deploy:staging", 90, main=True), job("deploy:prod", 60, main=True, manual=True)])],
     "branches": ["feature/dark-mode", "fix/empty-cart", "feature/vouchers"]},
    {"id": 103, "path": "acme/docs", "name": "Docs", "every": 3600,
     "stages": [("build", [job("html", 90, 0.03), job("pdf", 150, 0.05)]),
                ("publish", [job("pages", 30, main=True)])],
     "branches": ["docs/install-guide"]},
    {"id": 104, "path": "acme/ci-images", "name": "CI images", "every": 5400,
     "stages": [("build", [job("debian", 300), job("ubuntu", 330), job("alpine", 200, 0.1)]),
                ("push", [job("push", 60, main=True)])],
     "branches": ["update/debian-13"]},
    {"id": 105, "path": "acme/mobile-app", "name": "Mobile app", "every": 2700,
     "stages": [("build", [job("build:android", 420, 0.05), job("build:ios", 540, 0.08)]),
                ("test", [job("test", 300, 0.1)])],
     "branches": ["feature/push-messages", "fix/login-crash"]},
    {"id": 106, "path": "tools/release", "name": "Release", "every": 7200,
     "stages": [("check", [job("lint", 30), job("test", 120, 0.05)]),
                ("release", [job("release", 90, tag=True)])],
     "branches": ["feature/changelog"]},
)

# Fake Lämplis: ID, LEDs, report data, LED configuration (list of LEDs, or rows of the Stägge)
BRIGHT = [[16, "very dim"], [32, "dim"], [64, "medium"], [96, "bright"], [128, "very bright"], [160, "brightest"]]
ANIMS = ["cosine 1 s", "breathe 2 s", "breathe 3 s", "breathe 4 s, deep", "breathe 3 s, rest", "cosine 0.3 s",
         "pulse 0.6 s", "pulse 0.4 s", "heartbeat", "flicker", "flutter", "soft blink 0.5 s"]
MELODIES = ["Fanfare:3", "Tada:2", "Trombone:3", "Alarm:2", "Bells:4"]


def led(path, name, **opts):
    """LED config entry: job path (without "gitlab"), name"""
    return dict(opts, path=["gitlab"] + path, name=name)


DEVICES = (
    {"id": "chlotz-de0001", "leds": 12, "cpu": "ESP32-D0WD-V3 rev 3.1, 2 cores, 240 MHz", "heap_size": 327680,
     "heap": 162000, "rssi": -58,
     "settings": {"name": "Office", "dim_timeout": 300, "dim_level": 20, "saver_timeout": 3600, "led_brightness": 64,
                  "anim_running": 3, "anim_waiting": 8, "led_reverse": 0},
     "options": {"led_brightness": BRIGHT, "anim_running": ANIMS, "anim_waiting": ANIMS, "melodies": MELODIES,
                 "led_test": True},
     "config": [led(["acme/firmware", "main"], "Firmware"),
                led(["acme/firmware", "main", "image"], "FW image"),
                led(["acme/firmware", "main", "test:hil"], "HIL test", sounds={"fail": "Trombone"}),
                led(["acme/web-shop", "main"], "Shop"),
                led(["acme/web-shop", "main", "deploy:staging"], "Staging"),
                led(["acme/web-shop", "main", "deploy:prod"], "Prod"),
                led(["acme/mobile-app", "main"], "App"),
                led(["acme/mobile-app", "main", "build:ios"], "iOS"),
                led(["acme/mobile-app", "main", "build:android"], "Android"),
                led(["acme/ci-images", "main"], "CI images"),
                led(["acme/docs", "main"], "Docs", max_age="7d"),
                led(["tools/release", "main"], "Release")]},
    {"id": "hello-de0002", "leds": 1, "cpu": "ESP8266, 80 MHz", "heap_size": 81920, "heap": 21500, "rssi": -67,
     "settings": {"name": "Desk", "ring_brightness": 128, "bow_brightness": 64, "led_reverse": 0},
     "options": {"ring_brightness": BRIGHT, "bow_brightness": BRIGHT, "sound_fx": True, "melodies": MELODIES,
                 "led_test": True, "no_ota": True},
     "config": [led(["acme/firmware", "main"], "Firmware", sounds={"fail": "fx", "fixed": "Tada"})]},
    {"id": "staegge-de0003", "leds": 60, "cpu": "ESP32-D0WD-V3 rev 3.1, 2 cores, 240 MHz", "heap_size": 327680,
     "heap": 121000, "rssi": -49,
     "settings": {"name": "Hallway", "led_brightness": 32, "anim_running": 1, "anim_waiting": 7, "led_reverse": 1},
     "options": {"led_brightness": BRIGHT, "anim_running": ANIMS, "anim_waiting": ANIMS, "melodies": [],
                 "led_test": True},
     "config": [{"path": ["gitlab", "acme/firmware"]}, {"path": ["gitlab", "acme/web-shop"]},
                {"path": ["gitlab", "acme/mobile-app"]}, {"path": ["gitlab", "acme/ci-images"]}]},
)

# Firmware version of the fake Lämplis
FW_VERSION = "20261120101500-0a1b2c3"


def gl_time(stamp):
    """GitLab time text of a Unix time"""
    return datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


class Simulation:
    """Fake GitLab: discrete event simulation, each process is a generator that yields delays [s]

    sink(stamp, event, payload): called for each webhook event. scale: time factor of all delays.
    """

    def __init__(self, seed, start, sink, scale=1.0, work_hours=True):
        self.rng = random.Random(seed)
        self.now = start
        self.sink = sink
        self.scale = scale
        self.work_hours = work_hours
        self.fails = 1.0  # Failure factor
        self.queue = []  # (time, seq, generator)
        self.seq = itertools.count()
        self.pipe_ids = itertools.count(48000)
        self.job_ids = itertools.count(910000)
        self.iids = {}
        self.tags = {}
        self.lock = threading.Lock()
        for proj in PROJECTS:
            self.spawn(self.project_loop(proj), self.rng.uniform(0, proj["every"]))

    def spawn(self, gen, delay=0.0):
        """Start a process after delay (scaled)"""
        heapq.heappush(self.queue, (self.now + (delay * self.scale), next(self.seq), gen))

    def next_time(self):
        """Time of the next step"""
        return self.queue[0][0]

    def step(self):
        """Run the next process step"""
        stamp, _, gen = heapq.heappop(self.queue)
        self.now = max(self.now, stamp)
        try:
            delay = next(gen)
        except StopIteration:
            return
        self.spawn(gen, delay)

    def run_until(self, end):
        """Run all steps up to end"""
        while self.queue and (self.next_time() <= end):
            self.step()
        self.now = end

    def set_scale(self, scale):
        """Change the time factor, also of the waiting steps"""
        factor = scale / self.scale
        self.queue = [(self.now + ((stamp - self.now) * factor), seq, gen) for stamp, seq, gen in self.queue]
        heapq.heapify(self.queue)
        self.scale = scale

    # Processes

    def activity(self):
        """Activity factor: work days 8-18 h local time: 1, else less"""
        if not self.work_hours:
            return 1.0
        local = datetime.datetime.fromtimestamp(self.now)
        if local.weekday() >= 5:
            return 0.05
        return (1.0 if (8 <= local.hour < 18) else 0.08)

    def project_loop(self, proj):
        """Project: pipelines on main, sometimes a feature branch or a tag"""
        while True:
            yield (proj["every"] * self.rng.uniform(0.3, 1.7)) / self.activity()
            pick = self.rng.random()
            if pick < 0.25:
                self.spawn(self.branch_flow(proj))
            elif (pick < 0.32) and any(any(spec.get("tag") for spec in jobs) for _, jobs in proj["stages"]):
                num = self.tags[proj["id"]] = self.tags.get(proj["id"], 0) + 1
                self.spawn(self.pipeline(proj, "v1.{}.0".format(num), "push", tag=True))
            else:
                source = ("push" if (self.activity() > 0.5) else "schedule")
                self.spawn(self.pipeline(proj, "main", source))

    def branch_flow(self, proj):
        """Feature branch: merge request pipelines, then merge (main pipeline) and branch deletion"""
        branch = self.rng.choice(proj["branches"])
        mr_iid = self.rng.randint(100, 400)
        title = self.rng.choice(TITLES)
        user = self.rng.choice(USERS)
        for _ in range(self.rng.randint(1, 3)):
            yield from self.pipeline(proj, branch, "merge_request_event", user=user, title=title, mr_iid=mr_iid)
            yield self.rng.uniform(600, 3600)
        if self.rng.random() < 0.5:
            self.spawn(self.pipeline(proj, "main", "push", user=user, title="Merge branch '{}'".format(branch)))
            self.emit("Push Hook", {"object_kind": "push", "ref": "refs/heads/" + branch, "before": "1" * 40,
                                    "after": "0" * 40, "user_username": user[0], "project_id": proj["id"],
                                    "project": self.project_info(proj)})

    def pipeline(self, proj, ref, source, tag=False, user=None, title=None, mr_iid=None, parent=None, stages=None):
        """One pipeline (also a child pipeline: parent = source_pipeline), returns the final status"""
        user = (user or self.rng.choice(USERS))
        pipe = {"id": next(self.pipe_ids), "iid": self.next_iid(proj), "proj": proj, "ref": ref, "tag": tag,
                "sha": "{:040x}".format(self.rng.getrandbits(160)), "source": source, "status": "created",
                "created": self.now, "started": None, "finished": None, "user": user,
                "title": (title or self.rng.choice(TITLES)), "mr_iid": mr_iid, "parent": parent,
                "builds": [], "bridges": []}
        plan = []
        for stage, specs in (stages or proj["stages"]):
            jobs = []
            for spec in specs:
                if (spec.get("main") and (ref != "main")) or (spec.get("tag") and (not tag)) or \
                        (tag and (not spec.get("tag")) and (stage != "check")):
                    continue
                entry = {"id": next(self.job_ids), "name": spec["name"], "stage": stage, "status": "created",
                         "allow_failure": bool(spec.get("manual")), "manual": bool(spec.get("manual")),
                         "created": self.now, "started": None, "finished": None, "spec": spec}
                (pipe["bridges"] if spec.get("trigger") else pipe["builds"]).append(entry)
                jobs.append(entry)
            if jobs:
                plan.append(jobs)
        for jobs in plan:
            for entry in jobs:
                if entry["manual"]:
                    entry["status"] = "manual"
        self.emit_pipeline(pipe)
        yield self.rng.uniform(2, 8)
        pipe["status"] = "pending"
        self.emit_pipeline(pipe)
        failed = False
        for jobs in plan:
            if failed:
                for entry in jobs:
                    entry["status"] = "skipped"
                continue
            for entry in jobs:
                if entry["spec"].get("trigger"):
                    entry["status"] = "running"
                    entry["started"] = self.now
                    pipe["status"] = "running"
                    pipe["started"] = (pipe["started"] or self.now)
                    self.emit_pipeline(pipe)
                    status = yield from self.pipeline(
                        proj, ref, "parent_pipeline", user=user, title=pipe["title"], stages=proj["child"],
                        parent={"project": {"id": proj["id"]}, "pipeline_id": pipe["id"], "job_id": entry["id"]})
                    entry["status"] = status
                    entry["finished"] = self.now
                    failed = (failed or (status == "failed"))
                    self.emit_pipeline(pipe)
            run = [entry for entry in jobs if (not entry["spec"].get("trigger")) and (not entry["manual"])]
            if not run:
                continue
            for entry in run:
                entry["status"] = "pending"
                self.emit_job(pipe, entry)
            yield self.rng.uniform(3, 30)
            for entry in run:
                entry["status"] = "running"
                entry["started"] = self.now
                self.emit_job(pipe, entry)
            if pipe["status"] != "running":
                pipe["status"] = "running"
                pipe["started"] = (pipe["started"] or self.now)
                self.emit_pipeline(pipe)
            ends = sorted((entry["spec"]["duration"] * self.rng.uniform(0.6, 1.4), ix) for ix, entry in enumerate(run))
            done = 0.0
            for end, ix in ends:
                yield end - done
                done = end
                entry = run[ix]
                entry["status"] = self.job_result(entry["spec"]["fail"])
                entry["finished"] = self.now
                self.emit_job(pipe, entry)
            for entry in [entry for entry in run if (entry["status"] == "failed")]:
                if self.rng.random() < 0.4:
                    yield from self.retry(pipe, entry)
            failed = (failed or any(entry["status"] == "failed" for entry in pipe["builds"]
                                    if (entry["stage"] == run[0]["stage"]) and (not entry.get("retried"))))
        pipe["status"] = ("failed" if failed else "success")
        pipe["finished"] = self.now
        self.emit_pipeline(pipe)
        return pipe["status"]

    def retry(self, pipe, old):
        """Retry of a failed job (someone clicks retry): new job ID, same name"""
        yield self.rng.uniform(60, 600)
        old["retried"] = True
        entry = dict(old, id=next(self.job_ids), status="pending", created=self.now, started=None, finished=None,
                     retried=False, retries=(old.get("retries", 0) + 1))
        pipe["builds"].append(entry)
        pipe["status"] = "running"
        self.emit_job(pipe, entry)
        self.emit_pipeline(pipe)
        yield self.rng.uniform(3, 30)
        entry["status"] = "running"
        entry["started"] = self.now
        self.emit_job(pipe, entry)
        yield entry["spec"]["duration"] * self.rng.uniform(0.6, 1.4)
        entry["status"] = self.job_result(entry["spec"]["fail"] / 2)
        entry["finished"] = self.now
        self.emit_job(pipe, entry)

    def job_result(self, fail):
        """Random job result: failure probability fail (times the failure factor)"""
        return ("failed" if (self.rng.random() < (fail * self.fails)) else "success")

    def next_iid(self, proj):
        """Next pipeline number in a project"""
        self.iids[proj["id"]] = self.iids.get(proj["id"], 200) + 1
        return self.iids[proj["id"]]

    # Events

    def emit(self, event, payload):
        """Send one webhook event to the sink"""
        self.sink(self.now, event, json.loads(json.dumps(payload)))

    @staticmethod
    def project_info(proj):
        """Project object of the events"""
        return {"id": proj["id"], "name": proj["name"], "path_with_namespace": proj["path"],
                "web_url": "{}/{}".format(GITLAB_URL, proj["path"]), "default_branch": "main"}

    def job_info(self, entry):
        """Job fields of a pipeline event (builds, bridges)"""
        info = {"id": entry["id"], "name": entry["name"], "stage": entry["stage"], "status": entry["status"],
                "created_at": gl_time(entry["created"]), "allow_failure": entry["allow_failure"],
                "manual": entry["manual"], "when": ("manual" if entry["manual"] else "on_success"),
                "started_at": (gl_time(entry["started"]) if entry["started"] else None),
                "finished_at": (gl_time(entry["finished"]) if entry["finished"] else None),
                "duration": self.duration(entry), "queued_duration": 4.2,
                "failure_reason": ("script_failure" if (entry["status"] == "failed") else None)}
        if entry["spec"].get("trigger"):
            info["bridge"] = True
        elif entry["started"]:
            info["runner"] = {"id": 7, "description": "runner-{} (docker)".format(1 + (entry["id"] % 4))}
        return info

    @staticmethod
    def duration(item):
        """Duration [s] of a job or pipeline, None if not finished"""
        if not (item["started"] and item["finished"]):
            return None
        return round(item["finished"] - item["started"], 1)

    def emit_pipeline(self, pipe):
        """Pipeline Hook"""
        proj = pipe["proj"]
        name = {"merge_request_event": "Merge request pipeline", "parent_pipeline": "Child pipeline",
                "schedule": "Nightly pipeline"}.get(pipe["source"], "Commit pipeline")
        detailed = {"success": "passed"}.get(pipe["status"], pipe["status"])
        attrs = {"id": pipe["id"], "iid": pipe["iid"], "name": name, "ref": pipe["ref"], "tag": pipe["tag"],
                 "sha": pipe["sha"], "status": pipe["status"], "detailed_status": detailed,
                 "source": pipe["source"], "url": "{}/{}/-/pipelines/{}".format(GITLAB_URL, proj["path"], pipe["id"]),
                 "created_at": gl_time(pipe["created"]),
                 "finished_at": (gl_time(pipe["finished"]) if pipe["finished"] else None),
                 "duration": self.duration(pipe), "queued_duration": 5}
        payload = {"object_kind": "pipeline", "object_attributes": attrs, "project": self.project_info(proj),
                   "user": {"username": pipe["user"][0], "name": pipe["user"][1]},
                   "commit": {"id": pipe["sha"], "title": pipe["title"], "message": pipe["title"]},
                   "builds": [self.job_info(entry) for entry in pipe["builds"]],
                   "bridges": [self.job_info(entry) for entry in pipe["bridges"]]}
        if pipe["parent"]:
            payload["source_pipeline"] = pipe["parent"]
        if pipe["mr_iid"]:
            payload["merge_request"] = {"iid": pipe["mr_iid"], "title": pipe["title"], "source_branch": pipe["ref"],
                                        "target_branch": "main",
                                        "url": "{}/{}/-/merge_requests/{}".format(GITLAB_URL, proj["path"],
                                                                                  pipe["mr_iid"])}
        self.emit("Pipeline Hook", payload)

    def emit_job(self, pipe, entry):
        """Job Hook"""
        proj = pipe["proj"]
        ref = ("refs/merge-requests/{}/head".format(pipe["mr_iid"]) if pipe["mr_iid"] else pipe["ref"])
        payload = {"object_kind": "build", "ref": ref, "tag": pipe["tag"], "sha": pipe["sha"],
                   "pipeline_id": pipe["id"], "build_id": entry["id"], "build_name": entry["name"],
                   "build_stage": entry["stage"], "build_status": entry["status"],
                   "build_created_at": gl_time(entry["created"]),
                   "build_started_at": (gl_time(entry["started"]) if entry["started"] else None),
                   "build_finished_at": (gl_time(entry["finished"]) if entry["finished"] else None),
                   "build_duration": self.duration(entry), "build_queued_duration": 4.2,
                   "build_allow_failure": entry["allow_failure"],
                   "build_failure_reason": ("script_failure" if (entry["status"] == "failed") else "unknown_failure"),
                   "retries_count": entry.get("retries", 0), "project_id": proj["id"],
                   "project_name": proj["name"], "project": self.project_info(proj),
                   "user": {"username": pipe["user"][0], "name": pipe["user"][1]},
                   "commit": {"id": pipe["id"], "sha": pipe["sha"], "message": pipe["title"],
                              "status": pipe["status"]}}
        if entry["started"]:
            payload["runner"] = {"id": 7, "description": "runner-{} (docker)".format(1 + (entry["id"] % 4))}
        self.emit("Job Hook", payload)


# Backend


def write_config(dirname, port, verbose):
    """Write the backend config of the demo, returns its path"""
    path = os.path.join(dirname, "demo.yaml")
    with open(path, "w", encoding="utf-8") as file:
        file.write("# Demo backend (tools/demo/demo.py), made new at each start\n")
        file.write("listen: \"127.0.0.1\"\nport: {}\n".format(port))
        file.write("data: \"{}\"\n".format(os.path.join(dirname, "data")))
        file.write("log_level: \"{}\"\n".format("info" if verbose else "warning"))
        file.write("editors: [\"{}\"]\n".format(EDITOR))
    return path


def prepare_dir(dirname):
    """Check the demo directory, remove the old data. Raises RuntimeError for a directory that is not a demo."""
    if os.path.isdir(dirname) and os.listdir(dirname) and (not os.path.exists(os.path.join(dirname, "demo.yaml"))):
        raise RuntimeError("{}: not a demo directory (no demo.yaml), not used".format(dirname))
    shutil.rmtree(os.path.join(dirname, "data"), ignore_errors=True)
    os.makedirs(os.path.join(dirname, "data"), exist_ok=True)


def write_history(dirname, sim, end):
    """Run the simulation up to end, write its events into the event store. Returns the number of events."""
    events = []
    sim.sink = (lambda stamp, event, payload: events.append((round(stamp, 3), event, payload)))
    sim.run_until(end)
    store = EventStore(os.path.join(dirname, "data"))
    try:
        return store.add_events("gitlab", events)
    finally:
        store.close()


def request(url, data=None, headers=None):
    """HTTP request, returns the parsed JSON response. Raises OSError."""
    body = (json.dumps(data).encode() if (data is not None) else None)
    req = urllib.request.Request(url, data=body, headers=dict(headers or {}, **{"Content-Type": "application/json"}))
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read() or b"null")


def wait_backend(base, proc, timeout=60.0):
    """Wait until the backend answers. Raises RuntimeError."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise RuntimeError("backend stopped (exit code {})".format(proc.returncode))
        try:
            request(base + "/api/devices")
            return
        except OSError:
            time.sleep(0.3)
    raise RuntimeError("backend does not answer")


# Fake Lämplis


async def device_conn(dev, ws_base, stop):
    """One fake Lämpli: connect, report, take the settings, reconnect"""
    rng = random.Random(dev["id"])
    settings = dict(dev["settings"])
    query = urllib.parse.urlencode({"device": dev["id"], "fw": FW_VERSION, "ssid": "demo-wifi",
                                    "ip": "192.168.1.{}".format(20 + int(dev["id"][-1])), "rssi": dev["rssi"],
                                    "leds": dev["leds"], "app": "laempli"})
    start = time.monotonic()
    ack = 0
    while not stop.is_set():
        try:
            async with ws_connect(ws_base + "/ws/device?" + query) as conn:
                LOG.info("%s connected", dev["id"])
                options = True
                while not stop.is_set():
                    data = {"device": dev["id"], "rssi": (dev["rssi"] + rng.randint(-3, 3)),
                            "uptime": (int(time.monotonic() - start) + 86400),
                            "heap": (dev["heap"] + rng.randint(-900, 900)), "settings": settings, "ack": ack}
                    if options:
                        data.update(cpu=dev["cpu"], heap_size=dev["heap_size"], options=dev["options"])
                        options = False
                    await conn.send(json.dumps({"event": "report", "data": data}))
                    try:
                        msg = json.loads(await asyncio.wait_for(conn.recv(), timeout=30))
                    except asyncio.TimeoutError:
                        continue
                    if msg.get("event") == "settings":
                        values = {key: value for key, value in msg["data"].get("settings", {}).items()
                                  if key in settings}
                        settings.update(values)
                        ack = msg["data"].get("seq", ack)
                        LOG.info("%s settings: %s", dev["id"], values)
                    elif msg.get("event") not in ("hello", "state", "time"):
                        LOG.info("%s: %s (ignored)", dev["id"], msg.get("event"))
                        await asyncio.sleep(0.5)
        except (OSError, asyncio.TimeoutError) as ex:
            LOG.debug("%s: %s", dev["id"], ex)
        except Exception as ex:  # pylint: disable=broad-except
            LOG.warning("%s: %s", dev["id"], ex)
        if not stop.is_set():
            await asyncio.sleep(2)


async def devices_run(ws_base, stop):
    """All fake Lämplis"""
    await asyncio.gather(*(device_conn(dev, ws_base, stop) for dev in DEVICES))


def set_leds(base):
    """LED config of the fake Lämplis (API)"""
    for dev in DEVICES:
        request(base + "/api/leds/" + dev["id"], dev["config"], {"X-Forwarded-User": EDITOR})


# Live


def live_loop(sim, base, stop):
    """Run the simulation in real time, post the events to the backend"""
    def post(_stamp, event, payload):
        try:
            request(base + "/api/gitlab", payload, {"X-Gitlab-Event": event})
        except OSError as ex:
            LOG.warning("GitLab event %s: %s", event, ex)
        LOG.debug("%s %s", event, payload.get("object_attributes", payload).get("status", payload.get("build_status")))
    sim.sink = post
    while not stop.is_set():
        wait = sim.next_time() - time.time()
        if wait > 0:
            stop.wait(min(wait, 1.0))
            continue
        sim.step()


def setup_log(verbose, colours):
    """Logging: journal prefixes under systemd, else coloured (auto: on a terminal) or plain"""
    level = ("debug" if verbose else "info")
    if is_journal(sys.stderr):
        setup_logging(level, journal=True)
    elif (colours == "yes") or ((colours == "auto") and sys.stderr.isatty()):
        setup_logging(level, journal=False)
    else:
        logging.basicConfig(level=level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def parse_args():
    """Command line"""
    parser = argparse.ArgumentParser(prog="demo.py", description=__doc__.split("\n\n", 1)[0],
                                     epilog=__doc__.split("\n\n", 1)[1],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dir", nargs="?", default=os.path.join(TOP, "data", "demo"),
                        help="demo directory (default: data/demo)")
    parser.add_argument("--port", type=int, default=8099, help="web port, WebSocket: port + 1 (default: 8099)")
    parser.add_argument("--history", type=float, default=7.0, help="days of history (default: 7)")
    parser.add_argument("--seed", type=int, default=1, help="random seed (default: 1)")
    parser.add_argument("--pace", choices=("normal", "fast"), default="normal",
                        help="live: normal (as real CI) or fast (60 times faster, more failures)")
    parser.add_argument("--verbose", "-v", action="store_true", help="more output (also of the backend)")
    parser.add_argument("--colours", choices=("auto", "yes", "no"), default="auto", help="coloured output")
    return parser.parse_args()


def main():
    """Prepare the data, start the backend, run fake GitLab and fake Lämplis"""
    args = parse_args()
    setup_log(args.verbose, args.colours)
    dirname = os.path.abspath(args.dir)
    try:
        prepare_dir(dirname)
    except RuntimeError as ex:
        LOG.error("%s", ex)
        return 1
    config = write_config(dirname, args.port, args.verbose)

    now = time.time()
    # Start on a full hour, so the same seed gives the same pipelines
    sim = Simulation(args.seed, (now - (args.history * 86400)) // 3600 * 3600, None)
    num = write_history(dirname, sim, now)
    LOG.info("History: %d GitLab events over %.1f days", num, args.history)
    if args.pace == "fast":
        sim.work_hours = False
        sim.fails = FAST_FAILS
        sim.set_scale(FAST)

    base = "http://127.0.0.1:{}".format(args.port)
    proc = subprocess.Popen(["/usr/bin/python3", os.path.join(TOP, "backend", "tschenggins-laempli.py"),  # noqa
                             "--config", config])
    stop = threading.Event()
    try:
        wait_backend(base, proc)
        LOG.info("Backend: %s/web (editor: X-Forwarded-User: %s)", base, EDITOR)
        thread = threading.Thread(target=live_loop, args=(sim, base, stop), daemon=True)
        thread.start()
        loop = asyncio.new_event_loop()
        astop = asyncio.Event()

        async def run():
            task = asyncio.ensure_future(devices_run("ws://127.0.0.1:{}".format(args.port + 1), astop))
            await asyncio.sleep(1.5)
            await loop.run_in_executor(None, set_leds, base)
            LOG.info("Fake Lämplis: %s", ", ".join(dev["id"] for dev in DEVICES))
            await task

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, astop.set)
        main_task = loop.create_task(run())
        loop.run_until_complete(astop.wait())
        main_task.cancel()
        try:
            loop.run_until_complete(main_task)
        except asyncio.CancelledError:
            pass
    except (RuntimeError, OSError) as ex:
        LOG.error("%s", ex)
        return 1
    finally:
        stop.set()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        LOG.info("Demo stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
