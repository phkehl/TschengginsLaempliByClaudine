"""Firmware files for OTA updates

Files <data dir>/fw/<name>_<version>.bin, e.g. laempli-chlotz_20261023012737-9e085f6.bin. The name is laempli-<type>,
the version is <yyyymmddhhmmss>-<hash> (see tools/version.py, the build copies the files there). For each name,
the file with the newest timestamp is the update for the devices of this type (device ID <type>-<hex>). The
housekeeping removes older files, see prune().
"""

import hashlib
import logging
import os
import re
import threading

LOG = logging.getLogger(__name__)

# The device types of this project (PlatformIO envs laempli-<type>). Only these may connect: the energy monitor
# (EnergyMonByClaudine) talks a similar protocol.
DEVICE_TYPES = ("chlotz", "hello", "staegge", "chloetzli", "gitta", "chischtli")

# File name: name, version (time, hash)
FILE_RE = re.compile(r"^(?P<name>laempli-[a-z0-9]+)_(?P<version>(?P<time>[0-9]{14})-(?P<hash>[0-9a-z-]+))\.bin$")


def firmware_name(dev_id):
    """Get firmware name for a device ID"""
    return "laempli-" + dev_id.split("-", 1)[0]


def device_type(dev_id):
    """Get device type of a device ID (the part before the "-")"""
    return dev_id.split("-", 1)[0]


def is_newer(latest, version):
    """Check if a firmware (info, None: none) is newer than the version that a device runs ("": not known)"""
    if (latest is None) or (not version):
        return False
    stamp = version.split("-", 1)[0]
    return ((not re.fullmatch("[0-9]{14}", stamp)) or (latest["time"] > stamp))


class Firmware:
    """Firmware files for OTA updates in a directory

    The directory is read again on each request. The MD5 of each file is cached.
    """

    def __init__(self, dirname):
        self.dirname = dirname
        self.lock = threading.Lock()
        self._md5 = {}  # File name -> (mtime, size, md5)

    def path(self, file):
        """Path of a firmware file, None if not a valid existing file"""
        if (not self.dirname) or (FILE_RE.match(file) is None):
            return None
        path = os.path.join(self.dirname, file)
        return (path if os.path.isfile(path) else None)

    def read(self, file, offset, length):
        """Read part of a firmware file, None if not a valid file or range"""
        path = self.path(file)
        if (path is None) or (offset < 0) or (length <= 0):
            return None
        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                data = fh.read(length)
        except OSError:
            return None
        return (data if data else None)

    def _info(self, file):
        match = FILE_RE.match(file)
        path = os.path.join(self.dirname, file)
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return None
        with self.lock:
            cached = self._md5.get(file)
            if (cached is None) or (cached[:2] != (st.st_mtime_ns, st.st_size)):
                md5 = hashlib.md5()
                with open(path, "rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        md5.update(chunk)
                cached = (st.st_mtime_ns, st.st_size, md5.hexdigest())
                self._md5[file] = cached
                LOG.info("Firmware %s, %d bytes", file, st.st_size)
        return {"name": match["name"], "version": match["version"], "hash": match["hash"], "time": match["time"],
                "file": file, "size": st.st_size, "md5": cached[2]}

    def latest(self):
        """Get newest firmware info for each name, dict name -> info"""
        if not self.dirname:
            return {}
        try:
            files = os.listdir(self.dirname)
        except FileNotFoundError:
            return {}
        best = {}
        for file in files:
            match = FILE_RE.match(file)
            if match is None:
                continue
            name, stamp = match["name"], match["time"]
            if (name not in best) or (stamp > FILE_RE.match(best[name])["time"]):
                best[name] = file
        result = {}
        for name, file in best.items():
            info = self._info(file)
            if info is not None:
                result[name] = info
        return result

    def info(self, name):
        """Get newest firmware info (name, version, hash, time, file, size, md5) for name, None if none"""
        return self.latest().get(name)

    def prune(self, keep):
        """Remove all but the newest keep files of each name, returns the removed file names"""
        if not self.dirname:
            return []
        try:
            files = os.listdir(self.dirname)
        except FileNotFoundError:
            return []
        names = {}
        for file in files:
            match = FILE_RE.match(file)
            if match is not None:
                names.setdefault(match["name"], []).append((match["time"], file))
        removed = []
        for items in names.values():
            for _, file in sorted(items, reverse=True)[keep:]:
                os.remove(os.path.join(self.dirname, file))
                with self.lock:
                    self._md5.pop(file, None)
                LOG.info("Removed old firmware %s", file)
                removed.append(file)
        return removed
