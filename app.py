"""MO2 Fight Log as a window: open it, drop a clip in, watch it read.

The console version is still there and unchanged -- drag a clip onto the exe
and it works exactly as before. This is the same pipeline with a face on it,
so a run shows its progress and the report opens in place instead of in a
browser tab, next to a list of every fight read so far.

The window is the system's own browser view rather than a bundled one, so the
report -- which is already a self-contained page -- renders here exactly as it
does anywhere else, and the app costs about a megabyte rather than the hundred
and fifty a bundled browser would.
"""
import io
import json
import os
import sys
import threading
import traceback
from urllib.parse import quote

import webview

from mo2fightlog import app_dir, report_paths
from mo2log import run
from make_report import build

VIDEO_TYPES = ("Video files (*.mp4;*.mkv;*.mov;*.avi;*.webm)", "All files (*.*)")

# Deliberately not an attribute of the Api object below. Everything on that
# object is walked and serialised to build window.pywebview.api for the page,
# and a pywebview Window leads, through .native, into the whole WinForms and
# COM tree -- which recurses until it gives up, throwing a COM exception per
# property on the way. Thousands of them, before the window has even appeared.
# That was the freeze, and it had nothing to do with the drag handlers.
WINDOW = None

# The strip is drawn by Windows, not by the page, so its colours are set here
# rather than in the stylesheet. Kept in step with app.html by hand.
QUIET = None   # set once System.Drawing is available
LIT = None
STRIP = None   # the drop area itself, so the page can ask for it to step aside
PAGE = EDGE = EDGE_LIT = INK = SOFT = None
LAYOUT = None  # re-places the box and the browser view; set once the app is up


def _log(msg):
    print("[app] %s" % msg)
    sys.stdout.flush()


def _fmt_len(seconds):
    m, s = divmod(int(seconds or 0), 60)
    return "%d:%02d" % (m, s)


