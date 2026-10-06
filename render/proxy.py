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

    def _send_bytes(self, status, payload=b"", content_type=None, extra_headers=None, head_only=False):
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        if extra_headers:
            for key, value in extra_headers:
                if key.lower() not in HOP_BY_HOP and key.lower() not in {
                    "content-length",
                    "connection",
                }:
                    self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if not head_only and payload:
            self.wfile.write(payload)

    def _json(self, status, body, head_only=False):
        self._send_bytes(
            status,
            body.encode("utf-8"),
            content_type="application/json",
            head_only=head_only,
        )

    def _authorized(self):
        if self.path == "/health":
            return True
        supplied = self.headers.get("X-API-Key", "")
        return bool(API_KEY) and supplied == API_KEY

    def _proxy(self, head_only=False):
        if not self._authorized():
            self._json(401, '{"error":"unauthorized"}', head_only=head_only)
            return

        if self.path == "/health":
            if backend_is_ready():
                self._json(200, '{"status":"ok"}', head_only=head_only)
            else:
                self._json(503, '{"status":"starting"}', head_only=head_only)
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else None

        headers = {}
        for key, value in self.headers.items():
            if key.lower() not in HOP_BY_HOP and key.lower() not in {"host", "content-length"}:
                headers[key] = value
        if body is not None:
            headers["Content-Length"] = str(len(body))

        conn = http.client.HTTPConnection(BACKEND_HOST, BACKEND_PORT, timeout=120)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()

            # Buffer the upstream response before sending headers. The upstream API
            # can use chunked transfer encoding; forwarding decoded chunks while
            # stripping Transfer-Encoding leaves HTTP/1.1 clients waiting forever.
            payload = resp.read()
            response_headers = resp.getheaders()
            content_type = resp.getheader("Content-Type")

            self._send_bytes(
                resp.status,
                payload,
                content_type=content_type,
                extra_headers=response_headers,
                head_only=head_only,
            )
        except Exception as exc:
            print(f"proxy error: {exc}", flush=True)
            self._json(502, '{"error":"backend_unavailable"}', head_only=head_only)
        finally:
            conn.close()

    def do_HEAD(self):
        self._proxy(head_only=True)

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
