"""Serve the webview/ folder on localhost to check the dashboard.

Uses only the Python standard library. Run from the repo root:

    python serve.py            # http://localhost:8000
    python serve.py 9000       # custom port
"""

import functools
import http.server
import sys
from pathlib import Path

WEBVIEW = Path(__file__).parent / "webview"


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    if not (WEBVIEW / "data.js").exists():
        print("webview/data.js not found, run `python build.py` first.")
        sys.exit(1)

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=WEBVIEW)
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        print(f"Serving dashboard at http://localhost:{port} (Ctrl+C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped")


if __name__ == "__main__":
    main()
