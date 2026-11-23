"""LED configuration and LED state of the devices

Each LED of a device shows one job (see jobs.py). The config of a device (web page /web/device/<ID>, or
/api/leds/<ID>) is a list with one entry per LED:

    {"path": ["gitlab", "core/release-config", "feature/scarthgap", "yocto_trigger"], "name": "Yocto"}

or null for an unused LED. "name" is optional, a short label for the device display. "max_age" is optional (seconds,
missing: no limit), see below. "sounds" is optional (missing: no sounds), see below. Old entries
({"project", "ref", "downstream", "job", "name"}, GitLab only; "status_timeout", "result_timeout") are converted, see
convert_config().

The state that the device gets ("state" event) has one entry per configured LED, null for unused LEDs:

    {"leds": [{"label": "Yocto", "path": [...], "status": "RUNNING", "result": "PASS", "link": "https://...",
               "time": 1792756431.6, "info": {"pipeline": "87295 (#4305)", ...}}, null, ...]}

status, result, link, time and info are those of the job (status and result "UNKNOWN" if there is no job of that
path). The device shows the result as colour and the status as pulse or blink, see jobs.py. The entry also has the
"sounds" of the LED config, if there are some.

Sounds (per LED, the device plays them when the job stops): {<event>: <sound>}, only the events with a sound.
Events (see SOUND_EVENTS):

- "fail": the job stopped with result FAIL or WARN
- "pass": the job stopped with result PASS
- "broken": the job stopped with FAIL or WARN, and the result before was PASS (instead of "fail", if set)
- "fixed": the job stopped with PASS, and the result before was FAIL or WARN (instead of "pass", if set)

Sound: a melody name (the device reports its melodies in the settings options, see settings.py), or SOUND_FX (the
sound module of the Hello). The device ignores sounds that it does not have. The device finds the events: it compares
the new state of the LED with the one before (see fw/src/common/jobsound.cpp).

Max. age (per LED, optional): if the last change of the job ("time") is older than max_age, the job data is outdated:
the LED gets the entry of a job without data (status and result UNKNOWN, no link, no time) with the only info line
"outdated", e.g. "no change for 7d". This also covers a final event that never comes (e.g. a job that stays RUNNING).
Jobs without time (no events, unchanged test jobs) are never outdated. The main script checks the states each AGE_CHECK
seconds.

Project rows (devices in ROW_LAYOUTS, e.g. the Stägge with 4 rows of 15 LEDs): the config has one entry per row instead
of one entry per LED, null for an unused row:

    {"leds": [], "row_len": 15, "rows": [{"path": ["gitlab", "core/sw/common/fpxx"], "max_age": 604800}, null, ...]}

Each row shows the pipelines of one project, one LED per pipeline, without the stages and jobs in them: paths of
PIPELINE_DEPTH names (GitLab: source, project, ref. GitHub: source, repository, branch, workflow). The first LED of the
row has the oldest pipeline, then newer ones, the rest of the row is off. The order is by the start of the newest run
("started", see jobs.py), thus a pipeline moves to the new end when it runs again, and pipelines that run at the same
time do not swap on each event. Pipelines without change for max_age (optional, seconds or text, 0: no limit, missing:
MAX_AGE) are not shown. If there are too many for the row, the oldest are not shown. The state has the same entries as
above, all LEDs of all rows.

After each source event and each config change, update_states() makes the state of the devices again. The devices
get a new state only if it changed.
"""

import logging
import posixpath
import re
import time

from .jobs import unknown_job

LOG = logging.getLogger(__name__)

# Most LEDs per device
MAX_LEDS = 64

# Path length (source and 1 to 7 more names), max. length of a name
PATH_MIN = 2
PATH_MAX = 8
PATH_NAME_MAX = 200

# Max. length of the LED name
NAME_MAX = 40