class Api:
    """What the page is allowed to ask for."""

    def __init__(self):
        self.busy = False

    # ---- reading a clip -------------------------------------------------

    def choose(self):
        picked = WINDOW.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False, file_types=VIDEO_TYPES)
        return picked[0] if picked else None

    def locate(self, name):
        """A drop that carried only a filename, no location.

        Nothing here can turn one into the other, so the page falls back to
        asking. Kept as its own call so that stays explicit rather than being
        a silent failure inside the drop handler.
        """
        return None

    def start(self, video):
        if self.busy:
            return "busy"
        self.busy = True
        # Tell the page here rather than leaving it to whoever called. A run
        # begun by a drop is started from the native side, which cannot reach
        # into the page's own click handler -- so without this the work began
        # and the window sat there unchanged, looking for all the world like
        # the drop had been ignored.
        self._js("window.onStarted(%s)" % json.dumps(os.path.basename(video)))
        threading.Thread(target=self._work, args=(video,), daemon=True).start()
        return "started"

    def _work(self, video):
        try:
            if not os.path.exists(video):
                return self._failed("That file is not there any more.", video)

            js, html = report_paths(video)
            run(video, fps=2, out=js, verbose=False, progress=self._progress)
            build(js, html)

            with io.open(js, encoding="utf-8") as f:
                data = json.load(f)
            if not data.get("events"):
                # The commonest way this "breaks" is a clip recorded with the
                # chat panel on the wrong tab, and an empty report explains
                # nothing by itself.
                return self._failed(
                    "No combat lines were found in that clip.",
                    "The log is read out of the chat panel, so a clip recorded "
                    "with it showing Guild or Party chat has nothing in it to "
                    "read.\n\nLeave the panel on the Combat tab and record "
                    "again.\n\nIf it was on Combat, the panel may be somewhere "
                    "other than the bottom-left of the screen, which the "
                    "default crop assumes.")

            self._js("window.onDone(%s)" % json.dumps(html))
        except Exception:                                    # noqa: BLE001
            self._failed("Something went wrong reading that clip.",
                         traceback.format_exc())
        finally:
            self.busy = False

    # ---- what the page shows --------------------------------------------

    def _progress(self, stage, done, total):
        self._js("window.onProgress(%s,%s,%s)" % (
            json.dumps(stage),
            "null" if done is None else int(done),
            "null" if total is None else int(total)))

    def _failed(self, msg, detail):
        self._js("window.onFailed(%s,%s)" % (json.dumps(msg), json.dumps(detail)))

    def _js(self, code):
        try:
            if WINDOW:
                WINDOW.evaluate_js(code)
        except Exception:                                    # noqa: BLE001
            # The window can go away mid-run; that is not worth crashing over.
            pass

    # ---- everything read so far -----------------------------------------

    def history(self):
        folder = os.path.join(app_dir(), "reports")
        rows = []
        if not os.path.isdir(folder):
            return rows
        for name in os.listdir(folder):
            if not name.lower().endswith(".json"):
                continue
            path = os.path.join(folder, name)
            html = path[:-5] + ".html"
            if not os.path.exists(html):
                continue
            try:
                with io.open(path, encoding="utf-8") as f:
                    d = json.load(f)
                hits = [e for e in d.get("events", []) if e.get("kind", "hit") == "hit"]
                out = sum(e["amount"] for e in hits if e["dir"] == "out")
                inc = sum(e["amount"] for e in hits if e["dir"] == "in")
                rows.append({
                    "name": os.path.splitext(name)[0],
                    "html": html,
                    "when": _when(path),
                    "length": _fmt_len(d.get("duration")),
                    "events": len(d.get("events", [])),
                    "net": out - inc,
                    "_sort": os.path.getmtime(path),
                })
            except Exception:                                # noqa: BLE001
                continue                                     # a half-written run
        rows.sort(key=lambda r: r["_sort"], reverse=True)
        for r in rows:
            r.pop("_sort", None)
        return rows

    def read_report(self, path):
        """The report's own HTML, handed straight to the page.

        Pointing the frame at file:// never worked here -- the view stayed
        blank whether or not the address was escaped, and said nothing about
        why. These reports are self-contained by design, with no stylesheet,
        script or image to fetch, so the whole thing can simply be passed
        across and written into the frame. No address, nothing to block.
        """
        try:
            with io.open(path, encoding="utf-8") as f:
                return f.read()
        except Exception:                                    # noqa: BLE001
            _log("could not read report: %s" % traceback.format_exc())
            return None

    def show_strip(self, on):
        """The drop area belongs to the landing view. While a run is going or a
        report is open it would only be taking up room, so the page says when
        it is wanted."""
        try:
            if STRIP is not None and WINDOW is not None and WINDOW.native:
                from System import Action

                def apply():
                    STRIP.Visible = bool(on)
                    if LAYOUT:
                        LAYOUT()

                WINDOW.native.BeginInvoke(Action(apply))
        except Exception:                                    # noqa: BLE001
            pass
        return None

    def open_folder(self):
        folder = os.path.join(app_dir(), "reports")
        if os.path.isdir(folder):
            os.startfile(folder)
        return None


def _release_child_drop_targets(control, tag=""):
    """Stop the browser control claiming drops, at the level it claims them.

    It does not take drops through WinForms at all -- it registers an OLE drop
    target against its own window handle, so Windows offers the drag there, it
    refuses, and the form underneath never hears about it. That is why a clip
    dropped on the title bar worked and the same clip on the page did not.

    Revoking leaves it without a drop target, so Windows walks up to the form,
    which has one. The catch is timing: the browser builds its inner Chromium
    windows after the control exists, so a single sweep at start-up finds only
    the outer one and misses the window the pointer is actually over. Hence
    sweeping more than once, and logging what is found each time.
    """
    import ctypes
    from ctypes import wintypes

    ole32 = ctypes.oledll.LoadLibrary("ole32.dll")
    user32 = ctypes.windll.user32
    NOT_REGISTERED = (0x80040100, -2147221248)

    seen, revoked = [], []

    def revoke(hwnd, cls):
        try:
            ole32.RevokeDragDrop(wintypes.HWND(hwnd))
            revoked.append(cls)
        except OSError as e:
            if getattr(e, "winerror", None) not in NOT_REGISTERED:
                raise

    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def classname(hwnd):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(wintypes.HWND(hwnd), buf, 256)
        return buf.value

    def walk(hwnd):
        cls = classname(hwnd)
        seen.append(cls)
        revoke(hwnd, cls)

        def each(child, _):
            walk(child)
            return True

        user32.EnumChildWindows(wintypes.HWND(hwnd), CB(each), 0)

    try:
        walk(int(str(control.Handle)))
        _log("wire%s: windows %s | released %s"
             % (tag, seen, revoked or "none"))
    except Exception:                                        # noqa: BLE001
        _log("wire%s: sweep failed: %s" % (tag, traceback.format_exc()))


