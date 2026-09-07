# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Mortal Online 2 never writes its combat log to disk — it only draws it on screen — so this reads the log back off gameplay video with OCR and turns it into a report. Windows only: the OCR engine is the operating system's own, which is why the packaged app has nothing to install.

`README.md` is the design record and explains *why* each rule exists, with the measurements behind it. Read it before changing the parsing or the crop.

## Commands

```bash
# The whole thing, the way the .exe runs it (writes into <tool folder>/reports/)
python mo2fightlog.py "clip.mp4"

# The two stages by hand, when you want an intermediate JSON
python mo2log.py "clip.mp4" --fps 2 --out fight.json
python make_report.py fight.json          # -> fight.html

# Every distinct line OCR reads from a clip -- the first thing to run when
# events go missing and you need to see what the parser was actually given
python dump_lines.py "clip.mp4" 2

# Build the distributable (needs ffmpeg/ffprobe at C:\ffmpeg)
python -m PyInstaller --noconfirm --distpath dist --workpath build MO2VODReview.spec
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
- **Lift the mid greys, never push contrast.** Contrast drives values away from the middle, so anything near black or white clips -- and an anti-aliased stroke is made of the mid greys at its edge, which are what separate an `8` from a `B`. A hit whose line was crisp to the eye came back as `You hit Moocifer for`; the untreated crop read it whole. Gamma lifts the middle without moving either end and costs nothing: five of five hits on the fight the in-game log covers, still eleven of eleven on the one verified line for line, and `CLAUDEMASTER` stops reading as `CLAUDEMASrER`.
- **The pipeline can read every frame through several treatments** -- `TREATMENTS` in `mo2log.py` -- and which one a reading came from travels with it, because two readings of one line would otherwise look exactly like one line shown twice and every hit would double. One treatment is enough now that it stopped destroying text; adding `plain` back is a one-word change if a clip ever needs it.
- **The 3× upscale before OCR is load-bearing.** Halving it removes 56% of the pixels from the two slowest stages and took a hand-checked clip from 100% to 79%.
- **OCR gets its own worker count, and it is not the core count.** OCR spends its life waiting on the system's OCR service, so it keeps gaining well past where a CPU-bound stage stops: over 145 frames, 8 workers took 20s and 16 took 5s. Lowering the CPU-bound number for CPU reasons must not drag this one down with it.
- **Extract is split across four ffmpeg processes**, each decoding its own stretch. A frame's time comes from its number, never its position in the list — a segment coming up one frame short would otherwise slide every later frame half a second early, and every mark on the report's timeline with it.
- **The noise floor asks how many times, which is the wrong question twice.** A long line -- name, number, three flags -- keeps its ends and loses its middle, so a real hit can give up its number a handful of times while sitting on screen as long as any other; and a line still on screen when the recording stops was never going to be read often. Both are let back in on `span` and `last`, and both need a recovered timestamp first. That is what a stray misreading never has.
- **The noise floor is relative, not fixed — but only downwards.** How many times a line gets read is a property of the clip. A fixed cutoff of 3 took a fast-scrolling fight from 139 damage to nothing. It is capped at 2 going the other way: on a clip whose lines are bimodal (a few read 27–60 times, several read 2–6) the median sits in the high cluster and a quarter of it cuts through the real hits, deleting a clean `61[Torso]` and a player with it. Improving the readings *raises* that median, so better parsing made the report smaller until the cap went in.
- **How long a line stays on screen is not a constant.** It depends on how busy the fight is: measured across four fights, lines sat there for 16 to 78 seconds. Nothing may assume a fixed window. A stretch of identical readings is one line for as long as it keeps being read; only distinct timestamps, or two copies in a single frame, split it. A fixed twelve-second window used to fabricate a quarter of a report.
- **Numbers are settled across frames, not per line.** `551` is a well-formed reading; nothing in that line says it is wrong. A line is read 6–25 times, so a reading seen once that stands one character away from another reading is that reading — one character wedged in (`55`→`551`), swapped (`28`→`98`, `0[Parry]`→`9[Pany]`), or lost (`for 23`→`forQ3`→3). Only a strict majority moves anything. A swap needs two digits: replacing the only character of a one-digit number replaces the number, which turned a stray `for'S` into a hit for 0 that never happened.
- **A bracket read as a digit inflates the number and duplicates the hit.** `for 64[Torso]` comes back as `for 641 Torso]`, which is both a wrong number and, since other frames read the line whole, a second hit beside the real one. Recovered by the body part behind it -- but only across a gap when the last digit is `1`, since a dropped bracket leaves a number that is already right.
- **Reject rather than guess.** A run of 4+ digits is a smear, not a hit — the reading is dropped and the neighbouring frames supply the value. Guessing put a 1200-damage hit in a report.
- **A flag reading that is all real vocabulary outvotes one that is not.** OCR drops the middle of a run of flags and welds the ends together -- `[Left Limb][Armor Pierced]` becomes `[Left Pierced]` -- and that wreckage reads the same way frame after frame, so it wins a plain majority. It won 12 to 3 on one hit. An unrecognised word is still kept when it is all there is, in case it is a tag the vocabulary lacks.
- **A spell or a weapon is not a player.** `Ith's Corrupt hit you` with the apostrophe mangled reads the spell as the attacker, putting a DoT called Corrupt in the roster and a second copy of the hit beside the real one. Caught by the log's own grammar: a *thing* of someone's **hit** you, a *person* **hits** you. Without that distinction the looser pattern parsed `bang hits you` as someone called `ba`.
- **A name that is the tail of a longer one usually is not.** OCR loses the front too, handing back `dooty` for `Troglodooty` — 3 readings against 65, still enough to clear `anchor_min` and stand as a fourth player in a three-player fight, with a duplicate of one real incoming hit filed under it. But a tail cannot fold on spelling: `Bloodletter` sits at the end of `AkYabanBloodletter` and is a different mob. What separates them is where the cut falls — a tail starting on a capital is a word the game meant, one starting mid-word is OCR damage (`_cut_midword`).
- **A name that is the front of a longer one is that name.** OCR gives up on a long name and hands back `Father` for `Father of Bones`. Left alone the fragment becomes a player of its own, and the readings under it never join the real line -- so hits from before the recording started, correctly placed at a negative time and dropped, came back inside the report as three that never happened. Folding is one-way, into the longer name, and needs the fragment to be a fifth as common.
- **One line read two ways is caught on the line, not the fight.** Settling numbers only reconsiders a reading seen once *in the whole clip*, so a phantom `24[Torso][Handle]` survived it for weeks — 24 is a real amount elsewhere in that fight. Two events sharing an exact log timestamp, fighter and flags are one line; when their amounts are one glyph apart the rarer reading is dropped. Fired exactly once across five clips, and took that clip to 430 over 15 hits against a log of 430 over 15.
- **Mob names that look alike are not alike.** `AkYabanFlayer`, `AkYabanSnatcher` and `AkYabanBloodletter` are different mobs, and `Bloodletter` is a fourth, unrelated one. Nothing may fold names together on spelling. Two readings are the same line only on evidence -- same second, same amount, same flags.
- **Names and flags snap to a vocabulary.** A misspelt flag is a miss, not a near miss: parries are counted by matching the name, so `Pany` was a parry that never happened.
- **The word boundary before `for` is preferred, not required.** Requiring it discarded 180 of 2,624 lines because OCR loses that space so often. The same goes for the space before `hits`: `CTOUKhits you for 24[Torso]` was read 31 times on one clip and parsed none of them, leaving that player and every hit they landed out of the report. Where the space is missing the whole word `hits` must be there, or the name swallows it and every attacker ends up called something ending in "hits".
- **Everything speaks UTF-8 explicitly.** OCR emits bytes the Windows default codec cannot decode, which used to kill whole frames silently.
- **Output location is decided once**, in `report_paths()`, and it is the tool's own `reports` folder. It was decided in two places before and they disagreed; then it followed the clip, which left a `reports` folder in every folder a clip had been dragged out of.

## The report

`viewer.html` is a template with the data baked in at build time, so a report opens with no server and no network. Everything on the page derives from `counted()` — the events inside the time window, minus anyone excluded — so the chart is redrawn rather than shaded when either changes. Tooltip handlers are bound once, outside the redraw.
