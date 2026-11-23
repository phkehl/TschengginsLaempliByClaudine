"""Housekeeping: remove old data, in a thread

Each housekeeping_interval seconds (first run one interval after the start, see run_once()):

1. For each source (GitLab, GitHub): raw events older than events_keep_days (expire_events(), see source.py)
2. For each source: jobs without events in events_keep_days (expire_jobs(): GitLab pipeline slots, GitHub workflow
   slots), then the jobs and the device states are made again (the LEDs of these jobs show status UNKNOWN)
3. Firmware files: only the newest firmware_keep files of each name stay (Firmware.prune())
4. Devices not connected for devices_keep_days, with their file (Devices.expire(), 0 = never)

An error in one step is logged; the other steps and the next runs continue.
"""

import logging
import threading
import time

from .leds import update_states

LOG = logging.getLogger(__name__)

# Log steps slower than this [s] (IDEA4)
SLOW_STEP = 0.1


class Housekeeping:
    """Housekeeping thread"""

    def __init__(self, cfg, sources, jobs, devices, firmware, changes=None):
        self.interval = cfg["housekeeping_interval"]
        self.firmware_keep = cfg["firmware_keep"]
        self.devices_keep_days = cfg["devices_keep_days"]
        self.sources = list(sources)
        self.jobs = jobs
        self.devices = devices
        self.firmware = firmware
        self.changes = changes  # Changes: event logs, firmwares (None: no notifications)
        self.stop_event = threading.Event()
        self.thread = None

    def _expire_jobs(self, source, now):
        # Expire jobs of a source, then update jobs and devices
        removed = source.expire_jobs(now)
        if (removed > 0) and self.jobs.update(source.SOURCE, source.jobs()):
            changed = update_states(self.devices, self.jobs)
            if changed > 0:
                LOG.info("New LED state for %d devices", changed)
        return removed

    def run_once(self, now=None):
        """Run all steps once, returns {step: number of removed items} (None: step failed)

        Steps: "<source> events", "<source> jobs" (for each source), "firmware", "devices".
        """
        steps = []
        for source in self.sources:
            steps.append(("{} events".format(source.SOURCE), lambda source=source: source.expire_events(now)))
            steps.append(("{} jobs".format(source.SOURCE), lambda source=source: self._expire_jobs(source, now)))
        steps.append(("firmware", lambda: len(self.firmware.prune(self.firmware_keep))))
        steps.append(("devices", lambda: len(self.devices.expire(self.devices_keep_days, now))))
        result = {}
        for name, step in steps:
            start = time.monotonic()
            try:
                result[name] = step()
            except Exception:  # pylint: disable=broad-except
                LOG.exception("Housekeeping %s failed", name)
                result[name] = None
            duration = time.monotonic() - start
            if duration > SLOW_STEP:
                LOG.warning("Slow housekeeping %s: %.3f s", name, duration)
        if any(result.values()):
            LOG.info("Housekeeping removed: %s", ", ".join(
                "{} {}".format(num, name) for name, num in result.items() if num))
        if self.changes is not None:
            # Step names start with the topic
            self.changes.notify(*sorted({name.split()[0].replace("firmware", "firmwares")
                                         for name, num in result.items() if num}))
        return result

    def _run(self):
        while not self.stop_event.wait(self.interval):
            self.run_once()

    def start(self):
        """Start the thread"""
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="housekeeping", daemon=True)
        self.thread.start()
        LOG.info("Housekeeping every %d s", self.interval)

    def stop(self):
        """Stop the thread, wait for it"""
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join()
            self.thread = None
