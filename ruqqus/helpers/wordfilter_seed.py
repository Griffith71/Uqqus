"""Starter word list for the word filter (see helpers/wordfilter.py).

This is the list a fresh site starts with; after that the list lives in the
`word_filter_entries` table and is maintained from the admin screen. It is
intentionally modest - a short, hand-picked set of extreme terms, and the
common profanity a child filter is expected to catch - and it WILL need
tuning for a real community.

severity 2 (EXTREME):   hidden for the Standard filter and the Child filter
severity 1 (PROFANITY): hidden for the Child filter only
severity 0:             explicitly allowed (innocent look-alikes)

Fields: word, severity, mode ("word" = whole token, "anywhere" = also inside
longer tokens), variants (other spellings), suffixes (None = the engine's
default endings: s, es, ed, er, ers, ing, in).
"""

EXTREME = 2
PROFANITY = 1
ALLOWED = 0


def _e(word, severity, mode="word", variants=(), suffixes=None):
    return {"word": word, "severity": severity, "mode": mode,
            "variants": list(variants), "suffixes": None if suffixes is None else list(suffixes)}


ENTRIES = [
    # ------------------------------------------------------------- extreme
    # "anywhere" entries carry no loose spellings: those would match inside
    # innocent longer words, so alternate spellings are whole-word entries.
    _e("nigger", EXTREME, mode="anywhere"),
    _e("niggr", EXTREME, variants=["nigar", "niggar", "nigor"], suffixes=["s"]),
    _e("faggot", EXTREME, variants=["faggit", "fagget", "faggut", "phaggot"], suffixes=["s", "y", "ry"]),
    _e("fag", EXTREME, variants=["phag"], suffixes=["s"]),
    _e("kike", EXTREME, variants=["kyke"], suffixes=["s"]),
    _e("spic", EXTREME, suffixes=["s"]),
    _e("chink", EXTREME, suffixes=["s"]),
    _e("gook", EXTREME, suffixes=["s"]),
    _e("wetback", EXTREME, suffixes=["s"]),
    _e("coon", EXTREME, suffixes=["s"]),
    _e("tranny", EXTREME, variants=["trannie"], suffixes=["s", "es"]),
    _e("shemale", EXTREME, suffixes=["s"]),
    _e("raghead", EXTREME, suffixes=["s"]),
    _e("towelhead", EXTREME, suffixes=["s"]),
    _e("beaner", EXTREME, suffixes=["s"]),
    _e("paki", EXTREME, suffixes=["s"]),
    _e("jigaboo", EXTREME, variants=["jiggaboo"], suffixes=["s"]),
    _e("porchmonkey", EXTREME, suffixes=["s"]),
    _e("junglebunny", EXTREME, suffixes=["s"]),
    _e("zipperhead", EXTREME, suffixes=["s"]),
    _e("sandnigger", EXTREME, mode="anywhere"),
    _e("mongoloid", EXTREME, suffixes=["s"]),

    # ----------------------------------------------------------- profanity
    _e("nigga", PROFANITY, variants=["niggah", "nigguh", "niggaz", "nigg"], suffixes=["s", "z"]),
    _e("fuck", PROFANITY, mode="anywhere"),
    _e("fuk", PROFANITY, variants=["fck", "fvck", "phuck", "phuk", "fuq", "fucc", "fukk"],
       suffixes=["s", "ed", "er", "ers", "ing", "in"]),
    _e("shit", PROFANITY, variants=["sht", "shyt", "shiz"],
       suffixes=["s", "ty", "tier", "ter", "ters", "ting", "ted", "head", "heads", "hole", "holes",
                 "face", "show", "storm", "bag", "bags", "post", "posts", "posting", "load", "loads"]),
    _e("bullshit", PROFANITY, suffixes=["s", "ter", "ting", "ted"]),
    _e("horseshit", PROFANITY, suffixes=[]),
    _e("dipshit", PROFANITY, suffixes=["s"]),
    _e("batshit", PROFANITY, suffixes=[]),
    _e("apeshit", PROFANITY, suffixes=[]),
    _e("bitch", PROFANITY, variants=["biatch", "bich", "b1tch"], suffixes=["es", "y", "ing", "in", "ed", "ass"]),
    _e("ass", PROFANITY, variants=["arse"], suffixes=["es", "ed"]),
    _e("asshole", PROFANITY, variants=["arsehole", "ahole"], suffixes=["s"]),
    _e("asshat", PROFANITY, suffixes=["s"]),
    _e("asswipe", PROFANITY, suffixes=["s"]),
    _e("dumbass", PROFANITY, suffixes=["es"]),
    _e("jackass", PROFANITY, suffixes=["es"]),
    _e("smartass", PROFANITY, suffixes=["es"]),
    _e("bastard", PROFANITY, suffixes=["s"]),
    _e("damn", PROFANITY, variants=["dammit", "damnit"], suffixes=["s", "ed", "ing", "it"]),
    _e("goddamn", PROFANITY, variants=["goddam", "goddammit", "goddamnit"], suffixes=["ed"]),
    _e("crap", PROFANITY, suffixes=["s", "py", "ped", "ping"]),
    _e("piss", PROFANITY, suffixes=["es", "ed", "ing", "er", "y"]),
    _e("dick", PROFANITY, suffixes=["s", "head", "heads", "wad", "wads", "face"]),
    _e("cock", PROFANITY, suffixes=["s", "sucker", "suckers", "sucking", "head"]),
    _e("pussy", PROFANITY, variants=["pussies"], suffixes=[]),
    _e("cunt", PROFANITY, suffixes=["s", "y", "ing"]),
    _e("twat", PROFANITY, suffixes=["s"]),
    _e("wank", PROFANITY, suffixes=["s", "er", "ers", "ing", "ed"]),
    _e("bollocks", PROFANITY, suffixes=[]),
    _e("bugger", PROFANITY, suffixes=["s", "ed", "ing"]),
    _e("slut", PROFANITY, suffixes=["s", "ty"]),
    _e("whore", PROFANITY, suffixes=["s", "d"]),
    _e("skank", PROFANITY, suffixes=["s", "y"]),
    _e("thot", PROFANITY, suffixes=["s"]),
    _e("tits", PROFANITY, variants=["titties", "titty"], suffixes=[]),
    _e("jizz", PROFANITY, suffixes=["ed", "ing"]),
    _e("dildo", PROFANITY, suffixes=["s"]),
    _e("blowjob", PROFANITY, suffixes=["s"]),
    _e("handjob", PROFANITY, suffixes=["s"]),
    _e("douche", PROFANITY, suffixes=["s", "bag", "bags"]),
    _e("idiot", PROFANITY, suffixes=["s", "ic"]),
    _e("moron", PROFANITY, suffixes=["s", "ic"]),
    _e("imbecile", PROFANITY, suffixes=["s"]),
    _e("retard", PROFANITY, suffixes=["s", "ed"]),
    _e("dyke", PROFANITY, suffixes=["s"]),
    _e("wtf", PROFANITY, suffixes=[]),
    _e("stfu", PROFANITY, suffixes=[]),
    _e("gtfo", PROFANITY, suffixes=[]),
    _e("lmfao", PROFANITY, suffixes=[]),
    _e("omfg", PROFANITY, suffixes=[]),

    # ------------------------------------------- innocent look-alikes (allowed)
    _e("snigger", ALLOWED, variants=["sniggers", "sniggered", "sniggering"]),
    _e("niggard", ALLOWED, variants=["niggardly", "niggards", "niggardliness"]),
    _e("shiitake", ALLOWED, variants=["shitake"]),
    _e("assess", ALLOWED),   # reads as a stretched "asses" otherwise
]

# Phrases whose words are never counted, whatever the list says.
ALLOW_PHRASES = [
    "maine coon",
    "maine coons",
    "chink in",
    "chinks in",
    "moby dick",
    "spotted dick",
    "van dyke",
]
