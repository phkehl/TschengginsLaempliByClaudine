#!/usr/bin/env python3
"""Keep the Chlotz serial port open: log all output, send lines from a FIFO.

Usage: fw/.venv/bin/python tools/test/serial-hold.py [--port <dev>] <fifo> <log>

Opens the serial port (115200 baud) without a reset (DTR and RTS off, RTS first) and keeps it open, so no output gets
lost between commands (e.g. during a long test on the Chlotz). It appends all output to <log> and sends each line
written to <fifo> as a shell command (CRLF). It makes the FIFO if it does not exist. Stop it with Ctrl-C or kill. Needs
pyserial (in the PlatformIO fw/.venv).

Example:

    fw/.venv/bin/python tools/test/serial-hold.py /tmp/chlotz.fifo /tmp/chlotz.log &
    echo status > /tmp/chlotz.fifo
    tail -f /tmp/chlotz.log
"""

import argparse
import os
import stat
import sys
import threading

try:
    import serial
except ImportError:
    sys.exit("serial missing, please run: sudo apt install python3-serial (or use the PlatformIO fw/.venv)")


def main():
    """Parse arguments, copy output to the log, FIFO lines to the port"""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--port", default="/dev/ttyUSB4", help="serial port (default: %(default)s)")
    parser.add_argument("fifo", help="FIFO for commands")
    parser.add_argument("log", help="log file (appended)")
    args = parser.parse_args()

    if not os.path.exists(args.fifo):
        os.mkfifo(args.fifo)
    elif not stat.S_ISFIFO(os.stat(args.fifo).st_mode):
        sys.exit("serial-hold: {} is not a FIFO".format(args.fifo))
    port = serial.Serial()
    port.port = args.port
    port.baudrate = 115200
    port.timeout = 0.2
    try:
        port.open()
    except serial.SerialException as ex:
        sys.exit("serial-hold: {}".format(ex))
    # Open: DTR and RTS on (no reset). RTS off first: RTS alone resets the ESP32.
    port.rts = False
    port.dtr = False
    log = open(args.log, "a", buffering=1, encoding="utf-8")  # pylint: disable=consider-using-with

    def reader():
        while True:
            data = port.read(4096)
            if data:
                log.write(data.decode(errors="replace"))

    threading.Thread(target=reader, daemon=True).start()
    try:
        while True:
            with open(args.fifo, encoding="utf-8") as fh:
                for line in fh:
                    port.write((line.rstrip("\n") + "\r\n").encode())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
