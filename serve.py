"""Local web server for the dashboard.

Serves the static files in webview/ (index.html, app.js, style.css, data.js)
at http://localhost:8000, and keeps the data in sync with the repo:

  - On startup it runs the build (build.main()), so webview/data.js always
    matches the results/test-*/ folders currently on disk.
  - It exposes one endpoint, POST /api/refresh, which the page's Refresh
    button calls to re-run the build without restarting the server.
  - Every response is sent with "Cache-Control: no-store", so reloading the
    page always loads the freshly built data.js.

The server only listens on 127.0.0.1, so it isn't reachable from other
machines. Uses only the Python standard library. Run from the repo root:

    python serve.py            # http://localhost:8000
    python serve.py 9000       # custom port
"""

import functools
import http.server
import json
import sys
import threading
import traceback
from pathlib import Path

import build

WEBVIEW = Path(__file__).parent / "webview"  # folder served as the site root
BUILD_LOCK = threading.Lock()


def rebuild() -> dict:
    '''Run the build; the lock stops two Refresh clicks from writing data.js at once.'''
    with BUILD_LOCK:
        return build.main()


class Handler(http.server.SimpleHTTPRequestHandler):
    '''Static file handler from the standard library, plus the refresh endpoint.'''

    def end_headers(self):
        # Always serve fresh files, so a reload picks up the new data.js.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):
        '''POST /api/refresh: rebuild data.js and reply with a JSON summary.

        Response: {"ok": true, "runs": [...], "errors": [...]} on success, or
        {"ok": false, "error": "..."} with status 500 if the build itself failed.
        Broken run folders don't fail the build; they come back in "errors".
        '''
        if self.path != "/api/refresh":
            self.send_error(404)
            return
        try:
            body = {"ok": True, **rebuild()}
            status = 200
        except Exception as error:
            traceback.print_exc()  # full details in the terminal, short message to the page
            body = {"ok": False, "error": f"{type(error).__name__}: {error}"}
            status = 500
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    rebuild()

    # Serve webview/ no matter which directory the script was started from.
    handler = functools.partial(Handler, directory=WEBVIEW)
    # Threading server so a slow rebuild doesn't block other requests.
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        print(f"Serving dashboard at http://localhost:{port} (Ctrl+C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped")


if __name__ == "__main__":
    main()