def wire_native_drop(window, on_files, on_hover, api_choose):
    """Let a clip dropped on the window arrive with its real location.

    A web page is handed a dropped file's bytes and its name, never its path,
    and ffmpeg opens the clip by path -- copying a two-gigabyte recording
    somewhere else first is not an answer. So the drop is caught underneath the
    page: WebView2 is told to stop accepting dropped files, and they fall
    through to the window hosting it, which Windows does hand the paths to.

    These handlers run on the UI thread, and that is the danger. Talking to the
    page needs the UI thread too, so doing it from inside one of them
    deadlocks -- the thread waits on a reply only it could deliver. From
    DragOver, which fires many times a second, it wedges the window solid, and
    Windows holds the input loop for the length of a drag, so it takes the
    desktop with it. That is not hypothetical.

    So these do the least possible: set the cursor effect, take the paths, and
    hand anything slower to a thread that is not this one.
    """
    import clr
    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    from System import Action
    from System.Drawing import (Color, Font, Pen, RectangleF, SolidBrush,
                                StringAlignment, StringFormat)
    from System.Drawing.Drawing2D import DashStyle, SmoothingMode
    from System.Windows.Forms import (Cursors, DataFormats, DockStyle,
                                      DragDropEffects, Padding, Panel)

    # "None" is a Python keyword, so the enum member has to be fetched by name.
    DOCK_NONE = getattr(DockStyle, "None")

    global QUIET, LIT, PAGE, EDGE, EDGE_LIT, INK, SOFT
    PAGE = Color.FromArgb(0x0b, 0x0d, 0x11)        # the page's own background
    QUIET = Color.FromArgb(0x13, 0x16, 0x1c)
    LIT = Color.FromArgb(0x16, 0x20, 0x38)
    EDGE = Color.FromArgb(0x3a, 0x41, 0x4e)
    EDGE_LIT = Color.FromArgb(0x6d, 0x8c, 0xf5)
    INK = Color.FromArgb(0xe9, 0xed, 0xf3)
    SOFT = Color.FromArgb(0x77, 0x82, 0x92)

    form = window.native
    _log("wire: window.native is %s" % ("None -- nothing to hook" if form is None
                                        else type(form).__name__))
    if form is None:
        return False

    def off_thread(fn, *a):
        threading.Thread(target=fn, args=a, daemon=True).start()

    def has_files(e):
        try:
            return e.Data.GetDataPresent(DataFormats.FileDrop)
        except Exception:                                    # noqa: BLE001
            return False

    def over(sender, e):
        # Fires many times a second, and is what keeps the cursor showing the
        # drop as allowed. Nothing but the effect may happen here.
        if has_files(e):
            e.Effect = DragDropEffects.Copy

    def enter(sender, e):
        if has_files(e):
            e.Effect = DragDropEffects.Copy
            off_thread(on_hover, True)

    def leave(sender, e):
        off_thread(on_hover, False)

    def drop(sender, e):
        off_thread(on_hover, False)
        if not has_files(e):
            return
        try:
            paths = [str(x) for x in e.Data.GetData(DataFormats.FileDrop)]
        except Exception:                                    # noqa: BLE001
            return
        if paths:
            off_thread(on_files, paths)

    def setup():
        # Controller members, and the controller only accepts changes from the
        # UI thread -- which is what this Invoke is for.
        _log("wire: setup running on the UI thread")

        def attach(target, what):
            target.AllowDrop = True
            target.DragEnter += enter
            target.DragOver += over
            target.DragLeave += leave
            target.DragDrop += drop
            _log("wire: hooked %s, AllowDrop=%s" % (what, target.AllowDrop))

        attach(form, "form")
        # The control has to be hooked too, and this is the part that was
        # missing. It fills the window, so the pointer is always over it and
        # never over the form beneath -- telling it to refuse external drops is
        # not the same as telling WinForms to route the drag anywhere. Without
        # its own AllowDrop the drag is simply never offered to us, which is
        # exactly what the log showed: handlers attached, and not one event.
        for c in form.Controls:
            if "WebView2" not in str(type(c)):
                continue
            try:
                c.AllowExternalDrop = False
            except Exception:                                # noqa: BLE001
                _log("wire: could not set AllowExternalDrop")
            try:
                attach(c, "webview2")
            except Exception:                                # noqa: BLE001
                _log("wire: could not hook the control")
            # The browser owns drops over its own area, out of process and
            # out of our reach -- proved by sweeping for drop targets and
            # finding none of its windows holds one here. So it does not get
            # the whole window: a strip above it stays ours, and that strip
            # can take a drop the way the title bar already does.
            global STRIP
            # Drawn rather than assembled out of labels. Two stacked labels
            # fought over docking order and one ended up hidden behind the
            # other; painting the whole thing puts the text, the border and the
            # highlight under one set of rules.
            outer = STRIP = Panel()
            outer.BackColor = PAGE
            outer.Padding = Padding(20, 16, 20, 12)

            zone = Panel()
            zone.Dock = DockStyle.Fill
            zone.BackColor = QUIET
            zone.AllowDrop = True
            zone.Cursor = Cursors.Hand
            outer.Controls.Add(zone)

            state = {"over": False}
            big = Font("Segoe UI Semibold", 16.0)
            small = Font("Segoe UI", 10.0)

            def paint(sender, e):
                g = e.Graphics
                g.SmoothingMode = SmoothingMode.AntiAlias
                r = zone.ClientRectangle
                pen = Pen(EDGE_LIT if state["over"] else EDGE, 2.0)
                pen.DashStyle = DashStyle.Dash
                g.DrawRectangle(pen, 2, 2, r.Width - 5, r.Height - 5)

                fmt = StringFormat()
                fmt.Alignment = StringAlignment.Center
                title = "Let go to read it" if state["over"] else "Drop a clip here"
                note = "" if state["over"] else "or click anywhere in this box to choose one"
                mid = r.Height / 2.0
                g.DrawString(title, big, SolidBrush(INK),
                             RectangleF(0, mid - 30, r.Width, 34), fmt)
                g.DrawString(note, small, SolidBrush(SOFT),
                             RectangleF(0, mid + 8, r.Width, 24), fmt)

            zone.Paint += paint
            # Without this the panel repaints only the newly exposed sliver
            # when it changes height, so every border it has ever drawn stays
            # on screen -- a ladder of dashed lines down the box.
            zone.Resize += lambda sender, e: zone.Invalidate()

            def lit(on):
                state["over"] = on
                zone.BackColor = LIT if on else QUIET
                zone.Invalidate()

            def clicked(sender, e):
                def ask():
                    path = api_choose()
                    if path:
                        on_files([path])
                threading.Thread(target=ask, daemon=True).start()

            zone.Click += clicked

            def strip_enter(sender, e):
                if has_files(e):
                    e.Effect = DragDropEffects.Copy
                    lit(True)

            def strip_leave(sender, e):
                lit(False)

            def strip_drop(sender, e):
                lit(False)
                drop(sender, e)

            zone.DragEnter += strip_enter
            zone.DragOver += over
            zone.DragLeave += strip_leave
            zone.DragDrop += strip_drop

            # Both are placed by hand rather than docked. Docking lays out in
            # reverse z-order, so bringing the box to the front made the
            # browser lay out first at full size and the box simply cover it --
            # the page was there the whole time, with its list of fights and
            # its finished report hidden underneath.
            global LAYOUT
            c.Dock = DOCK_NONE
            outer.Dock = DOCK_NONE

            def layout(*_):
                try:
                    w = form.ClientSize.Width
                    h = form.ClientSize.Height
                    top = max(190, int(h * 0.5)) if outer.Visible else 0
                    if outer.Visible:
                        outer.SetBounds(0, 0, w, top)
                    c.SetBounds(0, top, w, max(0, h - top))
                except Exception:                            # noqa: BLE001
                    pass

            LAYOUT = layout
            form.Controls.Add(outer)
            layout()
            form.Resize += layout
            _log("wire: drop strip added above the browser view")

            def sweep_again(control=c):
                import time
                for delay, tag in ((1.5, " (after 1.5s)"), (4.0, " (after 4s)")):
                    time.sleep(delay)
                    try:
                        form.Invoke(Action(
                            lambda: _release_child_drop_targets(control, tag)))
                    except Exception:                        # noqa: BLE001
                        pass

            threading.Thread(target=sweep_again, daemon=True).start()

    try:
        form.BeginInvoke(Action(setup))
        return True
    except Exception:                                        # noqa: BLE001
        _log("wire: BeginInvoke failed: " + traceback.format_exc())
        return False


