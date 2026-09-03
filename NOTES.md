# How it works, and the things that broke

The pipeline is short: sample frames, make the text legible, OCR it, parse
what comes back, and settle each number by what the frames agree on. What
follows is the part worth writing down -- every place it was confidently
wrong, and what the measurement said.

Each of these is load-bearing. They all look like simplifications from the
outside, and undoing one costs accuracy quietly.

**AV1 clips.** Most capture tools now produce AV1, which ffmpeg's software
decoder chokes on ("no sequence header"). Hardware decoding is tried first.

**Preprocessing depends on the scene.** Dark backgrounds need upscale plus
contrast; bright ones (sand, snow) need adaptive thresholding, because white
text on pale ground can't be separated by contrast alone. Using the wrong one
destroys the text — thresholding a dark scene lost every timestamp. The crop's
brightness is measured and the method chosen to match.

**Damage numbers fail three ways, and they pull against each other.**
`for 4S[Torso]` is 45 with a letter misread as a digit; `for 225Arms]` is 22
with the bracket misread as a digit; `for 34Vorso]` is 34, where the bracket
merged into the `T` beside it and took no digit with it. The body part is what
separates the last two: clean and flush against the digits means the bracket
became a digit, damaged means it did not. Reading a letter after the number as
proof a digit was eaten was wrong eight times out of eight on a hand-checked
clip — it turned 43 into 4 and 26 into 2.

The third failure has no answer: `for 120041.5;` is a 12 with the rest of the
line smeared across it, and nothing in that string says where the number
stopped. A run of four or more digits is that smear, never a real hit, so the
reading is discarded. That costs nothing — the line is on screen for seconds
and read from every frame in that span, and the neighbouring frames read the
same line cleanly. Guessing instead put a 1200-damage hit in a report.

**Some wrong numbers look perfectly right.** `for 551` is a well-formed
reading; nothing in that line says the `[` of `[Torso]` was read as a `1`.
Neither does `for818`, where debris crowded up against `for` and joined an 18
from the other side. The line itself cannot settle it — but the line is on
screen for seconds and read from every frame in that span, so the true number
comes back repeatedly while a glyph collision happens in one frame and not its
neighbours. A number read once, holding a number the same fighter's line shows
more often nearby, is that number wearing something extra. Names were already
resolved by agreement across frames; the amounts were the part still taking
each frame at its word. Only a strict majority moves anything — two readings
that disagree and are equally attested are left alone rather than guessed at.
Confirmed against the in-game log: `551` was 55, `for818` was 18, `221` was 22.

**Names come back different every frame** (`Qiade`, `Qlade`). All readings are
clustered by similarity and collapsed onto the most frequent spelling, with
`i`/`l` folded together since that's the most confused pair in this font.

**A lost space is not a lost line.** OCR drops the space in front of `for` all
the time — `hit youfor 26`, `hit Steetchfor 38` — and requiring a word
boundary there threw away 180 of 2624 lines on one clip, 7% of everything
read, including hits no other frame recovered. The boundary is now preferred
rather than required.

**A misread apostrophe can invent a player.** `ALADIM's Outburst` comes back
`ALADIM:s Outburst` often enough to matter, and when the possessive doesn't
match, the name match starts after the mark and reads the leftover `s` as part
of the attacker — putting `sOutburst` in the roster as though it were a person
and filing ALADIM's damage under it. The possessive now accepts the `:` and
`;` that OCR substitutes.

**OCR emits bytes Windows' default codec can't decode.** That silently killed
whole frames and lost whichever part of the fight they covered. Everything
speaks UTF-8 explicitly now.

**Healing and spells were being thrown away.** The parser only knew
"X hit Y for N", so `Karyna's Lesser Heal heals you for 24` matched nothing and
vanished -- along with the healer, who never appeared as a participant at all
if healing was all she did. Worse, the incoming pattern makes the hit verb
optional, so a line ending "...heals you for 19" was one small change away from
being read as 19 points of damage from somebody called "heals". Healing now
parses as its own kind of event, kept out of the damage figures, and abilities
that are not weapon nouns count as spell damage. One clip gave up 96 healing
and another 129 that had simply been discarded.

