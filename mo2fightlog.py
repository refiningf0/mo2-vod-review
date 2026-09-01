"""Entry point for the packaged app: a video in, an open report out.

Drag a clip onto the executable and this runs the whole pipeline, writing the
JSON and the HTML report next to the video itself rather than next to the
program -- the report belongs with the footage it came from.

    MO2FightLog.exe "some fight.mp4"
"""
import multiprocessing
import os
import sys
import traceback

from mo2log import run
from make_report import build


def main():
    if len(sys.argv) < 2:
        print()
        print("  Drag a video file onto MO2FightLog and let go.")
        print()
        return 1

    video = os.path.abspath(sys.argv[1])
    if not os.path.exists(video):
        print("\n  No such file:\n    %s\n" % video)
        return 1

    stem = os.path.splitext(video)[0]
    js, html = stem + ".json", stem + ".html"

    print()
    print("  Reading: %s" % os.path.basename(video))
    print("  About 90 seconds per 2 minutes of footage.")
    print()

    run(video, fps=2, out=js)
    build(js, html)

    print("\n  Done. Opening %s" % os.path.basename(html))
    try:
        os.startfile(html)
    except OSError:
        print("  (could not open it automatically -- it is next to your video)")
    return 0


if __name__ == "__main__":
    # Without this a frozen build re-launches itself for every worker instead
    # of starting one, and the machine fills with copies of the app.
    multiprocessing.freeze_support()
    try:
        code = main()
    except SystemExit as e:                 # probe() raises these with a message
        print("\n  %s\n" % e)
        code = 1
    except Exception:
        traceback.print_exc()
        print()
        print("  Something went wrong. The lines above say what.")
        print()
        print("  A common cause is the combat log sitting somewhere other than")
        print("  the bottom-left of the frame, so nothing was found.")
        code = 1

    if code != 0:
        try:
            input("  Press Enter to close...")
        except EOFError:
            pass
    sys.exit(code)
