"""WebSocket server (python3-websockets, sync API: one thread per connection), next to the Flask app on waitress

Endpoints (the path may have the X-Forwarded-Prefix in front, e.g. /laempli/ws/device):

    /ws/device?device=<ID>&fw=...  Device connection, query parameters: see stream.device_info(). Backend ->
                                   device: the stream events (stream.device_events(): hello, state, time, settings).
                                   Device -> backend: report events (devices.report()), ota events (firmware
                                   update, see below).
    /ws/web                        Web pages (live updates). Backend -> page: "changed" events with the changed topics
                                   (changes.py), at most one each MIN_INTERVAL seconds, and a "time" event each
                                   heartbeat interval without changes.

Only Lämplis: the type of the device ID must be one of firmware.DEVICE_TYPES, and "app" must be "laempli" (missing:
firmware before 2026-11-12). Else HTTP 403 before the handshake and a warning in the log, at most once per hour for
each device and reason: the energy monitor (EnergyMonByClaudine) talks a similar protocol, a wrong URL in its settings
must not make it a device here. The devices check the hello too ("server": "laempli").

Messages are JSON text: {"event": <name>, "data": <data>}.

Firmware update (OTA) on the device connection: after the "update" event, the device gets the file in parts. It sends
"ota" events {"state": "running", "version", "done", "size", "file", "offset", "length"}: "done" is the number of bytes
it wrote to the flash, "offset" and "length" ask for the next part. The backend answers with a binary message: offset
(4 bytes, big endian), then the data. The device asks for more parts before it has the data (up to a window, see the
firmware config), and it checks the MD5 at the end. Last event: {"state": "done"} or {"state": "failed", "error"}.
The state goes to the device info (devices.set_ota()), the web page shows the progress. A bad request gets an "ota"
event {"error": <text>}, the device then stops the update.

The reverse proxy (e.g. Apache) does authentication and TLS, as for the HTTP endpoints.
"""

import json
import logging
import struct
import threading
import time
import urllib.parse

try:
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.server import serve
except ImportError:
    import sys
    sys.exit("websockets missing, please run: sudo apt install python3-websockets")

from .devices import valid_id
from .firmware import DEVICE_TYPES, device_type
from .stream import device_events, device_info, peer_ip, time_info

LOG = logging.getLogger(__name__)

# Largest message from a client [bytes]
MAX_MESSAGE = 64 * 1024

# Largest firmware part [bytes]
MAX_CHUNK = 32 * 1024

# Shortest time between two "changed" events [s]
MIN_INTERVAL = 1.0

# The protocol name a device sends in "app"
APP = "laempli"

# Rejected devices: log each one at most this often [s]
REJECT_LOG_S = 3600.0

# Paths (end of the request path)
DEVICE_PATH = "/ws/device"
WEB_PATH = "/ws/web"


def message(event, data):
    """Format one message"""
    return json.dumps({"event": event, "data": data}, separators=(",", ":"))


def split_path(request_path):
    """Get endpoint ("device", "web" or None) and query parameters (dict) of a request path"""
    path, _, query = request_path.partition("?")
    path = path.rstrip("/")
    args = {key: values[0] for key, values in urllib.parse.parse_qs(query).items()}
    if path.endswith(DEVICE_PATH):
        return "device", args
    if path.endswith(WEB_PATH):
        return "web", args
    return None, args


def device_error(args):
    """Check the query of a device connection: None if OK, else (HTTP status, reason)"""
    dev_id = args.get("device", "").strip().lower()
    if not valid_id(dev_id):
        return (400, "bad device ID")
    app = args.get("app")
    if (app is not None) and (app != APP):
        return (403, "not a Lämpli (app {})".format(app[:20]))
    if device_type(dev_id) not in DEVICE_TYPES:
        return (403, "not a Lämpli (type {})".format(device_type(dev_id)[:20]))
    return None