**The game draws combat text twice.** MO2 paints damage numbers over the
world as well as writing them to the log, and both read as combat lines. That
makes a generous crop worse than a tight one: widening the default by 100px
upward swept in the floating numbers and invented hits on a hand-checked clip,
while a crop 3px too high clipped real ones. Reading the log is therefore
sensitive to crop geometry in both directions, which is why the tuned default
is used whenever it works.

**Finding the log on someone else's screen.** The default crop is expressed as
fractions of the frame, so other resolutions are fine, but a moved chat panel
or an unusual UI scale is not. The tool now peeks at four frames first; only if
no combat text is where it expects does it OCR whole frames, keep the lines
shaped like combat, and take their bounding box. The log is a left-aligned
column, so lines whose left edge sits near the common one are the log and the
floating damage numbers scattered elsewhere are not. A detected crop reads a
little worse than the tuned one -- roughly 95% against 100% on the checked clip
-- so it is a fallback, never a replacement.

**Half the time went on one core.** Preprocessing and OCR both handle each
frame independently, but ran one after another in a single thread: on a
16-core machine preprocessing alone was 50% of the runtime and OCR another
26%, with the rest of the box idle. Both now fan out across cores, which
roughly halves the wall clock and changes no number.

Two other ideas measured and rejected. Writing the temporary frames with
ffmpeg's fastest PNG setting saved nothing -- the cost there is decoding AV1,
not encoding PNGs, since a 5-minute 60fps clip means decoding 18,000 frames to
keep 600. And halving the upscale before OCR, which would have cut 56% of the
pixels out of both slow stages, took a hand-checked clip from 100% to 79%:
`24` read as `4`, three hits lost entirely. The 3x upscale is load-bearing.

**A fixed noise threshold deleted most of a fight.** Requiring three readings
before an event counts works when a line is read 20 to 40 times, which is what
both hand-checked clips do. A fast-scrolling fight is not like that -- one
30-second clip caught each line once or twice, and the rule quietly took it
from 139 damage down to nothing while every validation clip stayed at 100%. How
often a line gets read is a property of the clip, not a constant, so the cutoff
is now a quarter of whatever is typical for that clip: 5 where lines are read
23 times, 1 where they are read twice. Found only by running the packaged build
against a clip that had not been touched in hours -- agreeing with two similar
clips is not the same as being right.

**A repeated hit vanished into the first one.** Readings that lost their
timestamp were folded into whichever timestamped hit shared their damage and
target, no matter how far apart -- so hitting someone for 30 twice in a fight
recorded one hit. The fix is to ask whether the timestamped line was still on
screen when the untimed reading was taken, which only became a meaningful
question once the two clocks agreed; attempted against the old skew it produced
phantoms and was abandoned. Rescued hits are then held to a higher bar than
timestamped ones: a real one leaves a full line's worth of readings behind it,
while the stragglers that fall just past the window come in threes and fours.
On the hand-checked clip the genuine rescue was seen 26 times and every false
one 3 to 5, so there is a wide gap to cut in. This took the same clip from 92%
to 100%.

**And then the window that saved it started inventing hits.** The rule above
asks whether the timestamped line was still on screen, and answered it with a
fixed twelve seconds. How long a line stays up is not a property of the line
though -- it is a property of how busy the fight is. Nothing new arrives to
push it off and it just sits there. Measured across four fights, lines stayed
on screen for 16, 24, 31, 40, even 78 seconds. Every reading past the twelfth
second was then treated as a fresh sighting, and a run of them promoted into a
second hit: on one clip 14 of 54 events were the same hit read twice, each
landing exactly 12.5 seconds after its twin. The in-game log confirmed none of
them happened.

