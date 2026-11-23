"""Flask app: web interface and API

See backend/tschenggins-laempli.py for the endpoints. The device connection and the live updates of the web pages
are on the WebSocket server (wsserver.py).
"""

import base64
import datetime
import hashlib
import json
import logging
import os
import sys
import threading
import time
import urllib.parse

try:
    import flask
except ImportError:
    sys.exit("flask missing, please run: sudo apt install python3-flask")

from .dashboard import DashboardError
from .devices import LED_TEST_MODES, valid_id
from .firmware import Firmware, firmware_name, is_newer
from .generic import check_path
from .history import STATES as PLOT_STATES, job_history, statuses_of
from .jobs import RESULTS, STATUS
from .source import EventError
from .leds import (MAX_AGE, MAX_LEDS, PIPELINE_DEPTH, SOUND_EVENTS, SOUND_FX, ConfigError, check_config, check_rows,
                   device_state, fmt_age, row_layout, update_states)
from .settings import KEYS as SETTING_KEYS, NAME_MAX, SettingsError, check_settings
from .stream import auth_user, forwarded_user, peer_ip

LOG = logging.getLogger(__name__)

# Log requests slower than this [s] (IDEA4)
SLOW_REQUEST = 0.5

# Log source updates slower than this [s]
SLOW_UPDATE = 0.2

# Filter buttons (tablefilters.html): (value, title, button colour). Event logs: plot states (history.py).
LOG_STATES = (("running", "running", "primary"), ("waiting", "waiting", "info"),
              ("success", "success", "success"), ("failed", "failed", "danger"),
              ("canceled", "canceled", "secondary"), ("skipped", "skipped", "secondary"))
JOB_RESULTS = (("PASS", "PASS", "success"), ("FAIL", "FAIL", "danger"), ("WARN", "WARN", "warning"),
               ("UNKNOWN", "UNKNOWN", "secondary"))
JOB_STATUSES = (("RUNNING", "RUNNING", "primary"), ("WAITING", "WAITING", "info"), ("STOPPED", "STOPPED", "secondary"),
                ("UNKNOWN", "UNKNOWN", "secondary"))

# Event log entries per request: default, max
LOG_LENGTH = 100
LOG_MAX = 1000


