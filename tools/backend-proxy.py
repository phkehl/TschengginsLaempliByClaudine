#!/usr/bin/env python3
"""HTTPS proxy to the Tschenggins Lämpli backend, for firmware tests in a network without access to the backend

The proxy runs on a host that the Lämpli can reach and that can reach the backend. No other web server is needed.

    Lämpli --- HTTPS (self-signed certificate) ---> this proxy --- HTTPS (certificate check) ---> backend

- The Lämpli uses TLS as with the real backend: the TLS buffers and the TLS records are as in production, the
  certificate check is not (see "Differences to production" below).
- The proxy has no authentication. It passes the Authorization header of the client to the backend, and the 401 of the
  backend to the client.
- HTTP requests and WebSocket connections (/ws/...) use the same port. The backend URL must serve both, as Apache does.
- The path stays as it is: only paths below the path of the backend URL go through, the others get 404. Thus the Lämpli
  uses the same path as with the real backend. The web pages also work through the proxy (browser warning).
- The proxy makes one backend connection per client connection ("Connection: close" for HTTP requests).

Use:

    tools/backend-proxy.py --backend https://<host>/<path>              # Port 8443
    tools/backend-proxy.py --backend https://<host>/<path> --port 9443  # Other port

On the Lämpli (serial shell), in this order, as "backend url" clears the insecure flag:

    backend url https://<host of the proxy>:8443/<path>
    backend insecure on

The user and the password on the Lämpli stay as they are. Back to the real backend: "backend url <URL>", which also
turns the certificate check on again.

Certificates: the first start makes a certificate chain with the openssl command, in --cert-dir (default
data/backend-proxy/, not in git). Later starts use these files. The chain has the same form and about the same size as
the production chain (see CERT_CHAIN): ECDSA P-256 certificate, 2 P-384 CA certificates, a P-384 CA certificate signed
by an RSA-4096 root, without the root. The ESP32 keeps the received chain in the heap. To use another certificate, put
it there as cert.pem (with its chain) and key.pem.

Differences to production:

- No certificate check on the Lämpli ("backend insecure on"): the TLS handshake needs less time and less heap than in
  production (lowest free heap is higher), the open connection needs about the same heap.
- One more network hop: the delay is longer.

TLS records: the proxy sends each received backend record as one record, so the size limits of the ESP8266
(BACKEND_TLS_RX) apply as in production. A backend with http:// has no records: the proxy sends what one read gets.

Only needs the Python standard library and the openssl command.
"""

import argparse
import logging
import os
import select
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.parse

# Default certificate directory
CERT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "backend-proxy")

# Validity of the certificates [days]
CERT_DAYS = 3650

# Chain as production: name, key, padding [bytes]
CERT_CHAIN = (
    ("Laempli proxy root 1", ("rsa:4096",), 0),
    ("Laempli proxy root 2", ("ec", "-pkeyopt", "ec_paramgen_curve:secp384r1"), 241),
    ("Laempli proxy root 3", ("ec", "-pkeyopt", "ec_paramgen_curve:secp384r1"), 200),
    ("Laempli proxy intermediate", ("ec", "-pkeyopt", "ec_paramgen_curve:secp384r1"), 166),
    ("laempli-backend-proxy", ("ec", "-pkeyopt", "ec_paramgen_curve:prime256v1"), 429),
)

# Largest request or response head [bytes]
HEAD_MAX = 64 * 1024

# Read size: the largest TLS record [bytes]
READ_SIZE = 16384

# Timeout for connects and request heads [s]
CONNECT_TIMEOUT = 20

# Close connections without data [s], backend heartbeat is 15 s
IDLE_TIMEOUT = 120

# Headers for one connection only (lower case)
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection", "upgrade"}

LOG = logging.getLogger("backend-proxy")


def split_head(head):
    """Split a request or response head (bytes, without the empty line) into first line and list of (name, value)

    Raises ValueError for a bad header line.
    """
    lines = head.decode("latin-1").split("\r\n")
    headers = []
    for line in lines[1:]:
        name, colon, value = line.partition(":")
        if (not colon) or (not name) or (name != name.strip()):
            raise ValueError("bad header line")
        headers.append((name, value.strip()))
    return lines[0], headers


