"""Parse OCR'd MO2 combat-log lines into structured events.

OCR on compressed gameplay footage is lossy in predictable ways, so the
matching is deliberately loose:

  * Timestamps go missing on some lines. When that happens the frame's own
    position in the video is used instead, so the event still lands in roughly
    the right place on the timeline.
  * Verbs get mangled -- "hit" reads as "lit", "nit", "bit". The verb is
    matched as a fuzzy class rather than a literal.
  * Names come back slightly wrong and differently wrong on each frame
    ("Troglodooty", "Trogiodooty", "Troglodootv"). Rather than trusting any
    single reading, all name-shaped tokens are collected across the whole run
    and clustered by similarity; each cluster collapses to whichever spelling
    was seen most often, which is almost always the correct one.

The numbers are the part that has to be right, and digits survive OCR far
better than letters do.
"""
import difflib
import re
from collections import Counter

TS = re.compile(r"(\d{1,2})\s*[:.]\s*(\d{2})\s*[:.]\s*(\d{2})")
# Letters alone missed a flag OCR had dropped a mark into -- "[Par1.y]" is a
# parry that went uncounted. The bracket is the real evidence that something
# was written here; read_flags decides whether what is inside is a flag.
FLAGS = re.compile(r"[\[\(\{]([A-Za-z][A-Za-z0-9 .,'’!\-]{1,18})[\]\)\}]")

CHANNELS = ("combat", "nave", "local", "global", "say", "yell",
            "guild", "party", "whisper", "help", "trade")

NAME = r"([A-Za-z][A-Za-z0-9_'\-\(\) ]{1,22}?)"
HIT = r"[hlnbk]i[tf]"      # hit / lit / nit / bit / hif
HITS = r"[hlnbk]i[tf][sz]" # hits / lits / ...

# The words around the number get mangled as reliably as the number does, so
# each is matched as a small class rather than a literal: "for" reads as "foi"
# or "f0r", "you" as "ypu", and the space between tokens disappears entirely
# ("for18"). OCR also drops punctuation into the gaps ("hits.ypu",
# "HighPsych@for"), so the separators tolerate a little of it.
AMT_TOK = r"[0-9SsOoIilZzBGq]{1,4}"
FOR = r"f[o0][ri]"
YOU = r"y[o0p]u"
SEP = r"[\s@#.,:;'\"\-]*"

AMOUNT_RE = r"\bfor\s+(\d+)([^\d\n]{0,3})"
BRACKET_RE = re.compile(r"[\[\(\{]")

# No hit in this log runs to four digits, so a longer run of them is not a
# number that got misread -- it is a number that ran into whatever followed it.
MAX_DIGITS = 3

# The words that can open the bracket closing a damage line. Used as a
# landmark: text standing where one of these belongs says the bracket is gone,
# and matching one exactly is what separates a bracket OCR turned into a digit
# from one it merged into the letter after it.
BODY_PARTS = ("torso", "head", "arms", "arm", "legs", "leg", "hands", "hand",
              "feet", "foot", "left", "right", "lower", "upper", "parry",
              "blocked")

# A spell name, one or two words: "Corrupt", "Lesser Heal", "Greater Heal".
SPELL = r"([A-Za-z][A-Za-z']{1,13}(?:\s+[A-Za-z][A-Za-z']{1,13})?)"
# The apostrophe is what marks a possessive, and OCR renders it as a colon or
# a semicolon often enough to matter: "ALADIM:s Outburst", "Ashers;s arrow".
# Missing these does not just lose the line -- the name match then starts after
# the mark and reads the leftover "s" as part of the attacker, putting
# "sOutburst" in the roster as though it were a player.
APOS = r"['\u2019;:]"
POSS = r"\s*" + APOS + r"\s*s\s*"       # "Karyna's "
HEAL = r"[hn]ea[lI][sz5]?"                # heals / heal, and what OCR makes of them

# Healing reads the same way damage does, with a different verb:
#   "Karyna's Lesser Heal heals you for 24"
# The outgoing form is inferred from that symmetry -- no clip to hand has the
# player doing the healing, so both plausible shapes are accepted.
RE_HEAL_IN = re.compile(NAME + POSS + SPELL + r"\s*" + HEAL + SEP + YOU + SEP +
                        FOR + SEP + AMT_TOK, re.I)
