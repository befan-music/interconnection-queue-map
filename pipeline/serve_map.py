"""Build the atlas as a standalone site and serve it on localhost.

map/index.html is written in the Claude artifact format (page content without the
<!doctype>/<head>/<body> skeleton, which the artifact host adds). This wraps it the same way
.github/workflows/pages.yml does for GitHub Pages, copies map/data, and serves _site/.

Usage:  python -I pipeline/serve_map.py [port]        (default 8000; Ctrl+C to stop)
        python -I pipeline/serve_map.py --build-only
"""
import shutil
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "_site"
HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>body{margin:0}[hidden]{display:none!important}</style>
</head>
<body>
"""


def build():
    if SITE.exists():
        shutil.rmtree(SITE)
    shutil.copytree(ROOT / "map" / "data", SITE / "data")
    page = (ROOT / "map" / "index.html").read_text(encoding="utf-8")
    (SITE / "index.html").write_text(HEAD + page + "\n</body>\n</html>\n", encoding="utf-8")
    (SITE / ".nojekyll").touch()
    return SITE


if __name__ == "__main__":
    site = build()
    if "--build-only" in sys.argv:
        print(f"built {site}")
        sys.exit()
    port = int(next((a for a in sys.argv[1:] if a.isdigit()), 8000))
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(SimpleHTTPRequestHandler, directory=str(site)))
    print(f"Interconnection Headroom Atlas: http://localhost:{port}/  (Ctrl+C to stop)", flush=True)
    server.serve_forever()
