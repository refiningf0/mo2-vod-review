"""Dump every raw reading of a given name, before dedupe merges them.

    python dump_raw.py <video> <name-fragment>

Shows each reading's frame time and whether OCR recovered a timestamp, which
is what decides where the event lands on the timeline.
"""
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image

from mo2log import default_crop, extract, probe
from preprocess import prep
from parse import parse_line

video, needle = sys.argv[1], sys.argv[2].lower()
fps = 2.0

w, h, dur = probe(video)
crop = default_crop(w, h)
tmp = tempfile.mkdtemp(prefix="mo2dump_")
prepdir = os.path.join(tmp, "prep")
os.makedirs(prepdir)
try:
    frames = extract(video, tmp, fps, crop)
    for f in frames:
        try:
            prep(Image.open(os.path.join(tmp, f))).save(os.path.join(prepdir, f))
        except Exception:
            pass
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr_batch.ps1"),
         "-Dir", prepdir],
        capture_output=True)
    txt = r.stdout.decode("utf-8", errors="replace")

    idx = -1
    rows = []
    for ln in txt.splitlines():
        ln = ln.strip()
        if ln.startswith("##FILE"):
            idx += 1
            continue
        if not ln:
            continue
        ev = parse_line(ln, idx / fps)
        if ev and needle in (str(ev["who"]) + str(ev["target"])).lower():
            rows.append(ev)

    print("%-8s %-8s %-6s %-5s %5s  %s" % ("frame_t", "t", "dir", "exact", "amt", "raw"))
    for e in sorted(rows, key=lambda e: e["ft"] if e.get("ft") is not None else e["t"]):
        print("%-8.1f %-8.1f %-6s %-5s %5s  %s" % (
            e.get("ft") if e.get("ft") is not None else -1,
            e["t"], e["dir"], e["exact"], e["amount"], (e.get("raw") or "")[:62]))
    print("\n%d raw readings" % len(rows))
finally:
    shutil.rmtree(tmp, ignore_errors=True)