def join_head(first, headers):
    """Make a head (bytes, with the empty line) from the first line and the list of (name, value)"""
    lines = [first] + [f"{name}: {value}" for name, value in headers]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def get_header(headers, name):
    """Get the value of a header, empty if not there"""
    for key, value in headers:
        if key.lower() == name:
            return value
    return ""


def path_allowed(target, prefix):
    """Check if a request target is below the prefix (empty prefix: all paths), without dot segments"""
    path = target.partition("?")[0]
    if not path.startswith("/"):
        return False
    words = urllib.parse.unquote(path).replace("\\", "/").split("/")
    if ("." in words) or (".." in words):
        return False
    return (prefix == "") or (path == prefix) or path.startswith(prefix + "/")


def is_upgrade(headers):
    """Check if a request asks for a connection upgrade (WebSocket)"""
    words = [word.strip().lower() for word in get_header(headers, "connection").split(",")]
    return ("upgrade" in words) and (get_header(headers, "upgrade") != "")


class Backend:
    """The backend to connect to"""

    def __init__(self, url):
        parts = urllib.parse.urlsplit(url)
        if (parts.scheme not in ("http", "https")) or (not parts.hostname) or parts.username or parts.query:
            raise ValueError(f"bad backend URL (http(s)://host[:port][/path]): {url}")
        self.https = (parts.scheme == "https")
        self.host = parts.hostname
        self.port = (parts.port or (443 if self.https else 80))
        self.netloc = parts.netloc
        self.origin = f"{parts.scheme}://{parts.netloc}"
        self.prefix = parts.path.rstrip("/")
        self.context = (ssl.create_default_context() if self.https else None)

    def connect(self):
        """Open a connection, certificate check for https. Raises OSError."""
        sock = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT)
        if not self.https:
            return sock
        try:
            return self.context.wrap_socket(sock, server_hostname=self.host)
        except OSError:
            sock.close()
            raise


class ResponseHead:
    """Change the response heads in the data from the backend, other data stays as it is"""

    def __init__(self, backend, client_host, label):
        self.backend = backend
        self.client_host = client_host
        self.label = label
        self.data = b""
        self.done = False

    def _rewrite(self, head):
        # Change one head, returns (head, status)
        first, headers = split_head(head)
        status = int(first.split(" ")[1])
        if (status >= 100) and (status < 200):
            return head + b"\r\n\r\n", status
        out = []
        for name, value in headers:
            if name.lower() in ("connection", "keep-alive"):
                continue
            if (name.lower() == "location") and self.client_host and value.startswith(self.backend.origin):
                value = f"https://{self.client_host}{value[len(self.backend.origin):]}"
            out.append((name, value))
        out.append(("Connection", "close"))
        return join_head(first, out), status

    def feed(self, data):
        """Process data from the backend, returns the data for the client"""
        if self.done:
            return data
        self.data += data
        out = b""
        while True:
            head, found, rest = self.data.partition(b"\r\n\r\n")
            if not found:
                if len(self.data) > HEAD_MAX:
                    raise ValueError("response head too large")
                return out
            self.data = rest
            changed, status = self._rewrite(head)
            out += changed
            if (status >= 200) or (status == 101):
                LOG.info("%s -> %d", self.label, status)
                self.done = True
                out += self.data
                self.data = b""
                return out


