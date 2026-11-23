#!/usr/bin/env python3
"""Send shell commands to the Chlotz and print the output.

Usage: fw/.venv/bin/python tools/test/serial-cmd.py [--port <dev>] [--reset] [--wait <s>] [<command> ...]

Opens the serial port (115200 baud) without a reset (DTR and RTS off, RTS first), sends each command (CRLF), and prints
the output for --wait seconds after each one. --reset first resets the Chlotz (RTS pulse) and prints the start log for
--boot seconds. Needs pyserial (in the PlatformIO fw/.venv). Do not use it while a serial monitor has the port.

Examples:

    fw/.venv/bin/python tools/test/serial-cmd.py status
    fw/.venv/bin/python tools/test/serial-cmd.py --reset --boot 15 config "name Office"
"""

import argparse
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("serial missing, please run: sudo apt install python3-serial (or use the PlatformIO fw/.venv)")


def read_for(port, seconds):
    """Read and print output for some seconds"""
    end = time.time() + seconds
    while time.time() < end:
        data = port.read(4096)
        if data:
            sys.stdout.write(data.decode(errors="replace").replace("\r\n", "\n"))
            sys.stdout.flush()


def main():
    """Parse arguments, run the commands"""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--port", default="/dev/ttyUSB4", help="serial port (default: %(default)s)")
    parser.add_argument("--reset", action="store_true", help="reset the Chlotz first")
    parser.add_argument("--boot", type=float, default=10.0, help="start log time after reset [s]")
    parser.add_argument("--wait", type=float, default=1.5, help="output time after each command [s]")
    parser.add_argument("commands", nargs="*", help="shell commands, e.g. status")
    args = parser.parse_args()

    port = serial.Serial()
    port.port = args.port
    port.baudrate = 115200
    port.timeout = 0.2
    try:
        port.open()
    except serial.SerialException as ex:
        sys.exit("serial-cmd: {}".format(ex))
    # Open: DTR and RTS on (no reset). RTS off first: RTS alone resets the ESP32.
    port.rts = False
    port.dtr = False
    if args.reset:
        port.rts = True
        time.sleep(0.1)
        port.rts = False
        read_for(port, args.boot)
    else:
        port.reset_input_buffer()
    for command in args.commands:
        port.write((command + "\r\n").encode())
        read_for(port, args.wait)
    print()


if __name__ == "__main__":
    main()