# Project rows: default max. age of a pipeline [s] (LEDs: no limit), max. value
MAX_AGE = 7 * 86400
AGE_MAX = 366 * 86400

# Max. age check interval [s]
AGE_CHECK = 60

# Old timeout fields, see convert_config()
OLD_TIMEOUTS = ("status_timeout", "result_timeout")

# Duration units
UNITS = (("d", 86400), ("h", 3600), ("m", 60), ("s", 1))

# Sound events: (key, label), sound of the sound module, max. length and chars of a sound name
SOUND_EVENTS = (("fail", "Fail"), ("pass", "Pass"), ("broken", "Pass \u2192 fail"), ("fixed", "Fail \u2192 pass"))
SOUND_FX = "fx"
SOUND_MAX = 24
SOUND_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

# Project rows: device type -> (rows, LEDs per row)
ROW_LAYOUTS = {"staegge": (4, 15)}

# Project rows: sources -> names in the path of a pipeline
PIPELINE_DEPTH = {"gitlab": 3, "github": 4}

# Old config fields (GitLab), in path order
OLD_FIELDS = ("project", "ref", "downstream", "job")


class ConfigError(Exception):
    """Bad LED config"""


def convert_config(leds):
    """Convert old LED entries, returns the new list

    {project, ref, downstream, job, name} become path entries. Of the old timeouts, result_timeout becomes max_age
    (if there is no max_age), status_timeout is removed.
    """
    if not isinstance(leds, list):
        return leds
    result = []
    for led in leds:
        if isinstance(led, dict) and ("path" not in led) and ("project" in led):
            path = ["gitlab"] + [led[field] for field in OLD_FIELDS if led.get(field)]
            led = ({"path": path, "name": led.get("name", "")} if led.get("project") else None)
        if isinstance(led, dict) and any((key in led) for key in OLD_TIMEOUTS):
            new = {key: value for key, value in led.items() if key not in OLD_TIMEOUTS}
            if ("max_age" not in new) and (led.get("result_timeout") not in (None, "")):
                new["max_age"] = led["result_timeout"]
            led = new
        result.append(led)
    return result


def parse_duration(value):
    """Get duration [s] from int or text ("90", "90s", "30m", "1h30m", "3d"), None if empty. Raises ValueError."""
    if (value is None) or (isinstance(value, str) and (not value.strip())):
        return None
    if isinstance(value, bool):
        raise ValueError("not a duration")
    if isinstance(value, int):
        seconds = value
    else:
        text = str(value).strip().lower().replace(" ", "")
        if text.isdigit():
            seconds = int(text)
        elif re.fullmatch(r"(\d+[dhms])+", text):
            seconds = sum(int(num) * dict(UNITS)[unit] for num, unit in re.findall(r"(\d+)([dhms])", text))
        else:
            raise ValueError("not a duration")
    if (seconds < 0) or (seconds > AGE_MAX):
        raise ValueError("out of range")
    return seconds


def fmt_age(seconds):
    """Format duration [s] as text, e.g. "1h30m", "3d", "0" """
    parts = []
    for unit, size in UNITS:
        num, seconds = divmod(seconds, size)
        if num:
            parts.append("{}{}".format(num, unit))
    return ("".join(parts) or "0")


def check_sounds(ix, sounds):
    """Check and clean up the sounds of LED ix (dict or None), returns them without empty ones. Raises ConfigError."""
    sounds = ({} if sounds is None else sounds)
    if not isinstance(sounds, dict):
        raise ConfigError("LED {}: sounds must be an object".format(ix))
    keys = [key for key, _ in SOUND_EVENTS]
    unknown = sorted(set(sounds) - set(keys))
    if unknown:
        raise ConfigError("LED {}: unknown sound events {}".format(ix, ", ".join(unknown)))
    result = {}
    for key in keys:
        value = sounds.get(key)
        value = ("" if value is None else value)
        if not isinstance(value, str):
            raise ConfigError("LED {}: sound {} must be a string".format(ix, key))
        value = value.strip()
        if not value:
            continue
        if (len(value) > SOUND_MAX) or (SOUND_RE.match(value) is None):
            raise ConfigError("LED {}: sound {}: max. {} letters, digits, - _ .".format(ix, key, SOUND_MAX))
        result[key] = value
    return result


