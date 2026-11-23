"""Event store: raw events and event logs of all sources in one SQLite file (<data dir>/events.sqlite)

Tables:

    events(id, source, time, event, data)   raw events (data: JSON payload), in time order (id), see source.py
    log(id, source, time, entry, text)      event log entries (entry: JSON), made again by the replay at start;
                                            text: all values in lower case, for the search
    meta(key, value)                        "schema": schema version

The raw events are the only stored source data. Read them with the sqlite3 tool, e.g.:

    sqlite3 data/events.sqlite "SELECT datetime(time, 'unixepoch'), event FROM events WHERE source = 'gitlab'"

One connection for all threads, with a lock (RLock: a transaction() holds it for all its statements). WAL journal:
readers of other processes (sqlite3 tool) do not block the backend. Each statement outside of a transaction() commits
at once. Lock order: source lock first, then the store lock (never the other way).
"""

import contextlib
import json
import logging
import os
import re
import sqlite3
import threading
import time

LOG = logging.getLogger(__name__)

FILE_NAME = "events.sqlite"

# Schema version
SCHEMA = 1

# Rows per read in iter_events()
BATCH = 500

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, source TEXT NOT NULL, time REAL NOT NULL,
                                   event TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS events_source_time ON events (source, time);
CREATE TABLE IF NOT EXISTS log (id INTEGER PRIMARY KEY, source TEXT NOT NULL, time REAL NOT NULL,
                                entry TEXT NOT NULL, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS log_source_time ON log (source, time);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

# Search words: "quoted text" or a word
WORD_RE = re.compile(r'"([^"]*)"|(\S+)')


def search_words(text):
    """Get the lower case search words of a text ("quoted words" are one word)"""
    return [(quoted or word).lower() for quoted, word in WORD_RE.findall(text or "") if (quoted or word)]


def entry_text(entry):
    """Get the search text of a log entry: local time and all other values, lower case"""
    parts = []
    if isinstance(entry.get("time"), (int, float)):
        parts.append(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry["time"])))
    parts.extend(str(value) for key, value in entry.items() if (key != "time") and (value is not None))
    return " ".join(parts).lower()


