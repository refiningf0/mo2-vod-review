# MO2 VOD Review

Turns a Mortal Online 2 gameplay recording into a fight report: who hit you for
what, when, and how it went.

Mortal Online 2 never writes its combat log to disk — it only draws it on
screen — so the numbers are read back off the video frames with OCR.

Windows only. The text is read by PaddleOCR's models, running on your own
machine and shipped inside the app, so there's nothing to install, nothing to
download and nothing sent anywhere. On four fights checked line for line against
the in-game log it found all 109 hits and invented none.

## Use it

**Open the app and drop a clip on it.** Roughly half a minute per minute of
footage. The report opens in place when it's done, and every report lands in
the `reports` folder beside the app, whatever folder the clip came from.

**Leave the chat panel on the Combat tab when you record.** Everything is read
out of that panel — a clip taken with it showing Guild or Party chat has
nothing in it to read, and the report comes back empty. This is far and away
the most common reason it "doesn't work".

## Does it work

Measured against hits counted by hand, on 1080p with the log unobstructed:

| Target | Counted by hand | Read | Result |
|---|---|---|---|
| Aims | 5 hits / 187 | 5 hits / 187 | exact |
| Qlade | 10 hits / 194 | 10 hits / 194 | exact |
| BIGBROker | 7 hits / 218 | 7 hits / 218 | exact, and in the right order |

When it's wrong it undercounts rather than over-counts — every rule drops a
doubtful reading instead of guessing at it. Heavier compression, a smaller UI
scale or a busy log all read worse. Treat 100% as the ceiling, not the
expectation.

## The report

One self-contained HTML file — email it, post it, it opens anywhere with no
internet.

Drag either handle under the chart to zoom into part of the fight, and click
the eye beside a name to take them out of the count; the chart and every number
redraw for what's left. Click a parry or counter stat to mark those moments on
the chart, and hover a mark for when it happened. Click the title to play the
clip it was read from.

## From source

```
python app.py                             # the window
python mo2fightlog.py "clip.mp4"          # no window, straight to a report
python dump_lines.py "clip.mp4" 2         # every line OCR read, when it reads badly
```

`mo2log.py --crop x,y,w,h` if the log isn't bottom-left, `--players A,B` to snap
every OCR spelling to real names, `--fps 3` to sample more often.

Build the app with
`python -m PyInstaller --noconfirm --distpath dist --workpath build MO2VODReview.spec`,
then zip `dist/MO2-VOD-Review` — Python, ffmpeg and the report template are all
bundled, so whoever you send it to installs nothing.

## How it actually works

[**NOTES.md**](NOTES.md) is the interesting part: the pipeline, and every place
this was confidently wrong — a crop 3px too high that clipped real hits, a noise
threshold that deleted a whole fight, a line that sat on screen through a lull
and got counted twice, and why a perfectly well-formed `for 551` was really 55.