def check_config(leds):
    """Check and clean up the LED config (list), returns the new list. Raises ConfigError.

    Converts old entries (see convert_config()). Strips the strings. An entry without path (or empty path) is unused
    (None). Trailing unused entries are removed. max_age: seconds or text (see parse_duration()), stored as seconds,
    left out if empty or 0 (no limit). Sounds: left out if empty. Error texts number the LEDs from 1 (as the web page
    and the Chlotz).
    """
    if not isinstance(leds, list):
        raise ConfigError("LED config must be a list")
    leds = convert_config(leds)
    if len(leds) > MAX_LEDS:
        raise ConfigError("too many LEDs (max. {})".format(MAX_LEDS))
    result = []
    for ix, led in enumerate(leds, start=1):
        if led is None:
            result.append(None)
            continue
        if not isinstance(led, dict):
            raise ConfigError("LED {}: not an object".format(ix))
        unknown = sorted(set(led) - {"path", "name", "max_age", "sounds"})
        if unknown:
            raise ConfigError("LED {}: unknown fields {}".format(ix, ", ".join(unknown)))
        name = led.get("name")
        name = ("" if name is None else name)
        if not isinstance(name, str):
            raise ConfigError("LED {}: name must be a string".format(ix))
        name = name.strip()
        if len(name) > NAME_MAX:
            raise ConfigError("LED {}: name too long (max. {})".format(ix, NAME_MAX))
        path = led.get("path")
        path = ([] if path is None else path)
        if (not isinstance(path, list)) or (not all(isinstance(item, str) for item in path)):
            raise ConfigError("LED {}: path must be a list of strings".format(ix))
        path = [item.strip() for item in path]
        if not path:
            result.append(None)
            continue
        if (len(path) < PATH_MIN) or (len(path) > PATH_MAX):
            raise ConfigError("LED {}: path must have {}-{} names".format(ix, PATH_MIN, PATH_MAX))
        if not all(0 < len(item) <= PATH_NAME_MAX for item in path):
            raise ConfigError("LED {}: path names must have 1-{} chars".format(ix, PATH_NAME_MAX))
        entry = {"path": path, "name": name}
        try:
            max_age = parse_duration(led.get("max_age"))
        except ValueError as ex:
            raise ConfigError("LED {}: max. age must be 0-{} s or e.g. 12h, 7d".format(ix, AGE_MAX)) from ex
        if max_age:
            entry["max_age"] = max_age
        sounds = check_sounds(ix, led.get("sounds"))
        if sounds:
            entry["sounds"] = sounds
        result.append(entry)
    while result and (result[-1] is None):
        result.pop()
    return result


def row_layout(dev_id):
    """Get (rows, LEDs per row) of a device with project rows, None for other devices"""
    return ROW_LAYOUTS.get(dev_id.split("-")[0])


