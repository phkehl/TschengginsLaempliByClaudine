# Test helpers

Helpers for manual and browser tests. The unit tests are in `tests/` (`python3 -m unittest discover -s tests`).

| File                    | Use                                                                                        |
|-------------------------|--------------------------------------------------------------------------------------------|
| `serial-cmd.py`         | Send shell commands to the Chlotz and print the output (`--reset`: start log first)        |
| `serial-hold.py`        | Keep the serial port open: log all output to a file, send lines from a FIFO               |
| `local-backend.sh`      | Start a local test backend (127.0.0.1:8099, editor `editor`, own data directory)           |
| `device-page-test.mjs`  | Device page test in headless Chrome (LED selection, LED and settings forms, modal, pending) |
| `dashboard-test.mjs`    | Dashboard test in headless Chrome (fit, live changes and animations, filter, kiosk)        |
| `ws2801-order/`         | ESP8266 program: WS2801 channel order, see [Chlötzli](../../laempli/chloetzli/chloetzli.md#led-test) |

The serial helpers need pyserial: use the PlatformIO `fw/.venv` (`fw/.venv/bin/python tools/test/serial-cmd.py status`).
They open the port without a reset of the Chlotz (RTS alone resets the ESP32, so they switch RTS off before DTR).

Browser test (Node 20 or newer, Chrome):

```sh
tools/test/local-backend.sh /tmp/laempli-test &
google-chrome --headless=new --remote-debugging-port=9333 --user-data-dir=/tmp/laempli-chrome about:blank &
node --experimental-websocket tools/test/device-page-test.mjs              # optional: base URL, screenshot dir
```

The browser test makes a fake device `chlotz-0000aa` on the test backend. It refuses other hosts than 127.0.0.1 and
localhost.
