"""A fake HTTP provider that shuts down cleanly.

The client under test times out on purpose, so a handler can find its socket closed when it
replies; that is expected and stays quiet. Teardown stops the server, closes it (which joins
every handler thread), then joins the serving thread, so nothing is left running at exit.
"""

import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GONE = (BrokenPipeError, ConnectionResetError)


class QuietHandler(BaseHTTPRequestHandler):
    """Writes swallow a client that already hung up; nothing is logged."""

    def log_message(self, *a):
        pass

    def reply(self, code: int, body: bytes, content_type: str = "application/json") -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except GONE:
            pass


class QuietServer(ThreadingHTTPServer):
    daemon_threads = False          # handler threads are joined by server_close(), never abandoned
    block_on_close = True

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], GONE):
            return
        super().handle_error(request, client_address)


@contextmanager
def serve(handler, stop: threading.Event | None = None, join_timeout: float = 5.0):
    """Run `handler` on a local port; yield the base URL. `stop` is set first on teardown, so a
    handler that is deliberately hanging can wake and finish before the server is closed."""
    srv = QuietServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, name="fake-provider")
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    finally:
        if stop is not None:
            stop.set()
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=join_timeout)
        assert not thread.is_alive(), "fake provider did not stop"
