#!/usr/bin/env python3
"""Tschenggins Lämpli backend.

CI jobs report their status to this backend. The Tschenggins Lämpli devices (Chlotz, hello) connect to it with a
WebSocket and get the status and the status changes; they send reports on the same connection.

Configuration: tschenggins-laempli.yaml next to this script (or --config <file>), a copy of
tschenggins-laempli.yaml.example. It sets the listen address and port, the data directory, the heartbeat interval,
the log level, the GitLab webhook signing tokens, the GitHub webhook secrets, the generic job tokens, the days to keep
raw events, the housekeeping (interval, firmware files to keep, days to keep devices) and the editors.

Endpoints (behind a reverse proxy below its path, see X-Forwarded-Prefix):

    /             Redirect to /web
    /web          Web interface (single page app), the hash has the view: /web#/devices, /web#/device/<ID>
    /web/devices  Tab Devices
    /web/jobs     Tab Jobs: all jobs with result, status and info
    /web/firmwares      Tab Firmwares: newest firmware file of each name
    /web/gitlab   Tab GitLab: log of the last 1000 GitLab pipeline and job events
    /web/github   Tab GitHub: log of the last 1000 GitHub workflow run and job events
    /web/test     Tab Test: change the test jobs
    /web/sources  Tab Sources: overview of the sources (jobs, events, last event)
    /web/docs/<name>    Tab Docs: the Markdown docs (README.md, laempli/*.md, backend/backend.md), rendered at start
    /web/docs/file/<path>  Image of the docs
    /web/dashboard      Tab Dashboard: tiles of all jobs, sticky jobs first
    /api/dashboard      GET: dashboard config (sticky jobs), POST: set it (JSON {"sticky": [<job path>, ...]})
    /api/job?path=<JSON>  One job as JSON
    /web/test/<name>  POST: set status or result of a test job (form)
    /web/device/<ID>    LED config of a device (form) and LED states, device settings (form)
    /web/device/<ID>/settings  POST: device settings form
    /api/devices  Devices as JSON
    /api/settings/<ID>  GET: device settings, options and pending changes as JSON, POST: change settings (JSON
                  {key: value}, see laempli/settings.py)
    /api/device/<ID>/update  POST: start firmware update of a connected device
    /api/device/<ID>/sound   POST: play a sound on a connected device (JSON {"sound": <name>})
    /api/device/<ID>/led     POST: LED test on a connected device (JSON {"mode": ..., "led": n} or "row": n)
    /api/state/<ID>     State of a device as JSON (404 if unknown device)
    /api/firmware       Newest firmware of each name as JSON
    /api/firmware/<file>  Firmware file for OTA updates
    /api/gitlab   POST: GitLab webhook (pipeline and job events, others are ignored), GET: GitLab data as JSON
    /api/jobs     All jobs as JSON: {"jobs": [...]} (see laempli/jobs.py)
    /api/gitlab/log  Log of the last 1000 GitLab pipeline and job events as JSON, newest first
    /api/github   POST: GitHub webhook (workflow run and job events, ping, others are ignored), GET: GitHub data as JSON
    /api/github/log  Log of the last 1000 GitHub workflow run and job events as JSON, newest first
    /web/generic  Tab Generic: log of the last 1000 generic job updates
    /api/generic/<name>/...  POST: set a generic job (fields in query string, form or JSON), DELETE: remove it, GET:
                  the job as JSON (see laempli/generic.py and the README)
    /api/generic  GET: generic job data as JSON
    /api/generic/log  Log of the last 1000 generic job updates as JSON, newest first
    /api/leds/<ID>   GET: LED config and LED states of a device as JSON, POST: set LED config (JSON list, see
                     laempli/leds.py)

WebSocket endpoints (own port ws_port, default port + 1; laempli/wsserver.py), messages {"event": ..., "data": ...}:

    /ws/device    Device connection. Backend -> device: the stream events below. Device -> backend: "report" events
                  after each heartbeat and each settings change: {"device": <ID>, "rssi": <dBm>, "uptime": <s>,
                  "heap": <bytes>, "settings": {...}, "ack": <seq>, "options": {...} (first report of a connection),
                  "ota": {...} (update runs or failed)}; "ota" events while a firmware update runs (state, progress,
                  part requests; the backend sends the parts as binary messages, see laempli/wsserver.py)
    /ws/web       Web pages: "changed" events with the changed topics (laempli/changes.py), "time" events

Stream events on /ws/device (data is JSON):

    hello   {"server": "laempli", "time": <unix time [s], with ms>, "utc_offset": <s>, "heartbeat": <s>,
             "firmware": {"name": ..., "version": ..., "hash": ..., "time": <yyyymmddhhmmss>, "file": ...,
                          "size": <bytes>, "md5": ...} or null}
    state   {"version": <n>, "state": {"leds": [{"label": ..., "path": [...], "status": ..., "result": ...,
             "link": ..., "time": ..., "info": {...}} or null, ...]}} (see laempli/leds.py)
    time    {"time": <unix time [s], with ms>, "utc_offset": <s>}
    settings  {"seq": <n>, "settings": {key: value}}: changes from the web page, at connect and on change (see
             laempli/settings.py)
    update  {"firmware": {...}}: button on the web page, install this firmware
    sound   {"sound": <name>}: button on the web page, play this sound
    ota     {"error": <text>}: bad firmware part request

The devices identify themselves with query parameters on /ws/device: device (ID, required), fw
(firmware version), ssid (WiFi network), ip (local IP), rssi (WiFi signal [dBm]), leds (number of LEDs). The remote
IP is the first X-Forwarded-For address (reverse proxy), else the peer address.

Device ID: <type>-<6 hex chars>, e.g. chlotz-96c480. The type selects the firmware: laempli-<type>.

Data: one file <data dir>/laempli_<device ID>.json for each device, with the device info, the LED config and the
device state.

Jobs: sources (GitLab, GitHub, generic, test) give the state of their jobs (laempli/jobs.py). A job is one thing to
watch, e.g. a GitLab pipeline or job, a GitHub workflow or job, or a cron job. A path identifies it: [source, name,
...], e.g. ["gitlab", "group/project", "main", "build"] or ["github", "owner/repo", "main", "ci", "build"]. Job state:
status (STOPPED, RUNNING, WAITING, UNKNOWN) and result of the last run (PASS, FAIL, WARN, UNKNOWN). The jobs are in
memory only, GET /api/jobs gives all of them.

LEDs: each LED of a device shows one job, set on the device page. The backend sends the LED states to the device
after each change. The device shows the result as colour and the status as pulse or blink.

Settings: the device name, display and LED settings of a device can be changed on the device and on the device page.
The device has the values and reports them; changes from the web page are pending until the device has them
(laempli/settings.py).

GitLab: add a webhook in the project (or group) settings: URL <backend URL>/api/gitlab, triggers "Pipeline events" and
"Job events" (other events are ignored), signing token (whsec_..., GitLab generates it). Add each token to the
gitlab_signing_tokens list in the configuration (one per webhook). The backend checks the webhook-signature header. It
keeps the newest main pipeline (with its jobs) of each ref of each project, and separately the newest downstream (child)
pipeline of each trigger job (laempli/gitlab.py). It stores only the raw events, in <data dir>/events.sqlite,
for events_keep_days days (laempli/eventdb.py). At start, it replays them to make the state again.

GitHub: add a webhook in the repository (or organisation) settings: payload URL <backend URL>/api/github, content type
application/json, a secret, events "Workflow runs" and "Workflow jobs". Add each secret to the github_webhook_secrets
list in the configuration. The backend checks the X-Hub-Signature-256 header. It keeps the newest run (with its jobs)
of each workflow of each branch of each repository (laempli/github.py). It stores only the raw events, in
<data dir>/events.sqlite, as for GitLab.

Generic: scripts (e.g. cron jobs) set their jobs with curl: POST <backend URL>/api/generic/<name>/<name>?status=...
&result=...&timeout=... (laempli/generic.py, examples in the README). Header X-Laempli-Token: one of the tokens in
generic_tokens (empty list: no check). A job with a timeout gets status UNKNOWN when no update comes in time (checked
each minute). Raw events in <data dir>/events.sqlite.

Test: jobs ["test", "test01"] ... ["test", "test10"] (laempli/testjobs.py). Set one for an LED and change its status
and result on the tab Test (select both, then Send: one event), to check how a device shows a job. Raw events in
<data dir>/events.sqlite.

LED max. age (laempli/leds.py): each LED can have a max. age (default: none). When the last change of its job is
older, the job data is outdated and the LED shows no data (UNKNOWN). A thread makes the device states again each
minute.

Housekeeping (laempli/housekeeping.py): a thread removes old raw events, jobs without events, old firmware files and
devices not connected for a long time, every housekeeping_interval seconds.

utc_offset is the offset of the server local time to UTC (includes daylight saving time). The devices use it to show
the local time.

The backend has no authentication. In production, run it behind a reverse proxy (e.g. Apache) that does
authentication (e.g. HTTP basic auth) and TLS. Headers from the proxy:

    X-Forwarded-Prefix  Path of the backend on the proxy, e.g. /laempli. The backend makes its URLs with it. The
                        proxy can pass the path with or without the prefix.
    X-Forwarded-User    Name of the logged in user. Only users in the editors list can change the LED config and
                        the settings (POST /web/device/<ID>, /web/device/<ID>/settings, /api/leds/<ID>,
                        /api/settings/<ID>; else 403), and the test jobs (POST /web/test/<name>). Users in
                        device_editors can change only their devices. The web pages show the forms disabled
                        for other users.

The backend trusts these headers from all clients: listen on 127.0.0.1 (the default), with the proxy on the same
host.

Firmware updates (OTA): files <data dir>/fw/<name>_<version>.bin, e.g. laempli-chlotz_20261023012737-9e085f6.bin (the
build copies them to data/fw/). The hello event has the newest file for the device type. The devices ask the user to
install a newer version.

Logging: as systemd service, lines with "<n>" priority prefix for the journal. Else coloured lines.

Requires the Debian packages python3-flask, python3-waitress, python3-yaml and python3-coloredlogs.
"""

