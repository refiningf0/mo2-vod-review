"""Entry point for the packaged app: a video in, an open report out.

Drag a clip onto the executable and this runs the whole pipeline, writing the
JSON and the HTML report next to the video itself rather than next to the
program -- the report belongs with the footage it came from.

    MO2VODReview.exe "some fight.mp4"
"""
import multiprocessing
import os
import sys
import traceback

from mo2log import run
from make_report import build


def app_dir():
    """The folder the tool lives in, as the person running it sees it.

    Deliberately not mo2log._here(): inside a PyInstaller build that resolves
    to the temporary directory the bundle unpacks itself into, which is gone
    the moment the run ends. What is wanted here is the folder holding
    MO2VODReview.exe -- somewhere a person can open.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def report_paths(video):
    """Where a clip's report goes: a `reports` folder inside the tool's folder.

    All of them in one place, beside the program that wrote them. Putting each
    report next to its own clip seemed tidier and was not: a `reports` folder
    appeared in every folder a clip had ever been dragged out of, and finding
    an old report meant remembering which one that was.

    One place decides this. When the drag-and-drop batch file and the packaged
    app each decided it separately they disagreed, and half the reports landed
    somewhere other than where the other half went.

    A clip run twice overwrites its own report, which is the point. Two
    different clips sharing a filename would land on each other, which no
    clip named after a date and time is going to do.
    """
    folder = os.path.join(app_dir(), "reports")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        # Unzipped somewhere unwritable. Beside the clip is not where these are
        # meant to go, but it beats not writing them at all.
        folder = os.path.dirname(video)
    stem = os.path.splitext(os.path.basename(video))[0]
    base = os.path.join(folder, stem)
    return base + ".json", base + ".html"


def main():
    if len(sys.argv) < 2:
        print()
        print("  Drag a video file onto MO2VODReview and let go.")
        print()
        return 1

    video = os.path.abspath(sys.argv[1])
    if not os.path.exists(video):
        print("\n  No such file:\n    %s\n" % video)
        return 1

    js, html = report_paths(video)

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