class WsServer:
    """WebSocket server thread"""

    def __init__(self, devices, firmware, heartbeat, changes):
        self.devices = devices
        self.firmware = firmware
        self.heartbeat = heartbeat
        self.changes = changes
        self.server = None
        self.thread = None
        self.lock = threading.Lock()
        self.open = {}      # Open connections: connection -> handler thread
        self.rejected = {}  # (device, reason) -> time of the last log line

    def _peer(self, conn):
        # Peer address of a connection
        return peer_ip(conn.request.headers, conn.remote_address[0])

    def _check(self, conn, request):
        # Before the handshake: known path, valid device ID, only Lämplis
        endpoint, args = split_path(request.path)
        if endpoint is None:
            LOG.info("WebSocket: no such path %s from %s", request.path,
                     peer_ip(request.headers, conn.remote_address[0]))
            return conn.respond(404, "Not found\n")
        error = (device_error(args) if (endpoint == "device") else None)
        if error is not None:
            status, reason = error
            key = (args.get("device", "")[:40], reason)
            now = time.time()
            with self.lock:
                last = self.rejected.get(key, 0.0)
                if (now - last) >= REJECT_LOG_S:
                    self.rejected[key] = now
                if len(self.rejected) > 1000:
                    self.rejected = {k: t for k, t in self.rejected.items() if (now - t) < REJECT_LOG_S}
            if (now - last) >= REJECT_LOG_S:
                LOG.warning("WebSocket: rejected %.40s from %s: %s", args.get("device", ""),
                            peer_ip(request.headers, conn.remote_address[0]), reason)
            return conn.respond(status, reason[0].upper() + reason[1:] + "\n")
        return None

    def _handle(self, conn):
        endpoint, args = split_path(conn.request.path)
        with self.lock:
            self.open[conn] = threading.current_thread()
        try:
            if endpoint == "device":
                self._device(conn, args)
            else:
                self._web(conn)
        except ConnectionClosed:
            pass
        finally:
            with self.lock:
                del self.open[conn]

    def _device(self, conn, args):
        info = device_info(args, conn.request, conn.remote_address[0])
        dev_id = info["id"]

        def reader():
            # Reports from the device
            try:
                for text in conn:
                    try:
                        msg = json.loads(text)
                    except ValueError:
                        msg = None
                    if (not isinstance(msg, dict)) or (msg.get("event") not in ("report", "ota")) or \
                            (not isinstance(msg.get("data"), dict)):
                        LOG.warning("WebSocket %s: bad message from %s: %.100r", dev_id, info["remote_ip"], text)
                        continue
                    if msg["event"] == "report":
                        self.devices.report(dev_id, msg["data"])
                    else:
                        self._ota(conn, dev_id, msg["data"])
            except ConnectionClosed:
                pass

        threading.Thread(target=reader, name="ws-" + dev_id, daemon=True).start()
        events = device_events(self.devices, self.firmware, info, self.heartbeat)
        try:
            for event, data in events:
                conn.send(message(event, data))
        finally:
            events.close()

    def _ota(self, conn, dev_id, data):
        # OTA event: state, send the asked part
        self.devices.set_ota(dev_id, data)
        if "offset" not in data:
            return
        offset, length, file = data.get("offset"), data.get("length"), data.get("file")
        chunk = None
        if isinstance(offset, int) and isinstance(length, int) and isinstance(file, str) and \
                (0 < length <= MAX_CHUNK) and (offset < (1 << 32)):
            chunk = self.firmware.read(file, offset, length)
        if chunk is None:
            LOG.warning("WebSocket %s: bad firmware part %r %r %r", dev_id, file, offset, length)
            conn.send(message("ota", {"error": "bad firmware part"}))
            return
        conn.send(struct.pack(">I", offset) + chunk)

    def _web(self, conn):
        LOG.debug("Web page connected: %s", self._peer(conn))
        rev = self.changes.current()
        while True:
            rev, topics = self.changes.wait(rev, self.heartbeat)
            if topics:
                conn.send(message("changed", topics))
                # Collect the next changes
                time.sleep(MIN_INTERVAL)
            else:
                conn.send(message("time", time_info()))

    def start(self, host, port):
        """Start the server thread, returns the port (port 0: a free port)"""
        self.server = serve(self._handle, host, port, compression=None, max_size=MAX_MESSAGE,
                            process_request=self._check, logger=LOG)
        self.thread = threading.Thread(target=self.server.serve_forever, name="websocket", daemon=True)
        self.thread.start()
        port = self.server.socket.getsockname()[1]
        LOG.info("WebSocket on %s:%d", host, port)
        return port

    def stop(self):
        """Stop the server thread, close the connections and wait for their threads (up to a heartbeat)"""
        if self.server is not None:
            self.server.shutdown()
            self.thread.join()
            self.server = None
            self.thread = None
        with self.lock:
            handlers = list(self.open.items())
        for conn, thread in handlers:
            conn.close()
            thread.join(self.heartbeat + 1)