class Pipe:
    """One direction of a connection: non-blocking, one send per read (keeps the TLS records)"""

    def __init__(self, src, dst, convert=None, data=b""):
        self.src = src
        self.dst = dst
        self.convert = convert
        self.data = data
        self.eof = False

    def step(self, want_read, want_write):
        """Read and send what is possible, add the sockets to wait for. Returns True if something moved."""
        moved = False
        if (not self.data) and (not self.eof):
            try:
                data = self.src.recv(READ_SIZE)
            except (ssl.SSLWantReadError, BlockingIOError):
                want_read.add(self.src)
            except ssl.SSLWantWriteError:
                want_write.add(self.src)
            else:
                moved = True
                if not data:
                    self.eof = True
                else:
                    self.data = (self.convert(data) if self.convert else data)
        if self.data:
            try:
                sent = self.dst.send(self.data)
            except (ssl.SSLWantWriteError, BlockingIOError):
                want_write.add(self.dst)
            except ssl.SSLWantReadError:
                want_read.add(self.dst)
            else:
                moved = True
                self.data = self.data[sent:]
        return moved


def relay(pipes):
    """Move data until one side closes or no data comes for IDLE_TIMEOUT"""
    while True:
        want_read = set()
        want_write = set()
        moved = [pipe.step(want_read, want_write) for pipe in pipes]
        if any(pipe.eof and (not pipe.data) for pipe in pipes):
            return
        if any(moved):
            continue
        readable, writable, _ = select.select(want_read, want_write, [], IDLE_TIMEOUT)
        if (not readable) and (not writable):
            LOG.info("No data for %d s, closing", IDLE_TIMEOUT)
            return


def close_socket(sock):
    """Close a socket, for TLS with a close notify (best effort)"""
    if isinstance(sock, ssl.SSLSocket):
        try:
            sock.setblocking(False)
            sock.unwrap()
        except (OSError, ValueError):
            pass
    sock.close()


def respond(sock, status, text):
    """Send a short error response"""
    body = (text + "\n").encode()
    sock.sendall(join_head(f"HTTP/1.1 {status} {text}", [("Content-Type", "text/plain"),
                                                         ("Content-Length", str(len(body))),
                                                         ("Connection", "close")]) + body)


def read_head(sock):
    """Read a request head (blocking), returns head (without the empty line) and the data after it, None if no head"""
    data = b""
    while True:
        head, found, rest = data.partition(b"\r\n\r\n")
        if found:
            return head, rest
        chunk = sock.recv(READ_SIZE)
        if (not chunk) or (len(data) > HEAD_MAX):
            return None
        data += chunk


def request_head(first, headers, backend, peer):
    """Make the request head for the backend"""
    out = [("Host", backend.netloc)]
    for name, value in headers:
        lower = name.lower()
        if (lower == "host") or (lower in HOP_HEADERS) or lower.startswith("x-forwarded-"):
            continue
        out.append((name, value))
    out.append(("X-Forwarded-For", peer))
    if is_upgrade(headers):
        out.append(("Connection", "Upgrade"))
        out.append(("Upgrade", get_header(headers, "upgrade")))
    else:
        out.append(("Connection", "close"))
    return join_head(first, out)


def handle_client(client, backend, peer):
    """Handle one client connection (blocking socket with timeout): one request, or one WebSocket connection"""
    upstream = None
    try:
        client.do_handshake()
        result = read_head(client)
        if result is None:
            return
        head, rest = result
        try:
            first, headers = split_head(head)
            method, target, _ = first.split(" ")
        except ValueError:
            respond(client, 400, "Bad request")
            return
        label = f"{peer} {method} {target.partition('?')[0]}"
        if not path_allowed(target, backend.prefix):
            LOG.info("%s -> 404, not below %s", label, (backend.prefix or "/"))
            respond(client, 404, "Not found")
            return
        try:
            upstream = backend.connect()
            upstream.sendall(request_head(first, headers, backend, peer))
        except OSError as err:
            LOG.warning("%s -> 502: %s", label, err)
            respond(client, 502, "Bad gateway")
            return
        response = ResponseHead(backend, get_header(headers, "host"), label)
        client.setblocking(False)
        upstream.setblocking(False)
        relay([Pipe(client, upstream, None, rest), Pipe(upstream, client, response.feed)])
    except ssl.SSLError as err:
        LOG.warning("%s: TLS: %s", peer, err)
    except (OSError, ValueError) as err:
        LOG.info("%s: closed: %s", peer, err)
    finally:
        close_socket(client)
        if upstream is not None:
            close_socket(upstream)


