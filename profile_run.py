"""Time each stage of the pipeline separately.

    python profile_run.py <video>

Prints seconds and per-frame cost for extraction, preprocessing and OCR, so
the slow one is obvious rather than assumed.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

from PIL import Image

from mo2log import default_crop, extract, probe, _here
from preprocess import prep

video = sys.argv[1]
fps = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0

w, h, dur = probe(video)
crop = default_crop(w, h)
print("video   %dx%d  %.0fs   cpus %d" % (w, h, dur, os.cpu_count()))

tmp = tempfile.mkdtemp(prefix="mo2prof_")
prepdir = os.path.join(tmp, "prep")
os.makedirs(prepdir)
try:
    t = time.time()
    frames = extract(video, tmp, fps, crop)
    t_extract = time.time() - t

    t = time.time()
    for f in frames:
        prep(Image.open(os.path.join(tmp, f))).save(os.path.join(prepdir, f))
    t_prep = time.time() - t

    sample = Image.open(os.path.join(prepdir, frames[0]))
    t = time.time()
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", os.path.join(_here(), "ocr_batch.ps1"), "-Dir", prepdir],
        capture_output=True)
    t_ocr = time.time() - t
    lines = len(r.stdout.decode("utf-8", "replace").splitlines())

    n = len(frames)
    total = t_extract + t_prep + t_ocr
    print("frames  %d at %s fps" % (n, fps))
    print("prepped size %dx%d  (crop %dx%d upscaled)" % (sample.width, sample.height, crop[2], crop[3]))
    print()
    for name, secs in (("ffmpeg extract", t_extract),
                       ("preprocess", t_prep),
                       ("OCR", t_ocr)):
        print("  %-16s %7.1fs  %5.0f ms/frame  %4.0f%%"
              % (name, secs, 1000*secs/n, 100*secs/total))
    print("  %-16s %7.1fs" % ("TOTAL", total))
    print("\n  %.2fs per frame -> %.1f min for a 5 minute clip at %s fps"
          % (total/n, (total/n) * (300*fps) / 60, fps))
    print("  ocr lines: %d" % lines)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
