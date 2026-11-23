"""GitHub webhook events: workflow runs and jobs of the repositories

GitHub sends workflow run events (X-GitHub-Event "workflow_run") and workflow job events ("workflow_job") as POST to
/api/github. The web layer answers "ping" (sent when the webhook is made) and ignores all other events. If secrets are
configured (one per webhook, the user sets them), the X-Hub-Signature-256 header must be valid for one of them (see
check_signature()).

For each repository, the backend keeps the newest run (highest run ID) of each workflow of each branch (head_branch:
the branch of a push, the source branch of a pull request, or the tag). A newer run replaces the older one. Replaced
runs stay for PRUNE_AGE (late events), then the backend removes them. Events of an older run than the known one are
ignored (but they can still set a result). A re-run keeps the run ID with a higher run_attempt: the events of an
older attempt do not change the run. The jobs of a re-run get new job IDs. Within a run, the newest job ID of each
name wins, and a completed job does not go back to an unfinished status (events can come out of order). A job event
can come before the event of its run: it makes the run entry.

Each slot (workflow of a branch) also keeps its result: the result of its newest completed run (run ID and attempt),
and the result of each job name: the result of the newest completed job (highest job ID) of that name in the runs of
the slot. Results (see run_result()): success -> PASS; failure, timed_out, startup_failure -> FAIL; cancelled,
action_required, stale -> UNKNOWN; skipped and neutral do not change the result.

Jobs (see jobs.py and jobs()): each slot is a job, and each job name in it:

    ["github", <repository full name>, <branch>, <workflow>]            newest run of the workflow
    ["github", <repository full name>, <branch>, <workflow>, <job>]     job of the workflow

Status of a job (see job_status()): newest run (or newest job of that name in it) in WAITING: WAITING, in_progress:
RUNNING, else STOPPED.

Deleted branches and tags: a "delete" event (webhook event "Branch or tag deletion", ref_type branch or tag, both use
the head_branch name) removes the slots of the branch. The Remove button of the Jobs page does the same (remove(),
event "laempli_remove" in the raw events only, the web route does not take it). The repository then keeps the branch in
"deleted" with the highest known run ID: later events of that branch with a run ID up to it are ignored (a running
workflow of a deleted branch still sends events), a higher ID (new run) removes the entry. The housekeeping removes
entries older than keep_days.

Ignored repositories (config github_ignored_repos, "owner/repo" or pattern, see is_ignored()): their run and job
events are stored (raw events), but only logged (ignored, reason "repository ignored"), they make no data and no
jobs. The replay applies the list again, so a change applies at the next start.

Storage: only the raw events (see source.py, in the event store, "event" is the X-GitHub-Event header). The
data (in memory, see get()):

    {
        "format": 1,
        "repos": {
            "<repository ID>": {
                "id": 123, "name": "owner/repo", "url": "https://github.com/owner/repo",
                "deleted": {"feature/old": [455, <unix time [s]>]},
                "slots": {
                    "main": {
                        "ci": {"run": 456, "result": "PASS", "result_run": [455, 1],
                               "jobs": {"build": {"result": "PASS", "result_job": 789}}}
                    }
                },
                "runs": {
                    "456": {
                        "id": 456, "number": 12, "attempt": 1, "branch": "main", "workflow": "ci",
                        "title": "Fix foo", "event": "push", "status": "in_progress", "conclusion": null,
                        "sha": ..., "commit": "<first line of the commit message>", "user": "flip",
                        "url": "https://github.com/owner/repo/actions/runs/456", "created_at": ...,
                        "started_at": ..., "updated_at": ..., "updated": <unix time [s]>,
                        "jobs": {
                            "<job ID>": {"id": 789, "name": "build", "attempt": 1, "status": "completed",
                                         "conclusion": "success", "url": ..., "started_at": ...,
                                         "completed_at": ..., "runner": "GitHub Actions 2", "failed_step": null}
                        }
                    }
                }
            }
        }
    }

Event log: a short entry for each run event and each job event (kind "job"): time, repository, branch, workflow, run
number and link, job name, status (conclusion when completed), event, user; "ignored" for an old run or a deleted
branch), and each removed branch (kind "deleted" for a delete event, "removed" by hand, time, repository, branch,
user). All entries in the event store (made again by the replay at start), see events(). LOG_COLUMNS: entry key
of each column of the GitHub events table.
"""

import datetime
import hashlib
import hmac
import json
import logging
import time