class Proxy:
    """Listening socket, one thread per client connection"""

    def __init__(self, host, port, context, backend):
        self.context = context
        self.backend = backend
        self.stopped = threading.Event()
        self.listener = socket.create_server((host, port), backlog=32)
        self.listener.settimeout(0.5)
        self.port = self.listener.getsockname()[1]

    def run(self):
        """Accept connections until stop()"""
        while not self.stopped.is_set():
            try:
                sock, addr = self.listener.accept()
            except TimeoutError:
                continue
            sock.settimeout(CONNECT_TIMEOUT)
            client = self.context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
            threading.Thread(target=handle_client, args=(client, self.backend, addr[0]), daemon=True).start()
        self.listener.close()

    def stop(self):
        """End run()"""
        self.stopped.set()


def make_cert(cert_dir):
    """Make key and certificate chain as CERT_CHAIN if not there, returns the paths of both"""
    cert = os.path.join(cert_dir, "cert.pem")
    key = os.path.join(cert_dir, "key.pem")
    if os.path.exists(cert) and os.path.exists(key):
        return cert, key
    os.makedirs(cert_dir, mode=0o700, exist_ok=True)
    LOG.info("Making certificates in %s", cert_dir)
    host = socket.gethostname()
    pems = []
    with tempfile.TemporaryDirectory(dir=cert_dir) as tmp:
        parent = None
        for level, (name, key_args, padding) in enumerate(CERT_CHAIN):
            leaf = (level == (len(CERT_CHAIN) - 1))
            level_cert = os.path.join(tmp, f"{level}.crt")
            level_key = os.path.join(tmp, f"{level}.key")
            command = ["openssl", "req", "-x509", "-newkey", *key_args, "-nodes", "-keyout", level_key,
                       "-out", level_cert, "-days", str(CERT_DAYS), "-subj", f"/CN={name}", "-addext",
                       f"basicConstraints=critical,CA:{'FALSE' if leaf else 'TRUE'}"]
            if padding > 0:
                command += ["-addext", "nsComment=" + ("x" * padding)]
            if leaf:
                command += ["-addext", f"subjectAltName=DNS:{host},DNS:localhost,IP:127.0.0.1"]
            if parent is not None:
                command += ["-CA", parent[0], "-CAkey", parent[1]]
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            parent = (level_cert, level_key)
            # Production does not send the root
            if level > 0:
                with open(level_cert, encoding="ascii") as file:
                    pems.insert(0, file.read())
        os.replace(level_key, key)
    with open(cert, "w", encoding="ascii") as file:
        file.write("".join(pems))
    return cert, key


def server_context(cert_dir):
    """Make the TLS context of the proxy"""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*make_cert(cert_dir))
    # TLS 1.2: same first choice as production
    context.set_ciphers("ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305")
    return context


def main():
    """Start the proxy"""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backend", required=True, help="backend URL, e.g. https://<host>/<path>")
    parser.add_argument("--listen", default="0.0.0.0", help="address to listen on (default: %(default)s)")
    parser.add_argument("--port", type=int, default=8443, help="port to listen on (default: %(default)s)")
    parser.add_argument("--cert-dir", default=CERT_DIR, help="directory of cert.pem, key.pem (default: %(default)s)")
    parser.add_argument("--debug", action="store_true", help="more log output")
    args = parser.parse_args()
    logging.basicConfig(level=(logging.DEBUG if args.debug else logging.INFO),
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        backend = Backend(args.backend)
        proxy = Proxy(args.listen, args.port, server_context(args.cert_dir), backend)
    except (ValueError, OSError, subprocess.CalledProcessError) as err:
        sys.exit(f"Cannot start: {err}")
    LOG.info("Proxy on %s:%d to %s", args.listen, proxy.port, args.backend)
    LOG.info("Lämpli: backend url https://%s:%d%s", socket.gethostname(), proxy.port, backend.prefix)
    LOG.info("Lämpli: backend insecure on")
    try:
        proxy.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