def _when(path):
    import datetime
    return datetime.datetime.fromtimestamp(
        os.path.getmtime(path)).strftime("%d %b, %H:%M")


def _as_url(path):
    """A file:// URL the window will actually load.

    Built by hand this used to be wrong for most reports: clips are named
    things like "op fight.mp4", and an unescaped space makes the address
    invalid, so the view opened blank with nothing to say about it.
    """
    return "file:///" + quote(os.path.abspath(path).replace("\\", "/"), safe="/:")


def main():
    shell = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(
        os.path.abspath(__file__))), "app.html")
    global WINDOW
    api = Api()
    webview.settings["ALLOW_FILE_URLS"] = True
    WINDOW = webview.create_window(
        "MO2 Fight Log", shell, js_api=api,
        width=1240, height=880, min_size=(900, 620),
        background_color="#0b0d11")

    def on_ready():
        # Off a thread and after a beat, not off the page's loaded event: the
        # native window is not necessarily built by the time the page is, and
        # asking too early gets None and hooks nothing at all -- silently.
        import time
        for _ in range(40):
            if WINDOW is not None and WINDOW.native is not None:
                break
            time.sleep(0.1)
        ok = wire_native_drop(
            WINDOW,
            on_files=lambda paths: api.start(paths[0]),
            on_hover=lambda over: None,
            api_choose=api.choose)
        _log("wire: drop available = %s" % ok)
        api._js("window.onDropReady(%s)" % ("true" if ok else "false"))

    threading.Thread(target=on_ready, daemon=True).start()

    # A clip dragged onto the .exe arrives as an argument. That gesture worked
    # before this window existed and is what HOW-TO-USE.txt tells people to do,
    # so it still starts a run -- the window opens and shows it happening.
    clip = sys.argv[1] if len(sys.argv) > 1 else None
    if clip and os.path.exists(clip):
        def kick():
            import time
            for _ in range(60):
                if WINDOW is not None and WINDOW.native is not None:
                    break
                time.sleep(0.1)
            time.sleep(0.8)          # let the page finish loading first
            api.start(os.path.abspath(clip))

        threading.Thread(target=kick, daemon=True).start()

    webview.start()


if __name__ == "__main__":
    import multiprocessing
    # Without this a frozen build re-launches itself for every worker.
    multiprocessing.freeze_support()
    main()