from .source import KEEP_DAYS, EventError, Source, clean_info, first_line, fmt_duration, fmt_time
from .source import is_ignored, unix_time

LOG = logging.getLogger(__name__)

# Keep replaced runs for this time [s] (late events)
PRUNE_AGE = 3600

# Data format version
FORMAT = 1

# Source name (first element of the job paths)
SOURCE = "github"

# Events that the source handles (from the webhook)
EVENTS = ("workflow_run", "workflow_job", "delete")

# Event name of a removal by hand (raw events only)
REMOVE_EVENT = "laempli_remove"

# Run and job status of a waiting job (LED pulses fast) and a running job (LED pulses slow). Other status: stopped.
WAITING = ("requested", "queued", "pending", "waiting")
RUNNING = ("in_progress",)

# Status of a finished run or job
COMPLETED = "completed"

# Conclusions: result, and conclusions that do not change the result
CONCLUSIONS = {"success": "PASS", "failure": "FAIL", "timed_out": "FAIL", "startup_failure": "FAIL",
               "cancelled": "UNKNOWN", "action_required": "UNKNOWN", "stale": "UNKNOWN"}
NO_RESULT = ("skipped", "neutral")


def run_result(conclusion):
    """Get result of a conclusion, None if it does not change the result"""
    if (conclusion in NO_RESULT) or (conclusion is None):
        return None
    return CONCLUSIONS.get(conclusion, "UNKNOWN")


def job_status(status):
    """Get job status of a run or job status"""
    if status in WAITING:
        return "WAITING"
    if status in RUNNING:
        return "RUNNING"
    return "STOPPED"


def check_ids(repo, run_id):
    """Check repository and run ID of an event, raise EventError if bad"""
    if (not isinstance(repo, dict)) or (not isinstance(repo.get("id"), int)) or (not isinstance(run_id, int)):
        raise EventError("no repository or run id")


def empty_data():
    """Get data without repositories"""
    return {"format": FORMAT, "repos": {}}


