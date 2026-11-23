"""Generic source: scripts set the state of their jobs (e.g. cron jobs with curl)

A script sets a job with POST /api/generic/<name>/<name>... (the web layer reads the fields from the query string, the
form or a JSON object, see parse_update()) and removes it with DELETE. If tokens are configured, the X-Laempli-Token
header must be one of them. The job path is ["generic", <name>, ...] (1 to 7 names).

Fields of an update (all optional, an update changes only the fields it has):

- status: STOPPED, RUNNING, WAITING or UNKNOWN (any case)
- result: PASS, FAIL, WARN or UNKNOWN (any case)
- exit: exit code of a command: 0 -> result PASS, else FAIL; status STOPPED (if no status is given)
- link: URL for more info (http or https)
- info: {name: text}, replaces the info lines of the script (form and query string: info.<name>=<text>)
- timeout: expect the next update within this many seconds (0: no timeout). It stays until an update changes it.
  When it is over, the status is UNKNOWN (the LED blinks), info "status" is "late", until the next update.

A new job starts with status STOPPED and result UNKNOWN. The time of the job is the time of its last update.

Storage: only the raw events (see source.py, in the event store, "event" is "set" or "delete", "data" is
the checked update with "path" and "client" (remote IP)). The data (in memory, see get()):

    {
        "format": 1,
        "jobs": {
            "<path as JSON>": {"path": ["nas", "backup"], "status": "STOPPED", "result": "PASS", "link": null,
                               "info": {"size": "12 GB"}, "timeout": 90000, "updated": <unix time [s]>}
        }
    }

Late jobs: check_late() (each LATE_CHECK seconds, see start()) calls a function if the set of late jobs changed.
The housekeeping removes jobs without update in keep_days (expire_jobs()).

Event log: time, action (set, delete), path, status, result, timeout, client. All entries in the event store (made
again by the replay at start), see events(). LOG_COLUMNS: entry key of each column of the generic events table.
"""

import hmac
import json
import logging
import threading
import time
import urllib.parse

from .jobs import RESULTS, STATUS
from .leds import PATH_MAX, PATH_NAME_MAX
from .source import KEEP_DAYS, EventError, Source, clean_info, fmt_duration

LOG = logging.getLogger(__name__)

# Data format version
FORMAT = 1

# Source name (first element of the job paths)
SOURCE = "generic"

# Most jobs, info lines, length of info names and texts, link and timeout
MAX_JOBS = 1000
MAX_INFO = 20
INFO_NAME_MAX = 40
INFO_TEXT_MAX = 200
LINK_MAX = 1000
TIMEOUT_MAX = 366 * 86400

# Late job check interval [s]
LATE_CHECK = 60

# Fields of an update
FIELDS = ("status", "result", "exit", "link", "info", "timeout")


def empty_data():
    """Get data without jobs"""
    return {"format": FORMAT, "jobs": {}}


def check_path(path):
    """Check path of a job (without the source name), returns it with stripped names. Raises EventError."""
    if (not isinstance(path, list)) or (not all(isinstance(name, str) for name in path)):
        raise EventError("path must be a list of names")
    path = [name.strip() for name in path]
    if (not path) or (len(path) > (PATH_MAX - 1)):
        raise EventError("path must have 1-{} names".format(PATH_MAX - 1))
    if not all(0 < len(name) <= PATH_NAME_MAX for name in path):
        raise EventError("path names must have 1-{} chars".format(PATH_NAME_MAX))
    return path


def to_int(value, name):
    """Get integer of a field (int or decimal text). Raises EventError."""
    if isinstance(value, bool):
        raise EventError("{} must be an integer".format(name))
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except ValueError as ex:
        raise EventError("{} must be an integer".format(name)) from ex


