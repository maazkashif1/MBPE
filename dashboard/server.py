import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dashboard.summary import build

DATA = Path("data")

STATIC = Path(__file__).parent / "static"
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class Handler(BaseHTTPRequestHandler):
    def send(self, status: int, body: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/summary":
            try:
                body = json.dumps(build(DATA), default=str).encode()
                self.send(200, body, "application/json")
            except Exception as e:
                self.send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}).encode(), "application/json")
        elif path in FILES:
            name, kind = FILES[path]
            self.send(200, (STATIC / name).read_bytes(), kind)
        else:
            self.send(404, b"not found", "text/plain")

    def log_message(self, fmt: str, *args) -> None:
        pass


def main() -> None:
    global DATA
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8765
    if "--data" in sys.argv:
        DATA = Path(sys.argv[sys.argv.index("--data") + 1])
    host = sys.argv[sys.argv.index("--host") + 1] if "--host" in sys.argv else "127.0.0.1"
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"MBPE dashboard on http://localhost:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
