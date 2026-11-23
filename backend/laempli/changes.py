"""Change notifications for the web pages (live updates)

Topics: "devices" (device list), "device:<ID>" (one device: info, LED state, config, settings), "jobs", "firmwares",
"dashboard" (dashboard config) and the source names ("gitlab", "github", "generic": event logs). Each notify() increments a revision. A waiter gives
the last revision it has and gets the topics that changed after it.
"""

import threading


class Changes:
    """Thread-safe change revisions per topic"""

    def __init__(self):
        self.cond = threading.Condition()
        self.rev = 0
        self.topics = {}    # topic -> revision of the last change

    def notify(self, *topics):
        """Mark topics as changed, wake the waiters"""
        if not topics:
            return
        with self.cond:
            self.rev += 1
            for topic in topics:
                self.topics[topic] = self.rev
            self.cond.notify_all()

    def current(self):
        """Get the current revision"""
        with self.cond:
            return self.rev

    def wait(self, rev, timeout):
        """Wait for changes after revision rev, or timeout

        Returns the current revision and the sorted list of topics changed after rev (empty on timeout).
        """
        with self.cond:
            self.cond.wait_for(lambda: (self.rev != rev), timeout=timeout)
            return self.rev, sorted(topic for topic, trev in self.topics.items() if trev > rev)
