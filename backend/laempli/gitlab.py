"""GitLab webhook events: pipeline status of the projects

GitLab sends pipeline events ("Pipeline Hook", object_kind "pipeline") and job events ("Job Hook", object_kind "build")
as POST to /api/gitlab. The web layer ignores all other events at once. If signing tokens are configured (one per
webhook, GitLab generates them), the webhook-signature header must be valid for one of them (see check_signature()).

For each project, the backend keeps the newest main pipeline of each ref (branch, tag, merge request ref) and,
separately, the newest downstream pipeline (child pipeline, or multi-project pipeline) of each trigger job of that
ref. Downstream pipelines have a source_pipeline (parent project, pipeline and trigger job ID); the trigger job
(bridge) name comes from the bridges of the parent pipeline event. A newer pipeline replaces the older one. Replaced
pipelines stay for PRUNE_AGE (late events), then the backend removes them. Events for an older pipeline than the
known one are ignored. Within a pipeline, the last event wins, but a finished job does not go back to an unfinished
status (events can come out of order). The jobs come from the "builds" of the pipeline events and from the job
events. A job event can come before the event of its pipeline: it makes the pipeline entry, with the pipeline status
from its "commit" object until the pipeline event comes.

Retry: a new job ID for a job name (or trigger job name) that the pipeline already has. GitLab keeps the pipeline and
its created_at, thus the backend keeps the time of the newest retry ("retried", also in the parent pipelines). The
"started" of the pipeline is the later of the two, thus a retry moves the pipeline like a GitHub re-run.

Each slot (main pipeline of a ref, or downstream pipeline of a trigger job) also keeps its result: the result of its
newest finished pipeline, also of a replaced or ignored pipeline, and the result of each job name: the result of the
newest finished job (highest job ID, so a retry wins) of that name in the pipelines of the slot. Results (see
pipeline_result(), job_result()): success -> pass ("passed with warnings", or a failed job with allow_failure:
warn), failed -> fail, canceled -> unknown. skipped and scheduled do not change the result.

Jobs (see jobs.py and jobs()): each slot is a job, and each job name in it:

    ["gitlab", <project path>, <ref>]                       main pipeline
    ["gitlab", <project path>, <ref>, <job>]                job of the main pipeline
    ["gitlab", <project path>, <ref>, <trigger>]            downstream pipeline (the trigger is a job of the parent)
    ["gitlab", <project path>, <ref>, <trigger>, <job>]     job of the downstream pipeline

Merge request pipelines: the pipeline event has the source branch as ref, the job events have
"refs/merge-requests/<iid>/head". The ref of the pipeline event wins: a job event of a known pipeline uses its ref, and
a pipeline event moves the pipeline out of the slot that an earlier job event made.

Status of a job: newest pipeline (or newest job of that name in it) in WAITING: waiting, in RUNNING: running, else
stopped. Manual jobs (see pipeline_state()): a job with status "manual" is waiting. A pipeline with status "running" or
"manual" is running while it has active jobs or active downstream pipelines. Without, it is waiting if it has a manual
job (also in a downstream pipeline): GitLab keeps a pipeline "running" while a downstream pipeline (trigger with
strategy: depend) waits for a manual job. A downstream pipeline of a downstream pipeline has its own trigger name on
the same level (no deeper path).

Deleted branches and tags: a push event ("Push Hook", "Tag Push Hook") with "after" all zeros removes the slots of the
ref (other push events are ignored and not stored). The Remove button of the Jobs page does the same (remove(), event
"Laempli Remove" in the raw events only, the web route does not take it). The project then keeps the ref in "deleted"
with the highest known pipeline ID: later events of that ref with a pipeline ID (downstream: parent pipeline ID) up to
it are ignored (a running pipeline of a deleted branch still sends events), a higher ID (new pipeline) removes the
entry. The housekeeping removes entries older than keep_days.

Ignored projects (config gitlab_ignored_projects, path or pattern, see is_ignored()): their pipeline and job events
are stored (raw events), but only logged (ignored, reason "project ignored"), they make no data and no jobs. The
replay applies the list again, so a change applies at the next start. Push events: their project is not known, so
they do nothing.

Storage: only the raw events (see below). At start, load() removes the raw events older than keep_days and replays
the others. The data (in memory, see get()):

    {
        "format": 4,
        "projects": {
            "<project ID>": {
                "id": 123, "name": "Foo", "path": "group/foo", "url": "https://gitlab.example.com/group/foo",
                "default_branch": "main", "deleted": {"feature/old": [4566, <unix time [s]>]},
                "refs": {
                    "main": {
                        "main": {"pipeline": 4567, "result": "PASS", "result_pipeline": 4560,
                                 "jobs": {"build": {"result": "PASS", "result_job": 1234}}},
                        "downstream": {"yocto_trigger": {"pipeline": 4568}}
                    }
                },
                "pipelines": {
                    "4567": {
                        "id": 4567, "iid": 89, "name": "Commit pipeline", "ref": "main", "tag": false, "sha": ...,
                        "status": "running", "detailed_status": "running", "source": "push", "url": ...,
                        "created_at": ..., "finished_at": ..., "duration": ..., "queued_duration": ..., "user": "flip",
                        "commit": "<commit title>",
                        "updated": <unix time [s]>, "retried": <unix time [s]>,
                        "jobs": {
                            "<job ID>": {"id": 1234, "name": "build", "stage": "build", "status": "success",
                                         "allow_failure": false, "started_at": ..., "finished_at": ...,
                                         "duration": ..., "queued_duration": ..., "failure_reason": ...,
                                         "runner": "server7 (docker)", "retries": 0}
                        },
                        "bridges": {
                            "<job ID>": {"id": 1235, "name": "yocto_trigger", "stage": "yocto", "status": "running"}
                        }
                    },
                    "4568": {
                        "id": 4568, ..., "source": "parent_pipeline", "trigger": "yocto_trigger",
                        "parent": {"project": 123, "pipeline": 4567, "job": 1235}, "jobs": {...}, "bridges": {}
                    }
                }
            }
        }
    }

The trigger name of a multi-project downstream pipeline is "<parent project path>:<trigger job name>". If the parent
pipeline is unknown, the trigger name is "downstream".

Event log: a short entry for each pipeline event (time, project, ref, trigger, pipeline ID and name, status, source,
user, job counts; "ignored" for an old pipeline) and each job event (kind "job", time, project, ref, trigger, pipeline
ID, job name and stage, job status, user; "ignored" for an old pipeline or a deleted ref), each removed ref (kind
"deleted" for a push event, "removed" by hand, time, project, ref, user), all entries in the event store (made
again by the replay at start), see events(). LOG_COLUMNS: entry key of each column of the GitLab events table.

Raw events (see source.py): in the event store, "event" is the X-Gitlab-Event header. The housekeeping
removes events older than keep_days (expire_events()) and slots without events in that time (expire_jobs()).
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import time

from .source import KEEP_DAYS, EventError, Source, clean_info, first_line, fmt_duration, fmt_time
from .source import is_ignored, unix_time

LOG = logging.getLogger(__name__)

# Largest age of a signed event [s] (replay protection)
SIGNATURE_MAX_AGE = 300

# Keep replaced pipelines for this time [s] (late events)
PRUNE_AGE = 3600

# Data format version
FORMAT = 4

# Source name (first element of the job paths)
SOURCE = "gitlab"

# Commit of a deleted branch or tag ("after" of a push event)
NO_COMMIT = "0" * 40

# Prefixes of push event refs
REF_PREFIXES = ("refs/heads/", "refs/tags/")

# Event name of a removal by hand (raw events only)
REMOVE_EVENT = "Laempli Remove"

# Job status that do not change any more
FINISHED = ("success", "failed", "canceled", "skipped")

# Pipeline sources of downstream pipelines (child, multi-project)
DOWNSTREAM_SOURCES = ("parent_pipeline", "pipeline")

# Pipeline and job status that are a result
RESULTS = ("success", "failed", "canceled")

# Pipeline and job status of a waiting job (LED pulses fast) and a running job (LED pulses slow). Other status:
# stopped.
WAITING = ("created", "waiting_for_resource", "preparing", "pending", "canceling", "manual", "waiting_for_callback")
RUNNING = ("running",)

# Active job status (not "created": later stages of a stuck pipeline stay created)
ACTIVE_JOB = ("running", "pending", "preparing", "waiting_for_resource", "canceling", "waiting_for_callback")

# Active downstream pipeline status (a running one only with active jobs)
ACTIVE_PIPELINE = ("created", "waiting_for_resource", "preparing", "pending")

# Max. depth of downstream pipelines to check
MAX_DEPTH = 5

# Pipeline status to check the jobs of (a job event can come before the pipeline event)
CHECK_JOBS = ("running", "manual")

# Job fields from the "builds" of pipeline events
JOB_KEYS = ("name", "stage", "status", "allow_failure", "started_at", "finished_at", "duration", "queued_duration",
            "failure_reason")

# Job fields from job events: (our key, job event key)
JOB_EVENT_KEYS = (("name", "build_name"), ("stage", "build_stage"), ("status", "build_status"),
                  ("allow_failure", "build_allow_failure"), ("started_at", "build_started_at"),
                  ("finished_at", "build_finished_at"), ("duration", "build_duration"),
                  ("queued_duration", "build_queued_duration"), ("failure_reason", "build_failure_reason"),
                  ("retries", "retries_count"))

# Pipeline fields from object_attributes
PIPELINE_KEYS = ("iid", "name", "ref", "tag", "sha", "status", "detailed_status", "source", "url", "created_at",
                 "finished_at", "duration", "queued_duration")


def signing_key(token):
    """Get HMAC key of a signing token ("whsec_<base64>"), raise ValueError if bad"""
    if (not isinstance(token, str)) or (not token.startswith("whsec_")):
        raise ValueError("signing token must start with whsec_")
    try:
        key = base64.b64decode(token[6:], validate=True)
    except binascii.Error as ex:
        raise ValueError("signing token not base64") from ex
    if not key:
        raise ValueError("signing token empty")
    return key


def check_ids(ref, pipeline_id):
    """Check ref and pipeline ID of an event, raise EventError if bad"""
    if (not isinstance(ref, str)) or (not ref) or (not isinstance(pipeline_id, int)):
        raise EventError("no ref or pipeline id")


def push_ref(ref):
    """Get ref name of a push event ref (without refs/heads/ or refs/tags/), None if bad"""
    if (not isinstance(ref, str)) or (not ref):
        return None
    for prefix in REF_PREFIXES:
        if ref.startswith(prefix):
            return (ref[len(prefix):] or None)
    return None


def empty_data():
    """Get data without projects"""
    return {"format": FORMAT, "projects": {}}


def job_counts(pipe):
    """Get number of jobs: all, success, failed"""
    jobs = list(pipe["jobs"].values())
    return {"jobs": len(jobs), "jobs_ok": sum(1 for job in jobs if job.get("status") == "success"),
            "jobs_failed": sum(1 for job in jobs if job.get("status") == "failed")}


def parent_info(source):
    """Get parent of a downstream pipeline from source_pipeline, None for a main pipeline"""
    if not isinstance(source, dict):
        return None
    proj = source.get("project")
    parent = {"project": (proj.get("id") if isinstance(proj, dict) else None), "pipeline": source.get("pipeline_id"),
              "job": source.get("job_id")}
    if not all(isinstance(value, int) for value in parent.values()):
        return None
    return parent


def pipeline_started(pipe):
    """Get start of the newest run of a pipeline [s]: creation or newest retry, None if not known"""
    stamps = [stamp for stamp in (unix_time(pipe.get("created_at")), pipe.get("retried")) if (stamp is not None)]
    return (max(stamps) if stamps else None)


def is_retry(jobs, job_id, name):
    """Check if a job is a new job of a known name (jobs: job or bridge entries of a pipeline)"""
    return ((str(job_id) not in jobs) and bool(name) and
            any(((job.get("name") == name) and (job["id"] < job_id)) for job in jobs.values()))


def pipeline_result(status, detailed_status):
    """Get result of a finished pipeline (status in RESULTS)"""
    if status == "success":
        return ("WARN" if ("warning" in (detailed_status or "")) else "PASS")
    if status == "failed":
        return "FAIL"
    return "UNKNOWN"


def job_result(status, allow_failure):
    """Get result of a finished job (status in RESULTS)"""
    if status == "success":
        return "PASS"
    if status == "failed":
        return ("WARN" if allow_failure else "FAIL")
    return "UNKNOWN"


def job_status(status):
    """Get job status of a pipeline or job status"""
    if status in WAITING:
        return "WAITING"
    if status in RUNNING:
        return "RUNNING"
    return "STOPPED"


def child_index(projects):
    """Get the downstream pipelines of each pipeline: {(project ID, pipeline ID): [(project ID, pipeline), ...]}"""
    index = {}
    for proj in projects:
        for pipe in proj["pipelines"].values():
            parent = pipe.get("parent")
            if parent is not None:
                index.setdefault((parent["project"], parent["pipeline"]), []).append((proj["id"], pipe))
    return index


def pipeline_active(proj_id, pipe, index, depth=0):
    """Check if a running pipeline has active jobs, bridges or downstream pipelines"""
    if any((job.get("status") in ACTIVE_JOB) for job in pipe.get("jobs", {}).values()):
        return True
    children = index.get((proj_id, pipe["id"]), [])
    known = {child["parent"]["job"] for _, child in children}
    if any(((bridge.get("status") in ACTIVE_JOB) and (bridge["id"] not in known))
           for bridge in pipe.get("bridges", {}).values()):
        return True
    for child_proj, child in children:
        status = child.get("status")
        if status in ACTIVE_PIPELINE:
            return True
        if (status in CHECK_JOBS) and (depth < MAX_DEPTH) and pipeline_active(child_proj, child, index, depth + 1):
            return True
    return False


def pipeline_manual(proj_id, pipe, index, depth=0):
    """Check if a pipeline or its downstream pipelines have a manual job"""
    if any((job.get("status") == "manual") for job in pipe.get("jobs", {}).values()):
        return True
    for child_proj, child in index.get((proj_id, pipe["id"]), []):
        status = child.get("status")
        if status == "manual":
            return True
        if (status in CHECK_JOBS) and (depth < MAX_DEPTH) and pipeline_manual(child_proj, child, index, depth + 1):
            return True
    return False


def pipeline_state(proj_id, pipe, index):
    """Get status of a pipeline: WAITING if it waits for a manual job"""
    status = pipe.get("status")
    if status not in CHECK_JOBS:
        return job_status(status)
    if pipeline_active(proj_id, pipe, index):
        return "RUNNING"
    if (status == "manual") or pipeline_manual(proj_id, pipe, index):
        return "WAITING"
    return "RUNNING"


def pipeline_info(pipe):
    """Get job info of a pipeline"""
    commit = " ".join(part for part in ((pipe.get("sha") or "")[:8], pipe.get("commit")) if part)
    return clean_info({
        "pipeline": ("{} (#{})".format(pipe["id"], pipe["iid"]) if pipe.get("iid") else pipe.get("id")),
        "name": pipe.get("name"), "status": pipe.get("status"), "source": pipe.get("source"), "commit": commit,
        "user": pipe.get("user"), "created": fmt_time(pipe.get("created_at")),
        "finished": fmt_time(pipe.get("finished_at")), "duration": fmt_duration(pipe.get("duration")),
        "queued": fmt_duration(pipe.get("queued_duration"))})


def job_info(pipe, run):
    """Get job info of a job (run: newest job of that name in the pipeline, or None)"""
    pipeline = pipeline_info(pipe)
    if run is None:
        return clean_info({"status": "not in pipeline", "pipeline": pipeline.get("pipeline"),
                           "commit": pipeline.get("commit"), "user": pipeline.get("user")})
    return clean_info({
        "job": run.get("id"), "status": run.get("status"), "stage": run.get("stage"),
        "failure": (run.get("failure_reason") if (run.get("status") == "failed") else None),
        "pipeline": pipeline.get("pipeline"), "commit": pipeline.get("commit"), "user": pipeline.get("user"),
        "started": fmt_time(run.get("started_at")), "finished": fmt_time(run.get("finished_at")),
        "duration": fmt_duration(run.get("duration")), "queued": fmt_duration(run.get("queued_duration")),
        "runner": run.get("runner"), "retries": (run.get("retries") or None)})


class Gitlab(Source):
    """Pipeline status from GitLab webhooks"""

    SOURCE = SOURCE
    TITLE = "GitLab"
    LOG_COLUMNS = ("time", "kind", "project", "ref", "trigger", "pipeline", "job", "status", "jobs", "source", "user")
    LOG_KINDS = {"pipelines": [None], "jobs": ["job"]}

    def __init__(self, dirname, signing_tokens=(), keep_days=KEEP_DAYS, store=None, ignored=()):
        super().__init__(dirname, keep_days, store)
        self.ignored = list(ignored)  # project paths or patterns, see is_ignored()
        self.keys = [signing_key(token) for token in signing_tokens]
        self.data = empty_data()

    def check_signature(self, msg_id, timestamp, signature, body, now=None):
        """Check webhook signature (Standard Webhooks, as GitLab does)

        msg_id, timestamp, signature: webhook-id, webhook-timestamp, webhook-signature headers, body: raw body
        (bytes). Signature: "v1,<base64 HMAC-SHA256 of '<msg_id>.<timestamp>.<body>'>", several space-separated
        ones possible. One signing token must match. The timestamp must be at most SIGNATURE_MAX_AGE from now.

        Returns None if OK (or no signing tokens configured), else the error text.
        """
        if not self.keys:
            return None
        if (not msg_id) or (not timestamp) or (not signature):
            return "no signature"
        try:
            age = abs((time.time() if now is None else now) - int(timestamp))
        except ValueError:
            return "bad timestamp"
        if age > SIGNATURE_MAX_AGE:
            return "timestamp too old"
        message = "{}.{}.".format(msg_id, timestamp).encode("utf-8") + body
        sigs = [sig.encode("utf-8") for sig in signature.split()]
        for key in self.keys:
            expected = b"v1," + base64.b64encode(hmac.new(key, message, hashlib.sha256).digest())
            for sig in sigs:
                if hmac.compare_digest(sig, expected):
                    return None
        return "bad signature"

    def _project(self, info):
        # Get project entry, create if new, update its info. Lock held.
        if (not isinstance(info, dict)) or (not isinstance(info.get("id"), int)):
            raise EventError("no project id")
        proj = self.data["projects"].setdefault(str(info["id"]), {"id": info["id"], "refs": {}, "pipelines": {}})
        for key, src in (("name", "name"), ("path", "path_with_namespace"), ("url", "web_url"),
                         ("default_branch", "default_branch")):
            if info.get(src) is not None:
                proj[key] = info[src]
        return proj

    def _trigger(self, proj, parent, old):
        # Get trigger name of a downstream pipeline, old name if parent unknown. Lock held.
        parent_proj = self.data["projects"].get(str(parent["project"]))
        parent_pipe = (parent_proj["pipelines"].get(str(parent["pipeline"])) if parent_proj is not None else None)
        bridge = (parent_pipe["bridges"].get(str(parent["job"])) if parent_pipe is not None else None)
        if (bridge is None) or (not bridge.get("name")):
            return (old or "downstream")
        if parent["project"] != proj["id"]:
            return "{}:{}".format(parent_proj.get("path", parent["project"]), bridge["name"])
        return bridge["name"]

    def _slot(self, proj, ref, parent, pipe):
        # Get slot of a pipeline (create if new) and its trigger name (None for main). Lock held.
        slots = proj["refs"].setdefault(ref, {"downstream": {}})
        if parent is None:
            return slots.setdefault("main", {}), None
        key = self._trigger(proj, parent, (pipe or {}).get("trigger"))
        old = (pipe or {}).get("trigger")
        if (old is not None) and (old != key):
            # Trigger name now known: remove the slot of the old name
            old_slot = slots["downstream"].get(old)
            if (old_slot is not None) and (old_slot.get("pipeline") == pipe["id"]):
                del slots["downstream"][old]
        return slots["downstream"].setdefault(key, {}), key

    @staticmethod
    def _drop_slot(proj, pipe):
        # Remove the slot of a pipeline under its old ref (if it is the newest there). Lock held.
        slots = proj["refs"].get(pipe.get("ref"))
        if slots is None:
            return
        trigger = pipe.get("trigger")
        if trigger is None:
            if slots.get("main", {}).get("pipeline") == pipe["id"]:
                del slots["main"]
        elif slots["downstream"].get(trigger, {}).get("pipeline") == pipe["id"]:
            del slots["downstream"][trigger]
        if ("main" not in slots) and (not slots["downstream"]):
            del proj["refs"][pipe["ref"]]

    @staticmethod
    def _use_pipeline(proj, slot, key, ref, pipeline_id, parent, pipe, now):
        # Get pipeline entry (create if new) and make it the newest of its slot. None if it is older than the newest
        # one and already removed. Lock held.
        newest = slot.get("pipeline")
        if (pipe is None) and (newest is not None) and (newest > pipeline_id):
            return None
        if pipe is None:
            pipe = {"id": pipeline_id, "ref": ref, "jobs": {}, "bridges": {}}
            proj["pipelines"][str(pipeline_id)] = pipe
        if parent is not None:
            pipe["parent"] = parent
            pipe["trigger"] = key
        if (newest is None) or (newest <= pipeline_id):
            slot["pipeline"] = pipeline_id
        pipe["updated"] = round(now, 3)
        return pipe

    @staticmethod
    def _prune(proj, now):
        # Remove old replaced pipelines. Lock held.
        used = set()
        for slots in proj["refs"].values():
            used.add(slots.get("main", {}).get("pipeline"))
            used.update(slot.get("pipeline") for slot in slots["downstream"].values())
        for key in [key for key, pipe in proj["pipelines"].items()
                    if (pipe["id"] not in used) and (pipe.get("updated", 0) < (now - PRUNE_AGE))]:
            del proj["pipelines"][key]

    def _set_retried(self, pipe, now):
        # Set retry time of a pipeline and its parents. Lock held.
        stamp = round(now, 3)
        while (pipe is not None) and (pipe.get("retried") != stamp):
            pipe["retried"] = stamp
            parent = pipe.get("parent")
            proj = (self.data["projects"].get(str(parent["project"])) if (parent is not None) else None)
            pipe = (proj["pipelines"].get(str(parent["pipeline"])) if (proj is not None) else None)

    def _job(self, pipe, job_id, values, now):
        # Update job entry. Lock held.
        if not isinstance(job_id, int):
            raise EventError("no job id")
        if is_retry(pipe["jobs"], job_id, values.get("name")):
            self._set_retried(pipe, now)
        job = pipe["jobs"].setdefault(str(job_id), {"id": job_id})
        if (job.get("status") in FINISHED) and (values.get("status") not in FINISHED):
            return
        job.update(values)

    @staticmethod
    def _parent(payload, pipe):
        # Parent of a downstream pipeline: from the event, else from the known pipeline (GitLab sends some events of
        # a child pipeline without source_pipeline, e.g. "canceled" after the parent was canceled). Lock held.
        parent = parent_info(payload.get("source_pipeline"))
        if (parent is None) and (pipe is not None):
            parent = pipe.get("parent")
        return parent

    @staticmethod
    def _job_result(slot, job_id, name, status, allow_failure):
        # Job result of the slot: result of the newest finished job of that name. Lock held.
        if (status not in RESULTS) or (not name) or (not isinstance(job_id, int)):
            return
        result = slot.setdefault("jobs", {}).setdefault(name, {})
        if job_id >= result.get("result_job", 0):
            result["result"] = job_result(status, allow_failure)
            result["result_job"] = job_id

    @staticmethod
    def _runner(runner):
        # Runner description of a job
        return (runner.get("description") if isinstance(runner, dict) else None)

    @staticmethod
    def _name(proj, pipe):
        # Pipeline name for the log
        return "{} {} pipeline {}{}".format(proj.get("path", proj["id"]), pipe["ref"], pipe["id"],
                                            (" ({})".format(pipe["trigger"]) if pipe.get("trigger") else ""))

    def _on_pipeline(self, payload, now):
        attrs = payload.get("object_attributes")
        if not isinstance(attrs, dict):
            raise EventError("no object_attributes")
        check_ids(attrs.get("ref"), attrs.get("id"))
        proj = self._project(payload.get("project"))
        pipeline_id = attrs["id"]
        status = attrs.get("status")
        pipe = proj["pipelines"].get(str(pipeline_id))
        parent = self._parent(payload, pipe)
        if (parent is None) and (attrs.get("source") in DOWNSTREAM_SOURCES):
            # Downstream pipeline without parent info: not a main pipeline
            self._add_log({"time": round(now, 3), "project": proj.get("path", proj["id"]), "ref": attrs["ref"],
                           "pipeline": pipeline_id, "status": status, "source": attrs.get("source"),
                           "url": attrs.get("url"), "ignored": True})
            return "downstream pipeline {} without parent ignored".format(pipeline_id)
        if self._deleted(proj, attrs["ref"], pipeline_id, parent):
            self._add_log({"time": round(now, 3), "project": proj.get("path", proj["id"]), "ref": attrs["ref"],
                           "pipeline": pipeline_id, "status": status, "source": attrs.get("source"),
                           "url": attrs.get("url"), "ignored": True})
            return "pipeline {} of deleted ref {} ignored".format(pipeline_id, attrs["ref"])
        if (pipe is not None) and (pipe.get("ref") != attrs["ref"]):
            self._drop_slot(proj, pipe)
        slot, key = self._slot(proj, attrs["ref"], parent, pipe)

        # Result: newest finished pipeline of the slot, and of each job name
        if (status in RESULTS) and (pipeline_id >= slot.get("result_pipeline", 0)):
            slot["result"] = pipeline_result(status, attrs.get("detailed_status"))
            slot["result_pipeline"] = pipeline_id
        for build in (payload.get("builds") or []):
            if isinstance(build, dict):
                self._job_result(slot, build.get("id"), build.get("name"), build.get("status"),
                                 build.get("allow_failure"))

        user = payload.get("user")
        entry = {"time": round(now, 3), "project": proj.get("path", proj["id"]), "ref": attrs["ref"],
                 "pipeline": pipeline_id, "name": attrs.get("name"), "status": status,
                 "source": attrs.get("source"), "url": attrs.get("url"),
                 "user": (user.get("username") if isinstance(user, dict) else None)}

        pipe = self._use_pipeline(proj, slot, key, attrs["ref"], pipeline_id, parent, pipe, now)
        if pipe is None:
            entry.update(trigger=key, ignored=True)
            self._add_log(entry)
            return "old pipeline {} ignored".format(pipeline_id)
        for field in PIPELINE_KEYS:
            if field in attrs:
                pipe[field] = attrs[field]
        if entry["user"]:
            pipe["user"] = entry["user"]
        commit = payload.get("commit")
        if isinstance(commit, dict) and first_line(commit.get("title") or commit.get("message")):
            pipe["commit"] = first_line(commit.get("title") or commit.get("message"))
        for build in (payload.get("builds") or []):
            if isinstance(build, dict):
                values = {field: build[field] for field in JOB_KEYS if field in build}
                if self._runner(build.get("runner")):
                    values["runner"] = self._runner(build.get("runner"))
                self._job(pipe, build.get("id"), values, now)
        for bridge in (payload.get("bridges") or []):
            if isinstance(bridge, dict) and isinstance(bridge.get("id"), int):
                if is_retry(pipe["bridges"], bridge["id"], bridge.get("name")):
                    self._set_retried(pipe, now)
                pipe["bridges"][str(bridge["id"])] = {key: bridge.get(key) for key in ("id", "name", "stage", "status")}
        entry.update(trigger=pipe.get("trigger"), **job_counts(pipe))
        self._add_log(entry)
        return "{} {}".format(self._name(proj, pipe), pipe.get("status"))

    def _on_job(self, payload, now):
        # Older GitLab versions have no "project" object in job events
        info = payload.get("project")
        if not isinstance(info, dict):
            info = {"id": payload.get("project_id")}
        check_ids(payload.get("ref"), payload.get("pipeline_id"))
        if not isinstance(payload.get("build_id"), int):
            raise EventError("no job id")
        proj = self._project(info)
        pipeline_id = payload["pipeline_id"]
        pipe = proj["pipelines"].get(str(pipeline_id))
        parent = self._parent(payload, pipe)
        # Known pipeline: its ref (merge request pipelines)
        ref = ((pipe or {}).get("ref") or payload["ref"])
        if self._deleted(proj, ref, pipeline_id, parent):
            self._add_log({"time": round(now, 3), "kind": "job", "project": proj.get("path", proj["id"]), "ref": ref,
                           "pipeline": pipeline_id, "job": payload.get("build_name"),
                           "stage": payload.get("build_stage"), "status": payload.get("build_status"),
                           "ignored": True})
            return "job of pipeline {} of deleted ref {} ignored".format(pipeline_id, ref)
        slot, key = self._slot(proj, ref, parent, pipe)
        values = {field: payload.get(src) for field, src in JOB_EVENT_KEYS if src in payload}
        if self._runner(payload.get("runner")):
            values["runner"] = self._runner(payload.get("runner"))
        self._job_result(slot, payload["build_id"], values.get("name"), values.get("status"),
                         values.get("allow_failure"))
        user = payload.get("user")
        entry = {"time": round(now, 3), "kind": "job", "project": proj.get("path", proj["id"]),
                 "ref": ref, "pipeline": pipeline_id, "job": values.get("name"),
                 "stage": values.get("stage"), "status": values.get("status"),
                 "url": ("{}/-/pipelines/{}".format(proj["url"], pipeline_id) if proj.get("url") else None),
                 "user": (user.get("username") if isinstance(user, dict) else None)}
        pipe = self._use_pipeline(proj, slot, key, ref, pipeline_id, parent, pipe, now)
        if pipe is None:
            entry.update(trigger=key, ignored=True)
            self._add_log(entry)
            return "job of old pipeline {} ignored".format(pipeline_id)

        # Job event before the pipeline event: pipeline status from the commit
        pipe.setdefault("tag", payload.get("tag"))
        pipe.setdefault("sha", payload.get("sha"))
        commit = payload.get("commit")
        if isinstance(commit, dict) and ("status" not in pipe):
            pipe["status"] = commit.get("status")
        if isinstance(commit, dict) and ("commit" not in pipe) and first_line(commit.get("message")):
            pipe["commit"] = first_line(commit.get("message"))
        self._job(pipe, payload["build_id"], values, now)
        entry["trigger"] = pipe.get("trigger")
        self._add_log(entry)
        return "{} job {} {}".format(self._name(proj, pipe), values.get("name"), values.get("status"))

    @staticmethod
    def _deleted(proj, ref, pipeline_id, parent):
        # Check if an event is of a deleted ref (see "Deleted branches and tags" above). Lock held.
        mark = proj.get("deleted", {}).get(ref)
        if mark is None:
            return False
        if (parent["pipeline"] if (parent is not None) else pipeline_id) <= mark[0]:
            return True
        # New pipeline: the ref is back
        del proj["deleted"][ref]
        if not proj["deleted"]:
            del proj["deleted"]
        return False

    def _remove_ref(self, proj, ref, now, user, kind):
        # Remove the slots of a ref, mark it as deleted, log it (kind: deleted, removed). Lock held.
        newest = max((pipe["id"] for pipe in proj["pipelines"].values()), default=0)
        mark = proj.setdefault("deleted", {}).get(ref)
        proj["deleted"][ref] = [max(newest, (mark[0] if mark else 0)), round(now, 3)]
        slots = proj["refs"].pop(ref, None) or {"downstream": {}}
        num = (1 if ("main" in slots) else 0) + len(slots["downstream"])
        self._add_log({"time": round(now, 3), "kind": kind, "project": proj.get("path", proj["id"]), "ref": ref,
                       "user": user})
        return "{} {} {}: {} slots removed".format(proj.get("path", proj["id"]), ref, kind, num)

    def _on_push(self, payload, now):
        # Push event of a deleted branch or tag. Lock held.
        info = payload.get("project")
        if not isinstance(info, dict):
            info = {"id": payload.get("project_id")}
        ref = push_ref(payload.get("ref"))
        if (ref is None) or (not isinstance(info.get("id"), int)):
            raise EventError("no ref or project id")
        proj = self.data["projects"].get(str(info["id"]))
        if proj is None:
            return "{} {} deleted: unknown project".format(info.get("path_with_namespace", info["id"]), ref)
        return self._remove_ref(proj, ref, now, payload.get("user_username"), "deleted")

    def _on_remove(self, payload, now):
        # Removal by hand (see remove()). Lock held.
        proj = self.data["projects"].get(str(payload.get("project")))
        ref = payload.get("ref")
        if (proj is None) or (not isinstance(ref, str)) or (not ref):
            return "remove: unknown project or ref"
        return self._remove_ref(proj, ref, now, payload.get("user"), "removed")

    def remove(self, project, ref, user=None, now=None):
        """Remove the slots of a ref by hand (project: path, e.g. "group/foo"), returns False if unknown

        Adds a raw event, thus the removal stays after a restart. A new pipeline brings the ref back.
        """
        with self.lock:
            found = [proj for proj in self.data["projects"].values()
                     if (proj.get("path") == project) and (ref in proj["refs"])]
            if not found:
                return False
            payload = {"project": found[0]["id"], "ref": ref, "user": user}
        self.handle(REMOVE_EVENT, payload, now)
        return True

    def handle(self, event, payload, now=None, raw=True):
        """Handle webhook event (X-Gitlab-Event header, JSON payload)

        now: event time (default: current time), raw: add to the raw events. Returns a short description (for the
        log), None for other events. Raises EventError.
        """
        if not isinstance(payload, dict):
            raise EventError("payload not an object")
        kind = ("remove" if (event == REMOVE_EVENT) else payload.get("object_kind"))
        now = (time.time() if now is None else now)
        with self.lock:
            if (kind in ("push", "tag_push")) and (payload.get("after") != NO_COMMIT):
                return None
            handler = {"pipeline": self._on_pipeline, "build": self._on_job, "push": self._on_push,
                       "tag_push": self._on_push, "remove": self._on_remove}.get(kind)
            if handler is None:
                return None
            if raw:
                self._log_event(event, payload, now)
            path = self._event_path(payload)
            if (kind in ("pipeline", "build")) and is_ignored(path, self.ignored):
                self._add_log(self._ignored_entry(kind, payload, path, now))
                return "{} {} event ignored (gitlab_ignored_projects)".format(path, kind)
            text = handler(payload, now)
            for proj in self.data["projects"].values():
                self._prune(proj, now)
            return text

    def _event_path(self, payload):
        # Project path of an event (older job events: only the ID, path of the known project), None if not known
        info = payload.get("project")
        if isinstance(info, dict) and isinstance(info.get("path_with_namespace"), str):
            return info["path_with_namespace"]
        proj = self.data["projects"].get(str(payload.get("project_id")))
        return (proj.get("path") if (proj is not None) else None)

    @staticmethod
    def _ignored_entry(kind, payload, path, now):
        # Event log entry of a pipeline or job event of an ignored project
        user = payload.get("user")
        entry = {"time": round(now, 3), "project": path, "ignored": True, "reason": "project ignored",
                 "user": (user.get("username") if isinstance(user, dict) else None)}
        if kind == "pipeline":
            attrs = (payload.get("object_attributes") if isinstance(payload.get("object_attributes"), dict) else {})
            entry.update(ref=attrs.get("ref"), pipeline=attrs.get("id"), name=attrs.get("name"),
                         status=attrs.get("status"), source=attrs.get("source"), url=attrs.get("url"))
        else:
            entry.update(kind="job", ref=payload.get("ref"), pipeline=payload.get("pipeline_id"),
                         job=payload.get("build_name"), stage=payload.get("build_stage"),
                         status=payload.get("build_status"))
        return entry

    @staticmethod
    def _old_slot(proj, slot, limit):
        # Slot without events since limit. Lock held.
        pipe = proj["pipelines"].get(str(slot.get("pipeline")))
        return ((pipe is None) or (pipe.get("updated", 0) < limit))

    @staticmethod
    def log_job(entry):
        """Get (job path, kind, GitLab status) of an event log entry, None if no job state (see Source)"""
        if (entry.get("kind") not in (None, "job")) or (not entry.get("status")) or (not entry.get("project")):
            return None
        path = [SOURCE, str(entry["project"]), entry.get("ref")] + ([entry["trigger"]] if entry.get("trigger") else [])
        if entry.get("kind") == "job":
            if not entry.get("job"):
                return None
            path.append(entry["job"])
        return (path, ("job" if (entry.get("kind") == "job") else "pipeline"), entry["status"])

    def expire_jobs(self, now=None):
        """Remove slots without events in keep_days, then empty refs and projects and unused pipelines

        Returns the number of removed slots.
        """
        now = (time.time() if now is None else now)
        limit = now - (self.keep_days * 86400)
        removed = 0
        with self.lock:
            for key, proj in list(self.data["projects"].items()):
                for ref, slots in list(proj["refs"].items()):
                    if ("main" in slots) and self._old_slot(proj, slots["main"], limit):
                        del slots["main"]
                        removed += 1
                    for trigger, slot in list(slots["downstream"].items()):
                        if self._old_slot(proj, slot, limit):
                            del slots["downstream"][trigger]
                            removed += 1
                    if ("main" not in slots) and (not slots["downstream"]):
                        del proj["refs"][ref]
                for ref, mark in list(proj.get("deleted", {}).items()):
                    if mark[1] < limit:
                        del proj["deleted"][ref]
                self._prune(proj, now)
                if (not proj["refs"]) and (not proj.get("deleted")):
                    del self.data["projects"][key]
        if removed > 0:
            LOG.info("Removed %d old GitLab pipeline slots", removed)
        return removed

    def get(self):
        """Get a copy of the data"""
        with self.lock:
            return json.loads(json.dumps(self.data))

    def jobs(self):
        """Get job entries (see jobs.py) of all slots and of the job names in them"""
        entries = []
        with self.lock:
            index = child_index(self.data["projects"].values())
            for proj in self.data["projects"].values():
                project = proj.get("path", str(proj["id"]))
                for ref, slots in proj["refs"].items():
                    triggers = set(slots["downstream"])
                    for trigger, slot in ([("", slots["main"])] if ("main" in slots) else []) + \
                            sorted(slots["downstream"].items()):
                        path = [SOURCE, project, ref] + ([trigger] if trigger else [])
                        # Job names of the main pipeline that are trigger names: downstream pipeline wins
                        skip = (triggers if (not trigger) else set())
                        entries.extend(self._slot_jobs(proj, path, slot, skip, index))
        return entries

    @staticmethod
    def _slot_jobs(proj, path, slot, skip, index):
        # Job entries of a slot and of its job names (index: see child_index()). Lock held.
        if "pipeline" not in slot:
            return []
        pipe = proj["pipelines"].get(str(slot["pipeline"]), {"id": slot["pipeline"]})
        status = pipeline_state(proj["id"], pipe, index)
        entries = [{"path": path, "status": status, "result": slot.get("result", "UNKNOWN"),
                    "link": pipe.get("url"), "time": pipe.get("updated"), "started": pipeline_started(pipe),
                    "info": pipeline_info(pipe)}]
        # Newest job of each name (retries have a new ID)
        runs = {}
        for run in pipe.get("jobs", {}).values():
            name = run.get("name")
            if name and (run["id"] >= runs.get(name, {}).get("id", 0)):
                runs[name] = run
        results = slot.get("jobs", {})
        for name in sorted((set(runs) | set(results)) - skip):
            run = runs.get(name)
            job_id = (run["id"] if (run is not None) else results.get(name, {}).get("result_job"))
            link = ("{}/-/jobs/{}".format(proj["url"], job_id) if (proj.get("url") and job_id) else None)
            entries.append({"path": path + [name], "status": (job_status(run.get("status")) if run else "STOPPED"),
                            "result": results.get(name, {}).get("result", "UNKNOWN"), "link": link,
                            "time": pipe.get("updated"), "info": job_info(pipe, run)})
        return entries
