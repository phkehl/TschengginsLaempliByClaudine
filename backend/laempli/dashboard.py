"""Dashboard config: sticky jobs

The sticky jobs are shown first on the Dashboard tab, in this order, also without data (unknown job), with an optional
name (instead of the project name on the tile). One list for all dashboards (office screens), set by the editors in the
config dialog. File: <data>/dashboard.json:

    {"sticky": [{"path": ["gitlab", "core/release-config", "main"], "name": "Release"},
                {"path": ["generic", "nas", "backup"], "name": ""}, ...]}

An entry can also be the path alone (first format of the file, also in the POST of /api/dashboard).

A change notifies the topic "dashboard" (live update of the open dashboards).
"""

import json
import logging
import os
import tempfile
import threading

LOG = logging.getLogger(__name__)

FILE = "dashboard.json"
MAX_STICKY = 30
PATH_MIN = 2
PATH_MAX = 9
NAME_MAX = 200
LABEL_MAX = 40


class DashboardError(Exception):
    """Bad dashboard config"""


def check_sticky(entries):
    """Check the sticky jobs (entries {"path", "name"} or paths), returns the entries without duplicate paths. Raises
    DashboardError."""
    if not isinstance(entries, list):
        raise DashboardError("sticky must be a list of jobs")
    if len(entries) > MAX_STICKY:
        raise DashboardError("max. {} sticky jobs".format(MAX_STICKY))
    result = []
    for entry in entries:
        path, label = ((entry, "") if isinstance(entry, list) else
                       ((entry.get("path"), entry.get("name") or "") if isinstance(entry, dict) else (None, "")))
        if ((not isinstance(path, list)) or (not (PATH_MIN <= len(path) <= PATH_MAX)) or
                (not all(isinstance(name, str) and name and (len(name) <= NAME_MAX) for name in path))):
            raise DashboardError("bad job path: {}".format(json.dumps(path)[:100]))
        if (not isinstance(label, str)) or (len(label.strip()) > LABEL_MAX):
            raise DashboardError("name of {}: max. {} characters".format(" / ".join(path), LABEL_MAX))
        if path not in [item["path"] for item in result]:
            result.append({"path": path, "name": label.strip()})
    return result


class DashboardConfig:
    """Dashboard config in the data directory"""

    def __init__(self, dirname, changes=None):
        self.path = os.path.join(dirname, FILE)
        self.dirname = dirname
        self.changes = changes
        self.lock = threading.Lock()
        self.data = {"sticky": []}
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                self.data["sticky"] = check_sticky(json.load(fh).get("sticky", []))
        except FileNotFoundError:
            pass
        except (ValueError, AttributeError, DashboardError) as ex:
            LOG.warning("Dashboard: bad %s, no sticky jobs: %s", self.path, ex)

    def sticky(self):
        """Get a copy of the sticky jobs (entries {"path", "name"})"""
        with self.lock:
            return [{"path": list(entry["path"]), "name": entry["name"]} for entry in self.data["sticky"]]

    def set_sticky(self, entries):
        """Set the sticky jobs (checked, see check_sticky()), save, notify. Returns the list. Raises DashboardError."""
        entries = check_sticky(entries)
        with self.lock:
            self.data["sticky"] = entries
            os.makedirs(self.dirname, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".dashboard-", suffix=".json", dir=self.dirname)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self.data, fh, indent=4)
                    fh.write("\n")
                os.replace(tmp, self.path)
            except BaseException:
                os.unlink(tmp)
                raise
        LOG.info("Dashboard: %d sticky jobs", len(entries))
        if self.changes is not None:
            self.changes.notify("dashboard")
        return entries
