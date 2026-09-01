"""Bake a fight's data into a standalone HTML report.

Dropping a JSON file onto a page is one step too many, and opening the JSON
directly just shows Chrome's raw viewer. This writes a single self-contained
file: double-click it and the numbers are there.

    python make_report.py fight.json          -> fight.html
    python make_report.py fight.json out.html
"""
import io
import json
import os
import sys

# Inside a PyInstaller build viewer.html is unpacked beside the code, not
# next to this source file.
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
VIEWER = os.path.join(HERE, "viewer.html")


def build(json_path, out_path=None):
    data = json.load(io.open(json_path, encoding="utf-8"))
    html = io.open(VIEWER, encoding="utf-8").read()

    payload = json.dumps(data)
    inject = (
        "<script>\n"
        "// Data baked in at build time so this file works on its own.\n"
        "window.__BAKED__ = " + payload + ";\n"
        "</script>\n"
    )
    html = html.replace("</body>", inject + "</body>", 1)

    if out_path is None:
        out_path = os.path.splitext(json_path)[0] + ".html"
    io.open(out_path, "w", encoding="utf-8").write(html)
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    p = build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print("wrote %s" % p)
