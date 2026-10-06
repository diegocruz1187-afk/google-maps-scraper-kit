#!/usr/bin/env python3
import http.client
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8080
PORT = int(os.environ.get("PORT", "10000"))
API_KEY = os.environ.get("SCRAPER_API_KEY", "")

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def backend_is_ready():
    try:
        with socket.create_connection((BACKEND_HOST, BACKEND_PORT), timeout=2):
            return True
    except OSError:
        return False


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, status, body):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self):
        if self.path == "/health":
            return True
        supplied = self.headers.get("X-API-Key", "")
        return bool(API_KEY) and supplied == API_KEY

    def _proxy(self):
        if not self._authorized():
            self._json(401, '{"error":"unauthorized"}')
            return

        if self.path == "/health":
            if backend_is_ready():
                self._json(200, '{"status":"ok"}')
            else:
                self._json(503, '{"status":"starting"}')
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else None

        headers = {}
        for key, value in self.headers.items():
            if key.lower() not in HOP_BY_HOP and key.lower() != "host":
                headers[key] = value

        conn = http.client.HTTPConnection(BACKEND_HOST, BACKEND_PORT, timeout=120)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            self.send_response(resp.status, resp.reason)

            for key, value in resp.getheaders():
                if key.lower() not in HOP_BY_HOP:
                    self.send_header(key, value)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except Exception as exc:
            self._json(502, '{"error":"backend_unavailable"}')
            print(f"proxy error: {exc}", flush=True)
        finally:
            conn.close()

    def do_GET(self):
        self._proxy()

    def do_POST(self):
        self._proxy()

    def do_DELETE(self):
        self._proxy()

    def do_PUT(self):
        self._proxy()

    def do_PATCH(self):
        self._proxy()

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)


if not API_KEY:
    raise SystemExit("SCRAPER_API_KEY is required")

server = ThreadingHTTPServer(("0.0.0.0", PORT), ProxyHandler)
print(f"auth proxy listening on 0.0.0.0:{PORT}", flush=True)
server.serve_forever()