def check_rows(rows, layout):
    """Check and clean up the project rows (list), returns the config. Raises ConfigError.

    The config has an entry for each row. An entry without path is unused (None). max_age: seconds or text (see
    parse_duration()), stored as seconds, left out if empty. Error texts number the rows from 1.
    """
    if not isinstance(rows, list):
        raise ConfigError("rows must be a list")
    num, row_len = layout
    if len(rows) > num:
        raise ConfigError("too many rows (max. {})".format(num))
    result = []
    for ix, row in enumerate(rows, start=1):
        if (row is None) or (isinstance(row, dict) and (not row.get("path"))):
            result.append(None)
            continue
        if not isinstance(row, dict):
            raise ConfigError("row {}: not an object".format(ix))
        unknown = sorted(set(row) - {"path", "max_age"})
        if unknown:
            raise ConfigError("row {}: unknown fields {}".format(ix, ", ".join(unknown)))
        path = row["path"]
        if ((not isinstance(path, list)) or (len(path) != 2) or (not all(isinstance(item, str) for item in path))
                or (path[0] not in PIPELINE_DEPTH) or (not 0 < len(path[1].strip()) <= PATH_NAME_MAX)):
            raise ConfigError("row {}: path must be [{}, <project>]".format(ix, " or ".join(sorted(PIPELINE_DEPTH))))
        entry = {"path": [path[0], path[1].strip()]}
        try:
            max_age = parse_duration(row.get("max_age"))
        except ValueError as ex:
            raise ConfigError("row {}: max. age must be 0-{} s or e.g. 12h, 7d".format(ix, AGE_MAX)) from ex
        if max_age is not None:
            entry["max_age"] = max_age
        result.append(entry)
    return {"leds": [], "row_len": row_len, "rows": result + ([None] * (num - len(result)))}


def led_label(led):
    """Get display label of a configured LED: its name, else "<last part of path[1]> <path[2]> ..." """
    if led.get("name"):
        return led["name"]
    path = led["path"]
    return " ".join([posixpath.basename(path[1])] + path[2:])


def is_outdated(job, max_age, now):
    """Check if the last change of a job is older than max_age (0: no limit)"""
    return bool(max_age) and (job["time"] is not None) and ((now - job["time"]) > max_age)


def led_job(led, job, now):
    """Get the job for an LED: the job, or a job without data if it is outdated (see is_outdated())"""
    max_age = led.get("max_age", 0)
    if not is_outdated(job, max_age, now):
        return job
    return dict(unknown_job(job["path"]), info={"outdated": "no change for {}".format(fmt_age(max_age))})


def row_leds(jobs, row, row_len, now):
    """Get the state entries of the LEDs of a project row: oldest pipeline first, the rest None"""
    if not row:
        return [None] * row_len
    max_age = row.get("max_age", MAX_AGE)
    found = [job for job in jobs.find(row["path"], PIPELINE_DEPTH[row["path"][0]])
             if (job["time"] is not None) and (not is_outdated(job, max_age, now))]
    found.sort(key=lambda job: ((job.get("started") or job["time"]), job["path"]))
    leds = []
    for job in found[-row_len:]:
        leds.append({"label": " ".join(job["path"][2:]), "path": job["path"], "status": job["status"],
                     "result": job["result"], "link": job["link"], "time": job["time"], "info": job["info"]})
    return leds + ([None] * (row_len - len(leds)))


def device_state(jobs, config, now=None):
    """Make the state of a device from its config and the jobs (now: time for the max. age, default: current time)"""
    now = (time.time() if now is None else now)
    if config.get("rows") is not None:
        return {"leds": [led for row in config["rows"] for led in row_leds(jobs, row, config["row_len"], now)]}
    leds = []
    for led in config.get("leds", []):
        if not led:
            leds.append(None)
            continue
        job = led_job(led, jobs.get(led["path"]), now)
        entry = {"label": led_label(led), "path": led["path"], "status": job["status"], "result": job["result"],
                 "link": job["link"], "time": job["time"], "info": job["info"]}
        if led.get("sounds"):
            entry["sounds"] = led["sounds"]
        leds.append(entry)
    return {"leds": leds}


def update_states(devices, jobs, dev_ids=None):
    """Update the state of the devices (all, or the ones in dev_ids), returns the number of changed devices"""
    changed = 0
    for dev_id in (devices.ids() if dev_ids is None else dev_ids):
        config = devices.get_config(dev_id)
        if config is None:
            continue
        if devices.update_state(dev_id, device_state(jobs, config)):
            LOG.debug("New state for %s", dev_id)
            changed += 1
    return changed
