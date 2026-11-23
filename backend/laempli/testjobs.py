"""Test source: jobs that the user changes on a web page, to check how a device shows a job

Jobs (see jobs.py), always there:

    ["test", "test01"] ... ["test", "test10"]

A new job has status STOPPED and result UNKNOWN, and no time. The tab Test changes the status or the result of a job.
A change gives the job a time, so the LED timeouts apply as for the jobs of other sources.

Storage: only the raw events (see source.py, in the event store, "event" is "set", "data" is the change with
"name" and "user"). There is no event log.
"""

import logging
import time

from .generic import fmt_stamp, parse_update
from .source import KEEP_DAYS, EventError, Source, clean_info

LOG = logging.getLogger(__name__)

# Source name (first element of the job paths)
SOURCE = "test"

# Number of jobs
NUM_JOBS = 10


class TestJobs(Source):
    """Jobs that the user changes"""

    SOURCE = SOURCE
    TITLE = "test"

    def __init__(self, dirname, keep_days=KEEP_DAYS, store=None):
        super().__init__(dirname, keep_days, store)
        self.data = {"test{:02d}".format(num): {"status": "STOPPED", "result": "UNKNOWN", "updated": None,
                                                "user": None}
                     for num in range(1, NUM_JOBS + 1)}

    def has_job(self, name):
        """Check if the job exists"""
        return (name in self.data)

    def handle(self, event, payload, now=None, raw=True):
        """Handle an event ("set", payload: {"name": ..., "status": ..., "result": ..., "user": ...})

        now: event time (default: current time), raw: add to the raw events. Returns a short description (for the
        log). Raises EventError.
        """
        if (event != "set") or (not isinstance(payload, dict)):
            raise EventError("bad test event")
        name = payload.get("name")
        if name not in self.data:
            raise EventError("no job {}".format(name))
        update = parse_update({key: payload.get(key) for key in ("status", "result")})
        if not update:
            raise EventError("no status or result")
        now = (time.time() if now is None else now)
        with self.lock:
            job = self.data[name]
            job.update(update, updated=round(now, 3), user=payload.get("user"))
            if raw:
                self._log_event(event, dict(update, name=name, user=payload.get("user")), now)
        return "{} {} {}".format(name, job["status"], job["result"])

    def expire_jobs(self, now=None):
        """The jobs stay, returns 0"""
        return 0

    def jobs(self):
        """Get job entries (see jobs.py), in number order"""
        with self.lock:
            return [{"path": [SOURCE, name], "status": job["status"], "result": job["result"], "link": None,
                     "time": job["updated"], "info": clean_info({
                         "updated": (fmt_stamp(job["updated"]) if job["updated"] else None), "user": job["user"]})}
                    for name, job in self.data.items()]