def seconds(start, end):
    """Get seconds between two GitHub times, None if one is missing or bad"""
    try:
        return (datetime.datetime.fromisoformat(end.replace("Z", "+00:00")) -
                datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds()
    except (AttributeError, ValueError):
        return None


def status_text(entry):
    """Get status text of a run or job: the conclusion when completed, else the status"""
    if (entry.get("status") == COMPLETED) and entry.get("conclusion"):
        return entry["conclusion"]
    return entry.get("status")


def run_name(run):
    """Get short run name: "#<number>" (" attempt <n>" for a re-run), else the run ID"""
    if not run.get("number"):
        return str(run["id"])
    attempt = (run.get("attempt") or 1)
    return "#{}{}".format(run["number"], (" attempt {}".format(attempt) if (attempt > 1) else ""))


def run_info(run):
    """Get job info of a run"""
    commit = " ".join(part for part in ((run.get("sha") or "")[:8], run.get("commit")) if part)
    duration = (seconds(run.get("started_at"), run.get("updated_at")) if (run.get("status") == COMPLETED) else None)
    return clean_info({
        "run": run_name(run), "title": run.get("title"), "status": status_text(run), "event": run.get("event"),
        "commit": commit, "user": run.get("user"), "created": fmt_time(run.get("created_at")),
        "duration": fmt_duration(duration)})


def job_info(run, job):
    """Get job info of a job (job: newest job of that name in the run, or None)"""
    info = run_info(run)
    if job is None:
        return clean_info({"status": "not in run", "run": info.get("run"), "commit": info.get("commit"),
                           "user": info.get("user")})
    return clean_info({
        "job": job.get("id"), "status": status_text(job), "failed step": job.get("failed_step"),
        "run": info.get("run"), "commit": info.get("commit"), "user": info.get("user"), "runner": job.get("runner"),
        "started": fmt_time(job.get("started_at")), "finished": fmt_time(job.get("completed_at")),
        "duration": fmt_duration(seconds(job.get("started_at"), job.get("completed_at")))})


def failed_step(steps):
    """Get name of the first failed step of a job, None if none"""
    for step in (steps if isinstance(steps, list) else []):
        if isinstance(step, dict) and (step.get("conclusion") == "failure"):
            return step.get("name")
    return None


class Github(Source):
    """Workflow status from GitHub webhooks"""

    SOURCE = SOURCE
    TITLE = "GitHub"
    LOG_COLUMNS = ("time", "kind", "repo", "branch", "workflow", "run", "job", "status", "event", "user")
    LOG_KINDS = {"runs": [None], "jobs": ["job"]}

    def __init__(self, dirname, secrets=(), keep_days=KEEP_DAYS, store=None, ignored=()):
        super().__init__(dirname, keep_days, store)
        self.ignored = list(ignored)  # repository names or patterns, see is_ignored()
        self.keys = [secret.encode("utf-8") for secret in secrets]
        self.data = empty_data()

    def check_signature(self, signature, body):
        """Check webhook signature

        signature: X-Hub-Signature-256 header ("sha256=<hex HMAC-SHA256 of the body>"), body: raw body (bytes). One
        secret must match. Returns None if OK (or no secrets configured), else the error text.
        """
        if not self.keys:
            return None
        if not signature:
            return "no signature"
        sig = signature.strip().encode("utf-8")
        for key in self.keys:
            expected = b"sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest().encode("utf-8")
            if hmac.compare_digest(sig, expected):
                return None
        return "bad signature"

    def _repo(self, info):
        # Get repository entry, create if new, update its info. Lock held.
        repo = self.data["repos"].setdefault(str(info["id"]), {"id": info["id"], "slots": {}, "runs": {}})
        for key, src in (("name", "full_name"), ("url", "html_url")):
            if info.get(src) is not None:
                repo[key] = info[src]
        return repo

    @staticmethod
    def _use_run(repo, slot, run_id, branch, workflow, now):
        # Get run entry (create if new) and make it the newest of its slot. None if it is older than the newest one
        # and already removed. Lock held.
        newest = slot.get("run")
        run = repo["runs"].get(str(run_id))
        if (run is None) and (newest is not None) and (newest > run_id):
            return None
        if run is None:
            run = {"id": run_id, "branch": branch, "workflow": workflow, "jobs": {}}
            repo["runs"][str(run_id)] = run
        if (newest is None) or (newest <= run_id):
            slot["run"] = run_id
        run["updated"] = round(now, 3)
        return run

    @staticmethod
    def _prune(repo, now):
        # Remove old replaced runs. Lock held.
        used = {slot.get("run") for workflows in repo["slots"].values() for slot in workflows.values()}
        for key in [key for key, run in repo["runs"].items()
                    if (run["id"] not in used) and (run.get("updated", 0) < (now - PRUNE_AGE))]:
            del repo["runs"][key]

    @staticmethod
    def _run_result(slot, run_id, attempt, conclusion):
        # Result of the slot: result of the newest completed run (attempt). Lock held.
        result = run_result(conclusion)
        if result is None:
            return
        key = [run_id, (attempt if isinstance(attempt, int) else 1)]
        if key >= slot.get("result_run", [0, 0]):
            slot["result"] = result
            slot["result_run"] = key

    @staticmethod
    def _job_result(slot, job_id, name, conclusion):
        # Job result of the slot: result of the newest completed job of that name. Lock held.
        result = run_result(conclusion)
        if (result is None) or (not name):
            return
        entry = slot.setdefault("jobs", {}).setdefault(name, {})
        if job_id >= entry.get("result_job", 0):
            entry["result"] = result
            entry["result_job"] = job_id

    def _log_entry(self, now, repo, run, values):
        # Event log entry. Lock held.
        entry = {"time": round(now, 3), "repo": repo.get("name", repo["id"]), "branch": run["branch"],
                 "workflow": run["workflow"], "run": run_name(run), "run_id": run["id"],
                 "url": run.get("url") or ("{}/actions/runs/{}".format(repo["url"], run["id"]) if repo.get("url")
                                           else None)}
        entry.update(values)
        return entry

    @staticmethod
    def _ignored_entry(event, payload, name, now):
        # Event log entry of a run or job event of an ignored repository
        entry = {"time": round(now, 3), "repo": name, "ignored": True, "reason": "repository ignored"}
        if event == "workflow_run":
            info = (payload.get("workflow_run") if isinstance(payload.get("workflow_run"), dict) else {})
            actor = info.get("actor")
            entry.update(branch=info.get("head_branch"), workflow=info.get("name"),
                         run=(run_name({"id": info.get("id"), "number": info.get("run_number"),
                                        "attempt": info.get("run_attempt")})),
                         run_id=info.get("id"), url=info.get("html_url"), status=status_text(info),
                         event=info.get("event"), user=(actor.get("login") if isinstance(actor, dict) else None))
        else:
            info = (payload.get("workflow_job") if isinstance(payload.get("workflow_job"), dict) else {})
            entry.update(kind="job", branch=info.get("head_branch"), workflow=info.get("workflow_name"),
                         run=str(info.get("run_id")), run_id=info.get("run_id"), job=info.get("name"),
                         url=info.get("html_url"), status=status_text(info))
        return entry

    def _on_run(self, payload, now):
        info = payload.get("workflow_run")
        if not isinstance(info, dict):
            raise EventError("no workflow_run")
        check_ids(payload.get("repository"), info.get("id"))
        branch = info.get("head_branch")
        workflow = info.get("name")
        if (not isinstance(branch, str)) or (not branch) or (not isinstance(workflow, str)) or (not workflow):
            raise EventError("no branch or workflow name")
        repo = self._repo(payload["repository"])
        run_id = info["id"]
        if self._deleted(repo, branch, run_id):
            self._add_log(self._log_entry(now, repo, {"id": run_id, "branch": branch, "workflow": workflow},
                                          {"status": info.get("status"), "ignored": True}))
            return "run {} of deleted branch {} ignored".format(run_id, branch)
        slot = repo["slots"].setdefault(branch, {}).setdefault(workflow, {})
        status = info.get("status")
        attempt = (info.get("run_attempt") if isinstance(info.get("run_attempt"), int) else 1)
        if status == COMPLETED:
            self._run_result(slot, run_id, attempt, info.get("conclusion"))
        actor = info.get("actor")
        user = (actor.get("login") if isinstance(actor, dict) else None)
        head = info.get("head_commit")
        values = {"number": info.get("run_number"), "attempt": attempt, "title": info.get("display_title"),
                  "event": info.get("event"), "status": status, "conclusion": info.get("conclusion"),
                  "sha": info.get("head_sha"), "user": user, "url": info.get("html_url"),
                  "created_at": info.get("created_at"), "started_at": info.get("run_started_at"),
                  "updated_at": info.get("updated_at")}
        if isinstance(head, dict) and first_line(head.get("message")):
            values["commit"] = first_line(head.get("message"))
        values = {key: value for key, value in values.items() if value is not None}
        values.setdefault("conclusion", None)

        log_values = {"status": status_text(values), "event": values.get("event"), "user": user}
        run = self._use_run(repo, slot, run_id, branch, workflow, now)
        if run is None:
            self._add_log(self._log_entry(now, repo, dict(values, id=run_id, branch=branch, workflow=workflow),
                                          dict(log_values, ignored=True)))
            return "old run {} ignored".format(run_id)
        # Older attempt, or a late event of a completed attempt: no change
        old_attempt = run.get("attempt", 0)
        if (attempt > old_attempt) or ((attempt == old_attempt) and
                                       ((run.get("status") != COMPLETED) or (status == COMPLETED))):
            run.update(values)
        self._add_log(self._log_entry(now, repo, run, log_values))
        return "{} {} {} run {} {}".format(repo.get("name", repo["id"]), branch, workflow, run_name(run),
                                           status_text(run))

    def _on_job(self, payload, now):
        info = payload.get("workflow_job")
        if not isinstance(info, dict):
            raise EventError("no workflow_job")
        check_ids(payload.get("repository"), info.get("run_id"))
        if (not isinstance(info.get("id"), int)) or (not info.get("name")):
            raise EventError("no job id or name")
        repo = self._repo(payload["repository"])
        run_id = info["run_id"]
        run = repo["runs"].get(str(run_id))
        branch = info.get("head_branch") or (run or {}).get("branch")
        workflow = info.get("workflow_name") or (run or {}).get("workflow")
        if (not isinstance(branch, str)) or (not branch) or (not isinstance(workflow, str)) or (not workflow):
            raise EventError("no branch or workflow name")
        if self._deleted(repo, branch, run_id):
            self._add_log(self._log_entry(now, repo, {"id": run_id, "branch": branch, "workflow": workflow},
                                          {"kind": "job", "job": info["name"], "status": info.get("status"),
                                           "ignored": True}))
            return "job of run {} of deleted branch {} ignored".format(run_id, branch)
        slot = repo["slots"].setdefault(branch, {}).setdefault(workflow, {})
        status = info.get("status")
        if status == COMPLETED:
            self._job_result(slot, info["id"], info["name"], info.get("conclusion"))
        values = {"name": info["name"], "attempt": info.get("run_attempt"), "status": status,
                  "conclusion": info.get("conclusion"), "url": info.get("html_url"),
                  "started_at": info.get("started_at"), "completed_at": info.get("completed_at"),
                  "runner": info.get("runner_name"), "failed_step": failed_step(info.get("steps"))}
        log_values = {"kind": "job", "job": info["name"], "status": status_text(values)}
        run = self._use_run(repo, slot, run_id, branch, workflow, now)
        if run is None:
            self._add_log(self._log_entry(now, repo, {"id": run_id, "branch": branch, "workflow": workflow},
                                          dict(log_values, ignored=True)))
            return "job of old run {} ignored".format(run_id)

        # Job event before the run event: run status from the job
        if "status" not in run:
            run["status"] = ("in_progress" if (status in ((COMPLETED,) + RUNNING)) else status)
            run["conclusion"] = None
            run["sha"] = info.get("head_sha")
            run["attempt"] = info.get("run_attempt")
        job = run["jobs"].setdefault(str(info["id"]), {"id": info["id"]})
        if (job.get("status") != COMPLETED) or (status == COMPLETED):
            job.update(values)
        self._add_log(self._log_entry(now, repo, run, log_values))
        return "{} {} {} run {} job {} {}".format(repo.get("name", repo["id"]), branch, workflow, run_name(run),
                                                  info["name"], status_text(values))

    @staticmethod
    def _deleted(repo, branch, run_id):
        # Check if an event is of a deleted branch (see "Deleted branches and tags" above). Lock held.
        mark = repo.get("deleted", {}).get(branch)
        if mark is None:
            return False
        if run_id <= mark[0]:
            return True
        # New run: the branch is back
        del repo["deleted"][branch]
        if not repo["deleted"]:
            del repo["deleted"]
        return False

    def _remove_branch(self, repo, branch, now, user, kind):
        # Remove the slots of a branch, mark it as deleted, log it (kind: deleted, removed). Lock held.
        newest = max((run["id"] for run in repo["runs"].values()), default=0)
        mark = repo.setdefault("deleted", {}).get(branch)
        repo["deleted"][branch] = [max(newest, (mark[0] if mark else 0)), round(now, 3)]
        num = len(repo["slots"].pop(branch, None) or {})
        self._add_log({"time": round(now, 3), "kind": kind, "repo": repo.get("name", repo["id"]), "branch": branch,
                       "user": user})
        return "{} {} {}: {} slots removed".format(repo.get("name", repo["id"]), branch, kind, num)

    def _on_delete(self, payload, now):
        # Deleted branch or tag. Lock held.
        info = payload.get("repository")
        branch = payload.get("ref")
        if (not isinstance(info, dict)) or (not isinstance(info.get("id"), int)) or (not isinstance(branch, str)) or \
                (not branch):
            raise EventError("no repository or ref")
        repo = self.data["repos"].get(str(info["id"]))
        if repo is None:
            return "{} {} deleted: unknown repository".format(info.get("full_name", info["id"]), branch)
        sender = payload.get("sender")
        return self._remove_branch(repo, branch, now, (sender.get("login") if isinstance(sender, dict) else None),
                                   "deleted")

    def _on_remove(self, payload, now):
        # Removal by hand (see remove()). Lock held.
        repo = self.data["repos"].get(str(payload.get("repository")))
        branch = payload.get("ref")
        if (repo is None) or (not isinstance(branch, str)) or (not branch):
            return "remove: unknown repository or branch"
        return self._remove_branch(repo, branch, now, payload.get("user"), "removed")

    def remove(self, name, branch, user=None, now=None):
        """Remove the slots of a branch by hand (name: "owner/repo"), returns False if unknown

        Adds a raw event, thus the removal stays after a restart. A new run brings the branch back.
        """
        with self.lock:
            found = [repo for repo in self.data["repos"].values()
                     if (repo.get("name") == name) and (branch in repo["slots"])]
            if not found:
                return False
            payload = {"repository": found[0]["id"], "ref": branch, "user": user}
        self.handle(REMOVE_EVENT, payload, now)
        return True

    def handle(self, event, payload, now=None, raw=True):
        """Handle webhook event (X-GitHub-Event header, JSON payload)

        now: event time (default: current time), raw: add to the raw events. Returns a short description (for the
        log), None for other events. Raises EventError.
        """
        if not isinstance(payload, dict):
            raise EventError("payload not an object")
        now = (time.time() if now is None else now)
        with self.lock:
            handler = {"workflow_run": self._on_run, "workflow_job": self._on_job, "delete": self._on_delete,
                       REMOVE_EVENT: self._on_remove}.get(event)
            if handler is None:
                return None
            if raw:
                self._log_event(event, payload, now)
            info = payload.get("repository")
            name = (info.get("full_name") if isinstance(info, dict) else None)
            if (event in ("workflow_run", "workflow_job")) and is_ignored(name, self.ignored):
                self._add_log(self._ignored_entry(event, payload, name, now))
                return "{} {} event ignored (github_ignored_repos)".format(name, event)
            text = handler(payload, now)
            for repo in self.data["repos"].values():
                self._prune(repo, now)
            return text

    @staticmethod
    def log_job(entry):
        """Get (job path, kind, GitHub status or conclusion) of an event log entry, None if no job state"""
        if (entry.get("kind") not in (None, "job")) or (not entry.get("status")) or (not entry.get("repo")):
            return None
        path = [SOURCE, str(entry["repo"]), entry.get("branch"), entry.get("workflow")]
        if entry.get("kind") == "job":
            if not entry.get("job"):
                return None
            path.append(entry["job"])
        return (path, ("job" if (entry.get("kind") == "job") else "pipeline"), entry["status"])

    def expire_jobs(self, now=None):
        """Remove slots without events in keep_days, then empty branches and repositories and unused runs

        Returns the number of removed slots.
        """
        now = (time.time() if now is None else now)
        limit = now - (self.keep_days * 86400)
        removed = 0
        with self.lock:
            for key, repo in list(self.data["repos"].items()):
                for branch, workflows in list(repo["slots"].items()):
                    for workflow, slot in list(workflows.items()):
                        run = repo["runs"].get(str(slot.get("run")))
                        if (run is None) or (run.get("updated", 0) < limit):
                            del workflows[workflow]
                            removed += 1
                    if not workflows:
                        del repo["slots"][branch]
                for branch, mark in list(repo.get("deleted", {}).items()):
                    if mark[1] < limit:
                        del repo["deleted"][branch]
                self._prune(repo, now)
                if (not repo["slots"]) and (not repo.get("deleted")):
                    del self.data["repos"][key]
        if removed > 0:
            LOG.info("Removed %d old GitHub workflow slots", removed)
        return removed

    def get(self):
        """Get a copy of the data"""
        with self.lock:
            return json.loads(json.dumps(self.data))

    def jobs(self):
        """Get job entries (see jobs.py) of all slots and of the job names in them"""
        entries = []
        with self.lock:
            for repo in self.data["repos"].values():
                name = repo.get("name", str(repo["id"]))
                for branch, workflows in repo["slots"].items():
                    for workflow, slot in sorted(workflows.items()):
                        entries.extend(self._slot_jobs(repo, [SOURCE, name, branch, workflow], slot))
        return entries

    @staticmethod
    def _slot_jobs(repo, path, slot):
        # Job entries of a slot and of its job names. Lock held.
        if "run" not in slot:
            return []
        run = repo["runs"].get(str(slot["run"]), {"id": slot["run"]})
        entries = [{"path": path, "status": job_status(run.get("status")), "result": slot.get("result", "UNKNOWN"),
                    "link": run.get("url"), "time": run.get("updated"),
                    "started": unix_time(run.get("started_at") or run.get("created_at")), "info": run_info(run)}]
        # Newest job of each name (re-runs have a new ID)
        newest = {}
        for job in run.get("jobs", {}).values():
            if job["id"] >= newest.get(job.get("name"), {}).get("id", 0):
                newest[job.get("name")] = job
        results = slot.get("jobs", {})
        for name in sorted(set(newest) | set(results)):
            job = newest.get(name)
            job_id = (job["id"] if (job is not None) else results.get(name, {}).get("result_job"))
            link = ((job or {}).get("url") or
                    ("{}/actions/runs/{}/job/{}".format(repo["url"], run["id"], job_id) if (repo.get("url") and job_id)
                     else None))
            entries.append({"path": path + [name], "status": (job_status(job.get("status")) if job else "STOPPED"),
                            "result": results.get(name, {}).get("result", "UNKNOWN"), "link": link,
                            "time": run.get("updated"), "info": job_info(run, job)})
        return entries
