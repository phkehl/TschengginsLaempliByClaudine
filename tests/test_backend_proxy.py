#!/usr/bin/env python3
"""Tests for tools/backend-proxy.py

Run: python3 -m unittest discover -s tests
"""

import base64
import http.client
import http.server
import importlib.util
import json
import os
import ssl
import sys
import tempfile
import threading
import unittest

try:
    from websockets.exceptions import InvalidStatus
    from websockets.sync.client import connect
    from websockets.sync.server import serve
except ImportError:
    sys.exit("websockets missing, please run: sudo apt install python3-websockets")

TOP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SPEC = importlib.util.spec_from_file_location("backend_proxy", os.path.join(TOP, "tools", "backend-proxy.py"))
backend_proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend_proxy)

PREFIX = "/prefix"
AUTH = "Basic " + base64.b64encode(b"user:pass").decode()


class FakeBackend(http.server.BaseHTTPRequestHandler):
    """Answers with the request as JSON, needs AUTH for paths with "private" """

    protocol_version = "HTTP/1.1"

    def _answer(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        if "private" in self.path and (self.headers.get("Authorization") != AUTH):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="test"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.endswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", f"http://{self.headers['Host']}{PREFIX}/there")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = json.dumps({"method": self.command, "path": self.path, "body": body, "version": self.request_version,
                           "headers": {key.lower(): value for key, value in self.headers.items()}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = _answer
    do_POST = _answer

    def log_message(self, *args):
        pass


class ProxyTestBase(unittest.TestCase):
    """Runs the proxy in front of the given backend URL"""

    backend_url = None

    @classmethod
    def start_proxy(cls, url):
        cls.tmp = tempfile.TemporaryDirectory()
        context = backend_proxy.server_context(os.path.join(cls.tmp.name, "cert"))
        cls.proxy = backend_proxy.Proxy("127.0.0.1", 0, context, backend_proxy.Backend(url))
        cls.proxy_thread = threading.Thread(target=cls.proxy.run, daemon=True)
        cls.proxy_thread.start()
        cls.client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        cls.client_context.check_hostname = False
        cls.client_context.verify_mode = ssl.CERT_NONE
        # As the firmware. Threaded websockets client hangs with 1.3.
        cls.client_context.maximum_version = ssl.TLSVersion.TLSv1_2

    @classmethod
    def stop_proxy(cls):
        cls.proxy.stop()
        cls.proxy_thread.join()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPSConnection("127.0.0.1", self.proxy.port, context=self.client_context, timeout=10)
        self.addCleanup(conn.close)
        conn.request(method, path, body=body, headers=(headers or {}))
        response = conn.getresponse()
        return response, response.read()


class HttpTest(ProxyTestBase):

    @classmethod
    def setUpClass(cls):
        cls.upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeBackend)
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        cls.netloc = f"127.0.0.1:{cls.upstream.server_address[1]}"
        cls.start_proxy(f"http://{cls.netloc}{PREFIX}")

    @classmethod
    def tearDownClass(cls):
        cls.stop_proxy()
        cls.upstream.shutdown()
        cls.upstream.server_close()

    def test_get(self):
        response, data = self.request("GET", f"{PREFIX}/api/jobs?a=1")
        self.assertEqual(response.status, 200)
        seen = json.loads(data)
        self.assertEqual(seen["path"], f"{PREFIX}/api/jobs?a=1")
        self.assertEqual(seen["headers"]["host"], self.netloc)
        self.assertEqual(seen["headers"]["connection"], "close")
        self.assertEqual(seen["headers"]["x-forwarded-for"], "127.0.0.1")
        self.assertEqual(response.getheader("Connection"), "close")

    def test_post_body(self):
        _, data = self.request("POST", f"{PREFIX}/api/x", body="x" * 100000)
        seen = json.loads(data)
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(len(seen["body"]), 100000)

    def test_auth_passed(self):
        response, _ = self.request("GET", f"{PREFIX}/private")
        self.assertEqual(response.status, 401)
        self.assertIn("Basic", response.getheader("WWW-Authenticate"))
        response, _ = self.request("GET", f"{PREFIX}/private", headers={"Authorization": AUTH})
        self.assertEqual(response.status, 200)

    def test_forwarded_headers(self):
        # Client must not set forwarded headers
        _, data = self.request("GET", f"{PREFIX}/x", headers={"X-Forwarded-User": "flip", "X-Forwarded-For": "1.2.3.4"})
        seen = json.loads(data)["headers"]
        self.assertNotIn("x-forwarded-user", seen)
        self.assertEqual(seen["x-forwarded-for"], "127.0.0.1")

    def test_outside_prefix(self):
        for path in ("/", "/other", f"{PREFIX}x", f"{PREFIX}/../other", f"{PREFIX}/%2e%2e/other", "http://evil/"):
            response, _ = self.request("GET", path)
            self.assertEqual(response.status, 404, path)

    def test_redirect(self):
        response, _ = self.request("GET", f"{PREFIX}/redirect")
        self.assertEqual(response.status, 302)
        self.assertEqual(response.getheader("Location"), f"https://127.0.0.1:{self.proxy.port}{PREFIX}/there")

    def test_http10(self):
        # OTA client uses HTTP/1.0
        conn = http.client.HTTPSConnection("127.0.0.1", self.proxy.port, context=self.client_context, timeout=10)
        self.addCleanup(conn.close)
        conn._http_vsn = 10
        conn._http_vsn_str = "HTTP/1.0"
        conn.request("GET", f"{PREFIX}/api/firmware/x")
        response = conn.getresponse()
        self.assertEqual(json.loads(response.read())["version"], "HTTP/1.0")

    def test_certificate(self):
        # ECDSA as the production certificate
        pem = ssl.get_server_certificate(("127.0.0.1", self.proxy.port))
        der = ssl.PEM_cert_to_DER_cert(pem)
        self.assertIn(bytes.fromhex("2a8648ce3d0201"), der)