def parse_update(fields):
    """Check the fields of an update ({name: value}), returns the update. Raises EventError.

    status and result in uppercase, exit gives result (and status STOPPED), info as {name: text}.
    """
    if not isinstance(fields, dict):
        raise EventError("fields must be an object")
    info = fields.get("info")
    if (info is not None) and (not isinstance(info, dict)):
        raise EventError("info must be an object")
    info = (dict(info) if (info is not None) else None)
    unknown = []
    for key, value in fields.items():
        if key.startswith("info."):
            info = (info or {})
            info[key[5:]] = value
        elif key not in FIELDS:
            unknown.append(key)
    if unknown:
        raise EventError("unknown fields: {}".format(", ".join(sorted(unknown))))

    update = {}
    for key, values in (("status", STATUS), ("result", RESULTS)):
        if fields.get(key) is not None:
            value = str(fields[key]).strip().upper()
            if value not in values:
                raise EventError("{} must be one of {}".format(key, ", ".join(values)))
            update[key] = value
    if fields.get("exit") is not None:
        code = to_int(fields["exit"], "exit")
        update.setdefault("result", ("PASS" if (code == 0) else "FAIL"))
        update.setdefault("status", "STOPPED")
    if fields.get("link") is not None:
        link = str(fields["link"]).strip()
        if link and ((len(link) > LINK_MAX) or (urllib.parse.urlparse(link).scheme not in ("http", "https"))):
            raise EventError("link must be an http(s) URL (max. {} chars)".format(LINK_MAX))
        update["link"] = (link or None)
    if info is not None:
        if len(info) > MAX_INFO:
            raise EventError("max. {} info lines".format(MAX_INFO))
        clean = {}
        for name, text in info.items():
            name = str(name).strip()
            if (not name) or (len(name) > INFO_NAME_MAX) or (name in ("status", "updated", "timeout")):
                raise EventError("info names must have 1-{} chars, not status, updated, timeout".format(
                    INFO_NAME_MAX))
            text = ("" if (text is None) else str(text).strip())
            if len(text) > INFO_TEXT_MAX:
                raise EventError("info texts must have max. {} chars".format(INFO_TEXT_MAX))
            clean[name] = text
        update["info"] = clean
    if fields.get("timeout") is not None:
        timeout = to_int(fields["timeout"], "timeout")
        if (timeout < 0) or (timeout > TIMEOUT_MAX):
            raise EventError("timeout must be 0-{} s".format(TIMEOUT_MAX))
        update["timeout"] = timeout
    return update


def is_late(job, now):
    """Check if the timeout of a job is over"""
    return bool(job.get("timeout")) and (now > (job["updated"] + job["timeout"]))


