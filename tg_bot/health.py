"""tiny stdlib health listener so Render's Web Service sees an open port.

Render nags "No open ports detected" / can mark the service unhealthy when a
process binds nothing.  A Pyrogram userbot binds no HTTP port, so this thread
answers $PORT (Render default 10000) with HTTP 200 on any path.  Stdlib-only.
"""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_PORT = int(os.environ.get("PORT", "10000"))


class HealthHandler(BaseHTTPRequestHandler):
    def _ok(self) -> None:
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        self._ok()

    def do_HEAD(self) -> None:  # noqa: N802
        self._ok()

    def log_message(self, *args) -> None:  # keep Render log clean
        pass


def start_health_server() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", _PORT), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    from tg_bot.config import LOGGER

    LOGGER.info("Health listener live on 0.0.0.0:%d (Render sees an open port).", _PORT)
