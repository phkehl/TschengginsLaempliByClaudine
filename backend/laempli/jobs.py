"""Jobs: the things that the LEDs show, from all sources

A source (e.g. GitLab webhooks, see gitlab.py) gives the state of its jobs. A job is one thing to watch, e.g. a GitLab
pipeline or a GitLab job, later maybe a Jenkins build or a cron job. A path identifies a job: a list of strings, the
source name first, then the things inside the source, from coarse to fine. For GitLab:

    ["gitlab", "core/release-config", "feature/scarthgap"]                                  main pipeline of a ref
    ["gitlab", "core/release-config", "feature/scarthgap", "validate-config"]               job of the main pipeline
    ["gitlab", "core/release-config", "feature/scarthgap", "yocto_trigger"]                 downstream pipeline
    ["gitlab", "core/release-config", "feature/scarthgap", "yocto_trigger", "image-foo"]    job of it

Job entry:

    {"path": [...], "status": "RUNNING", "result": "PASS", "link": "https://...", "time": 1792756431.6,
     "info": {"pipeline": "87295 (#4305)", "commit": "7a36768 Fix foo", ...}}

- status: what the job does now: "STOPPED" (LED steady), "RUNNING" (slow pulse), "WAITING" (fast pulse), "UNKNOWN"
  (slow blink, e.g. no data for the path)
- result: result of the last finished run: "PASS" (green), "FAIL" (red), "WARN" (yellow), "UNKNOWN" (white)
- link: URL for more info (web page), or None
- time: time of the last change [s], or None
- started: start of the newest run [s] (pipelines only, optional): does not change while the run goes on
- info: more info for the web page and the device display, {name: text}, in display order

The jobs are in memory only (made again from the sources at start). GET /api/jobs gives all of them.
"""

import json
import logging
import threading

LOG = logging.getLogger(__name__)

# Status and result values
STATUS = ("STOPPED", "RUNNING", "WAITING", "UNKNOWN")
RESULTS = ("PASS", "FAIL", "WARN", "UNKNOWN")


def unknown_job(path):
    """Get entry of a job without data"""
    return {"path": list(path), "status": "UNKNOWN", "result": "UNKNOWN", "link": None, "time": None, "info": {}}


class Jobs:
    """Jobs of all sources"""

    def __init__(self, changes=None):
        self.changes = changes  # Changes, topic "jobs" (None: no notifications)
        self.lock = threading.Lock()
        self.jobs = {}  # tuple(path) -> entry

    def update(self, source, entries):
        """Replace all jobs of a source, returns True if something changed"""
        new = {tuple(entry["path"]): entry for entry in entries if entry["path"][0] == source}
        with self.lock:
            old = {key: entry for key, entry in self.jobs.items() if key[0] == source}
            if old == new:
                return False
            for key in old:
                del self.jobs[key]
            self.jobs.update(new)
            LOG.debug("Jobs of %s: %d", source, len(new))
        if self.changes is not None:
            self.changes.notify("jobs")
        return True

    def get(self, path):
        """Get a copy of a job entry, an unknown job if the path is unknown"""
        with self.lock:
            entry = self.jobs.get(tuple(path))
            return (json.loads(json.dumps(entry)) if entry is not None else unknown_job(path))

    def all(self):
        """Get a copy of all job entries, sorted by path"""
        with self.lock:
            return json.loads(json.dumps([self.jobs[key] for key in sorted(self.jobs)]))

    def find(self, prefix, length):
        """Get a copy of the job entries with a path of length that starts with prefix"""
        prefix = tuple(prefix)
        with self.lock:
            return json.loads(json.dumps([entry for key, entry in self.jobs.items()
                                          if (len(key) == length) and (key[:len(prefix)] == prefix)]))

    def tree(self):
        """Get all paths as tree: {name: {"job": <path is a job>, "children": {name: {...}}}}"""
        tree = {}
        with self.lock:
            for key in self.jobs:
                children = tree
                for name in key:
                    node = children.setdefault(name, {"job": False, "children": {}})
                    children = node["children"]
                node["job"] = True
        return tree