class Generic(Source):
    """Job states from scripts"""

    SOURCE = SOURCE
    TITLE = "generic"
    LOG_COLUMNS = ("time", "action", "path", "status", "result", "timeout", "client")

    def __init__(self, dirname, tokens=(), keep_days=KEEP_DAYS, store=None):
        super().__init__(dirname, keep_days, store)
        self.tokens = [token.encode("utf-8") for token in tokens]
        self.data = empty_data()
        self.late = set()
        self.stop_event = threading.Event()
        self.thread = None

    def check_token(self, token):
        """Check X-Laempli-Token header, returns None if OK (or no tokens configured), else the error text"""
        if not self.tokens:
            return None
        if not token:
            return "no token"
        for expected in self.tokens:
            if hmac.compare_digest(token.encode("utf-8"), expected):
                return None
        return "bad token"

    def handle(self, event, payload, now=None, raw=True):
        """Handle an event ("set" or "delete", payload: update (see parse_update()) with "path" and "client")

        now: event time (default: current time), raw: add to the raw events. Returns a short description (for the
        log). Raises EventError.
        """
        if not isinstance(payload, dict):
            raise EventError("payload not an object")
        if event not in ("set", "delete"):
            raise EventError("unknown event {}".format(event))
        path = check_path(payload.get("path"))
        update = parse_update({key: value for key, value in payload.items() if key not in ("path", "client")})
        now = (time.time() if now is None else now)
        key = json.dumps(path)
        with self.lock:
            job = self.data["jobs"].get(key)
            if event == "delete":
                if job is None:
                    raise EventError("no job {}".format(" / ".join(path)))
                del self.data["jobs"][key]
            else:
                if (job is None) and (len(self.data["jobs"]) >= MAX_JOBS):
                    raise EventError("too many jobs (max. {})".format(MAX_JOBS))
                if job is None:
                    job = {"path": path, "status": "STOPPED", "result": "UNKNOWN", "link": None, "info": {},
                           "timeout": 0}
                    self.data["jobs"][key] = job
                job.update(update)
                job["updated"] = round(now, 3)
            if raw:
                self._log_event(event, dict(update, path=path, client=payload.get("client")), now)
            self._add_log({"time": round(now, 3), "action": event, "path": " / ".join(path),
                           "status": update.get("status"), "result": update.get("result"),
                           "timeout": update.get("timeout"), "client": payload.get("client")})
        if event == "delete":
            return "{} deleted".format(" / ".join(path))
        return "{} {} {}".format(" / ".join(path), job["status"], job["result"])

    @staticmethod
    def log_job(entry):
        """Get (job path, "job", state) of an event log entry (RUNNING, WAITING, else the result or the status)

        None for a delete or an update without status and result.
        """
        status = entry.get("status")
        if status not in ("RUNNING", "WAITING"):
            status = (entry.get("result") or status)
        if (entry.get("action") != "set") or (not entry.get("path")) or (not status):
            return None
        return ([SOURCE] + entry["path"].split(" / "), "job", status)

    def expire_jobs(self, now=None):
        """Remove jobs without update in keep_days, returns the number of removed jobs"""
        limit = (time.time() if now is None else now) - (self.keep_days * 86400)
        with self.lock:
            old = [key for key, job in self.data["jobs"].items() if job["updated"] < limit]
            for key in old:
                del self.data["jobs"][key]
        if old:
            LOG.info("Removed %d old generic jobs", len(old))
        return len(old)

    def get(self):
        """Get a copy of the data"""
        with self.lock:
            return json.loads(json.dumps(self.data))

    def jobs(self, now=None):
        """Get job entries (see jobs.py)"""
        now = (time.time() if now is None else now)
        entries = []
        with self.lock:
            for job in self.data["jobs"].values():
                late = is_late(job, now)
                auto = {"status": ("late" if late else None), "updated": fmt_stamp(job["updated"]),
                        "timeout": (fmt_duration(job["timeout"]) if job.get("timeout") else None)}
                entries.append({"path": [SOURCE] + job["path"], "status": ("UNKNOWN" if late else job["status"]),
                                "result": job["result"], "link": job.get("link"), "time": job["updated"],
                                "info": dict(clean_info(auto), **job.get("info", {}))})
        return sorted(entries, key=lambda entry: entry["path"])

    def job(self, path, now=None):
        """Get job entry of a path (without the source name), None if unknown"""
        full = [SOURCE] + list(path)
        return next((entry for entry in self.jobs(now) if entry["path"] == full), None)

    def check_late(self, now=None):
        """Check for late jobs, returns True if the set of late jobs changed since the last check"""
        now = (time.time() if now is None else now)
        with self.lock:
            late = {key for key, job in self.data["jobs"].items() if is_late(job, now)}
            changed = (late != self.late)
            self.late = late
        return changed

    def start(self, on_change, interval=LATE_CHECK):
        """Start the late job check thread: calls on_change() if the set of late jobs changed"""
        def run():
            while not self.stop_event.wait(interval):
                try:
                    if self.check_late():
                        on_change()
                except Exception:  # pylint: disable=broad-except
                    LOG.exception("Late job check failed")
        self.stop_event.clear()
        self.thread = threading.Thread(target=run, name="generic-late", daemon=True)
        self.thread.start()

    def stop(self):
        """Stop the late job check thread, wait for it"""
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join()
            self.thread = None


def fmt_stamp(stamp):
    """Format unix time as local time"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp))