# The healer's name is often the first thing OCR loses, leaving just the
# spell. The amount is still real, so keep it and say the source is unknown.
RE_HEAL_ANON = re.compile(r"\b" + SPELL + r"\s*" + HEAL + SEP + YOU + SEP +
                          FOR + SEP + AMT_TOK, re.I)

RE_HEAL_OUT = re.compile(r"\byour" + r"\s*" + SPELL + r"\s*" + HEAL + r"\s+" +
                         NAME + SEP + FOR + SEP + AMT_TOK, re.I)
RE_HEAL_OUT2 = re.compile(r"\b" + YOU + r"\s+" + HEAL + r"\s+" + NAME + SEP +
                          FOR + SEP + AMT_TOK, re.I)

# "Your Corrupt hit Bigilo for 12" -- the outgoing twin of RE_IN_ABIL.
RE_OUT_ABIL = re.compile(r"\byour\s*" + SPELL + r"\s*" + HIT + SEP + NAME +
                         SEP + FOR + SEP + AMT_TOK, re.I)

RE_OUT = re.compile(r"\b" + YOU + r"\s+" + HIT + SEP + NAME + SEP + FOR + SEP + AMT_TOK, re.I)
RE_IN_ABIL = re.compile(NAME + POSS + r"([A-Za-z]{2,18})\s+" + HIT + r"s?" + SEP + YOU + SEP + FOR + SEP + AMT_TOK, re.I)
RE_IN = re.compile(NAME + r"\s+" + HITS + r"?" + SEP + YOU + SEP + FOR + SEP + AMT_TOK, re.I)
# "Ith's Corrupt hit you", but with the possessive damaged. OCR loses the
# apostrophe or the s after it -- "Ith'? Corrupt", "Itys Corrupt" -- and the
# pattern above then reads the spell as the attacker, so a DoT called Corrupt
# joined the roster as a player. Whatever sits between the name and the thing
# that hit you is allowed to be wreckage; the name in front of it is still the
# one who cast it. The log's own grammar keeps this from swallowing an
# ordinary attack: something of yours *hit* you, while a person *hits*
# you. Without that "bang hits you" parsed as someone called "ba".
RE_IN_ABIL_LOOSE = re.compile(
    NAME + r"[^A-Za-z0-9\n]{0,4}([A-Za-z][A-Za-z']{1,17})\s+" + HIT + r"(?![sz])" +
    SEP + YOU + SEP + FOR + SEP + AMT_TOK, re.I)


