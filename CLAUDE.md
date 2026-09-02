# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Mortal Online 2 never writes its combat log to disk — it only draws it on screen — so this reads the log back off gameplay video with OCR and turns it into a report. Windows only: the OCR engine is the operating system's own, which is why the packaged app has nothing to install.

`README.md` is the design record and explains *why* each rule exists, with the measurements behind it. Read it before changing the parsing or the crop.

## Commands

```bash
# The whole thing, the way the .exe runs it (writes into <clip folder>/reports/)
python mo2fightlog.py "clip.mp4"

# The two stages by hand, when you want an intermediate JSON
python mo2log.py "clip.mp4" --fps 2 --out fight.json
python make_report.py fight.json          # -> fight.html

# Every distinct line OCR reads from a clip -- the first thing to run when
# events go missing and you need to see what the parser was actually given
python dump_lines.py "clip.mp4" 2

# Build the distributable (needs ffmpeg/ffprobe at C:\ffmpeg)
python -m PyInstaller --noconfirm --distpath dist --workpath build MO2FightLog.spec
```

Useful flags on `mo2log.py`: `--crop x,y,w,h`, `--players Name1,Name2` (snaps every OCR spelling to real names), `--min-seen N`, `--keep-frames`.

## There are no tests

Nothing in this repo is verified by assertion, and the failure mode is silent — a change can read correctly and still drop or invent events. Verify like this instead:

1. **Cache the OCR once per clip**, then iterate the parser against the cache. A full run is minutes, and OCR is not deterministic between runs, so the same clip differs slightly pass to pass. Comparing two full runs will show noise you did not cause.
2. **Diff old parser vs new over every cached line**, and read every changed reading. "169 changed" means nothing until you have looked at them.
3. **Re-parse an unrelated fight** and require it to come out identical. That is what proves you changed only what you meant to.
4. **Ground truth is the in-game log.** The user can screenshot it. That is how `551` was confirmed to be 55 and `Pany` to be a parry — no amount of reasoning about the regex settles it.

## Architecture

One pass, in `mo2log.run()`:

```
probe        ffprobe for dimensions and duration
crop         default_crop(), and only if nothing is found there, detect_crop()
extract      ffmpeg -> PNG per frame, hardware decode first (AV1)
preprocess   prep() per frame, fanned across cores
OCR          ocr_batch.ps1, one PowerShell process per core over disjoint slices
parse_line   one line of OCR text -> one event, or None
canonical_names   every OCR spelling of a name collapsed onto one
timestamp shift   wall-clock lines and frame-timed lines reconciled onto one clock
dedupe       many readings of one log line -> one event (calls _settle_amounts)
seen floor   drop events read fewer times than is typical for this clip
```

| File | Owns |
|---|---|
| `mo2log.py` | the pipeline, ffmpeg/OCR orchestration, crop detection |
| `parse.py` | OCR text → events; the regexes, name clustering, dedupe, consensus |
| `preprocess.py` | choosing and applying the image treatment per frame |
| `make_report.py` | bakes a JSON into `viewer.html` as `window.__BAKED__` |
| `viewer.html` | the whole report — markup, CSS and JS in one file, no network |
| `mo2fightlog.py` | entry point; `report_paths()` decides where output goes |
| `DROP-VIDEO-HERE.bat` | finds a usable Python, then calls `mo2fightlog.py` |

## Load-bearing decisions

Each of these was arrived at by something breaking. Undoing one looks like a simplification and costs accuracy.

- **Do not widen the crop.** MO2 paints damage numbers over the world as well as into the log, and both read as combat lines. 100px more at the top invented hits; 3px too high clipped real ones. Sensitive in both directions.
- **The 3× upscale before OCR is load-bearing.** Halving it removes 56% of the pixels from the two slowest stages and took a hand-checked clip from 100% to 79%.
- **The noise floor is relative, not fixed.** How many times a line gets read is a property of the clip. A fixed cutoff of 3 took a fast-scrolling fight from 139 damage to nothing.
- **Numbers are settled across frames, not per line.** `551` is a well-formed reading; nothing in that line says it is wrong. A line is read 6–25 times, so a reading seen once that is another reading with one character wedged into it is that reading. Only a strict majority moves anything.
- **Reject rather than guess.** A run of 4+ digits is a smear, not a hit — the reading is dropped and the neighbouring frames supply the value. Guessing put a 1200-damage hit in a report.
- **Names and flags snap to a vocabulary.** A misspelt flag is a miss, not a near miss: parries are counted by matching the name, so `Pany` was a parry that never happened.
- **The word boundary before `for` is preferred, not required.** Requiring it discarded 180 of 2,624 lines because OCR loses that space so often.
- **Everything speaks UTF-8 explicitly.** OCR emits bytes the Windows default codec cannot decode, which used to kill whole frames silently.
- **Output location is decided once**, in `report_paths()`. It was decided in two places before and they disagreed, so reports landed in the tool's own folder half the time.

## The report

`viewer.html` is a template with the data baked in at build time, so a report opens with no server and no network. Everything on the page derives from `counted()` — the events inside the time window, minus anyone excluded — so the chart is redrawn rather than shaded when either changes. Tooltip handlers are bound once, outside the redraw.
