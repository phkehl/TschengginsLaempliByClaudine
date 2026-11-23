"""Device stream: the events that a device gets on its WebSocket connection (/ws/device, wsserver.py)

Events (data is JSON):

    hello     {"server": "laempli", "time": ..., "utc_offset": ..., "heartbeat": ..., "firmware": {...} or null}
    state     {"version": <n>, "state": {"leds": [...]}}
    time      {"time": ..., "utc_offset": ...}, each heartbeat interval without other event
    settings  {"seq": <n>, "settings": {key: value}}, pending changes from the web page, at connect and on change
    update    {"firmware": {...}}, button on the web page: install this firmware (the device gets the file on its
              stream, see wsserver.py; an old firmware downloads /api/firmware/<file>)
    ota       {"error": <text>}, bad firmware part request, see wsserver.py
    sound     {"sound": <name>}, button on the web page: play this sound (melody name or "fx")

Also the request helpers that both need (device info from the query parameters, user names from the headers of the
reverse proxy).
"""

import base64
import datetime
import logging

from .devices import valid_id
from .firmware import firmware_name
from .leds import MAX_LEDS

LOG = logging.getLogger(__name__)


def auth_user(request):
    """Get basic auth user name of request (an object with headers), for logging"""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return None
    try:
        return base64.b64decode(header[6:]).decode("utf-8").split(":", 1)[0]
    except ValueError:
        return None


def forwarded_user(request):
    """Get user name from the reverse proxy (X-Forwarded-User header), None if none"""
    user = request.headers.get("X-Forwarded-User", "").strip()
    # Apache %{REMOTE_USER}s without login
    if (not user) or (user == "(null)"):
        return None
    return user


def time_info():
    """Get current time and UTC offset of local time"""
    now = datetime.datetime.now().astimezone()
    return {"time": round(now.timestamp(), 3), "utc_offset": int(now.utcoffset().total_seconds())}


def peer_ip(headers, peer):
    """Get remote IP: first X-Forwarded-For address (reverse proxy), else peer address"""
    forwarded = headers.get("X-Forwarded-For", "")
    return (forwarded.split(",")[0].strip() if forwarded else peer)


def device_info(args, request, peer):
    """Get device info from the query parameters (args: mapping), None if no valid device ID

    request: object with headers (user names, X-Forwarded-For), peer: peer address.
    """
    dev_id = args.get("device", "").strip().lower()
    if not valid_id(dev_id):
        return None
    try:
        rssi = int(args.get("rssi", ""))
    except ValueError:
        rssi = None
    try:
        leds = int(args.get("leds", ""))
        leds = (leds if (0 < leds <= MAX_LEDS) else None)
    except ValueError:
        leds = None
    return {"id": dev_id, "firmware": args.get("fw", "")[:64], "ssid": args.get("ssid", "")[:32],
            "local_ip": args.get("ip", "")[:45], "rssi": rssi, "leds": leds,
            "remote_ip": peer_ip(request.headers, peer),
            "user": (forwarded_user(request) or auth_user(request))}


def device_events(devices, firmware, info, heartbeat):
    """Events for a device stream: endless generator of (event, data)

    The device is connected (devices.connect()) while the generator runs, close() disconnects it.
    """
    dev_id = info["id"]
    clients = devices.connect(info)
    LOG.info("Stream open: %s user=%s device=%s (%d clients)", info["remote_ip"], info["user"], dev_id, clients)
    try:
        yield "hello", dict(server="laempli", heartbeat=heartbeat, firmware=firmware.info(firmware_name(dev_id)),
                            **time_info())
        state, version = devices.get_state(dev_id)
        yield "state", {"version": version, "state": state}
        sent_seq = None
        while True:
            # Update and sound test from the web page
            for event, data in devices.pop_commands(dev_id):
                LOG.info("Command to %s: %s", dev_id, event)
                yield event, data
            # Settings from the web page: at connect and on change
            seq, values = devices.get_pending(dev_id)
            if values and (seq != sent_seq):
                LOG.info("Settings to %s: %s", dev_id, ", ".join(sorted(values)))
                yield "settings", {"seq": seq, "settings": values}
            sent_seq = seq
            new_version, pending = devices.wait_change(dev_id, version, heartbeat, sent_seq)
            if new_version != version:
                state, version = devices.get_state(dev_id)
                yield "state", {"version": version, "state": state}
            elif not pending:
                yield "time", time_info()
    finally:
        clients = devices.disconnect(dev_id)
        LOG.info("Stream closed: %s device=%s (%d clients)", info["remote_ip"], dev_id, clients)
