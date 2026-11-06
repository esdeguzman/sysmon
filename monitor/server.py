"""HTTP routes. The only component with authority to kill anything.

Bound to loopback on purpose. A kill endpoint reachable from the network
is not a monitoring tool.
"""

import json
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from monitor import killer, safety
from monitor.advice import read_advice
from monitor.constants import (
    ADVICE_PATH,
    BLOCKED_NAMES,
    BLOCKED_PIDS,
    HOST,
    PORT,
    WEB_DIR,
)
from monitor.sampler import Sampler

MAX_TARGETS = 64

_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def build_state(sampler, advice_path=ADVICE_PATH):
    """Everything the page needs in one response."""
    facts = sampler.facts()

    for group in facts["groups"]:
        blocked = group["name"] in BLOCKED_NAMES
        has_pid_block = any(pid in BLOCKED_PIDS for pid in group.get("pids", []))
        group["killable"] = not (blocked or has_pid_block)

    facts["advice"] = read_advice(advice_path)
    return facts


def handle_kill(payload, sampler, protected):
    """Revalidate every target, then act. The page has no authority here."""
    if not isinstance(payload, dict):
        return {"ok": False, "error": "body must be a JSON object"}

    targets = payload.get("targets")
    if not isinstance(targets, list):
        return {"ok": False, "error": "targets must be a list"}
    if len(targets) > MAX_TARGETS:
        return {"ok": False, "error": f"at most {MAX_TARGETS} targets per request"}
    if not all(isinstance(t, dict) for t in targets):
        return {"ok": False, "error": "each target must be an object"}

    force = bool(payload.get("force"))
    snapshot = sampler.latest()
    allowed, rejected = safety.filter_targets(targets, snapshot, protected)
    results = killer.kill_targets(allowed, force=force)
    return {"ok": True, "results": results, "rejected": rejected}


class Handler(BaseHTTPRequestHandler):
    sampler = None
    protected = frozenset()

    def log_message(self, fmt, *args):
        pass  # the console is for startup and errors, not a request log

    def _send(self, code, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, code, obj):
        self._send(code, json.dumps(obj, default=str))

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in _STATIC:
            name, content_type = _STATIC[path]
            try:
                self._send(200, (WEB_DIR / name).read_bytes(), content_type)
            except OSError:
                self._send_json(500, {"error": f"{name} is missing"})
            return
        if path == "/api/state":
            self._send_json(200, build_state(self.sampler))
            return
        if path == "/api/snapshot":
            self._send_json(200, {"processes": self.sampler.latest()})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?")[0] != "/api/kill":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_json(400, {"ok": False, "error": "bad Content-Length"})
            return
        if length <= 0 or length > 100_000:
            self._send_json(400, {"ok": False, "error": "bad body size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            self._send_json(400, {"ok": False, "error": f"bad JSON: {exc}"})
            return

        result = handle_kill(payload, self.sampler, self.protected)
        self._send_json(200 if result.get("ok") else 400, result)


def make_server(sampler):
    own_pid, ancestors = sampler.own_context()
    Handler.sampler = sampler
    Handler.protected = safety.protected_pids(own_pid, ancestors)
    return ThreadingHTTPServer((HOST, PORT), Handler)


def main():
    sampler = Sampler()
    sampler.start()
    try:
        httpd = make_server(sampler)
    except OSError as exc:
        print(f"Cannot bind {HOST}:{PORT}. {exc}", file=sys.stderr)
        print("Another sysmon may already be running.", file=sys.stderr)
        return 1

    url = f"http://{HOST}:{PORT}/"
    print(f"sysmon running at {url}")
    print(f"Protected PIDs: {sorted(Handler.protected)}")
    print("Press Ctrl+C to stop.")
    webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        sampler.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