def opt_float(args, name):
    """Get float request parameter, None if not there. Raises ValueError."""
    value = args.get(name, "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        raise ValueError("bad {}".format(name)) from None


def log_query(args, columns, kinds=None):
    """Get query_events() arguments from request parameters, raises ValueError

    start, length (default LOG_LENGTH, max LOG_MAX), q (search words), sort ("<column>.<asc|desc>,...", column: index
    in columns or entry key; default: newest first), since, until (Unix time [s]), kind (a key of kinds, see
    Source.LOG_KINDS), state (plot states, e.g. "failed,running", see history.py), ignored ("1": also the ignored
    entries; default: not).
    """
    try:
        start = int(args.get("start", "0") or 0)
        length = int(args.get("length", str(LOG_LENGTH)) or LOG_LENGTH)
    except ValueError:
        raise ValueError("bad start or length") from None
    if (start < 0) or (length < 0) or (length > LOG_MAX):
        raise ValueError("start or length out of range (length max. {})".format(LOG_MAX))
    sort = []
    for item in args.get("sort", "").split(","):
        if not item.strip():
            continue
        column, _sep, direction = item.strip().partition(".")
        if column.isdigit() and (int(column) < len(columns)):
            column = columns[int(column)]
        if (column not in columns) or (direction not in ("", "asc", "desc")):
            raise ValueError("bad sort {}".format(item))
        sort.append((column, (direction == "desc")))
    fields = {}
    kind = args.get("kind", "")
    if kind:
        if kind not in (kinds or {}):
            raise ValueError("bad kind {}".format(kind))
        fields["kind"] = kinds[kind]
    states = [state for state in args.get("state", "").split(",") if state]
    if any((state not in PLOT_STATES) for state in states):
        raise ValueError("bad state {}".format(args.get("state")))
    if states:
        fields["status"] = statuses_of(states)
    if args.get("ignored") != "1":
        fields["ignored"] = [None, 0]
    return {"start": start, "length": length, "q": (args.get("q") or None), "sort": sort,
            "since": opt_float(args, "since"), "until": opt_float(args, "until"), "fields": fields}


def fmt_dhms(seconds):
    """Format seconds as [[<d>:]<h>:]<m>:<ss>, e.g. "1:30:02", "0:08" (same as dhmsText() in laempli.js)"""
    rest = max(0, int(seconds))
    parts = []
    for size in (86400, 3600, 60, 1):
        if parts or (rest >= size) or (size <= 60):
            parts.append(("{:02d}" if parts else "{}").format(rest // size))
            rest %= size
    return ":".join(parts)


class ProxyMiddleware:
    """Path prefix from the reverse proxy: X-Forwarded-Prefix header

    Add the prefix to SCRIPT_NAME. Flask then builds URLs (url_for(), redirects) with the prefix. If the path starts
    with the prefix (the proxy passes the full path), remove it from the path.
    """

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        prefix = environ.get("HTTP_X_FORWARDED_PREFIX", "").strip().rstrip("/")
        if prefix.startswith("/"):
            environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + prefix
            path = environ.get("PATH_INFO", "")
            if (path == prefix) or path.startswith(prefix + "/"):
                environ["PATH_INFO"] = path[len(prefix):]
        return self.app(environ, start_response)


def remote_ip(request):
    """Get remote IP of a Flask request: first X-Forwarded-For address (reverse proxy), else peer address"""
    return peer_ip(request.headers, request.remote_addr)


# Largest GitLab event [bytes]
GITLAB_MAX_SIZE = 5 * 1024 * 1024

# Largest GitHub event [bytes]
GITHUB_MAX_SIZE = 5 * 1024 * 1024

# GitLab events that the source handles (pushes: only deleted branches and tags)
GITLAB_EVENTS = ("Pipeline Hook", "Job Hook", "Push Hook", "Tag Push Hook")

# GitHub events that the source handles
GITHUB_EVENTS = ("workflow_run", "workflow_job", "delete")

# LED rows on the device page if the device did not report its number of LEDs
DEFAULT_LEDS = 10

# Device page: LED arrangement of a device type: columns, LED order follows the setting led_reverse ("flip": LED 1 is
# at the end with led_reverse 0, "reverse": with 1, else no change). Project rows (Stägge): one line per row, "reverse"
# in each row. Other types: DEFAULT_COLS.
LED_VIEWS = {
    "chlotz": {"cols": 3},
    "chloetzli": {"cols": 1, "order": "flip"},
    "chischtli": {"cols": 1, "order": "flip"},
    "gitta": {"cols": 2, "order": "reverse"},
    "hello": {"cols": 1},
    "staegge": {"order": "reverse"},
}
DEFAULT_COLS = 4


def led_view(dev_id, settings, num):
    """Get LED arrangement of the device page: {"cols": columns, "order": LED indexes in the order shown}"""
    view = LED_VIEWS.get(dev_id.split("-")[0], {})
    reverse = ((settings or {}).get("values") or {}).get("led_reverse")
    order = list(range(num))
    if ((view.get("order") == "flip") and (reverse == 0)) or ((view.get("order") == "reverse") and (reverse == 1)):
        order.reverse()
    return {"cols": view.get("cols", DEFAULT_COLS), "order": order}


def form_rows(form, layout):
    """Get project rows from the device page form (project_<n>: <source>:<project>, max_age_<n>)"""
    rows = []
    for ix in range(layout[0]):
        source, _, project = form.get("project_{}".format(ix), "").partition(":")
        rows.append({"path": ([source, project] if project else []), "max_age": form.get("max_age_{}".format(ix), "")})
    return rows


def form_leds(form):
    """Get LED config list from the device page form (path_<n>: JSON list, name_<n>, max_age_<n>,
    sound_<event>_<n>)"""
    try:
        rows = min(int(form.get("rows", "0")), MAX_LEDS)
    except ValueError:
        rows = 0
    leds = []
    for ix in range(rows):
        path = form.get("path_{}".format(ix)) or "[]"
        try:
            path = json.loads(path)
        except ValueError:
            pass  # check_config() rejects the string
        led = {"path": path, "name": form.get("name_{}".format(ix), ""),
               "max_age": form.get("max_age_{}".format(ix), "")}
        led["sounds"] = {key: form.get("sound_{}_{}".format(key, ix), "") for key, _ in SOUND_EVENTS}
        leds.append(led)
    return leds


def form_settings(form):
    """Get the settings from the device page form (the fields that are there)"""
    return {key: form[key] for key, _, _, _ in SETTING_KEYS if key in form}


# Display names of the sources
SOURCE_NAMES = {"gitlab": "GitLab", "github": "GitHub", "generic": "Generic", "test": "Test"}


def source_name(source):
    """Get display name of a source, e.g. "GitLab" """
    return SOURCE_NAMES.get(source, str(source or "").capitalize())


def link_text(path, link):
    """Get text of a job link, e.g. "Open GitLab job". The URL tells pipeline or job (same path depth)."""
    source = (path[0] if path else "")
    link = (link or "")
    kind = "link"
    if source == "gitlab":
        kind = ("job" if ("/-/jobs/" in link) else ("pipeline" if ("/-/pipelines/" in link) else kind))
    elif source == "github":
        kind = ("job" if ("/job/" in link) else ("workflow run" if ("/actions/runs/" in link) else kind))
    return "Open {} {}".format(source_name(source), kind)


def events_query(path):
    """Get search text of the events tab for a job path, same as eventsHash() in laempli.js"""
    names = [name.replace('"', "") for name in path[1:]]
    return " ".join(('"{}"'.format(name) if (" " in name) else name) for name in names)


def settings_view(settings, values=None):
    """Get the settings card data: one row per setting (key, label, min, max, value, pending, options)

    Only the settings the device has (in its report; e.g. the hello has only the name). values: form values to show
    (after an error), else the device values with the pending ones.
    """
    shown = dict(settings["values"], **settings["pending"])
    shown.update(values or {})
    options = settings["options"]
    rows = []
    for key, label, low, high in SETTING_KEYS:
        if key not in settings["values"]:
            continue
        if key.endswith("_brightness"):
            choices = [(value, "{} ({})".format(name, value)) for value, name in options.get(key, [])]
        elif key.startswith("anim_"):
            choices = [(ix, "{} {}".format(ix, name)) for ix, name in enumerate(options.get(key, []))]
        elif key == "led_reverse":
            choices = [(0, "off"), (1, "on")]
        else:
            choices = []
        value = shown.get(key, "")
        if choices and (value not in [item[0] for item in choices]):
            choices.append((value, "{} (other)".format(value)))
        rows.append({"key": key, "label": label, "min": low, "max": high, "value": value,
                     "pending": (key in settings["pending"]), "choices": choices})
    return rows


def sound_choices(settings):
    """Get the sound choices of the LED config: [(value, text), ...], "" (none) first, then the sounds of the device
    (settings options: sound module, melodies)"""
    options = ((settings or {}).get("options") or {})
    choices = [("", "(none)")]
    if options.get("sound_fx"):
        choices.append((SOUND_FX, "Sound effect"))
    secs = options.get("melody_secs", {})
    choices += [(name, ("{} ({}s)".format(name, secs[name]) if (name in secs) else name))
                for name in options.get("melodies", [])]
    return choices


def github_payload(req, body):
    """Get payload of a GitHub event: JSON body, or form field "payload" (content type form), None if bad"""
    if req.mimetype == "application/x-www-form-urlencoded":
        body = (urllib.parse.parse_qs(body.decode("utf-8", "replace")).get("payload") or [""])[0]
    try:
        return json.loads(body)
    except ValueError:
        return None


def static_version(folder):
    """Get version of the scripts and styles in the static folder: first 12 hex digits of a hash of the files"""
    digest = hashlib.sha256()
    for top, dirs, files in sorted(os.walk(folder)):
        dirs.sort()
        for name in sorted(files):
            if name.endswith((".js", ".css")):
                digest.update(name.encode("utf-8"))
                with open(os.path.join(top, name), "rb") as fh:
                    digest.update(fh.read())
    return digest.hexdigest()[:12]


def favicon_uri(path):
    """Get data URI of a PNG file (favicon inlined in the pages), None if the file is missing"""
    try:
        with open(path, "rb") as fh:
            return "data:image/png;base64," + base64.b64encode(fh.read()).decode("ascii")
    except OSError:
        LOG.warning("No favicon %s", path)
        return None


def request_fields(req):
    """Get the fields of a generic job update: query string, form and JSON object (in this order, later wins)

    Returns None if the body is not a JSON object (JSON content type).
    """
    fields = dict(req.args.items())
    fields.update(req.form.items())
    if req.is_json:
        data = req.get_json(silent=True)
        if not isinstance(data, dict):
            return None
        fields.update(data)
    return fields


def create_app(devices, firmware=None, heartbeat=15, gitlab=None, jobs=None, github=None, generic=None,
               editors=None, changes=None, ws_port=None, testjobs=None, docs=None, dashboard=None,
               device_editors=None):
    """Create the Flask app

    devices: Devices, firmware: Firmware (None: no updates), heartbeat: time event interval [s], gitlab: Gitlab (None:
    no GitLab webhooks), jobs: Jobs (None: no LED config and state), github: Github (None: no GitHub webhooks),
    generic: Generic (None: no generic jobs), editors: user names (X-Forwarded-User) that can change the LED config
    and the settings (None: all, no check), changes: Changes (web page live updates, None: none), ws_port: port of
    the WebSocket server, for the pages without reverse proxy (with proxy: below the same path, see wsserver.py),
    testjobs: TestJobs (None: no test jobs), docs: Docs (None: no Docs tab), dashboard: DashboardConfig (None: no
    sticky jobs), device_editors: {user: [device ID, ...]}, these users can also change these devices (LED config,
    settings, firmware update, sound and LED test)
    """
    app = flask.Flask(__name__)
    firmware = (firmware if firmware is not None else Firmware(None))
    # Favicon, inlined in each page
    app.jinja_env.globals["favicon"] = favicon_uri(os.path.join(app.static_folder, "favicon.png"))
    # Version of the scripts and styles: in each page and in their URLs; the script reloads the page when it changes
    app.jinja_env.globals["app_version"] = static_version(app.static_folder)

    def source_changed(source):
        # After an event: event log, jobs of the source, device states
        t0 = time.monotonic()
        if changes is not None:
            changes.notify(source.SOURCE)
        changed = 0
        t1 = t2 = time.monotonic()
        if (jobs is not None) and jobs.update(source.SOURCE, source.jobs()):
            t2 = time.monotonic()
            changed = update_states(devices, jobs)
        t3 = time.monotonic()
        if changed > 0:
            LOG.info("New LED state for %d devices", changed)
        if (t3 - t0) > SLOW_UPDATE:
            LOG.warning("Slow %s update: %.3f s (notify %.3f, jobs %.3f, states %.3f)", source.SOURCE, t3 - t0,
                        t1 - t0, t2 - t1, t3 - t2)

    def request_user():
        # User from the proxy, else basic auth user
        return (forwarded_user(flask.request) or auth_user(flask.request))

    def can_edit(dev_id=None):
        # Editors change all, device editors their devices
        user = forwarded_user(flask.request)
        if (editors is None) or (user in editors):
            return True
        return ((dev_id is not None) and (dev_id in (device_editors or {}).get(user, ())))

    def edit_denied(dev_id=None):
        # Edit request of a user without edit rights
        if can_edit(dev_id):
            return False
        LOG.warning("No edit rights: user=%s %s %s%s", forwarded_user(flask.request), flask.request.method,
                    flask.request.script_root, flask.request.path)
        return True

    def firmware_view(info):
        # Firmware of a device: running version, newest file, is there an update, update only over USB
        latest = firmware.info(firmware_name(info["id"]))
        options = ((devices.get_settings(info["id"]) or {}).get("options") or {})
        return {"running": info.get("firmware"), "latest": latest, "newer": is_newer(latest, info.get("firmware")),
                "usb_only": bool(options.get("no_ota"))}

    def device_info(dev_id):
        # Info of one device, also an unknown one
        return next((dev for dev in devices.get() if dev["id"] == dev_id), {"id": dev_id})

    def render_device(dev_id, leds, error=None, settings_values=None, settings_error=None):
        # Device page: LED config form with the current LED states, settings form, firmware card
        all_devices = devices.get()
        info = next((dev for dev in all_devices if dev["id"] == dev_id), {"id": dev_id})
        settings = devices.get_settings(dev_id)
        layout = row_layout(dev_id)
        if layout is not None:
            return render_rows(dev_id, info, settings, layout, leds, error, settings_values, settings_error,
                               all_devices)
        num = max(info.get("leds") or DEFAULT_LEDS, len(leds))
        leds = leds + ([None] * (num - len(leds)))
        try:
            state = device_state(jobs, {"leds": check_config(leds)})["leds"]
        except ConfigError:
            state = []
        state = state + ([None] * (num - len(state)))
        return flask.render_template("device.html", info=info, firmware=firmware_view(info), all_devices=all_devices,
                                     rows=list(zip(range(num), leds, state)), view=led_view(dev_id, settings, num),
                                     tree=jobs.tree(), error=error,
                                     sound_events=SOUND_EVENTS, sound_choices=sound_choices(settings),
                                     can_edit=can_edit(dev_id),
                                     settings=settings, name_max=NAME_MAX, settings_error=settings_error,
                                     settings_rows=(settings_view(settings, settings_values)
                                                    if (settings and settings["values"]) else []))

    def render_rows(dev_id, info, settings, layout, rows, error, settings_values, settings_error, all_devices):
        # Device page of a device with project rows: rows form with the LED states, settings form
        rows = rows + ([None] * (layout[0] - len(rows)))
        try:
            state = device_state(jobs, check_rows(rows, layout))["leds"]
        except ConfigError:
            state = [None] * (layout[0] * layout[1])
        states = [state[ix * layout[1]:(ix + 1) * layout[1]] for ix in range(layout[0])]
        tree = jobs.tree()
        projects = [(source, project) for source in sorted(PIPELINE_DEPTH)
                    for project in sorted(tree.get(source, {"children": {}})["children"])]
        return flask.render_template("device.html", info=info, firmware=firmware_view(info), all_devices=all_devices,
                                     project_rows=list(zip(range(layout[0]), rows, states)),
                                     projects=projects, max_age=fmt_age(MAX_AGE), row_len=layout[1], rows=[],
                                     view=led_view(dev_id, settings, layout[1]),
                                     tree=tree, error=error, settings=settings, name_max=NAME_MAX,
                                     settings_error=settings_error, can_edit=can_edit(dev_id),
                                     settings_rows=(settings_view(settings, settings_values)
                                                    if (settings and settings["values"]) else []))

    def device_entries(dev_id):
        # Saved entries of the device page: project rows or LEDs
        config = (devices.get_config(dev_id) or {})
        return (config.get("rows") or [] if (row_layout(dev_id) is not None) else config.get("leds", []))

    def save_rows(dev_id, rows):
        # Save project rows, raises ConfigError
        config = check_rows(rows, row_layout(dev_id))
        devices.set_config(dev_id, config)
        update_states(devices, jobs, [dev_id])
        LOG.info("Project rows of %s: %d rows", dev_id, sum(1 for row in config["rows"] if row))

    @app.template_filter("events_url")
    def events_url_filter(path):
        # Events tab of a job, filtered for it; "" if the source has none
        if not {"gitlab": gitlab, "github": github, "generic": generic}.get(path[0] if path else None):
            return ""
        query = urllib.parse.quote(events_query(path), safe="")
        return "{}#/{}{}".format(flask.url_for("web_app"), path[0], ("?q=" + query) if query else "")

    @app.template_filter("source_name")
    def source_name_filter(source):
        return source_name(source)

    @app.template_filter("link_text")
    def link_text_filter(job):
        # Text of the link of a job (dict with path, link)
        return link_text(job.get("path"), job.get("link"))

    @app.template_filter("age")
    def age_filter(value):
        # Max. age: seconds as text, a bad form value as it is, none: ""
        if isinstance(value, int) and (not isinstance(value, bool)):
            return fmt_age(value)
        return ("" if (value is None) else str(value))

    @app.template_filter("timestamp")
    def timestamp_filter(value):
        return datetime.datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")

    @app.template_filter("ago")
    def ago_filter(value):
        return fmt_dhms(time.time() - value)

    @app.template_filter("dhms")
    def dhms_filter(value):
        return fmt_dhms(value)

    @app.template_filter("heap")
    def heap_filter(dev):
        # Free heap of a device: "<free> of <size> KiB", old firmware "<free> KiB" ("" if not known)
        if not isinstance(dev.get("heap"), int):
            return ""
        size = dev.get("heap_size")
        return ("{} of {} KiB".format(dev["heap"] // 1024, size // 1024) if isinstance(size, int) else
                "{} KiB".format(dev["heap"] // 1024))

    @app.context_processor
    def nav_context():
        # Navbar: jobs and source tabs only if enabled
        return {"server_now": time.time(), "nav_jobs": (jobs is not None), "nav_gitlab": (gitlab is not None),
                "nav_github": (github is not None), "nav_generic": (generic is not None),
                "nav_test": (testjobs is not None),
                "nav_sources": any(src is not None for src in (gitlab, github, generic, testjobs)),
                "nav_docs": (docs is not None),
                "user": forwarded_user(flask.request), "can_edit": can_edit(),
                "ws_port": (None if flask.request.headers.get("X-Forwarded-Prefix") else ws_port)}

    # Requests in work (for the slow request log)
    active = {"num": 0}
    active_lock = threading.Lock()

    @app.before_request
    def log_request():
        flask.g.start = time.monotonic()
        with active_lock:
            active["num"] += 1
        LOG.debug("%s %s%s user=%s", flask.request.method, flask.request.script_root, flask.request.path,
                  request_user())

    @app.teardown_request
    def time_request(_exc):
        start = flask.g.pop("start", None)
        if start is None:
            return
        with active_lock:
            num = active["num"]
            active["num"] -= 1
        duration = time.monotonic() - start
        if duration > SLOW_REQUEST:
            LOG.warning("Slow request: %s %s %.3f s (%d active)", flask.request.method, flask.request.path,
                        duration, num)

    @app.route("/")
    def root():
        return flask.redirect(flask.url_for("web_app"))

    @app.route("/web")
    def web_app():
        # Page without view: the script gets it (see laempli.js)
        return flask.render_template("base.html")

    @app.route("/web/devices")
    def web_devices():
        return flask.render_template("devices.html",
                                     devices=[dict(dev, fw=firmware_view(dev)) for dev in devices.get()])

    def source_rows():
        # Sources tab overview: events in the log, ignored ones, last event (else: newest job time)
        all_jobs = (jobs.all() if (jobs is not None) else [])
        rows = []
        for name, src, endpoint in (("gitlab", gitlab, "web_gitlab"), ("github", github, "web_github"),
                                    ("generic", generic, "web_generic"), ("test", testjobs, "web_test")):
            if src is None:
                continue
            mine = [job for job in all_jobs if (job["path"][0] == name)]
            last = src.last_event()
            times = ([last] if last else [job["time"] for job in mine if job["time"]])
            rows.append({"name": source_name(name), "url": flask.url_for(endpoint), "events": src.count_events(),
                         "ignored": src.count_ignored(), "last": max(times, default=None)})
        return rows

    def log_page(src, template):
        # Event log page: the table gets its rows from log_rows()
        if src is None:
            flask.abort(404)
        kinds = [(name, name.capitalize(), "secondary") for name in src.LOG_KINDS]
        return flask.render_template(template, total=src.count_events(), ignored=src.count_ignored(),
                                     keep_days=src.keep_days, kinds=kinds, states=LOG_STATES)

    def log_api(src):
        # Event log entries (JSON), see log_query()
        if src is None:
            flask.abort(404)
        try:
            query = log_query(flask.request.args, src.LOG_COLUMNS, src.LOG_KINDS)
        except ValueError as ex:
            return flask.jsonify({"error": str(ex)}), 400
        total, filtered, events = src.query_events(**query)
        return flask.jsonify({"total": total, "filtered": filtered, "events": events})

    def log_rows(src, macro):
        # Event log table rows (HTML, macro of log_rows.html), same parameters as log_api()
        if src is None:
            flask.abort(404)
        try:
            query = log_query(flask.request.args, src.LOG_COLUMNS, src.LOG_KINDS)
        except ValueError as ex:
            return flask.jsonify({"error": str(ex)}), 400
        total, filtered, events = src.query_events(**query)
        row = flask.get_template_attribute("log_rows.html", macro)
        return flask.jsonify({"total": total, "filtered": filtered, "rows": [str(row(ev)) for ev in events]})

    @app.route("/web/docs", defaults={"name": None})
    @app.route("/web/docs/<name>")
    def web_docs(name):
        # Docs tab: one Markdown doc as HTML (rendered at start), second navbar with the docs
        if docs is None:
            flask.abort(404)
        name = (name or docs.order[0])
        doc = docs.get(name)
        if doc is None:
            flask.abort(404)
        doc_tabs = [(title, flask.url_for("web_docs", name=key), (key == name), docs.get(key)["path"])
                    for key, title in docs.tabs()]
        return flask.render_template("docs.html", doc=doc, body=docs.html(name, flask.request.script_root),
                                     error=docs.error, doc_tabs=doc_tabs)

    @app.route("/web/docs/file/<path:path>")
    def web_docs_file(path):
        # Image of the docs (only the ones the docs use)
        full = (docs.file_path(path) if (docs is not None) else None)
        if full is None:
            flask.abort(404)
        return flask.send_file(full, max_age=3600)

    @app.route("/web/sources")
    def web_sources():
        if not any(src is not None for src in (gitlab, github, generic, testjobs)):
            flask.abort(404)
        return flask.render_template("sources.html", sources=source_rows())

    @app.route("/web/dashboard")
    def web_dashboard():
        # Dashboard: all jobs, newest first, as JSON in the page; the script makes the tiles (startDashboard)
        if jobs is None:
            flask.abort(404)
        entries = sorted(jobs.all(), key=lambda job: (job["time"] or 0), reverse=True)
        # Pipelines (GitLab, GitHub) have "started" (see jobs.py), jobs not
        data = [{"path": job["path"], "result": job["result"], "status": job["status"], "time": job["time"],
                 "link": job["link"], "linkText": (link_text(job["path"], job["link"]) if job["link"] else None),
                 "pipeline": ("started" in job)}
                for job in entries]
        sources = [(name, source_name(name)) for name, src in (("gitlab", gitlab), ("github", github),
                                                               ("generic", generic), ("test", testjobs))
                   if src is not None]
        return flask.render_template("dashboard.html", jobs=data, sources=sources,
                                     sticky=(dashboard.sticky() if (dashboard is not None) else None))

    @app.route("/api/dashboard", methods=["GET", "POST"])
    def api_dashboard():
        # Dashboard config: GET, POST (editors) {"sticky": [{"path": <job path>, "name": <name>}, ...]}
        if dashboard is None:
            flask.abort(404)
        if flask.request.method == "POST":
            if edit_denied():
                flask.abort(403)
            try:
                dashboard.set_sticky((flask.request.get_json(silent=True) or {}).get("sticky"))
            except DashboardError as ex:
                return flask.jsonify({"ok": False, "error": str(ex)}), 400
        return flask.jsonify({"sticky": dashboard.sticky()})

    @app.route("/web/jobs")
    def web_jobs():
        if jobs is None:
            flask.abort(404)
        return flask.render_template("jobs.html", jobs=jobs.all(), results=JOB_RESULTS, statuses=JOB_STATUSES)

    def plot_sources():
        # Sources with an event log: {name: Source}
        return {name: src for name, src in (("gitlab", gitlab), ("github", github), ("generic", generic))
                if src is not None}

    @app.route("/web/plots")
    def web_plots():
        # Plots tab: the script gets the data from /api/plots
        found = plot_sources()
        if (jobs is None) or (not found):
            flask.abort(404)
        return flask.render_template("plots.html", sources=[(name, source_name(name)) for name in found],
                                     keep_days=max(src.keep_days for src in found.values()))

    @app.route("/api/plots")
    def api_plots():
        # State changes of the jobs (history.py): since, until (default: last 24 h), kind, q, src, failed. Default
        # until: 1 s margin (event times are rounded)
        found = plot_sources()
        if not found:
            flask.abort(404)
        args = flask.request.args
        try:
            until = opt_float(args, "until")
            until = ((time.time() + 1) if (until is None) else until)
            since = opt_float(args, "since")
            since = ((until - 86400) if (since is None) else since)
        except ValueError as ex:
            return flask.jsonify({"error": str(ex)}), 400
        kind = (args.get("kind") or None)
        names = [name for name in (args.get("src") or "").split(",") if name]
        if (since >= until) or (kind not in (None, "pipelines", "jobs")) or any((name not in found) for name in names):
            return flask.jsonify({"error": "bad since, until, kind or src"}), 400
        if names:
            found = {name: found[name] for name in names}
        store = next(iter(found.values())).store
        return flask.jsonify(job_history(store, found, since, until, kind, args.get("q"),
                                         (args.get("failed") == "1")))

    @app.route("/api/log/<int:row_id>")
    def api_log_entry(row_id):
        # One event log entry (the cause of a plot point): {"id", "source", "entry"}
        found = plot_sources()
        item = (next(iter(found.values())).store.get_log(row_id) if found else None)
        if item is None:
            flask.abort(404)
        return flask.jsonify({"id": row_id, "source": item[0], "entry": item[1]})

    @app.route("/web/firmwares")
    def web_firmwares():
        latest = firmware.latest()
        return flask.render_template("firmwares.html", firmwares=[latest[name] for name in sorted(latest)])

    @app.route("/api/devices")
    def api_devices():
        return flask.jsonify(devices.get())

    @app.route("/api/state/<dev_id>")
    def api_state(dev_id):
        state, version = devices.get_state(dev_id)
        if state is None:
            flask.abort(404)
        return flask.jsonify({"version": version, "state": state})

    @app.route("/api/firmware")
    def api_firmware():
        return flask.jsonify(firmware.latest())

    @app.route("/api/firmware/<file>")
    def api_firmware_file(file):
        path = firmware.path(file)
        if path is None:
            flask.abort(404)
        LOG.info("Firmware download: %s %s", file, remote_ip(flask.request))
        return flask.send_file(path, mimetype="application/octet-stream", max_age=0)

    @app.route("/api/jobs")
    def api_jobs():
        if jobs is None:
            flask.abort(404)
        return flask.jsonify({"jobs": jobs.all()})

    @app.route("/api/jobs/remove", methods=["POST"])
    def api_jobs_remove():
        # Remove button: all jobs of a GitLab ref or GitHub branch, {"path": [source, project, ref]}
        if edit_denied():
            return flask.jsonify({"ok": False, "error": "no edit rights"}), 403
        path = (flask.request.get_json(silent=True) or {}).get("path")
        if (not isinstance(path, list)) or (len(path) != 3) or (not all(isinstance(name, str) and name
                                                                        for name in path)):
            return flask.jsonify({"ok": False, "error": "bad path"}), 400
        source = {"gitlab": gitlab, "github": github}.get(path[0])
        if source is None:
            return flask.jsonify({"ok": False, "error": "not removable"}), 400
        if not source.remove(path[1], path[2], request_user()):
            return flask.jsonify({"ok": False, "error": "no such branch"}), 404
        LOG.info("Removed %s by %s", " ".join(path), request_user())
        source_changed(source)
        return flask.jsonify({"ok": True})

    @app.route("/api/job")
    def api_job():
        # One job (?path=<JSON list>, e.g. ["gitlab","g/p","main"]) with its info, for the dashboard details
        if jobs is None:
            flask.abort(404)
        try:
            path = json.loads(flask.request.args.get("path", ""))
        except ValueError:
            path = None
        if (not isinstance(path, list)) or (not path) or (not all(isinstance(name, str) for name in path)):
            return flask.jsonify({"ok": False, "error": "bad path"}), 400
        found = jobs.find(path, len(path))
        if not found:
            return flask.jsonify({"ok": False, "error": "no such job"}), 404
        job = found[0]
        return flask.jsonify(dict(job, linkText=(link_text(job["path"], job["link"]) if job.get("link") else None)))

    @app.route("/web/device/<dev_id>", methods=["GET", "POST"])
    def web_device(dev_id):
        if (jobs is None) or (not valid_id(dev_id)):
            flask.abort(404)
        if flask.request.method == "GET":
            return render_device(dev_id, device_entries(dev_id))
        if edit_denied(dev_id):
            flask.abort(403)
        layout = row_layout(dev_id)
        if layout is not None:
            rows = form_rows(flask.request.form, layout)
            try:
                save_rows(dev_id, rows)
            except ConfigError as ex:
                return render_device(dev_id, rows, str(ex)), 400
            return flask.redirect(flask.url_for("web_device", dev_id=dev_id))
        leds = form_leds(flask.request.form)
        try:
            config = {"leds": check_config(leds)}
        except ConfigError as ex:
            return render_device(dev_id, leds, str(ex)), 400
        devices.set_config(dev_id, config)
        update_states(devices, jobs, [dev_id])
        LOG.info("LED config of %s: %d LEDs", dev_id, sum(1 for led in config["leds"] if led))
        return flask.redirect(flask.url_for("web_device", dev_id=dev_id))

    def change_settings(dev_id, values):
        # Check and add settings changes to pending, returns the pending values. Raises SettingsError.
        settings = devices.get_settings(dev_id)
        if (settings is None) or (not settings["values"]):
            raise SettingsError("no settings from the device yet")
        unknown = sorted(key for key in (values if isinstance(values, dict) else {}) if key not in settings["values"])
        if unknown:
            raise SettingsError("the device has no setting {}".format(", ".join(unknown)))
        values = check_settings(values, settings["options"])
        pending = devices.set_pending(dev_id, values)
        LOG.info("Settings of %s: pending %s", dev_id, (", ".join(sorted(pending)) or "none"))
        return pending

    @app.route("/web/device/<dev_id>/settings", methods=["POST"])
    def web_device_settings(dev_id):
        if (jobs is None) or (not valid_id(dev_id)):
            flask.abort(404)
        if edit_denied(dev_id):
            flask.abort(403)
        values = form_settings(flask.request.form)
        try:
            change_settings(dev_id, values)
        except SettingsError as ex:
            return render_device(dev_id, device_entries(dev_id),
                                 settings_values=values, settings_error=str(ex)), 400
        return flask.redirect(flask.url_for("web_device", dev_id=dev_id) + "#settings")

    @app.route("/api/settings/<dev_id>", methods=["GET", "POST"])
    def api_settings(dev_id):
        if not valid_id(dev_id):
            flask.abort(404)
        if flask.request.method == "POST":
            if edit_denied(dev_id):
                return flask.jsonify({"error": "no edit rights"}), 403
            try:
                change_settings(dev_id, flask.request.get_json(silent=True))
            except SettingsError as ex:
                return flask.jsonify({"error": str(ex)}), 400
        settings = devices.get_settings(dev_id)
        if settings is None:
            flask.abort(404)
        return flask.jsonify(settings)

    @app.route("/api/device/<dev_id>/update", methods=["POST"])
    def api_device_update(dev_id):
        # Firmware update button: only a newer firmware, only for a connected device, not while one runs
        if not valid_id(dev_id):
            flask.abort(404)
        if edit_denied(dev_id):
            return flask.jsonify({"error": "no edit rights"}), 403
        info = device_info(dev_id)
        view = firmware_view(info)
        if not view["newer"]:
            return flask.jsonify({"error": "no newer firmware"}), 409
        if view["usb_only"]:
            return flask.jsonify({"error": "device updates only over USB"}), 409
        ota = info.get("ota")
        if info.get("connected") and (ota is not None) and (ota["state"] in ("starting", "running", "done")):
            return flask.jsonify({"error": "update running"}), 409
        if not devices.send_command(dev_id, "update", {"firmware": view["latest"]}):
            return flask.jsonify({"error": "device not connected"}), 409
        devices.start_ota(dev_id, view["latest"]["version"])
        LOG.info("Firmware update of %s to %s by %s", dev_id, view["latest"]["version"], request_user())
        return flask.jsonify({"ok": True, "version": view["latest"]["version"]})

    @app.route("/api/device/<dev_id>/sound", methods=["POST"])
    def api_device_sound(dev_id):
        # Sound test button: a sound that the device has
        if not valid_id(dev_id):
            flask.abort(404)
        if edit_denied(dev_id):
            return flask.jsonify({"error": "no edit rights"}), 403
        sound = (flask.request.get_json(silent=True) or {}).get("sound")
        choices = [choice for choice, _ in sound_choices(devices.get_settings(dev_id))]
        if (not sound) or (sound not in choices):
            return flask.jsonify({"error": "device has no such sound"}), 400
        if not devices.send_command(dev_id, "sound", {"sound": sound}):
            return flask.jsonify({"error": "device not connected"}), 409
        return flask.jsonify({"ok": True})

    @app.route("/api/device/<dev_id>/led", methods=["POST"])
    def api_device_led(dev_id):
        # LED test buttons: {"mode": <LED_TEST_MODES or "normal">, "led": n} ("row": n for project rows, find only)
        if not valid_id(dev_id):
            flask.abort(404)
        if edit_denied(dev_id):
            return flask.jsonify({"error": "no edit rights"}), 403
        req = flask.request.get_json(silent=True)
        req = (req if isinstance(req, dict) else {})
        mode = req.get("mode")
        if mode not in (LED_TEST_MODES + ("normal",)):
            return flask.jsonify({"error": "bad mode"}), 400
        settings = devices.get_settings(dev_id)
        if (settings is None) or (not settings["options"].get("led_test")):
            return flask.jsonify({"error": "device firmware has no LED test"}), 409
        data = {"mode": mode}
        if mode == "find":
            layout = row_layout(dev_id)
            key = ("row" if layout else "led")
            num = (layout[0] if layout else (device_info(dev_id).get("leds") or MAX_LEDS))
            ix = req.get(key)
            if (not isinstance(ix, int)) or isinstance(ix, bool) or (ix < 0) or (ix >= num):
                return flask.jsonify({"error": "bad " + key}), 400
            data.update((dict(first=(ix * layout[1]), count=layout[1]) if layout else dict(first=ix, count=1)))
        if not devices.send_command(dev_id, "led", data):
            return flask.jsonify({"error": "device not connected"}), 409
        LOG.info("LED test of %s: %s by %s", dev_id, " ".join(str(value) for value in data.values()), request_user())
        return flask.jsonify({"ok": True})

    @app.route("/api/leds/<dev_id>", methods=["GET", "POST"])
    def api_leds(dev_id):
        if (jobs is None) or (not valid_id(dev_id)):
            flask.abort(404)
        if flask.request.method == "POST":
            if edit_denied(dev_id):
                return flask.jsonify({"error": "no edit rights"}), 403
            try:
                if row_layout(dev_id) is not None:
                    save_rows(dev_id, flask.request.get_json(silent=True))
                else:
                    config = {"leds": check_config(flask.request.get_json(silent=True))}
                    devices.set_config(dev_id, config)
                    update_states(devices, jobs, [dev_id])
                    LOG.info("LED config of %s: %d LEDs", dev_id, sum(1 for led in config["leds"] if led))
            except ConfigError as ex:
                return flask.jsonify({"error": str(ex)}), 400
        config = devices.get_config(dev_id)
        if config is None:
            flask.abort(404)
        return flask.jsonify({"config": (config["rows"] if ("rows" in config) else config["leds"]),
                              "state": device_state(jobs, config)["leds"]})

    @app.route("/web/gitlab")
    def web_gitlab():
        return log_page(gitlab, "gitlab_log.html")

    @app.route("/web/gitlab/rows")
    def web_gitlab_rows():
        return log_rows(gitlab, "gitlab_row")

    @app.route("/api/gitlab/log")
    def api_gitlab_log():
        return log_api(gitlab)

    @app.route("/api/gitlab", methods=["GET", "POST"])
    def api_gitlab():
        if gitlab is None:
            flask.abort(404)
        if flask.request.method == "GET":
            return flask.jsonify(gitlab.get())
        req = flask.request
        # Only pipeline, job and push events, ignore others at once
        event = req.headers.get("X-Gitlab-Event", "")
        if event not in GITLAB_EVENTS:
            LOG.debug("GitLab event '%s' ignored", event)
            return flask.jsonify({"ok": True, "ignored": True})
        if (req.content_length or 0) > GITLAB_MAX_SIZE:
            flask.abort(413)
        body = req.get_data()
        error = gitlab.check_signature(req.headers.get("webhook-id"), req.headers.get("webhook-timestamp"),
                                       req.headers.get("webhook-signature"), body)
        if error is not None:
            LOG.warning("GitLab event from %s: %s", remote_ip(req), error)
            flask.abort(401)
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None
        try:
            text = gitlab.handle(event, payload)
        except EventError as ex:
            LOG.warning("GitLab event '%s' bad: %s", event, ex)
            flask.abort(400)
        if text is None:
            # Each push comes here, only deletions count
            (LOG.debug if ("Push" in event) else LOG.info)("GitLab event '%s' ignored", event)
        else:
            LOG.info("GitLab: %s", text)
            source_changed(gitlab)
        return flask.jsonify({"ok": True})

    @app.route("/web/github")
    def web_github():
        return log_page(github, "github_log.html")

    @app.route("/web/github/rows")
    def web_github_rows():
        return log_rows(github, "github_row")

    @app.route("/api/github/log")
    def api_github_log():
        return log_api(github)

    @app.route("/api/github", methods=["GET", "POST"])
    def api_github():
        if github is None:
            flask.abort(404)
        if flask.request.method == "GET":
            return flask.jsonify(github.get())
        req = flask.request
        # Only workflow run, job and delete events (and ping), ignore others at once
        event = req.headers.get("X-GitHub-Event", "")
        if event not in (GITHUB_EVENTS + ("ping",)):
            LOG.debug("GitHub event '%s' ignored", event)
            return flask.jsonify({"ok": True, "ignored": True})
        if (req.content_length or 0) > GITHUB_MAX_SIZE:
            flask.abort(413)
        body = req.get_data()
        error = github.check_signature(req.headers.get("X-Hub-Signature-256"), body)
        if error is not None:
            LOG.warning("GitHub event from %s: %s", remote_ip(req), error)
            flask.abort(401)
        payload = github_payload(req, body)
        if event == "ping":
            repo = (payload or {}).get("repository") or (payload or {}).get("organization") or {}
            LOG.info("GitHub: ping, hook %s, %s", (payload or {}).get("hook_id"),
                     repo.get("full_name") or repo.get("login") or "?")
            return flask.jsonify({"ok": True, "ping": True})
        try:
            text = github.handle(event, payload)
        except EventError as ex:
            LOG.warning("GitHub event '%s' bad: %s", event, ex)
            flask.abort(400)
        LOG.info("GitHub: %s", text)
        source_changed(github)
        return flask.jsonify({"ok": True})

    @app.route("/web/generic")
    def web_generic():
        return log_page(generic, "generic_log.html")

    @app.route("/web/generic/rows")
    def web_generic_rows():
        return log_rows(generic, "generic_row")

    @app.route("/api/generic/log")
    def api_generic_log():
        return log_api(generic)

    @app.route("/api/generic")
    def api_generic():
        if generic is None:
            flask.abort(404)
        return flask.jsonify(generic.get())

    @app.route("/api/generic/<path:names>", methods=["GET", "POST", "PUT", "DELETE"])
    def api_generic_job(names):
        if generic is None:
            flask.abort(404)
        req = flask.request
        try:
            path = check_path(names.split("/"))
        except EventError as ex:
            return flask.jsonify({"ok": False, "error": str(ex)}), 400
        if req.method == "GET":
            entry = generic.job(path)
            if entry is None:
                return flask.jsonify({"ok": False, "error": "no such job"}), 404
            return flask.jsonify({"ok": True, "job": entry})
        error = generic.check_token(req.headers.get("X-Laempli-Token"))
        if error is not None:
            LOG.warning("Generic job from %s: %s", remote_ip(req), error)
            return flask.jsonify({"ok": False, "error": error}), 401
        event = ("delete" if (req.method == "DELETE") else "set")
        if (event == "delete") and (generic.job(path) is None):
            return flask.jsonify({"ok": False, "error": "no such job"}), 404
        fields = (request_fields(req) if (event == "set") else {})
        if fields is None:
            return flask.jsonify({"ok": False, "error": "body must be a JSON object"}), 400
        try:
            text = generic.handle(event, dict(fields, path=path, client=remote_ip(req)))
        except EventError as ex:
            LOG.warning("Generic job from %s bad: %s", remote_ip(req), ex)
            return flask.jsonify({"ok": False, "error": str(ex)}), 400
        LOG.info("Generic: %s", text)
        source_changed(generic)
        return flask.jsonify({"ok": True, "job": generic.job(path)})

    @app.route("/web/test")
    def web_test():
        if testjobs is None:
            flask.abort(404)
        return flask.render_template("test.html", jobs=testjobs.jobs(), results=RESULTS, statuses=STATUS)

    @app.route("/web/test/<name>", methods=["POST"])
    def web_test_job(name):
        if (testjobs is None) or (not testjobs.has_job(name)):
            flask.abort(404)
        if edit_denied():
            flask.abort(403)
        form = flask.request.form
        try:
            text = testjobs.handle("set", {"name": name, "status": form.get("status"), "result": form.get("result"),
                                           "user": request_user()})
        except EventError as ex:
            LOG.warning("Test job %s bad: %s", name, ex)
            flask.abort(400)
        LOG.info("Test: %s", text)
        source_changed(testjobs)
        return flask.redirect(flask.url_for("web_test"))

    return app
