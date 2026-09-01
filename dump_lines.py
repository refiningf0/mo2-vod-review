"""Dump every distinct line OCR reads from a clip.

    python dump_lines.py <video> [fps]

Used to find log shapes the parser does not know about yet -- healing, spells,
anything that is not "X hit Y for N".
"""
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter

from PIL import Image

from mo2log import default_crop, extract, probe, ocr_folder_parallel, _workers
from preprocess import prep

video = sys.argv[1]
fps = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0

w, h, dur = probe(video)
crop = default_crop(w, h)
tmp = tempfile.mkdtemp(prefix="mo2lines_")
prepdir = os.path.join(tmp, "prep")
os.makedirs(prepdir)
try:
    frames = extract(video, tmp, fps, crop)
    for f in frames:
        try:
            prep(Image.open(os.path.join(tmp, f))).save(os.path.join(prepdir, f))
        except Exception:
            pass
    pages = ocr_folder_parallel(prepdir, _workers())

    seen = Counter()
    for lines in pages.values():
        for ln in lines:
            seen[ln.strip()] += 1

    print("%d frames, %d distinct lines\n" % (len(frames), len(seen)))
    for ln, n in seen.most_common():
        print("%4d  %s" % (n, ln))
finally:
    shutil.rmtree(tmp, ignore_errors=True)
