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

**Then the relative threshold climbed into the real hits.** A quarter of the
median is the right cutoff only while the readings cluster around one number.
On a clip where the fight paused, eight lines sat on screen for the rest of the
recording and were read 27 to 60 times each, while four more flashed past and
were read 2 to 6 times. The median landed inside the high cluster and the
cutoff came out at 7 -- straight through the middle of the short-lived lines.
It deleted a `61[Torso]` and a `51[Left Limb]` that OCR had read cleanly, and
took a player off the roster with them.

Worse, it got there by improving. Correcting two misread numbers merged their
readings into the events they belonged to, which removed the low-read events
holding the median down and pushed the cutoff up. A better reading was making
the report smaller, which is the opposite of a threshold's job.

So the cutoff still falls with the clip -- that is the direction the
fast-scrolling fight cares about -- but it no longer rises past two. Across
every clip measured against an in-game log, everything the threshold rightly
dropped had been read exactly once; nothing needed a cutoff above two to catch
it. On the dungeon clip the cap also handed back two heals, `Xantheria's Lesser
Heal for 25` and `DiscoBaller's Lesser Heal for 31`, that the old cutoff of 4
had been quietly deleting.

**A misread digit is not always an extra one.** Settling numbers across frames
only knew one failure: a character wedged into the number, `55` reading `551`.
Against two clips with a screenshot of the in-game log beside them, the commoner
failure turned out to be a character *swapped* -- `28[Torso]` came back `98`,
`27[Left Limb]` came back `97`, `0[Parry]` came back `9[Pany]` -- or a character
*lost*, where `for 23` read `forQ3` and the stray letter was stripped as noise,
leaving 3. Each of those was a phantom hit standing beside the real one.
Widening the rule to all three shapes removed them and changed nothing else.

One character swapped is a wider net than one inserted, though, and it needs
the length to stay honest: a one-digit number has no shape left to go wrong, so
swapping its only character just replaces the number. Allowing that turned
`for'S` -- the wreckage of a 35 the report had already read correctly -- into a
hit for 0 that never happened. Swaps now need two digits to work with.

**The stubborn phantom was scoped wrong, not shaped wrong.** One hit survived
all of that: a `24[Torso][Handle]` on Alwane that the in-game log says was a
`21[Torso][Handle]`, sitting in the report beside the real one and putting the
fight 24 damage over for weeks. Widening the shapes did nothing for it, because
settling numbers only ever reconsiders a reading seen *once in the whole
fight* -- and 24 is a perfectly real amount in that fight, landed twice on
other targets. The count was 17, not 1, so the rule never looked.

The line's own timestamp is what identifies it. Both events carried
`[02:14:15]`, the same target and the same body part, and differed only in the
number: 21 read 17 times, 24 read 4. Two events agreeing on the second, the
fighter and the flags are one line unless MO2 printed two hits on one target
inside one second with identical flags -- and when their numbers stand one
glyph apart as well, the rarer one is the misreading. The loser has to be well
under half the winner, since two hits that both happened are on screen together
and get read about equally often.

Across five clips this fired exactly once, on that hit. Every other line in
every clip carries a single amount at its timestamp, so there was nothing else
for it to catch and nothing for it to damage. The clip now reads 430 over 15
hits against a log of 430 over 15 -- hit for hit.

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

**And a name with its front missing became a fourth player.** OCR gives up on
the start of a long name as readily as the end, handing back `dooty` for
`Troglodooty` -- 3 readings against 65, still enough to clear the bar. It stood
in the roster of a three-player fight as a fourth, and carried a duplicate of a
real hit with it: at the same second, the same 14, the same `[Torso]`, one copy
filed under `Troglodooty` and one under `dooty`, so that hit was counted twice.

A tail cannot be folded on spelling the way a front can. A real name sits at
the end of a longer one often enough to matter -- `AkYabanBloodletter` and
`Bloodletter` are different mobs, and folding those together would be worse
than the problem. What separates them is where the cut falls. MO2 builds mob
names by running capitalised words together, so a tail that begins on a capital
is a word the game meant; a tail that begins in the middle of a word is
damage. `dooty` starts inside `Troglodooty`, `Bloodletter` starts a word of its
own. The two readings merged, seen 8 and seen 3 becoming one hit seen 11.

**A missing space hid a whole player.** The gate for an incoming hit wanted a
name, then whitespace, then "hits". OCR loses that space exactly as readily as
the one in front of "for" -- `CTOUKhits you for 24[Torso]` came back 31 times
on one clip and not one of them parsed. CTOUK was not in the roster, and
neither was a single hit they landed; the same went for most of what OPE111HUK
threw. Eight real incoming hits, read between 14 and 51 times each, were being
discarded on a space.

Letting the space vanish needs care in one direction. With it gone the name is
free to swallow the word behind it, and every attacker in the log ends up
called something ending in "hits" -- so where the space survives, "hit" may
lose its own tail as before, and where it does not, the whole word has to be
there. Diffed across 17,822 cached OCR lines from seven clips: 621 readings
newly parsed, none lost, none altered. Incoming damage on that fight went from
135 to 322, and every other clip came out byte-identical.

