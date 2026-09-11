"""Read frames with PaddleOCR's PP-OCR models, run locally on ONNX Runtime.

This replaced Windows' own OCR as the reader. Scored against the in-game log on
four fights -- 109 hits between them, dark dungeon and outdoors -- Windows OCR
found 103 and this finds all 109, inventing none, and every damage total comes
out exact. Swapping to Tesseract changed nothing, which is why the engine was
wrongly written off once; these models are a different class of reader.

What made the difference is mostly the timestamps. This keeps [hh:mm:ss] on
nearly every line, and the timestamp is what tells two identical lines apart:
five `hits you for 0[Parry]` from one player, all spelled the same, merge into
three without it. It also reads the number out of a long line that Windows OCR
kept dropping from the middle.

It reads the raw crop. No preprocessing: every treatment tried under it scored
the same or worse, and skipping that stage pays back some of the time this
reader costs. Its one known weakness is text lying over sunlit sand, where it
returns debris for lines Windows OCR could still read -- none of the logged
fights sit there, but one unlogged clip did.

The runtime (rapidocr, onnxruntime) and the three model files ship with the
app. Nothing is downloaded when it runs.
"""
import logging
import os

# Two settings changed from the library's defaults, each measured against the
# logged fights before it was kept:
#
#   Global.use_cls  off. The classifier checks every line for being upside
#                   down and turns it over if it thinks so. The log is never
#                   upside down, and the check was occasionally flipping a line
#                   that had read correctly -- turning it off took 108 of 109
#                   hits to 109 of 109, and made it faster.
#
#   Det.limit_side_len  400, down from 736. Frames are enlarged until their
#                   short side reaches this before text is looked for; at 736 a
#                   1080p crop grew 2.6x. Every value down to no enlargement at
#                   all still found 109 of 109. 400 keeps some margin for
#                   anyone recording at a lower resolution, where the log's
#                   text is smaller still, at 150ms a frame against 270.
PARAMS = {
    "Global.use_cls": False,
    "Det.limit_side_len": 400,
    # Two threads per worker with half as many workers as cores measured
    # fastest; every split tried was within 15% of it, since the stage is
    # simply CPU-bound.
    "EngineConfig.onnxruntime.intra_op_num_threads": 2,
    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
}

_engine = None


def available():
    """True when the reader can load. The Windows OCR path is kept for when not."""
    try:
        import rapidocr      # noqa: F401
        import onnxruntime   # noqa: F401
        return True
    except Exception:
        return False


def workers():
    """Worker processes, two threads each: half the cores, capped at eight."""
    return max(1, min((os.cpu_count() or 2) // 2, 8))


def _init():
    global _engine
    # The library logs every model load at INFO. In the windowed app there is
    # no console to take it, and nobody reading the report wants it.
    logging.disable(logging.CRITICAL)
    from rapidocr import RapidOCR
    _engine = RapidOCR(params=PARAMS)


def _read(path):
    """One frame -> its text lines, top to bottom, left to right."""
    try:
        r = _engine(path)
    except Exception:
        return os.path.basename(path), []
    rows = []
    if r.txts:
        for box, text in zip(r.boxes, r.txts):
            rows.append((float(box[0][1]), float(box[0][0]), text.strip()))
    rows.sort()
    return os.path.basename(path), [t for _, _, t in rows if t]


def read_frames(folder, frames, n=None, progress=None):
    """Read every frame in `folder`. Returns {filename: [line, ...]}.

    `progress(done)` is called as frames finish, so the window can show a real
    bar for this stage rather than a sweep.
    """
    from concurrent.futures import ProcessPoolExecutor
    paths = [os.path.join(folder, fn) for fn in frames]
    pages = {}
    with ProcessPoolExecutor(max_workers=n or workers(), initializer=_init) as pool:
        for i, (fn, lines) in enumerate(pool.map(_read, paths, chunksize=2), 1):
            pages[fn] = lines
            if progress:
                progress(i)
    return pages
