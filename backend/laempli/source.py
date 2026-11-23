"""Sources of job states: shared parts (raw events, event log, info formatting)

A source (gitlab.py, github.py) gets webhook events, keeps its data in memory and gives job entries (see jobs.py).

Raw events: each received event goes to the event store (eventdb.py, <data dir>/events.sqlite, table events: time,
event header, payload). At start, load() removes the events older than keep_days and replays the others: the raw
events are the only stored data. The housekeeping (see housekeeping.py) removes old events (expire_events()) and
calls expire_jobs() of the source.

Old raw event files (<data dir>/<source>_raw.jsonl, one JSON object per line: {"time": <unix time [s]>, "event":
<event header>, "data": <payload>}): load() imports them once into the store and renames them to
<source>_raw.jsonl.imported.

Event log: short entries of all events (for the web page), in the store (table log), made again by the replay at
start, see events() and query_events(). LOG_COLUMNS: the entry key of each column of the web table (sort).
"""

import datetime
import fnmatch
import json
import logging
import os
import threading
import time

from .eventdb import EventStore

# Default days to keep raw events
KEEP_DAYS = 90


class EventError(Exception):
    """Bad event"""


def first_line(text):
    """Get first line of a text, None if empty"""
    return ((text.strip().split("\n", 1)[0].strip() or None) if isinstance(text, str) else None)


def parse_stamp(value):
    """Get datetime from time text ("2026-10-22T10:00:36.955Z", "2026-10-22 10:00:36 UTC", ...), None if no time"""
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace(" UTC", "+00:00").replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_time(value):
    """Format time text (see parse_stamp()) as local time"""
    stamp = parse_stamp(value)
    if stamp is None:
        return (value if isinstance(value, str) else None)
    return stamp.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def unix_time(value):
    """Get Unix time [s] from time text (see parse_stamp()), None if no time"""
    stamp = parse_stamp(value)
    return (stamp.timestamp() if (stamp is not None) else None)


