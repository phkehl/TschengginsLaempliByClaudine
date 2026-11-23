#!/usr/bin/env python3
"""Set, read and remove generic jobs of the Tschenggins Lämpli backend (e.g. from cron jobs)

The same as the curl commands in README.md (Generic jobs), with a "run" command that runs a command and reports it.
Only the Python standard library, copy this file to the machine that runs the jobs.

Commands:

    set <job> [--status S] [--result R] [--exit N] [--link URL] [--info NAME=TEXT]... [--timeout S]
        Set fields of a job (see README.md). A new job starts with status STOPPED, result UNKNOWN.
    run <job> [--timeout S] [--link URL] [--info NAME=TEXT]... -- <command> [<arg>...]
        Set status RUNNING, run the command, then set the exit code (result PASS or FAIL, status STOPPED), the
        timeout and the info lines "duration" and "exit code" (and --info). Exits with the exit code of the command.
        The command runs also if the backend is not there. "--" is needed if the command has options.
    get <job>       Show a job
    delete <job>    Remove a job
    list            Show all generic jobs

set, get and list: --json prints the answer of the backend as JSON.

<job> is the job path without "generic", names separated by "/", e.g. nas/backup.

Backend URL and token, each from the first that has it:

1. Options --url and --token
2. Config file (needs python3-yaml), keys "url" and "token": given with --config, else laempli-notify.yaml next to
   this script (same directory, if there; format: laempli-notify.yaml.example)
3. Environment variables LAEMPLI_NOTIFY_URL and LAEMPLI_NOTIFY_TOKEN

The URL is the backend base URL, e.g. https://<user>:<password>@example.com/tschenggins-laempli (user and
password for basic auth, optional). The token is one of generic_tokens of the backend config (header
X-Laempli-Token), not needed if the backend has no tokens.

Examples:

    export LAEMPLI_NOTIFY_URL="https://<user>:<password>@example.com/tschenggins-laempli"
    export LAEMPLI_NOTIFY_TOKEN="<token>"
    laempli-notify.py set nas/backup --status running --timeout 7200
    laempli-notify.py set nas/backup --exit 0 --timeout 90000 --info "size=12 GB"
    laempli-notify.py run nas/backup --timeout 90000 -- /usr/local/bin/backup.sh --full
    laempli-notify.py get nas/backup

Exit code: 0 OK, 1 backend error (message on stderr), 2 bad arguments; "run": the exit code of the command.
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# HTTP request timeout [s]
HTTP_TIMEOUT = 20

# Config file without --config (if there)
DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.realpath(__file__)), "laempli-notify.yaml")


class NotifyError(Exception):
    """Backend or config error"""


def load_config(path):
    """Get url and token from a YAML config file"""
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        raise NotifyError("{}: no such file".format(path))
    try:
        import yaml  # pylint: disable=import-outside-toplevel
    except ImportError as ex:
        raise NotifyError("{}: yaml missing, please run: sudo apt install python3-yaml".format(path)) from ex
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as ex:
        raise NotifyError("{}: {}".format(path, ex)) from ex
    if not isinstance(cfg, dict):
        raise NotifyError("{}: not a mapping".format(path))
    return {key: str(cfg[key]) for key in ("url", "token") if cfg.get(key)}


class Backend:
    """Generic job API of the backend"""

    def __init__(self, url, token=None):
        parts = urllib.parse.urlsplit(url.rstrip("/"))
        if parts.scheme not in ("http", "https"):
            raise NotifyError("URL must start with http:// or https://")
        self.headers = {"Accept": "application/json"}
        if parts.username is not None:
            auth = "{}:{}".format(urllib.parse.unquote(parts.username), urllib.parse.unquote(parts.password or ""))
            self.headers["Authorization"] = "Basic " + base64.b64encode(auth.encode("utf-8")).decode("ascii")
        if token:
            self.headers["X-Laempli-Token"] = token
        netloc = parts.hostname + ((":" + str(parts.port)) if parts.port else "")
        self.base = urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, "", ""))

    def request(self, method, job=None, fields=None, api="generic"):
        """Send a request, returns the answer (JSON). Raises NotifyError."""
        url = self.base + "/api/" + api
        if job is not None:
            url += "/" + "/".join(urllib.parse.quote(name, safe="") for name in job)
        headers = dict(self.headers)
        data = None
        if fields is not None:
            data = json.dumps(fields).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as ex:
            try:
                error = json.loads(ex.read().decode("utf-8")).get("error")
            except (ValueError, AttributeError):
                error = None
            raise NotifyError("{} {}{}".format(ex.code, ex.reason, (": " + error) if error else "")) from ex
        except (urllib.error.URLError, OSError, ValueError) as ex:
            raise NotifyError(str(getattr(ex, "reason", ex))) from ex

    def set(self, job, fields):
        """Set fields of a job, returns the job"""
        return self.request("POST", job, fields)["job"]

    def get(self, job):
        """Get a job"""
        return self.request("GET", job)["job"]

    def delete(self, job):
        """Remove a job"""
        self.request("DELETE", job)

    def jobs(self):
        """Get all generic jobs"""
        return [job for job in self.request("GET", api="jobs")["jobs"] if job["path"][0] == "generic"]


def job_path(text):
    """Get job path (list of names) from "name/name..." """
    path = [name.strip() for name in text.strip("/").split("/")]
    if not all(path):
        raise argparse.ArgumentTypeError("bad job path {!r}".format(text))
    return path


def info_line(text):
    """Get (name, text) from "name=text" """
    name, sep, value = text.partition("=")
    if (not sep) or (not name.strip()):
        raise argparse.ArgumentTypeError("info must be NAME=TEXT, not {!r}".format(text))
    return (name.strip(), value)


def update_fields(args):
    """Get the fields of a set command"""
    fields = {key: getattr(args, key) for key in ("status", "result", "exit", "link", "timeout")
              if getattr(args, key, None) is not None}
    if args.info:
        fields["info"] = dict(args.info)
    return fields


def show_job(job):
    """Print a job: path, status, result, link, info lines"""
    print("{}: {} {}".format(" / ".join(job["path"][1:]), job["status"], job["result"]))
    if job.get("link"):
        print("    link: {}".format(job["link"]))
    for name, text in (job.get("info") or {}).items():
        print("    {}: {}".format(name, text))


def run_command(backend, args):
    """Run a command, report it, returns its exit code"""
    try:
        backend.set(args.job, {"status": "RUNNING"})
    except NotifyError as ex:
        print("laempli-notify: start not reported: {}".format(ex), file=sys.stderr)
    start = time.monotonic()
    try:
        code = subprocess.call(args.command)
    except OSError as ex:
        print("laempli-notify: {}: {}".format(args.command[0], ex.strerror), file=sys.stderr)
        code = 127
    duration = round(time.monotonic() - start)
    info = {"duration": "{} s".format(duration), "exit code": str(code)}
    info.update(dict(args.info or []))
    fields = {"exit": code, "info": info}
    for key in ("link", "timeout"):
        if getattr(args, key) is not None:
            fields[key] = getattr(args, key)
    try:
        show_job(backend.set(args.job, fields))
    except NotifyError as ex:
        print("laempli-notify: end not reported: {}".format(ex), file=sys.stderr)
    return code


def parse_args(argv):
    """Parse the command line"""
    # Command of "run" after "--" (argparse does not do this well)
    command = None
    if "--" in argv:
        command = argv[(argv.index("--") + 1):]
        argv = argv[:argv.index("--")]
    parser = argparse.ArgumentParser(prog="laempli-notify.py", description=__doc__.split("\n", 1)[0],
                                     epilog=__doc__.split("\n", 1)[1], formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--url", help="backend base URL (else config file, LAEMPLI_NOTIFY_URL)")
    parser.add_argument("--token", help="X-Laempli-Token (else config file, LAEMPLI_NOTIFY_TOKEN)")
    parser.add_argument("--config", help="config file with url and token (YAML), default: {} (if there)"
                        .format(DEFAULT_CONFIG))
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="command")

    def add_fields(cmd):
        cmd.add_argument("--link", help="URL for more info (empty: none)")
        cmd.add_argument("--info", action="append", type=info_line, metavar="NAME=TEXT",
                         help="info line (repeat for more), replaces all info lines")
        cmd.add_argument("--timeout", type=int, metavar="S", help="expect next update within S seconds (0: never)")

    cmd = sub.add_parser("set", help="set fields of a job")
    cmd.add_argument("job", type=job_path, help="job path, e.g. nas/backup")
    cmd.add_argument("--status", help="STOPPED, RUNNING, WAITING or UNKNOWN")
    cmd.add_argument("--result", help="PASS, FAIL, WARN or UNKNOWN")
    cmd.add_argument("--exit", type=int, metavar="N", help="exit code: 0 PASS, else FAIL; status STOPPED")
    add_fields(cmd)
    cmd = sub.add_parser("run", help="run a command, report it")
    cmd.add_argument("job", type=job_path, help="job path, e.g. nas/backup")
    add_fields(cmd)
    cmd.add_argument("command", nargs="*", help="command and its arguments (after --)")
    for name, text in (("get", "show a job"), ("delete", "remove a job")):
        cmd = sub.add_parser(name, help=text)
        cmd.add_argument("job", type=job_path, help="job path, e.g. nas/backup")
    sub.add_parser("list", help="show all generic jobs")
    for name in ("set", "get", "list"):
        sub.choices[name].add_argument("--json", action="store_true", help="print the answer as JSON")
    args = parser.parse_args(argv)
    if command is not None:
        if (args.cmd != "run") or args.command:
            parser.error("-- only for run, before the command")
        args.command = command
    if (args.cmd == "run") and (not args.command):
        parser.error("run: no command")
    return args


def main(argv=None):
    """Run the command line, returns the exit code"""
    args = parse_args(sys.argv[1:] if (argv is None) else argv)
    try:
        config = (args.config or (DEFAULT_CONFIG if os.path.isfile(DEFAULT_CONFIG) else None))
        cfg = (load_config(config) if config else {})
        url = (args.url or cfg.get("url") or os.environ.get("LAEMPLI_NOTIFY_URL"))
        token = (args.token or cfg.get("token") or os.environ.get("LAEMPLI_NOTIFY_TOKEN"))
        if not url:
            raise NotifyError("no backend URL (--url, --config or LAEMPLI_NOTIFY_URL)")
        backend = Backend(url, token)
        if args.cmd == "run":
            return run_command(backend, args)
        if args.cmd == "list":
            jobs = backend.jobs()
            if args.json:
                print(json.dumps(jobs, indent=4))
            else:
                for job in jobs:
                    show_job(job)
            return 0
        if args.cmd == "delete":
            backend.delete(args.job)
            print("{}: deleted".format(" / ".join(args.job)))
            return 0
        job = (backend.get(args.job) if (args.cmd == "get") else backend.set(args.job, update_fields(args)))
        if args.json:
            print(json.dumps(job, indent=4))
        else:
            show_job(job)
        return 0
    except NotifyError as ex:
        print("laempli-notify: {}".format(ex), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