**The reader was the bottleneck after all.** Tesseract read exactly what
Windows OCR read, and that was taken to mean the engine did not matter. It meant
Tesseract did not. PaddleOCR's PP-OCR models, run locally through RapidOCR on
ONNX Runtime, were scored against the same four in-game logs -- 109 hits across
dungeon and outdoor fights. Windows OCR found 103; PaddleOCR found 109 and
invented none, and every damage total came out exact. Most of the gain is the
timestamp: it keeps `[hh:mm:ss]` on nearly every line, and the timestamp is
what tells five identical `hits you for 0[Parry]` apart when the rest of the
line cannot. It reads the raw crop; every treatment tried under it did the same
or worse.

Two of its defaults were wrong for this. It checks every line for being upside
down, and that check was occasionally turning over a line it had read
correctly -- off, 108 of 109 became 109. And it enlarged each crop 2.6x before
looking for text; every value down to no enlargement still found all 109, so it
now stops at 400px on the short side, about half the time. It is still slower
than Windows OCR -- roughly a minute a clip against forty seconds -- and it
drops spaces far more (`Youhit`, `[RightLimb]`), which the parser had to learn
to read through. Its one known weakness is text on sunlit sand.

**And it repeats its mistakes.** A slip Windows OCR made once, PaddleOCR can
make three or four times, because it reads every line five and ten times as
often. On one run of one fight it read `You hit Zayy for 40l` -- the bracket as
a letter, which becomes a 1 -- three times beside a `40[Left Limb]` read 37,
and did the same to a 39. The rule that folds a bracket back into its number
only looked at readings seen once, so both stood as hits for 401 and 391, and
the fight read 792 damage over. The same clip read again came out clean, which
is how OCR non-determinism looks from the outside. The fold now also takes a
reading that is its neighbour plus one character on the end with no body part
behind it, whenever the neighbour was read more than twice as often. Replaying
that misreading three, five and eight times over into a clean run, it is caught
every time; across nine real fights it changes nothing.

**A merged clip has more than one clock.** The log's `[hh:mm:ss]` and the
video's own timeline differ by a constant -- when the recording started -- and
every timestamped line gives one estimate of it. That holds inside a single
recording. Join two together and there are two constants, separated by however
long the player went without recording: on one 4v8 the parts were 33 minutes
apart, and crossed midnight besides. The median across the whole file then
picks whichever part is longest and flings every event from the others
thousands of seconds outside the clip, where the range check quietly deletes
them. That report opened with two silent minutes, while the frame at forty
seconds had eight hits on screen and two players who appeared nowhere in it.

The estimates are now grouped and each line shifted by its own group. The bar
for being a separate recording is 10% of the lines, not merely a gap: within
one recording stray estimates sit as much as a minute from the rest -- a
misread timestamp, or a line whose first sighting came late -- and those have
to be absorbed by the nearest real group, which is what they got before. The
4v8 went from 70 events to 127, recovering POWERSTROKE, OBESOdeBOSTA and
DERKKK, and every one of the eight lines in that frame now appears at the right
second. Nine unmerged fights came out identical, and a merged clip whose parts
were recorded back to back was untouched.

**Creatures do not hit you, they bite you.** The incoming pattern knew one
verb. Mobs use their own -- `Terror Bird bites you for 5[Torso]`, `Ak Yaban
Flayer slashes you`, `mauls`, `slices` -- so every mob attack ever recorded was
thrown away: 139 readings of `bites` across nine fights, and one terror-bird
fight understating what it took by 72 damage over thirteen attacks. The verbs
are listed rather than matched as any word ending in "s", because `Karyna's
Lesser Heal heals you for 24` is the same shape and is not damage. The log also
grades the blow -- `deeply bites` -- and without somewhere to put the adverb
the name absorbed it, so `TerrorBirddeeply` turned up as a player. A name
ending in "ly" is safe: the adverb needs the space in front of it.

This hid behind a ground truth that did not cover it. The dungeon clip has
`slashes` lines at 20:16:02, and the window transcribed from the in-game log
started at 20:17:17. Scoring 47 of 47 on that clip said nothing about a whole
class of line sitting just outside the window.

**An unknown flag is not a misreading of a shorter one.** `[Overhead]` contains
`Head` whole and scored 0.67 against a 0.66 bar, so an overhead swing was filed
as a hit to the head -- a real body part, in a real report, from a tag that
never said it. What separates them is shape: a misreading loses or swaps
letters, as `Pany` does for `Parry`, and does not keep the whole word and add
three more to the front. Tags found in reports that the vocabulary had never
met -- `Underhew`, `Sling Thrust`, `Piercing Shot`, `Resist`, `Mind` -- are in
it now. The report keeps a second list of which tags are weapon abilities, and
that one has to learn them too, or they parse correctly and are still not
counted.

**Two Pythons, one launcher.** The .bat said `python` and trusted PATH.
Explorer resolves that differently from a terminal, so the tool worked when run
by hand and died on `No module named PIL` when a video was dropped on it. It
now picks the first interpreter that can actually import what it needs.

**One PowerShell process, not one per frame.** Process startup dominated the
runtime — batching took a clip from 4–5 minutes to about 90 seconds.
