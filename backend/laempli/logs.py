"""Logging to the systemd journal or to a terminal

As systemd service (stderr goes to the journal), each line gets a "<n>" syslog priority prefix and no time stamp
(the journal adds it). Else, coloredlogs (Debian package python3-coloredlogs) prints coloured lines with time stamp
(without it: plain lines and a warning).
"""

import logging
import os
import sys

# Python log level to syslog priority
PRIORITIES = ((logging.CRITICAL, 2), (logging.ERROR, 3), (logging.WARNING, 4), (logging.INFO, 6))


def is_journal(stream):
    """Check if stream goes to the systemd journal

    systemd sets JOURNAL_STREAM=<device>:<inode> for the journal stream. See systemd.exec(5).
    """
    try:
        dev, ino = (int(part) for part in os.environ.get("JOURNAL_STREAM", "").split(":"))
        st = os.fstat(stream.fileno())
    except (ValueError, OSError, AttributeError):
        return False
    return (st.st_dev == dev) and (st.st_ino == ino)


class JournalFormatter(logging.Formatter):
    """Add syslog priority prefix to each line"""

    def format(self, record):
        prio = 7
        for level, value in PRIORITIES:
            if record.levelno >= level:
                prio = value
                break
        text = super().format(record)
        return "\n".join("<{}>{}".format(prio, line) for line in text.split("\n"))


def setup_logging(level="info", journal=None):
    """Set up logging for the root logger

    level: debug, info, warning or error. journal: True, False or None (detect).
    """
    num = getattr(logging, level.upper())
    if journal is None:
        journal = is_journal(sys.stderr)
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    if journal:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JournalFormatter("%(name)s: %(message)s"))
        root.addHandler(handler)
        root.setLevel(num)
    else:
        fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
        try:
            import coloredlogs  # pylint: disable=import-outside-toplevel
        except ImportError:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter(fmt))
            root.addHandler(handler)
            root.setLevel(num)
            logging.getLogger(__name__).warning(
                "coloredlogs missing, please run: sudo apt install python3-coloredlogs")
            return
        coloredlogs.install(level=num, fmt=fmt, stream=sys.stderr)