def like_pattern(word):
    """Get LIKE pattern (escape: backslash) that finds word"""
    return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class EventStore:
    """SQLite file with the raw events and the event logs"""

    def __init__(self, dirname):
        os.makedirs(dirname, exist_ok=True)
        self.path = os.path.join(dirname, FILE_NAME)
        self.lock = threading.RLock()
        self.depth = 0  # transaction() nesting
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SCHEMA_SQL)
            row = self.db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
            if row is None:
                self.db.execute("INSERT INTO meta (key, value) VALUES ('schema', ?)", (str(SCHEMA),))
            elif int(row[0]) != SCHEMA:
                raise RuntimeError("{}: schema {}, need {}".format(self.path, row[0], SCHEMA))

    def close(self):
        """Close the database"""
        with self.lock:
            self.db.close()

    @contextlib.contextmanager
    def transaction(self):
        """All statements in the block in one transaction (rollback on an exception), holds the lock"""
        with self.lock:
            self.depth += 1
            if self.depth == 1:
                self.db.execute("BEGIN")
            try:
                yield
            except BaseException:
                self.depth -= 1
                if self.depth == 0:
                    self.db.execute("ROLLBACK")
                raise
            self.depth -= 1
            if self.depth == 0:
                self.db.execute("COMMIT")

    def checkpoint(self):
        """Write the WAL file into the database and truncate it (e.g. after the replay at start)"""
        with self.lock:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def add_event(self, source, stamp, event, data):
        """Add a raw event"""
        with self.lock:
            self.db.execute("INSERT INTO events (source, time, event, data) VALUES (?, ?, ?, ?)",
                            (source, stamp, event, json.dumps(data, separators=(",", ":"))))

    def add_events(self, source, events):
        """Add raw events (iterable of (time, event, data)) in one transaction, returns the number"""
        num = 0
        with self.transaction():
            for stamp, event, data in events:
                self.add_event(source, stamp, event, data)
                num += 1
        return num

    def iter_events(self, source):
        """Get the raw events of a source as (time, event, data), in time order

        Reads in batches: the caller can write (e.g. the log) between the events.
        """
        last = 0
        while True:
            with self.lock:
                rows = self.db.execute("SELECT id, time, event, data FROM events WHERE (source = ?) AND (id > ?) "
                                       "ORDER BY id LIMIT ?", (source, last, BATCH)).fetchall()
            for row_id, stamp, event, data in rows:
                last = row_id
                try:
                    payload = json.loads(data)
                except (TypeError, ValueError):
                    payload = None
                yield (stamp, event, payload)
            if len(rows) < BATCH:
                return

    def count_events(self, source):
        """Get the number of raw events of a source"""
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM events WHERE source = ?", (source,)).fetchone()[0]

    def add_log(self, source, entry):
        """Add an event log entry (dict with "time")"""
        stamp = entry.get("time")
        stamp = (stamp if (isinstance(stamp, (int, float)) and (not isinstance(stamp, bool))) else 0)
        with self.lock:
            self.db.execute("INSERT INTO log (source, time, entry, text) VALUES (?, ?, ?, ?)",
                            (source, stamp, json.dumps(entry, separators=(",", ":")), entry_text(entry)))

    def clear_log(self, source):
        """Remove all event log entries of a source"""
        with self.lock:
            self.db.execute("DELETE FROM log WHERE source = ?", (source,))

    def expire(self, source, limit):
        """Remove raw events and log entries of a source older than limit [s], returns number of raw events"""
        with self.transaction():
            num = self.db.execute("DELETE FROM events WHERE (source = ?) AND (time < ?)", (source, limit)).rowcount
            self.db.execute("DELETE FROM log WHERE (source = ?) AND (time < ?)", (source, limit))
        return num

    def query_log(self, source, start=0, length=None, q=None, sort=None, since=None, until=None, fields=None):
        """Get event log entries of a source

        start, length: page (length None: all), q: search text (all words, see search_words()), sort: list of
        (field, descending), field "time" or an entry key (default: newest first), since, until: time range [s],
        fields: {entry key: [value, ...]}: the entry has one of the values (None: key missing or null, JSON true and
        false: 1 and 0).
        Returns (total, filtered, entries): number of entries of the source, number found, the entries of the page.
        """
        where = ["(source = ?)"]
        params = [source]
        if since is not None:
            where.append("(time >= ?)")
            params.append(since)
        if until is not None:
            where.append("(time < ?)")
            params.append(until)
        for word in search_words(q):
            where.append("(text LIKE ? ESCAPE '\\')")
            params.append(like_pattern(word))
        for key, values in (fields or {}).items():
            path = ("$." + json.dumps(key))
            found = [value for value in values if (value is not None)]
            alts = []
            if found:
                alts.append("(json_extract(entry, ?) IN ({}))".format(", ".join("?" * len(found))))
                params.extend([path] + found)
            if None in values:
                alts.append("(json_extract(entry, ?) IS NULL)")
                params.append(path)
            where.append("({})".format(" OR ".join(alts) if alts else "0"))
        order = []
        order_params = []
        for field, desc in (sort or []):
            if field == "time":
                order.append("time")
            else:
                order.append("json_extract(entry, ?)")
                order_params.append("$." + json.dumps(field))
            order[-1] += (" DESC" if desc else " ASC")
        order.append("id {}".format("ASC" if (sort and (not sort[-1][1])) else "DESC"))
        cond = " AND ".join(where)
        page = [(max(0, int(length)) if (length is not None) else -1), max(0, int(start))]
        with self.lock:
            total = self.db.execute("SELECT COUNT(*) FROM log WHERE source = ?", (source,)).fetchone()[0]
            filtered = (total if (len(where) == 1) else
                        self.db.execute("SELECT COUNT(*) FROM log WHERE " + cond, params).fetchone()[0])
            rows = self.db.execute("SELECT entry FROM log WHERE {} ORDER BY {} LIMIT ? OFFSET ?".format(
                cond, ", ".join(order)), params + order_params + page).fetchall()
        return total, filtered, [json.loads(row[0]) for row in rows]

    def log_range(self, sources, since, until):
        """Get (id, source, entry) of the log entries of sources with since <= time < until, in time order

        The IDs change at each start (the replay makes the log again).
        """
        if not sources:
            return []
        sql = ("SELECT id, source, entry FROM log WHERE (source IN ({})) AND (time >= ?) AND (time < ?) "
               "ORDER BY time, id").format(", ".join("?" * len(sources)))
        with self.lock:
            rows = self.db.execute(sql, list(sources) + [since, until]).fetchall()
        return [(row_id, source, json.loads(entry)) for row_id, source, entry in rows]

    def get_log(self, row_id):
        """Get (source, entry) of a log entry, None if not there"""
        with self.lock:
            row = self.db.execute("SELECT source, entry FROM log WHERE id = ?", (row_id,)).fetchone()
        return ((row[0], json.loads(row[1])) if (row is not None) else None)

    def count_ignored(self, source):
        """Get the number of ignored event log entries of a source ("ignored": true)"""
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM log WHERE (source = ?) AND "
                                   "(json_extract(entry, '$.ignored') = 1)", (source,)).fetchone()[0]

    def last_time(self, source):
        """Get the time of the newest log entry of a source, None if none"""
        with self.lock:
            return self.db.execute("SELECT MAX(time) FROM log WHERE source = ?", (source,)).fetchone()[0]