import argparse
import logging
import os
import sys
import threading
import time

try:
    import waitress
except ImportError:
    sys.exit("waitress missing, please run: sudo apt install python3-waitress")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from laempli.changes import Changes  # noqa: E402
from laempli.config import ConfigError, load_config  # noqa: E402
from laempli.dashboard import DashboardConfig  # noqa: E402
from laempli.devices import Devices  # noqa: E402
from laempli.docs import Docs  # noqa: E402
from laempli.eventdb import EventStore  # noqa: E402
from laempli.firmware import Firmware  # noqa: E402
from laempli.generic import Generic  # noqa: E402
from laempli.github import Github  # noqa: E402
from laempli.gitlab import Gitlab  # noqa: E402
from laempli.housekeeping import Housekeeping  # noqa: E402
from laempli.jobs import Jobs  # noqa: E402
from laempli.leds import AGE_CHECK, update_states  # noqa: E402
from laempli.logs import setup_logging  # noqa: E402
from laempli.testjobs import TestJobs  # noqa: E402
from laempli.web import ProxyMiddleware, create_app  # noqa: E402
from laempli.wsserver import WsServer  # noqa: E402

# Check of the firmware files (new files, web page live updates) [s]
FIRMWARE_CHECK = 10

LOG = logging.getLogger("laempli")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n", 1)[1], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", "-c", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                               "tschenggins-laempli.yaml"),
                        help="configuration file (default: %(default)s)")
    args = parser.parse_args()

    setup_logging("info")
    try:
        cfg = load_config(args.config)
    except ConfigError as ex:
        LOG.error("%s", ex)
        return 1
    setup_logging(cfg["log_level"])
    LOG.info("Config %s", os.path.abspath(args.config))

    changes = Changes()
    devices = Devices(cfg["data"], changes)
    fwdir = os.path.join(cfg["data"], "fw")
    os.makedirs(fwdir, exist_ok=True)
    firmware = Firmware(fwdir)
    LOG.info("Firmware: %s", ", ".join(info["file"] for info in firmware.latest().values()) or "none")
    store = EventStore(cfg["data"])
    LOG.info("Events: %s", store.path)
    gitlab = Gitlab(cfg["data"], cfg["gitlab_signing_tokens"], cfg["events_keep_days"], store,
                    cfg["gitlab_ignored_projects"])
    if cfg["gitlab_ignored_projects"]:
        LOG.info("GitLab: ignored projects: %s", ", ".join(cfg["gitlab_ignored_projects"]))
    if cfg["gitlab_signing_tokens"]:
        LOG.info("GitLab: %d signing tokens", len(cfg["gitlab_signing_tokens"]))
    else:
        LOG.warning("No gitlab_signing_tokens, GitLab events are not checked")
    start = time.monotonic()
    num = gitlab.load()
    LOG.info("GitLab: replayed %d events in %.1f s", num, time.monotonic() - start)
    github = Github(cfg["data"], cfg["github_webhook_secrets"], cfg["events_keep_days"], store,
                    cfg["github_ignored_repos"])
    if cfg["github_ignored_repos"]:
        LOG.info("GitHub: ignored repositories: %s", ", ".join(cfg["github_ignored_repos"]))
    if cfg["github_webhook_secrets"]:
        LOG.info("GitHub: %d webhook secrets", len(cfg["github_webhook_secrets"]))
    else:
        LOG.warning("No github_webhook_secrets, GitHub events are not checked")
    start = time.monotonic()
    num = github.load()
    LOG.info("GitHub: replayed %d events in %.1f s", num, time.monotonic() - start)
    generic = Generic(cfg["data"], cfg["generic_tokens"], cfg["events_keep_days"], store)
    if cfg["generic_tokens"]:
        LOG.info("Generic: %d tokens", len(cfg["generic_tokens"]))
    else:
        LOG.warning("No generic_tokens, generic job updates are not checked")
    start = time.monotonic()
    num = generic.load()
    LOG.info("Generic: replayed %d events in %.1f s", num, time.monotonic() - start)
    generic.check_late()
    testjobs = TestJobs(cfg["data"], cfg["events_keep_days"], store)
    start = time.monotonic()
    num = testjobs.load()
    LOG.info("Test: replayed %d events in %.1f s", num, time.monotonic() - start)
    sources = [gitlab, github, generic, testjobs]
    store.checkpoint()
    jobs = Jobs(changes)
    for source in sources:
        jobs.update(source.SOURCE, source.jobs())
    LOG.info("Jobs: %d", len(jobs.all()))
    LOG.info("LED state changed for %d devices", update_states(devices, jobs))
    Housekeeping(cfg, sources, jobs, devices, firmware, changes).start()

    def generic_late():
        # Late generic jobs changed: new jobs and device states
        changes.notify(generic.SOURCE)
        if jobs.update(generic.SOURCE, generic.jobs()):
            LOG.info("Generic: late jobs changed, new LED state for %d devices", update_states(devices, jobs))

    generic.start(generic_late)

    def check_ages():
        # LED max. age: device states again each AGE_CHECK seconds
        while True:
            time.sleep(AGE_CHECK)
            try:
                changed = update_states(devices, jobs)
                if changed > 0:
                    LOG.info("LED max. age: new LED state for %d devices", changed)
            except Exception:  # pylint: disable=broad-except
                LOG.exception("LED max. age check failed")

    threading.Thread(target=check_ages, name="led-ages", daemon=True).start()

    def check_firmware():
        # New or removed firmware files (deploy, housekeeping)
        last = firmware.latest()
        while True:
            time.sleep(FIRMWARE_CHECK)
            try:
                latest = firmware.latest()
                if latest != last:
                    last = latest
                    changes.notify("firmwares")
            except Exception:  # pylint: disable=broad-except
                LOG.exception("Firmware check failed")

    threading.Thread(target=check_firmware, name="firmware-check", daemon=True).start()
    if cfg["listen"] not in ("127.0.0.1", "::1", "localhost"):
        LOG.warning("Listen on %s: clients can send X-Forwarded-User", cfg["listen"])
    if cfg["editors"]:
        LOG.info("Editors: %s", ", ".join(cfg["editors"]))
    else:
        LOG.warning("No editors, nobody can change LED config and settings")
    for user, dev_ids in sorted(cfg["device_editors"].items()):
        LOG.info("Device editor %s: %s", user, (", ".join(dev_ids) or "none"))
    docs = Docs()
    dashboard = DashboardConfig(cfg["data"], changes)
    LOG.info("Dashboard: %d sticky jobs", len(dashboard.sticky()))
    app = ProxyMiddleware(create_app(devices, firmware, cfg["heartbeat"], gitlab, jobs, github, generic,
                                     cfg["editors"], changes, cfg["ws_port"], testjobs, docs, dashboard,
                                     cfg["device_editors"]))
    WsServer(devices, firmware, cfg["heartbeat"], changes).start(cfg["listen"], cfg["ws_port"])
    LOG.info("Listening on %s:%d", cfg["listen"], cfg["port"])

    # HTTP requests (web pages, API, webhooks); the WebSocket server has a thread per connection
    waitress.serve(app, host=cfg["listen"], port=cfg["port"], threads=16, channel_timeout=3600, ident="laempli")
    return 0


if __name__ == "__main__":
    sys.exit(main())