The window is gone. A line's readings are contiguous -- it appears, stays, and
scrolls away, and never comes back -- so a stretch of identical readings is one
line for as long as it keeps being read, however long that is. Two things split
a stretch: distinct timestamps, and copies. Two readings of the same text from
the same frame are two lines on screen together, which one line cannot be, and
that is the witness for a line whose timestamp OCR lost -- without it three
real hits went missing across the other clips. Removing 19 phantoms and adding
nothing, across four fights.

Two traps on the way, both caught by re-reading every changed event rather than
the count. A timestamped reading is placed at the moment the hit *happened*, so
all sixty of its readings share one instant: asking when a line was last *seen*
has to use the frame, not the event time. And a gap in the reading is not proof
the line went away -- on a clip whose log OCRs patchily, nineteen seconds passed
with nothing parsing while the line sat there in plain sight.

**Two clocks, six seconds apart.** Lines whose `[hh:mm:ss]` survives OCR carry
wall time; the rest carry the frame they were read from. Reconciling them means
knowing when the recording started, and every timestamped line offers an
estimate -- but a line is read from every frame across its ten seconds on
screen, all carrying the same timestamp, and each later reading implies an
earlier start. Averaging over readings put the estimate about half a window
out, so every timestamped hit floated ~6s late and drifted past the untimed
hits around it. A hand-checked fight came back with two hits transposed, which
is how it surfaced -- the totals were right, only the order was wrong. Each
line now collapses to its first sighting before the median is taken.

**A single bad frame invented a hit.** OCR read `for99[Left Limb]` off one
frame of a fight where no 99 was ever dealt, and it went straight into the
totals. The defence was already in the data and unused: a real log line sits on
screen for seconds and is read from 12 to 40 frames, while a fabrication
appears exactly once. Events now have to be seen in at least three frames
(`--min-seen`). On the hand-counted clip this removes nothing at all, and on
the clip that reported the phantom it removed three readings seen once or
twice -- with the lowest surviving event seen 12 times, so the threshold sits
in a wide empty gap rather than on a boundary.

**Strict patterns threw away legible text.** The parse gate required a literal
digit after `for`, a space after `for`, and the exact word `you`. OCR had in
fact read `You hit HighPsyche for O[Torso]` and `for18[Right Limb]` correctly;
the patterns rejected them. On a heavily degraded clip that discarded 100 of
937 lines. Matching each surrounding word as a small class -- `for`/`foi`/`f0r`,
`you`/`ypu` -- with tolerant separators recovered 64% of them.

**Which then over-corrected.** Letting the separator vanish meant a stray
letter in the gap got read as part of the number: `forz33` became 233, since
`z` doubles for `2`. Stripping any leading letter fixed that and immediately
broke `for S6`, where the `S` really is a `5` -- turning 56 into 6. The space
is the discriminator: noise crowds against `for`, a real number keeps its
space and merely has a letter-shaped digit inside it. Ground truth caught
this; the parse-rate metric alone called it an improvement.

**Ranged attacks name a weapon, not a player.** MO2 writes `Ith's arrow hit
you for 19`. The possessive pattern demanded a space after the apostrophe and
OCR routinely loses it, so the line fell through to the generic one, which read
`arrow` as the attacker -- inventing a player *and* double-counting the hit
against the real archer. Incoming damage ran 17% high. Weapon nouns can no
longer be names, and a hit whose owner was lost merges into a named hit
carrying the same damage nearby.

**A misreading that repeats becomes a player.** Names seen three or more times
are taken as real, on the assumption that misreadings are random and will not
recur. Systematic ones are not random: `CLAUDEMASTER` read as `CLAUDEMASrER`
on every single frame, cleared the bar, and split one player's damage across
two names. Candidate names are now folded against each other first, keeping
whichever spelling was seen most.

**Two Pythons, one launcher.** The .bat said `python` and trusted PATH.
Explorer resolves that differently from a terminal, so the tool worked when run
by hand and died on `No module named PIL` when a video was dropped on it. It
now picks the first interpreter that can actually import what it needs.

**One PowerShell process, not one per frame.** Process startup dominated the
runtime — batching took a clip from 4–5 minutes to about 90 seconds.
