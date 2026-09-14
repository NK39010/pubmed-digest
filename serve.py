"""Local editor server: open the web viewer with saving enabled.

    python serve.py   # http://127.0.0.1:8765 ，改完点"保存到仓库"会写回 data/items.json 并重新生成 feed.xml

The page on GitHub Pages is read-only (edits stay in that browser); saving only works through this local server.
After saving, commit and push data/ and docs/ to publish the changes.
"""

import json
import sys
import tomllib
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from digest.feed import write_rss
from run import FEED_PATH, ITEMS_PATH, ROOT, WEB_ITEMS_PATH

HOST, PORT = "127.0.0.1", 8765


class Handler(SimpleHTTPRequestHandler):
    def _json(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/health":
            return self._json(200, {"editable": True})
        if self.path.split("?")[0] == "/items.json":  # always serve the canonical copy
            return self._json_file()
        return super().do_GET()

    def _json_file(self):
        data = ITEMS_PATH.read_bytes() if ITEMS_PATH.exists() else b"[]"
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_PUT(self):
        if not self.path.startswith("/api/items/"):
            return self._json(404, {"error": "not found"})
        pmid = self.path.rsplit("/", 1)[1]
        update = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        items = json.loads(ITEMS_PATH.read_text(encoding="utf-8"))
        item = next((it for it in items if it["pmid"] == pmid), None)
        if item is None:
            return self._json(404, {"error": f"PMID {pmid} 不存在"})
        for key in ("title", "html"):
            if isinstance(update.get(key), str) and update[key].strip():
                item[key] = update[key]

        payload = json.dumps(items, ensure_ascii=False, indent=2)
        ITEMS_PATH.write_text(payload, encoding="utf-8")
        WEB_ITEMS_PATH.write_text(payload, encoding="utf-8")
        cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
        write_rss(items, cfg["feed"], FEED_PATH)
        return self._json(200, {"ok": True})

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), partial(Handler, directory=str(ROOT / "docs")))
    url = f"http://{HOST}:{PORT}/"
    print(f"本地编辑器：{url}  （Ctrl+C 退出）", flush=True)
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    server.serve_forever()