def fmt_duration(value):
    """Format duration [s] as m:ss or h:mm:ss"""
    if isinstance(value, bool) or (not isinstance(value, (int, float))):
        return None
    hours, rest = divmod(int(round(value)), 3600)
    return ("{}:{:02d}:{:02d}".format(hours, rest // 60, rest % 60) if (hours > 0) else
            "{}:{:02d}".format(rest // 60, rest % 60))


def is_ignored(name, patterns):
    """Check if a name (project path, repository) matches one of the patterns (fnmatch: * ? [...], case matters)"""
    return (isinstance(name, str) and any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns))


def clean_info(info):
    """Get info without empty values, all values as string"""
    return {key: str(value) for key, value in info.items() if (value is not None) and (value != "")}


class Source:
    """Base class of a source: raw events and event log in the event store

    A subclass sets SOURCE (name, first element of its job paths) and implements handle(event, payload, now, raw).
    store: EventStore (None: own store in dirname).
    """

    SOURCE = None
    TITLE = None
    LOG_COLUMNS = ("time",)
    # Kind filter of the event log: {name: [entry "kind" values]}, empty: no filter
    LOG_KINDS = {}

    def __init__(self, dirname, keep_days=KEEP_DAYS, store=None):
        self.dirname = dirname
        self.keep_days = keep_days
        # Reentrant: load() holds it while the replay calls handle()
        self.lock = threading.RLock()
        self.raw_path = os.path.join(dirname, "{}_raw.jsonl".format(self.SOURCE))
        os.makedirs(dirname, exist_ok=True)
        self.store = (store if (store is not None) else EventStore(dirname))
        # Logger of the subclass module, e.g. laempli.gitlab
        self.logger = logging.getLogger(type(self).__module__)

    def handle(self, event, payload, now=None, raw=True):
        """Handle webhook event, returns a short description (None: ignored). Raises EventError."""
        raise NotImplementedError

    def _log_event(self, event, payload, now):
        # Add raw event to the store. Lock held.
        self.store.add_event(self.SOURCE, round(now, 3), event, payload)

    def _add_log(self, entry):
        # Add event log entry. Lock held.
        self.store.add_log(self.SOURCE, entry)

    @staticmethod
    def _read_jsonl(path, logger):
        # Raw events of a JSONL file as (time, event, data), bad lines skipped
        with open(path, "r", encoding="utf-8") as fh:
            for num, line in enumerate(fh, 1):
                try:
                    entry = json.loads(line)
                    stamp = entry["time"]
                    if isinstance(stamp, bool) or (not isinstance(stamp, (int, float))):
                        raise ValueError("bad time")
                except (ValueError, TypeError, KeyError) as ex:
                    logger.warning("%s line %d skipped: %s", path, num, ex)
                    continue
                yield (stamp, entry.get("event"), entry.get("data"))

    def _replay(self, events, name):
        # Handle raw events again, returns number. Lock and store transaction held.
        num = 0
        for stamp, event, data in events:
            try:
                self.handle(event, data, stamp, raw=False)
            except (ValueError, AttributeError, EventError) as ex:
                self.logger.warning("Replay %s: %s", name, ex)
            num += 1
        return num

    def replay(self, path):
        """Handle the raw events of a JSONL file again (to rebuild the data), returns number of events"""
        with self.lock, self.store.transaction():
            return self._replay(self._read_jsonl(path, self.logger), path)

    def import_jsonl(self):
        """Import the old raw event file once, then rename it (*.imported). Returns number of events."""
        if not os.path.exists(self.raw_path):
            return 0
        if self.store.count_events(self.SOURCE) > 0:
            self.logger.warning("%s not imported: store has %s events", self.raw_path, self.TITLE)
            return 0
        num = self.store.add_events(self.SOURCE, self._read_jsonl(self.raw_path, self.logger))
        os.replace(self.raw_path, self.raw_path + ".imported")
        self.logger.info("Imported %d raw %s events from %s", num, self.TITLE, self.raw_path)
        return num

    def load(self, now=None):
        """Import old raw event file, remove raw events older than keep_days, replay the others

        The replay makes the event log again. Returns number of events.
        """
        self.import_jsonl()
        self.expire_events(now)
        with self.lock, self.store.transaction():
            self.store.clear_log(self.SOURCE)
            return self._replay(self.store.iter_events(self.SOURCE), self.TITLE)

    def raw_events(self):
        """Get all raw events: [{"time": ..., "event": ..., "data": ...}, ...] in time order"""
        return [{"time": stamp, "event": event, "data": data}
                for stamp, event, data in self.store.iter_events(self.SOURCE)]

    def expire_events(self, now=None):
        """Remove raw events and log entries older than keep_days, returns the number of removed raw events"""
        limit = (time.time() if now is None else now) - (self.keep_days * 86400)
        removed = self.store.expire(self.SOURCE, limit)
        if removed > 0:
            self.logger.info("Removed %d old raw %s events", removed, self.TITLE)
        return removed

    def expire_jobs(self, now=None):
        """Remove jobs without events in keep_days, returns the number of removed items"""
        raise NotImplementedError

    def jobs(self):
        """Get job entries (see jobs.py)"""
        raise NotImplementedError

    @staticmethod
    def log_job(entry):
        """Get (job path, kind "pipeline" or "job", status text) of an event log entry, None if no job state"""
        return None

    def query_events(self, start=0, length=None, q=None, sort=None, since=None, until=None, fields=None):
        """Get event log entries: (total, filtered, entries), see EventStore.query_log()"""
        return self.store.query_log(self.SOURCE, start, length, q, sort, since, until, fields)

    def events(self, start=0, length=None, q=None, sort=None, since=None, until=None, fields=None):
        """Get event log entries, newest first (default), see query_events()"""
        return self.query_events(start, length, q, sort, since, until, fields)[2]

    def count_events(self):
        """Get the number of event log entries"""
        return self.query_events(length=0)[0]

    def count_ignored(self):
        """Get the number of ignored event log entries"""
        return self.store.count_ignored(self.SOURCE)

    def last_event(self):
        """Get the time of the newest event log entry, None if none"""
        return self.store.last_time(self.SOURCE)
