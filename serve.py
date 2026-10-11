"""Local web server for the dashboard: view results and start new evaluation runs.

Serves the static files in webview/ at http://localhost:8000 and provides a
small JSON API used by the page:

  GET  /api/datasets      HealthBench .jsonl files found in healthbench/
  POST /api/runs          start an evaluation run (settings in the JSON body)
  GET  /api/runs/current  progress of the current (or last) run
  POST /api/runs/cancel   stop the current run after the conversation in progress
  POST /api/refresh       rebuild webview/data.js from the results/ folders

How it keeps data in sync:
  - On startup it runs the build (build.main()), so webview/data.js always
    matches the results/test-*/ folders currently on disk. A finished run
    triggers a rebuild too.
  - Every response is sent with "Cache-Control: no-store", so reloading the
    page always loads the freshly built data.js.

Run settings (base URL, API key, models) come from the page for one run only:
they are kept in memory while the run is going and never written to disk,
logged or sent back to the page. Only one run can go at a time.

Security: the server only listens on 127.0.0.1, and POST requests are only
accepted from this dashboard's own page (checked with the Host and Origin
headers), so other websites open in the browser can't start runs.

Viewing results needs only the Python standard library. Starting runs needs
the packages in requirements.txt (openai). Run from the repo root:

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
from urllib.parse import urlparse

import build

ROOT = Path(__file__).parent
WEBVIEW = ROOT / "webview"           # folder served as the site root
DATASETS = ROOT / "healthbench"      # where the HealthBench .jsonl files are
RESULTS = ROOT / "results"           # where runs are saved
BUILD_LOCK = threading.Lock()
REASONING_CHOICES = {"low", "medium", "high", "none"}


def rebuild() -> dict:
    '''Run the build; the lock stops two rebuilds from writing data.js at once.'''
    with BUILD_LOCK:
        return build.main()


# ---------- datasets ----------

_line_counts = {}  # path -> (mtime, size, count), so the list stays fast for large files


def count_lines(path: Path) -> int:
    stat = path.stat()
    cached = _line_counts.get(path)
    if cached and cached[:2] == (stat.st_mtime, stat.st_size):
        return cached[2]
    count = 0
    with path.open("rb") as f:
        for line in f:
            if line.strip():
                count += 1
    _line_counts[path] = (stat.st_mtime, stat.st_size, count)
    return count


def list_datasets() -> list:
    if not DATASETS.is_dir():
        return []
    return [
        {"name": p.name, "conversations": count_lines(p), "size_mb": round(p.stat().st_size / 1e6, 1)}
        for p in sorted(DATASETS.glob("*.jsonl"))
    ]


# ---------- runs ----------

class RunManager:
    '''Runs one evaluation at a time in a background thread and tracks its progress.'''

    def __init__(self):
        self.lock = threading.Lock()
        self.start_lock = threading.Lock()  # makes "check nothing is running, then start" atomic
        self.thread = None
        self.cancel_event = threading.Event()
        self.progress = {"state": "idle"}

    def is_running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, config) -> None:
        from pygrader import run_evaluation  # imported here so viewing results works without openai

        template = (ROOT / "GRADER.md").read_text()
        self.cancel_event.clear()
        with self.lock:
            self.progress = {"state": "starting"}

        def update(progress):
            with self.lock:
                self.progress = progress

        def work():
            try:
                run_evaluation(config, RESULTS, template, root=ROOT,
                               on_progress=update, should_stop=self.cancel_event.is_set)
            except Exception as error:  # setup errors, e.g. unreadable dataset
                traceback.print_exc()
                with self.lock:
                    self.progress = dict(self.progress, state="failed",
                                         errors=self.progress.get("errors", []) + [f"{type(error).__name__}: {error}"])
            finally:
                rebuild()  # show the new run in the results view
                # Tell the page the rebuilt data.js is ready, so "View results" loads the new run.
                with self.lock:
                    self.progress = dict(self.progress, results_ready=True)

        # The config (with the API key) lives only in this thread's closure and is dropped when it ends.
        self.thread = threading.Thread(target=work, name="evaluation-run", daemon=True)
        self.thread.start()

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.progress)


RUNS = RunManager()


def parse_run_request(body: dict):
    '''Validate the page's run settings and build a RunConfig. Raises ValueError with a readable message.'''
    from pygrader import RunConfig

    def text(key, required=True):
        value = str(body.get(key) or "").strip()
        if required and not value:
            raise ValueError(f"'{key}' is required")
        return value

    base_url = text("base_url")
    if urlparse(base_url).scheme not in ("http", "https"):
        raise ValueError("Base URL must start with http:// or https://")

    reasoning = {}
    for key in ("reasoning_test", "reasoning_grader"):
        value = text(key, required=False).lower() or "none"
        if value not in REASONING_CHOICES:
            raise ValueError(f"'{key}' must be one of: low, medium, high, none")
        reasoning[key] = None if value == "none" else value

    # Only files directly inside healthbench/ can be chosen.
    name = text("dataset")
    dataset = DATASETS / name
    if Path(name).name != name or dataset.suffix != ".jsonl" or not dataset.is_file():
        raise ValueError(f"Dataset '{name}' not found in healthbench/")

    limit = body.get("limit")
    if limit in (None, ""):
        limit = None
    else:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            raise ValueError("'limit' must be a whole number") from None
        if limit < 1:
            raise ValueError("'limit' must be at least 1")

    return RunConfig(
        base_url=base_url,
        api_key=text("api_key"),
        model_test=text("model_test"),
        model_grader=text("model_grader"),
        reasoning_test=reasoning["reasoning_test"],
        reasoning_grader=reasoning["reasoning_grader"],
        dataset=dataset,
        limit=limit,
    )


# ---------- HTTP ----------

class Handler(http.server.SimpleHTTPRequestHandler):
    '''Static file handler from the standard library, plus the JSON API.'''

    def end_headers(self):
        # Always serve fresh files, so a reload picks up the new data.js.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format, *args):
        # Keep the terminal readable: don't log the page's once-a-second progress polling.
        if "/api/runs/current" not in self.path:
            super().log_message(format, *args)

    def send_json(self, body: dict, status: int = 200):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def same_origin(self) -> bool:
        '''True if the request comes from this dashboard's page (not from another website).'''
        port = self.server.server_address[1]
        allowed = {f"localhost:{port}", f"127.0.0.1:{port}"}
        if self.headers.get("Host") not in allowed:
            return False
        origin = self.headers.get("Origin")
        return origin is None or urlparse(origin).netloc in allowed

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(body, dict):
            raise ValueError("expected a JSON object")
        return body

    def do_GET(self):
        if self.path == "/api/datasets":
            self.send_json({"folder": "healthbench", "datasets": list_datasets()})
        elif self.path == "/api/runs/current":
            self.send_json(RUNS.snapshot())
        else:
            super().do_GET()

    def do_POST(self):
        if not self.same_origin():
            self.send_json({"ok": False, "error": "Requests are only accepted from the dashboard page"}, 403)
            return

        if self.path == "/api/refresh":
            # Broken run folders don't fail the build; they come back in "errors".
            try:
                self.send_json({"ok": True, **rebuild()})
            except Exception as error:
                traceback.print_exc()  # full details in the terminal, short message to the page
                self.send_json({"ok": False, "error": f"{type(error).__name__}: {error}"}, 500)

        elif self.path == "/api/runs":
            # JSON only: browsers can't send this content type cross-site without permission.
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                self.send_json({"ok": False, "error": "Expected application/json"}, 415)
                return
            with RUNS.start_lock:
                if RUNS.is_running():
                    self.send_json({"ok": False, "error": "A run is already in progress"}, 409)
                    return
                try:
                    config = parse_run_request(self.read_json())
                    RUNS.start(config)
                except ImportError as error:
                    self.send_json({"ok": False, "error": f"Missing package ({error.name}). Install it with: pip install -r requirements.txt"}, 500)
                    return
                except (ValueError, json.JSONDecodeError) as error:
                    self.send_json({"ok": False, "error": str(error)}, 400)
                    return
            self.send_json({"ok": True})

        elif self.path == "/api/runs/cancel":
            RUNS.cancel_event.set()
            self.send_json({"ok": True, "running": RUNS.is_running()})

        else:
            self.send_error(404)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    rebuild()

    # Serve webview/ no matter which directory the script was started from.
    handler = functools.partial(Handler, directory=WEBVIEW)
    # Threading server: the page keeps working while a rebuild or a run is going.
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        print(f"Serving dashboard at http://localhost:{port} (Ctrl+C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped")


if __name__ == "__main__":
    main()
