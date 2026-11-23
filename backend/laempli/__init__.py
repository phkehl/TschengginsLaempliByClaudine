"""Tschenggins Lämpli backend implementation.

Modules:

- config: load and check the YAML configuration
- logs: logging to the systemd journal or to a terminal
- devices: devices and their state, one JSON file per device
- firmware: firmware files for OTA updates
- web: Flask app (web interface, API)
- wsserver: WebSocket server (device connections, live updates of the web pages)
- stream: events of a device connection
- changes: change notifications for the web pages

The main script is backend/tschenggins-laempli.py.
"""