class NoBackendTest(ProxyTestBase):

    @classmethod
    def setUpClass(cls):
        # Port 1: nobody listens
        cls.start_proxy(f"http://127.0.0.1:1{PREFIX}")

    @classmethod
    def tearDownClass(cls):
        cls.stop_proxy()

    def test_bad_gateway(self):
        response, _ = self.request("GET", f"{PREFIX}/x")
        self.assertEqual(response.status, 502)


class WebSocketTest(ProxyTestBase):

    @classmethod
    def setUpClass(cls):
        cls.seen = {}

        def check(conn, request):
            # Same as Apache: basic auth needed
            cls.seen = request.headers
            if request.headers.get("Authorization") != AUTH:
                return conn.respond(401, "No\n")
            return None

        def echo(conn):
            for message in conn:
                conn.send(message)

        cls.server = serve(echo, "127.0.0.1", 0, process_request=check, compression=None)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.start_proxy(f"http://127.0.0.1:{cls.server.socket.getsockname()[1]}{PREFIX}")

    @classmethod
    def tearDownClass(cls):
        cls.stop_proxy()
        cls.server.shutdown()

    def url(self):
        return f"wss://127.0.0.1:{self.proxy.port}{PREFIX}/ws/device?device=x"

    def test_echo(self):
        with connect(self.url(), ssl=self.client_context, additional_headers={"Authorization": AUTH},
                     compression=None, open_timeout=10) as conn:
            for size in (10, 1000, 6000, 40000):
                text = "x" * size
                conn.send(text)
                self.assertEqual(conn.recv(timeout=10), text)
        self.assertEqual(self.seen.get("X-Forwarded-For"), "127.0.0.1")

    def test_no_auth(self):
        with self.assertRaises(InvalidStatus) as caught:
            connect(self.url(), ssl=self.client_context, compression=None, open_timeout=10)
        self.assertEqual(caught.exception.response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
