# MO2 Fight Log

Turns a Mortal Online 2 gameplay recording into a fight report: who hit you for
what, when, and how it went.

Mortal Online 2 never writes its combat log to disk — it only draws it on
screen — so the numbers are read back off the video frames with OCR.

Windows only. The text recognition is the engine built into Windows, which is
why there's nothing to install.

## Use it

**Drag your video onto `DROP-VIDEO-HERE.bat`.**

That's the whole thing. Roughly 40 seconds per minute of footage. The report
opens when it's done, and lands in a `reports` folder next to your clip.

By hand, if you'd rather:

```
python mo2log.py "clip.mp4" --fps 2 --out fight.json
python make_report.py fight.json
```

**Leave the chat panel on the Combat tab when you record.** Everything is read
out of that panel — a clip taken with it showing Guild or Party chat has
nothing in it to read, and the report comes back empty.

## Does it work

Measured against hits counted by hand, on 1080p with the log unobstructed:

| Target | Counted by hand | Read | Result |
|---|---|---|---|
| Aims | 5 hits / 187 | 5 hits / 187 | exact |
| Qlade | 10 hits / 194 | 10 hits / 194 | exact |
| BIGBROker | 7 hits / 218 | 7 hits / 218 | exact, and in the right order |

Damage totals are more reliable than swing counts, and when it's wrong it
undercounts rather than over-counts — every rule drops a doubtful reading
instead of guessing at it. Heavier compression, a smaller UI scale or a busy
log all read worse. Treat 100% as the ceiling, not the expectation.

## The report

One self-contained HTML file — email it, post it, it opens anywhere with no
internet.

Drag either handle under the chart to zoom into part of the fight, and click
the eye beside a name to take them out of the count; the chart and every number
redraw for what's left. Click a parry or counter stat to mark those moments on
the chart, and hover a mark for when it happened.

## Options

- `--fps 3` — sample more often. Catches fast-scrolling lines, runs slower.
- `--crop x,y,w,h` — where the log sits. Default assumes bottom-left.
- `--players Name1,Name2` — supply real names; every OCR spelling snaps to them.
- `--keep-frames` — leave the extracted frames on disk to see what OCR was given.

## If it reads badly

First check the crop actually contains the log:

```
ffmpeg -i yourclip.mp4 -vf "crop=998:280:0:756" -frames:v 1 crop_check.png
```

If the log isn't fully inside that PNG, pass your own `--crop` (ffmpeg wants
`w:h:x:y`, the flag wants `x,y,w,h`).

To see what OCR was actually given, `python dump_lines.py "clip.mp4" 2` prints
every distinct line it read.

## Giving it to someone else

`MO2-Fight-Log.zip` is a self-contained build — Python, Pillow, numpy, ffmpeg
and the report template all bundled. They unzip it and drag a video onto
`MO2FightLog.exe`. Nothing to install.

Rebuild after changing anything:

```
python -m PyInstaller --noconfirm --distpath dist --workpath build MO2FightLog.spec
```

## How it actually works

[**NOTES.md**](NOTES.md) is the interesting part: the pipeline, and every place
this was confidently wrong — a crop 3px too high that clipped real hits, a
noise threshold that deleted a whole fight, and why a perfectly well-formed
`for 551` was really 55.

Layout: `mo2log.py` is the pipeline, `preprocess.py` picks the image treatment,
`parse.py` turns OCR text into events, `make_report.py` bakes a JSON into
`viewer.html`. The rest are debugging tools — `dump_lines.py`, `dump_raw.py`,
`find_log.ps1`, `profile_run.py`.