def strip_channel(s):
    s = re.sub(r"^[\s\(\[\{\|:.,]+", "", s)
    for c in CHANNELS:
        s = re.sub(r"[\[\(\{]?\s*" + c + r"\s*[\]\)\}1lI]?", " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


WEAPONS = {
    "arrow", "arrows", "bolt", "bolts", "dagger", "sword", "axe", "mace",
    "spear", "javelin", "hammer", "bow", "blade", "lance", "club", "staff",
    "knife", "pike", "halberd", "shield", "fist", "fists",
}


def tidy_name(n):
    n = re.sub(r"[^A-Za-z0-9_'\-]", "", n or "").strip()
    # "Ith'sarrow" survives as one token when the space is lost; the owner is
    # the half before the possessive.
    m = re.match(r"^(.{2,})['’]s(.+)$", n)
    if m and m.group(2).lower() in WEAPONS:
        n = m.group(1)
    return n


def is_weapon(n):
    return n.lower().strip("'s") in WEAPONS


def _norm(n):
    """Fold the letter confusions OCR makes most often, for comparison only."""
    n = n.lower()
    for a, b in (("rn", "m"), ("cl", "d"), ("vv", "w"), ("vy", "w"),
                 ("0", "o"), ("5", "s"), ("8", "b"), ("2", "z")):
        n = n.replace(a, b)
    n = re.sub(r"[^a-z]", "", n)
    # i, l and 1 are the single most confused glyph group in this font --
    # "Qiade" and "Qlade" are one player. Folding them to one symbol lets the
    # comparison see through it.
    return n.replace("i", "l")


# Everything the log prints inside the brackets after a hit: where it landed,
# and what became of it.
FLAG_WORDS = (
    "Torso", "Head", "Arm", "Arms", "Hand", "Hands", "Leg", "Legs",
    "Foot", "Feet", "Left Limb", "Right Limb", "Lower Body", "Upper Body",
    "Parry", "Blocked", "Handle", "Equipment", "Impale", "Sting",
    "Armor Pierced", "Counter Reduced", "Spread Shot", "Forceful Strike",
    "Off-Hand", "Snap Shot", "Hack",
)
FLAG_BY_NORM = {_norm(f): f for f in FLAG_WORDS}
FLAG_SET = set(FLAG_WORDS)
FLAG_KEYS = list(FLAG_BY_NORM)
CHANNEL_KEYS = [_norm(c) for c in CHANNELS]


def read_flags(body):
    """The bracketed notes on a hit, put back into the log's own words.

    OCR mangles these the way it mangles everything else -- "Parry" comes back
    "Pany", "Armor Pierced" as "Arrnor Pierced", "Left Limb" as "Eeft Limb".
    A misspelt flag is not a near miss but a miss: the report counts parries by
    the name of the flag, so a "Pany" is a parry that did not happen and a
    "Combkt" is a body part nobody has.

    So each reading is matched against the words the log can actually print --
    exactly where it can be, by similarity where it cannot. A reading that
    lands closer to a channel name than to any flag is the channel bleeding
    into the brackets, and goes. Anything matching nothing is kept as it reads,
    but only if it still looks like a word, so a flag this list has not met
    survives while debris does not.
    """
    out = []
    for raw in FLAGS.findall(body):
        f = " ".join(raw.split())
        n = _norm(f)
        if not n:
            continue

        # "Party" spelled correctly is the channel, whatever it resembles.
        # The channel words are stripped from the line before this runs, so
        # this only catches one that survived intact.
        if n in CHANNEL_KEYS:
            continue

        hit = FLAG_BY_NORM.get(n)
        if hit is None:
            # Past that, flags are tried before channels and a tie goes to the
            # flag.
            # "Par1.y" reads as "pary", which is exactly as close to the
            # channel "party" as to "parry" -- but this is the inside of a
            # bracket on a damage line, which is where flags live and where
            # a channel only ever turns up by accident.
            m = difflib.get_close_matches(n, FLAG_KEYS, n=1, cutoff=0.66)
            if m:
                hit = FLAG_BY_NORM[m[0]]
            elif difflib.get_close_matches(n, CHANNEL_KEYS, n=1, cutoff=0.7):
                continue
            elif re.fullmatch(r"[A-Za-z][A-Za-z ]*", f):
                hit = f
            else:
                continue
        if hit not in out:
            out.append(hit)
    return out


DIGIT_FOR = {"S": "5", "s": "5", "O": "0", "o": "0", "l": "1", "I": "1",
             "i": "1", "Z": "2", "z": "2", "B": "8", "G": "6", "q": "9"}


def find_amount(body):
    """Locate the "for <number>" that carries the damage.

    OCR loses the space in front of "for" as readily as any other -- "hit
    youfor 26", "hit Steetchfor 38", "healsyoufor 25" -- so a word boundary
    cannot be demanded of it. It is still worth preferring: where a line has a
    "for" standing on its own that is the one that means "for". Only a line
    with none falls back to one fused to the word before it, which is the
    difference between reading such a line and throwing it away.
    """
    for edge in (r"\b" + FOR, FOR):
        m = re.search(edge + r"(" + SEP + r")([0-9SsOoIilZzBGq'’]{1,8})"
                      r"([^0-9\n]{0,3})", body, re.I)
        if m:
            return m
    return None


def read_amount(body):
    """Pull the damage number, undoing the OCR failures that reach into it.

    Every damage line ends with a bracketed body part -- "for 45[Torso]" --
    and spells end at the number itself. What goes wrong is always the join
    between the number and what follows it:

      * A digit inside the number was read as a letter ("for 4S[Torso]"), or
        stray punctuation landed in the middle of it ("for 1'8[Torso]"). Both
        are put back: the number is 45, and 18.
      * The bracket was read as a digit and joined the number ("for 225Arms]").
        The body part is the landmark -- text sitting exactly where "[Torso]"
        belongs, with nothing between it and the digits, says the bracket is
        the digit. When that text is damaged instead ("for 34Vorso]", where
        "[T" came back as "V") the bracket merged into the letter, not into
        the number, and every digit is real.
      * The number ran into whatever followed it and the two are no longer
        separable ("for 120041.5;" is a 12 with the body part smeared onto
        it). Nothing here can say where the number stopped, so the reading is
        thrown away rather than guessed at. The line is on screen for seconds
        and read from every frame in that span, so a discarded reading costs
        nothing -- the frames either side of it read the same line cleanly.
    """
    m = find_amount(body)
    if not m:
        return None
    sep, num, tail = m.group(1), m.group(2), m.group(3) or ""

    # OCR drops punctuation into the gaps between glyphs, and the gap between
    # two digits is no exception: "18" comes back as "1'8".
    num = num.replace("'", "").replace("’", "")
    if not num:
        return None

    # A leading letter is ambiguous: it can be noise that landed in the gap
    # ("forz33" is 33) or a digit OCR misread ("for S6" is 56). The space is
    # what separates them -- noise crowds straight up against "for", while a
    # real number keeps its space and merely has a letter-shaped digit in it.
    if (not re.search(r"\s", sep) and len(num) > 1
            and num[0].isalpha() and num[1:].isdigit()):
        num = num[1:]

    num = "".join(DIGIT_FOR.get(c, c) for c in num)
    if not num.isdigit():
        return None

    # Too many digits to be a number: the run swallowed the text after it, and
    # which digits were the damage is no longer recoverable.
    if len(num) > MAX_DIGITS:
        return None

    if BRACKET_RE.search(tail):
        return int(num)

    # No bracket. If the body part is standing where the bracket should be,
    # touching the digits, then the bracket is the last digit -- but only when
    # the body part reads cleanly. A damaged one means the bracket went into
    # the letter beside it and the number was never touched.
    if len(num) > 1 and _eats_bracket(tail, num):
        num = num[:-1]
    return int(num)


def _eats_bracket(tail, num):
    """True when the number ran into the bracket that follows it.

    Flush against a body part is the plain case: "for 225Arms]" is 22 on the
    arms, because nothing at all sits where the "[" belongs.

    A space is the same accident seen through worse OCR. On one recording the
    bracket came back as a "1" with a space after it -- "for 641 Torso]" for a
    hit of 64 -- and taking that at face value did double damage: it inflated
    the number, and because other frames read the same line whole it landed as
    a second hit beside the real one.

    A space on its own is not enough to say so, though. The bracket may simply
    have been dropped, leaving a number that was already right, and "for 48
    Torso]" is 48. So across a gap the last digit must be a "1" -- which is
    what "[" comes back as. Anything else in the gap, a surviving "[" above
    all, means this is not that accident.
    """
    word = re.match(r"(\s*)([A-Za-z]{2,})", tail)
    if not word:
        return False
    gap, w = word.group(1), word.group(2).lower()
    if gap and not num.endswith("1"):
        return False
    return any(b.startswith(w) or w.startswith(b) for b in BODY_PARTS)


def parse_line(raw, frame_t=None):
    """Return an event dict, or None. frame_t is the fallback timestamp."""
    m = TS.search(raw)
    if m:
        h, mi, s = (int(x) for x in m.groups())
        if h > 23 or mi > 59 or s > 59:
            t, exact = frame_t, False
        else:
            t, exact = h * 3600 + mi * 60 + s, True
        body = raw[m.end():]
    else:
        t, exact = frame_t, False
        body = raw

    if t is None:
        return None

    body = strip_channel(body)
    if not body or not find_amount(body):
        return None

    flags = read_flags(body)

    amt = read_amount(body)
    if amt is None:
        return None

    m2 = RE_HEAL_IN.search(body)
    if m2:
        return dict(t=t, ft=frame_t, exact=exact, kind="heal", dir="in",
                    who=tidy_name(m2.group(1)), target="You", amount=amt,
                    ability=m2.group(2).strip(), flags=flags, raw=raw)

    m2 = RE_HEAL_ANON.search(body)
    if m2:
        return dict(t=t, ft=frame_t, exact=exact, kind="heal", dir="in",
                    who="Unknown", target="You", amount=amt,
                    ability=m2.group(1).strip(), flags=flags, raw=raw)

    for rx in (RE_HEAL_OUT, RE_HEAL_OUT2):
        m2 = rx.search(body)
        if m2:
            g = m2.groups()
            who, abil = (g[1], g[0]) if rx is RE_HEAL_OUT else (g[0], None)
            who = tidy_name(who)
            if who and not who.lower().startswith("you"):
                return dict(t=t, ft=frame_t, exact=exact, kind="heal", dir="out",
                            who="You", target=who, amount=amt,
                            ability=abil.strip() if abil else None,
                            flags=flags, raw=raw)

    m2 = RE_OUT_ABIL.search(body)
    if m2:
        return dict(t=t, ft=frame_t, exact=exact, kind="hit", dir="out", who="You",
                    target=tidy_name(m2.group(2)), amount=amt,
                    ability=m2.group(1).strip(), flags=flags, raw=raw)

    m2 = RE_OUT.search(body)
    if m2:
        return dict(t=t, ft=frame_t, exact=exact, kind="hit", dir="out", who="You",
                    target=tidy_name(m2.group(1)), amount=amt,
                    ability=None, flags=flags, raw=raw)

    m2 = RE_IN_ABIL.search(body)
    if m2:
        return dict(t=t, ft=frame_t, exact=exact, kind="hit", dir="in", who=tidy_name(m2.group(1)),
                    target="You", amount=amt,
                    ability=m2.group(2).strip(), flags=flags, raw=raw)

    m2 = RE_IN_ABIL_LOOSE.search(body)
    if m2:
        who = tidy_name(m2.group(1))
        if not who.lower().startswith("you"):
            return dict(t=t, ft=frame_t, exact=exact, kind="hit", dir="in",
                        who="Unknown" if is_weapon(who) else who, target="You",
                        amount=amt, ability=m2.group(2).strip(), flags=flags,
                        raw=raw)

    m2 = RE_IN.search(body)
    if m2:
        who = tidy_name(m2.group(1))
        if who.lower().startswith("you"):
            return None
        # The owner's name was lost upstream of the weapon. The damage is real,
        # so keep it, but do not invent a player to hang it on.
        if is_weapon(who):
            who = "Unknown"
        return dict(t=t, ft=frame_t, exact=exact, kind="hit", dir="in", who=who, target="You",
                    amount=amt, ability=None, flags=flags, raw=raw)

    return None


def _junk(name):
    """Reject readings that are clearly OCR debris rather than a player."""
    n = (name or "").lower()
    if len(n) < 3:
        return True
    if any(c in n for c in CHANNELS):
        return True
    if not re.search(r"[aeiou]", n):
        return True
    return False


def canonical_names(events, roster=None, cutoff=0.55, anchor_min=3):
    """Map every OCR reading of a name onto one canonical spelling.

    Either a roster is supplied and readings snap to the closest real name, or
    the roster is inferred: readings seen at least `anchor_min` times are taken
    as real, since the correct spelling recurs across frames while each
    misreading tends to appear once. Everything else snaps to the nearest
    anchor, and anything too far from all of them is dropped -- an unmatchable
    reading is OCR noise, and keeping it would invent a player.
    """
    counts = Counter()
    for e in events:
        for k in ("who", "target"):
            v = e.get(k)
            if v and v != "You" and not _junk(v):
                counts[v] += 1

    if roster:
        anchors = list(roster)
    else:
        anchors = [n for n, c in counts.most_common() if c >= anchor_min]
        if not anchors and counts:
            anchors = [counts.most_common(1)[0][0]]

    # A systematic misreading repeats identically on every frame, so it clears
    # anchor_min as easily as the truth does and promotes itself to a separate
    # player -- CLAUDEMASTER and CLAUDEMASrER are one person. Fold anchors that
    # are near-duplicates of a more frequently seen one before anything snaps
    # to them. The bar is high here because these are all plausible names;
    # only a near-identical pair should collapse.
    if not roster:
        kept = []
        for a in sorted(anchors, key=lambda n: -counts[n]):
            na = _norm(a)
            if difflib.get_close_matches(na, [_norm(k) for k in kept],
                                         n=1, cutoff=0.86):
                continue
            # A name that is the front of a name already established, and seen
            # a small fraction as often, is that name with its end lost. OCR
            # gives up on a long one and hands back "Father" for "Father of
            # Bones"; left alone, the fragment becomes a player of its own, and
            # the readings filed under it never join the real line -- so hits
            # that happened before the recording started, and belong outside
            # the report, come back inside it as three more that never did.
            #
            # This only ever folds a fragment into something longer, never the
            # other way. Names that merely resemble each other stay apart:
            # AkYabanFlayer and AkYabanSnatcher are different mobs, and so are
            # AkYabanBloodletter and Bloodletter -- neither of which begins
            # with the other.
            if any(_norm(k).startswith(na) and counts[k] >= counts[a] * 5
                   for k in kept):
                continue
            kept.append(a)
        anchors = kept

    norm_anchor = {_norm(a): a for a in anchors}
    keys = list(norm_anchor)

    resolved = {}
    for name in counts:
        nn = _norm(name)
        if nn in norm_anchor:
            resolved[name] = norm_anchor[nn]
            continue
        m = difflib.get_close_matches(nn, keys, n=1, cutoff=cutoff)
        resolved[name] = norm_anchor[m[0]] if m else None

    for e in events:
        for k in ("who", "target"):
            v = e.get(k)
            if v and v != "You":
                e[k] = resolved.get(v)
    return anchors


def _settle_amounts(events, visible):
    """Put right the numbers that only one frame agrees with.

    A glyph standing next to the damage number sometimes joins it. The opening
    bracket of the body part does it from the right -- "for 55" comes back
    "551" -- and stray marks do it from the left, crowding up against "for"
    until "for 18" reads "for818". Occasionally the traffic goes the other way
    and a digit is lost into the debris beside it.

    No single line betrays any of this: "551" is a perfectly well-formed
    reading. What gives it away is the same thing that gives away a misread
    name -- the other frames. A line sits on screen for seconds and is read
    from every frame in that span, so the true number is read repeatedly while
    a glyph collision happens in one frame and not its neighbours. A number
    read once, holding a number the same fighter's line shows more often
    within that span, is that number wearing something extra.

    Names are compared this way already; the amounts were the part still
    taking each frame at its word. Only a strict majority moves anything: two
    readings that disagree and are equally attested are left alone rather than
    resolved by guesswork.
    """
    def nm(e):
        return e["who"] if e["dir"] == "in" else e["target"]

    # Which readings may vouch for each other. A fighter's own line is the
    # right company to judge a number in -- comparing across fighters lets one
    # player's 1 rewrite another's 12. The exception is a hit whose attacker
    # OCR lost: "Unknown" is not a fighter, so a reading filed under it is
    # weighed against every reading of its kind instead.
    named = {}
    loose = []
    for e in events:
        who = nm(e)
        if not who:
            continue
        if who == "Unknown":
            loose.append(e)
        else:
            named.setdefault((e.get("kind", "hit"), e["dir"], who), []).append(e)

    everything = [e for e in events if nm(e)]
    work = [(rows, rows) for rows in named.values()] + [(loose, everything)]

    for rows, company in work:
        counts = Counter(e["amount"] for e in company)
        for e in rows:
            if counts[e["amount"]] != 1:
                continue
            digits = str(e["amount"])
            best = best_n = None
            for other in company:
                d = str(other["amount"])
                if not _one_glyph_over(digits, d):
                    continue
                n = counts[other["amount"]]
                if n <= 1:
                    continue                      # a strict majority, or nothing
                if abs(other["t"] - e["t"]) > visible:
                    continue                      # too far apart to be one line
                # Where more than one number could explain it, the one the
                # frames agreed on most often is the reading to trust.
                if best_n is None or n > best_n:
                    best, best_n = other["amount"], n
            if best is not None:
                e["amount"] = best
    return events


def _one_glyph_over(digits, other):
    """True when `digits` is `other` with one extra character wedged into it.

    That is the whole shape of the failure. A mark beside the number joins it
    -- the opening bracket at the end ("55" reads "551"), debris at the front
    ("18" reads "818"), or something landing in the gap between two digits
    ("11" reads "Isl", which converts to 151). One character in, anywhere.
    """
    if len(digits) != len(other) + 1:
        return False
    return any(digits[:i] + digits[i + 1:] == other for i in range(len(digits)))


def _read_at(e):
    """When the frame this reading came from was taken.

    Not the same as e["t"]: a reading that gave up its timestamp is placed at
    the moment the hit happened, and every one of its dozens of readings then
    shares that one instant. Asking when the line was *seen* needs the frame.
    """
    return e["ft"] if e.get("ft") is not None else e["t"]


def _unbroken(rows, gap):
    """Split readings wherever nobody read the line for a while.

    `rows` are readings of one identical hit. A break longer than `gap` in
    when they were *read* means the line left the screen, so what follows is a
    new line that happens to say the same thing. Anything closer is the same
    line still sitting there -- however long it sits.
    """
    rows = sorted(rows, key=_read_at)
    runs, run = [], [rows[0]]
    for e in rows[1:]:
        if _read_at(e) - _read_at(run[-1]) <= gap:
            run.append(e)
        else:
            runs.append(run)
            run = [e]
    runs.append(run)

    # A gap only means the line went away if the readings either side are not
    # demonstrably the same line. Two things say they are:
    #
    #   * the same timestamp on both sides -- OCR simply lost the line for a
    #     while, which it does on a clip where the log reads patchily;
    #   * nothing but a timestamp on the near side and none at all on the far
    #     side. A timestamped line is on screen from its timestamp onward, and
    #     readings after it saying exactly the same thing, carrying no claim of
    #     their own, are it. A line that really was new would sooner or later
    #     show its own timestamp, or be caught overlapping this one.
    #
    # Without the second, a 19-second hole where nothing parsed -- on a clip
    # whose log OCRs badly -- turned one hit into two.
    joined = [runs[0]]
    for r in runs[1:]:
        before = {round(e["t"]) for e in joined[-1] if e.get("exact")}
        here = {round(e["t"]) for e in r if e.get("exact")}
        adrift = (not here and before
                  and _read_at(r[0]) - _read_at(joined[-1][-1]) <= gap * 2)
        if (before & here) or adrift:
            joined[-1].extend(r)
        else:
            joined.append(r)
    return joined


def _lines_in(run):
    """How many log lines produced one unbroken stretch of readings, and when
    each of them arrived.

    Returns (event or None, time) per line: an event where a timestamp named
    the line, and None where only the copies gave it away, for the caller to
    build from the readings nearest it.
    """
    by_ts = {}
    for e in run:
        if e.get("exact"):
            by_ts.setdefault(round(e["t"]), []).append(e)
    built = []
    for g in by_ts.values():
        made = _merge(g)
        built.append((made, made["t"]))
    built.sort(key=lambda b: b[1])

    # The most copies ever read from one frame is a floor on how many lines
    # were up at once -- and so on how many lines this stretch holds.
    #
    # Counted within one reading of the frame, not across them. Every frame is
    # read twice, through two image treatments, and the two disagree on the
    # odd character; two readings of one line would otherwise look exactly like
    # one line shown twice, and every hit would come out doubled.
    per = Counter((e.get("pass", 0), e["ft"]) for e in run if e.get("ft") is not None)
    if not per:
        return built
    most = max(per.values())
    if most <= len(built):
        return built

    # The kth line was there by the first frame that showed k copies.
    frames = sorted({f for _, f in per})
    at_least = lambda f, k: any(n >= k for (_, ff), n in per.items() if ff == f)
    for k in range(1, most + 1):
        if len(built) >= most:
            break
        arrived = next((f for f in frames if at_least(f, k)), None)
        if arrived is not None and all(abs(arrived - t) > 2.0 for _, t in built):
            built.append((None, arrived))
    built.sort(key=lambda b: b[1])
    return built


def dedupe(events, visible=12.0):
    """Collapse the many readings of one log line into a single event.

    A line sits on screen for several seconds and is read from every frame in
    that span, so the same hit arrives dozens of times. Telling a genuine
    repeat from a re-read rests on three things:

      * The readings of one line are contiguous. A line appears, stays, and
        scrolls away; it never comes back. So a stretch of identical readings
        is one line for as long as it keeps being read, and only a gap where
        nobody read it at all can mean the line went and another like it came.
      * Within a stretch, distinct wall-clock timestamps are distinct lines.
      * And so are copies. Two readings of the same text from the *same frame*
        are two lines on screen together, which one line cannot be. This is
        the witness for a line whose timestamp OCR never recovered -- and OCR
        loses them often.

    Readings that gave up neither are re-reads of whichever line they sit
    nearest, which is what they almost always are.

    This used to cut a stretch at a fixed distance from the line's first
    sighting -- twelve seconds -- and promote whatever fell beyond into a
    second hit. But how long a line stays up is not a property of the line, it
    is a property of how busy the fight is: nothing arrives to push it off and
    it sits there. Measured across four fights, lines stayed on screen for 16,
    24, 31, 40, even 78 seconds. On one clip that fixed cut invented 14 hits
    in a report of 54, and the in-game log confirmed they never happened.
    """
    def nm(e):
        return e["who"] if e["dir"] == "in" else e["target"]

    _settle_amounts(events, visible)


    groups = {}
    for e in sorted(events, key=lambda x: x["t"]):
        groups.setdefault((e.get("kind", "hit"), e["dir"], e["amount"], nm(e)), []).append(e)

    out = []
    for rows in groups.values():
        for run in _unbroken(rows, visible):
            built = _lines_in(run)
            if not built:
                out.append(_merge(run))
                continue

            # Every reading belongs to the line it sits nearest in time.
            buckets = [[] for _ in built]
            for e in run:
                i = min(range(len(built)), key=lambda j: abs(built[j][1] - e["t"]))
                buckets[i].append(e)

            for (made, t0), mine in zip(built, buckets):
                if made is None:
                    # A line the copies found but no timestamp named. It is
                    # placed where the copies first reached that many.
                    made = _merge(mine or run)
                    made["t"] = round(t0, 1)
                elif mine:
                    made["seen"] = len(mine)
                if mine:
                    made["span"] = _span(mine)
                    made["last"] = max(_read_at(r) for r in mine)
                out.append(made)

    out = [e for e in out if nm(e)]

    # A hit whose attacker OCR lost is a re-read of a named hit when it carries
    # the same damage and lands inside the window a log line stays on screen.
    # Without this the same arrow is counted twice: once against the archer and
    # once against nobody.
    named = [e for e in out if e["dir"] == "in" and e["who"] != "Unknown"]
    out = [e for e in out if not (
        e["dir"] == "in" and e["who"] == "Unknown" and
        any(n["amount"] == e["amount"] and abs(n["t"] - e["t"]) <= visible
            for n in named))]

    out.sort(key=lambda e: (e["t"], e["dir"]))
    return out


def _span(run):
    """How long the line was being read for, first sighting to last.

    A companion to `seen`, and a better witness than it for a long line. How
    many times a hit is read is not only how long it was up: the amount sits
    in the middle of the line, and on a long one -- a name, a number, three
    flags -- OCR drops the middle while still reading the ends. One real hit
    here was on screen for fifteen seconds across thirty readings and gave up
    its number in six of them, which read as noise beside lines whose number
    came back every time. Nothing misread in a single frame has any span.
    """
    return round(max(_read_at(r) for r in run) - min(_read_at(r) for r in run), 1)


def _merge(run):
    """One event from several readings: earliest time, majority name and flags."""
    e = dict(min(run, key=lambda r: r["t"]))
    field = "who" if e["dir"] == "in" else "target"
    names = Counter(r[field] for r in run if r.get(field))
    e[field] = names.most_common(1)[0][0] if names else None
    # Flags go to the majority, but only among readings that came back as
    # flags the log actually uses. OCR can drop the middle of a run of them and
    # weld the ends together -- "[Left Limb][Armor Pierced]" read as
    # "[Left Pierced]" -- and that wreckage is stable enough to be read the
    # same way in frame after frame. On one hit it won 12 readings to 3 and
    # took a body part and an ability off a swing that had both. A word nothing
    # recognises is kept when it is all there is, since it may be a tag this
    # does not know yet, but it never outvotes one that is recognised.
    tallies = [tuple(r["flags"]) for r in run]
    known = [t for t in tallies if all(f in FLAG_SET for f in t)]
    e["flags"] = list(Counter(known or tallies).most_common(1)[0][0])
    e["seen"] = len(run)
    e["span"] = _span(run)
    e["last"] = max(_read_at(r) for r in run)
    return e
