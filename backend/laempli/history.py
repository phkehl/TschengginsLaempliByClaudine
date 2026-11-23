"""Job history: the state changes of the jobs over time, from the event logs (Plots tab, GET /api/plots)

Each event log entry with a job state (Source.log_job(): path, kind, status text of the source) gives a point of that
job: time, state, ID of the log entry (the cause, see EventStore.get_log()). Ignored entries (old pipelines, deleted
refs) give no point. A point with the same state as the one before of that job is left out: a point is a change, its
cause is the first event with the new state. A job keeps its state until its next point.

The states are those of the events (e.g. GitLab "pending" is "waiting"), not the LED states (no timeouts, no "warn"
for an allowed failure). STATES: the names, a point has the index.
"""

import time

from .eventdb import search_words

# Plot states, index in the points
STATES = ("running", "waiting", "success", "failed", "warn", "canceled", "skipped", "unknown")

# Status texts of the sources (GitLab, GitHub status or conclusion, generic status or result) -> plot state
STATE_OF = {
    # GitLab
    "running": "running", "pending": "waiting", "created": "waiting", "waiting_for_resource": "waiting",
    "preparing": "waiting", "scheduled": "waiting", "manual": "waiting", "success": "success", "failed": "failed",
    "canceled": "canceled", "canceling": "canceled", "skipped": "skipped",
    # GitHub
    "queued": "waiting", "requested": "waiting", "waiting": "waiting", "in_progress": "running",
    "action_required": "waiting", "failure": "failed", "timed_out": "failed", "startup_failure": "failed",
    "cancelled": "canceled", "neutral": "success", "stale": "unknown",
    # Generic
    "RUNNING": "running", "WAITING": "waiting", "PASS": "success", "FAIL": "failed", "WARN": "warn",
    "STOPPED": "unknown", "UNKNOWN": "unknown",
}

# Most points in one answer
MAX_POINTS = 200000


def plot_state(status):
    """Get the state index of a status text"""
    return STATES.index(STATE_OF.get(status, "unknown"))


def statuses_of(states):
    """Get the status texts of plot states (sorted)"""
    return sorted(status for status, state in STATE_OF.items() if state in states)


def path_text(path):
    """Get the search text of a job path (lower case)"""
    return " ".join(str(name) for name in path).lower()


def job_history(store, sources, since, until, kind=None, q=None, failed=False):
    """Get the state changes of the jobs in a time range

    store: EventStore, sources: {name: Source}, since, until: time range [s], kind: None (all), "pipelines" or
    "jobs", q: search words (all in the job path, see search_words()), failed: only jobs with a failed point.
    Returns {"since", "until", "now", "states": STATES, "series": [{"path", "kind", "points": [[time, state index,
    log entry ID], ...]}, ...] (sorted by path), "points": number of points, "truncated": more than MAX_POINTS}.
    """
    words = search_words(q)
    series = {}  # tuple(path) -> series, None: not found
    num = 0
    truncated = False
    for row_id, source, entry in store.log_range(list(sources), since, until):
        if entry.get("ignored"):
            continue
        job = sources[source].log_job(entry)
        if job is None:
            continue
        path, job_kind, status = job
        if kind and ((kind == "pipelines") != (job_kind == "pipeline")):
            continue
        key = tuple(path)
        item = series.get(key, False)
        if item is False:
            found = all((word in path_text(path)) for word in words)
            item = series[key] = ({"path": path, "kind": job_kind, "points": []} if found else None)
        if item is None:
            continue
        state = plot_state(status)
        if item["points"] and (item["points"][-1][1] == state):
            continue
        if num >= MAX_POINTS:
            truncated = True
            break
        item["points"].append([entry["time"], state, row_id])
        num += 1
    failed_ix = STATES.index("failed")
    found = [item for item in series.values() if item and
             ((not failed) or any((point[1] == failed_ix) for point in item["points"]))]
    found.sort(key=lambda item: [str(name) for name in item["path"]])
    return {"since": since, "until": until, "now": round(time.time(), 3), "states": STATES, "series": found,
            "points": sum(len(item["points"]) for item in found), "truncated": truncated}
