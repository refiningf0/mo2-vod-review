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
FLAGS = re.compile(r"[\[\(\{]([A-Za-z][A-Za-z ]{1,18})[\]\)\}]")

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
LETTER_RE = re.compile(r"[A-Za-z]")

# A spell name, one or two words: "Corrupt", "Lesser Heal", "Greater Heal".
SPELL = r"([A-Za-z][A-Za-z']{1,13}(?:\s+[A-Za-z][A-Za-z']{1,13})?)"
POSS = r"\s*['\u2019]\s*s\s*"           # "Karyna's " -- the apostrophe is what marks it
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
RE_IN_ABIL = re.compile(NAME + r"\s*['\u2019]\s*s\s*([A-Za-z]{2,18})\s+" + HIT + r"s?" + SEP + YOU + SEP + FOR + SEP + AMT_TOK, re.I)
RE_IN = re.compile(NAME + r"\s+" + HITS + r"?" + SEP + YOU + SEP + FOR + SEP + AMT_TOK, re.I)


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


DIGIT_FOR = {"S": "5", "s": "5", "O": "0", "o": "0", "l": "1", "I": "1",
             "i": "1", "Z": "2", "z": "2", "B": "8", "G": "6", "q": "9"}


def read_amount(body):
    """Pull the damage number, undoing two OCR failures that pull in opposite
    directions.

    Every damage line ends with a bracketed body part -- "for 45[Torso]".

      * When the bracket survives but a digit inside the number was read as a
        letter ("for 4S[Torso]"), that letter is really a digit: 45.
      * When the bracket itself was read as a digit ("for 225Arms]"), the last
        digit is really the bracket: 22.

    The bracket is what tells them apart. If one is present, letters before it
    belong to the number; if it is missing, the trailing digit was the bracket.
    """
    m = re.search(r"\b" + FOR + r"(" + SEP + r")([0-9SsOoIilZzBGq]{1,4})([^0-9\n]{0,3})", body, re.I)
    if not m:
        return None
    sep, num, tail = m.group(1), m.group(2), m.group(3) or ""

    # A leading letter is ambiguous: it can be noise that landed in the gap
    # ("forz33" is 33) or a digit OCR misread ("for S6" is 56). The space is
    # what separates them -- noise crowds straight up against "for", while a
    # real number keeps its space and merely has a letter-shaped digit in it.
    if (not re.search(r"\s", sep) and len(num) > 1
            and num[0].isalpha() and num[1:].isdigit()):
        num = num[1:]

    has_bracket = BRACKET_RE.search(tail)

    if has_bracket:
        # letters inside the number are misread digits
        fixed = "".join(DIGIT_FOR.get(c, c) for c in num)
        if not fixed.isdigit():
            return None
        return int(fixed)

    if not num.isdigit():
        fixed = "".join(DIGIT_FOR.get(c, c) for c in num)
        if not fixed.isdigit():
            return None
        num = fixed

    # no bracket: a letter just past the number means the bracket was eaten
    if len(num) > 1 and LETTER_RE.search(tail):
        num = num[:-1]
    return int(num)


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
    if not body or not re.search(r"\b" + FOR + SEP + AMT_TOK, body, re.I):
        return None

    flags = [f.strip() for f in FLAGS.findall(body)
             if f.strip().lower() not in CHANNELS and not f.strip().isdigit()]

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
            if not difflib.get_close_matches(_norm(a), [_norm(k) for k in kept],
                                             n=1, cutoff=0.86):
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


def dedupe(events, visible=12.0, rescue_min=8):
    """Collapse the many readings of one log line into a single event.

    A line sits on screen for several seconds and is read from every frame in
    that span, so the same hit arrives dozens of times. Two things separate a
    genuine repeat from a re-read:

      * The wall-clock timestamp, when OCR recovered it. That is definitive --
        two readings sharing a timestamp are the same line, and two with
        different timestamps are different lines however close together.
      * Failing that, how far apart the readings are. Beyond the time a line
        can stay visible, it must be a new hit.

    Timestamps win where they exist. Within a group of identical hits, any
    reading that lost its timestamp is treated as a re-read of a timestamped
    one rather than a separate hit -- which is what it almost always is.
    """
    def nm(e):
        return e["who"] if e["dir"] == "in" else e["target"]

    groups = {}
    for e in sorted(events, key=lambda x: x["t"]):
        groups.setdefault((e.get("kind", "hit"), e["dir"], e["amount"], nm(e)), []).append(e)

    out = []
    for rows in groups.values():
        timed = [e for e in rows if e.get("exact")]
        if timed:
            # One event per distinct timestamp, then the untimed readings are
            # placed by whether the timed line was still on screen when each
            # was taken.
            by_ts = {}
            for e in timed:
                by_ts.setdefault(round(e["t"]), []).append(e)

            built = []
            for ts_rows in by_ts.values():
                built.append((_merge(ts_rows), min(r["t"] for r in ts_rows)))

            # Attach each untimed reading to a timed hit that was still on
            # screen when it was taken. Anything left over was read while no
            # matching line was displayed, so it is a separate occurrence.
            leftover = []
            for u in (e for e in rows if not e.get("exact")):
                home = next((m for m, t0 in built
                             if t0 - 1.0 <= u["t"] <= t0 + visible), None)
                if home is None:
                    leftover.append(u)
                else:
                    home["seen"] += 1

            for m, _ in built:
                out.append(m)

            # A line does not vanish the instant `visible` elapses, so a few
            # stragglers always fall outside the window. Promoting those makes
            # phantom hits: on a hand-checked fight the genuine rescued hit was
            # read 26 times while every false one came from 3 to 5 readings.
            # A hit that truly happened leaves a full line's worth of readings
            # behind it, so hold rescues to that standard.
            if leftover:
                runs, run = [], [leftover[0]]
                for e in leftover[1:]:
                    if e["t"] - run[-1]["t"] <= visible:
                        run.append(e)
                    else:
                        runs.append(run)
                        run = [e]
                runs.append(run)
                for r in runs:
                    if len(r) >= rescue_min:
                        out.append(_merge(r))
        else:
            run = [rows[0]]
            for e in rows[1:]:
                if e["t"] - run[-1]["t"] <= visible:
                    run.append(e)
                else:
                    out.append(_merge(run))
                    run = [e]
            out.append(_merge(run))

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


def _merge(run):
    """One event from several readings: earliest time, majority name and flags."""
    e = dict(min(run, key=lambda r: r["t"]))
    field = "who" if e["dir"] == "in" else "target"
    names = Counter(r[field] for r in run if r.get(field))
    e[field] = names.most_common(1)[0][0] if names else None
    e["flags"] = list(Counter(tuple(r["flags"]) for r in run).most_common(1)[0][0])
    e["seen"] = len(run)
    return e
