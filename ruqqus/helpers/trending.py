"""Trending topics: what an unusual number of people are posting about right now.

Pure rules, stdlib only (the queries are in helpers/trending_store.py).

How a topic is found:
* every post gives candidate terms (`terms_of`): runs of one to three words from its
  title and the start of its text, names (runs of capitalised words) and the link it
  shares. A post counts once per term however often it says it;
* a term is scored by its burst (`burst`): how many different accounts posted about it in
  the last day against what the week before predicts. Volume alone does not trend;
* every account counts once per term, so one account cannot make a topic, and a term
  needs `MIN_AUTHORS` different accounts at all;
* `tidy` keeps one topic where several say the same thing ("cup" under "world cup final",
  a link and the phrase its posts share).

Nothing here reads who wrote a post for display: authors are only counted.
"""
import hashlib
import html
import math
import re
from collections import Counter
from urllib.parse import parse_qsl, urlencode, urlsplit

WINDOW_SECONDS = 24 * 3600        # "right now"
BASELINE_DAYS = 7                 # what it is compared with
HALF_LIFE_SECONDS = 6 * 3600      # a post from 6 hours ago weighs half a new one
TOP = 10
MIN_AUTHORS = 3
MAX_WORDS = 3
TEXT_CHARS = 400                  # how much of a post's text is read
COMMON_SHARE = 0.2                # a term in more than this share of last week's posts says nothing
COMMON_MIN_POSTS = 30             # ... once there are enough posts to tell
COVER = 0.7                       # two topics sharing this much of their posts are one
TIDY_LIMIT = 200                  # only the best candidates are compared with each other
LABEL_CHARS = 80

PHRASE, NAME, LINK = "phrase", "name", "link"
WEIGHT = {NAME: 1.25, LINK: 1.1, PHRASE: 1.0}
ONE_WORD_WEIGHT = 0.7             # a single ordinary word is the weakest signal

# Words that say nothing by themselves, per language (words under three letters never count).
# A language with no list still works: `COMMON_SHARE` drops what its posts say all the time.
_STOP = {
    "en": """
        the and for are but not you all any can had her was one our out day get has him his how new now old
        see two way who did its let put say she too use that with have this will your from they know want been
        good much some time very when come here just like long make many more only over such take than them well
        were what about after again also back because before being between both could does doing done down each
        even every first found going gonna great into its made might most must never next other own same should
        since still their then there these thing things think this those through today under until upon very
        want wants where which while would year years yes yet people really someone something anyone everyone
        anything everything nothing got getting keep keeps kept look looks looking need needs post posts posted
        posting said says saying tell tells told thought got lot lots bit little big best better right left
        why off out per via another always often ever maybe please thanks thank hello guys guy stuff kind sort
        way ways week weeks month months yesterday tomorrow tonight morning night days hour hours minute minutes
        dont doesnt didnt cant wont isnt arent wasnt werent ive youre theyre thats whats heres theres lets
        don doesn didn won isn aren wasn weren couldn shouldn wouldn ain lol lmao omg wow yeah yep nope okay
        feel feels felt find finds give gives gave goes went gone help show shows start started stop try trying
        saw seen watch watched read heard hear came come comes took taken makes making has having
        use used using work works worked real true sure free full high low top part point case fact idea
    """,
    "es": """
        los las una uno unos unas del que con por para como pero mas más muy sin sobre este esta esto estos estas
        ese esa eso esos esas hay han has hemos son soy eres somos fue fui era eran ser estar está están estoy
        todo toda todos todas otro otra otros otras cuando donde quien porque también tambien entre desde hasta
        hace hacer puede pueden tiene tienen tengo solo sólo bien ahora aquí aqui así asi cada nos les sus mis tus
        hoy ayer año años día días gente algo alguien nada nadie mucho mucha muchos muchas poco
    """,
    "fr": """
        les des une que qui dans pour pas sur avec son ses est sont ont par plus tout tous toute toutes mais
        comme aux cette ces mon mes ton tes notre nos votre vos leur leurs elle elles ils nous vous été être avoir
        fait faire peut peuvent très tres aussi bien encore ici alors donc quand dont sans sous entre après apres
        avant chez depuis même meme autre autres rien quelque chose aujourd hui hier jour jours gens ans année
    """,
    "de": """
        der die das den dem des ein eine einer einem einen und ist sind war waren wird werden wurde hat haben
        hatte nicht mit von für fur auf aus bei nach über uber unter vor zum zur als auch aber oder wenn wie was
        wer dass daß sich ich du er sie es wir ihr mein dein sein unser euer noch nur schon sehr mehr kann können
        konnen muss man hier dort heute gestern jetzt immer wieder alle alles etwas nichts viel viele diese dieser
        dieses jahr jahre tag tage leute
    """,
    "pt": """
        que não nao com uma para por mais como mas dos das nos nas seu sua seus suas ele ela eles elas isso isto
        esse essa este esta são sao foi era ser estar está estão tem têm tenho muito muita muitos muitas também
        tambem quando onde quem porque sem sobre entre até ate depois antes já ainda aqui agora hoje ontem todo
        toda todos todas outro outra outros outras ano anos dia dias gente algo nada
    """,
    "it": """
        che non con una per più piu come ma dei delle degli nel nella nei nelle suo sua suoi sue lui lei loro
        questo questa questi queste quello quella sono era essere avere hanno molto molta molti molte anche
        quando dove chi perché perche senza sopra tra fra dopo prima già gia ancora qui ora oggi ieri tutto tutta
        tutti tutte altro altra altri altre anno anni giorno giorni gente niente
    """,
    "nl": """
        het een van den der dat die dit deze niet met voor aan uit bij naar over onder ook maar als dan nog wel
        zijn was waren wordt worden heeft hebben had kan kunnen moet men hier daar vandaag gisteren altijd weer
        alle alles iets niets veel dag dagen jaar jaren mensen wij jullie hun mijn jouw zijn haar ons onze
    """,
}
STOPWORDS = {lang: frozenset(words.split()) for lang, words in _STOP.items()}
DEFAULT_LANGUAGE = "en"

_URL = re.compile(r"(?:https?://|www\.)\S+")
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MENTION = re.compile(r"(?:(?<=\s)|^)[@+&]\w+")
_NOISE = re.compile(r"\{/?[a-z](?::[a-z]+)?\}|\{\.[a-z]+\}|:::[ \t]*\w*|:[A-Za-z0-9_-]+:|[*_~`>#=]+")
_BREAK = re.compile(r"[.!?;:,()\[\]{}\"“”«»‹›|/\\\n\r\t…—–]+|\s-\s")
_WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*")
_TRACKING = re.compile(r"^(?:utm_.*|fbclid|gclid|dclid|msclkid|igshid|mc_cid|mc_eid|ref|ref_src|ref_url|si|feature|share)$")
_FILE = re.compile(r"\.(?:jpe?g|png|gif|webp|svg|mp3|m4a|ogg|wav|flac|mp4|mov|webm|pdf)$")
_YOUTUBE = re.compile(r"^(?:(?:www\.|m\.|music\.)?youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/|live/)|youtu\.be/)([\w-]{11})")


class Post:
    """What a post contributes: who, when, its words and its link."""
    __slots__ = ("id", "author_id", "created_utc", "title", "text", "url", "link_title", "language", "votes", "comments")

    def __init__(self, id, author_id, created_utc, title="", text="", url="", link_title="", language=None,
                 votes=0, comments=0):
        self.id, self.author_id, self.created_utc = id, author_id, int(created_utc or 0)
        self.title, self.text, self.url, self.link_title = title or "", text or "", url or "", link_title or ""
        self.language, self.votes, self.comments = language, int(votes or 0), int(comments or 0)


class Entry:
    """A post with its terms. For last week's posts `terms` may be a plain set of keys."""
    __slots__ = ("post", "terms")

    def __init__(self, post, terms):
        self.post, self.terms = post, terms


class Topic:
    __slots__ = ("key", "kind", "label", "slug", "score", "authors", "post_ids")

    def __init__(self, key, kind, label, score, authors, post_ids):
        self.key, self.kind, self.label = key, kind, label
        self.slug, self.score, self.authors, self.post_ids = slug_of(key), score, authors, post_ids

    def __repr__(self):
        return f"<Topic({self.key!r}, {self.score}, authors={self.authors}, posts={len(self.post_ids)})>"


# --- the terms of one post -------------------------------------------------------------

def stopwords(language):
    """The filler words of a post's language; a post with no detected language reads as English."""
    code = re.split(r"[-_]", (language or DEFAULT_LANGUAGE).lower())[0]
    return STOPWORDS.get(code, frozenset())


def plain(text):
    """Post text as words and punctuation only: no addresses, markup, mentions or emoji codes."""
    text = html.unescape(text or "")
    text = _MD_LINK.sub(lambda m: f" {m.group(1)} ", text)
    text = _URL.sub(" . ", text)
    text = _MENTION.sub(" . ", text)
    return _NOISE.sub(" ", text)


def _fold(word):
    word = word.casefold()
    for ending in ("'s", "’s"):
        if word.endswith(ending):
            word = word[:-2]
    return word


def _is_filler(word, stop):
    return len(word) < 3 or word in stop or word.isdigit()


def phrases(text, stop):
    """{key: (kind, text as written)} for one piece of text."""
    found = {}
    segments = [[(w, _fold(w)) for w in _WORD.findall(seg) if len(w) <= 30] for seg in _BREAK.split(plain(text))]
    words = [w for seg in segments for w, _ in seg if w[0].isalpha()]
    # a title written In Title Case says nothing about which words are names
    title_case = len(words) >= 4 and sum(w[0].isupper() for w in words) > 0.7 * len(words)

    for seg in segments:
        for start in range(len(seg)):
            for size in range(1, MAX_WORDS + 1):
                gram = seg[start:start + size]
                if len(gram) < size:
                    break
                if _is_filler(gram[0][1], stop) or _is_filler(gram[-1][1], stop):
                    continue
                key = " ".join(folded for _, folded in gram)
                if len(key) > LABEL_CHARS:
                    continue
                solid = [w for w, folded in gram if not _is_filler(folded, stop)]
                capital = all(w[0].isupper() for w in solid)
                # the first word of a sentence is capitalised anyway, unless it is an acronym
                is_name = capital and not title_case and (size > 1 or start > 0 or (solid[0].isupper() and len(solid[0]) > 1))
                kind = NAME if is_name else PHRASE
                if key not in found or (kind == NAME and found[key][0] != NAME):
                    found[key] = (kind, " ".join(w for w, _ in gram))
    return found


def link_key(url, own_hosts=()):
    """A shared link as one comparable string ("example.org/story"), or None when it is not
    a topic: no real address, a bare home page, a file, or this site itself."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix) and host.count(".") > 1:
            host = host[len(prefix):]
    if host in own_hosts or "." not in host:
        return None
    path = re.sub(r"/+$", "", parts.path)
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k.lower())))
    whole = host + path + ("?" + query if query else "")
    video = _YOUTUBE.match(whole)
    if video:
        return "youtube.com/watch?v=" + video.group(1)
    if not path and not query:
        return None
    if _FILE.search(path.lower()):
        return None
    return whole[:300]


def terms_of(post, own_hosts=()):
    """{key: (kind, label as written)} for one post."""
    stop = stopwords(post.language)
    found = phrases(post.text[:TEXT_CHARS], stop)
    for key, value in phrases(post.title, stop).items():     # the title's spelling wins
        if key not in found or value[0] == NAME or found[key][0] != NAME:
            found[key] = value
    link = link_key(post.url, own_hosts)
    if link:
        label = " ".join(html.unescape(post.link_title).split())[:LABEL_CHARS]
        found["link:" + link] = (LINK, label or link[:LABEL_CHARS])
    return found


def prepare(posts, own_hosts=()):
    return [Entry(p, terms_of(p, own_hosts)) for p in posts]


# --- scoring ---------------------------------------------------------------------------

def burst(now_count, expected):
    """How far above what last week predicts: a count compared with its usual scatter."""
    return (now_count - expected) / math.sqrt(expected + 1.0)


def freshness(age_seconds):
    return 0.5 ** (max(age_seconds, 0) / HALF_LIFE_SECONDS)


def engagement(points):
    """Votes and comments lift a topic a little, never more than half as much again."""
    return 1.0 + min(math.log1p(max(points, 0)) / 12.0, 0.5)


def slug_of(key):
    if key.startswith("link:"):
        return "link-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^\w]+", "-", key, flags=re.UNICODE).strip("-")
    if len(slug) > 60 or not slug:
        slug = (slug[:48].rstrip("-") + "-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]).lstrip("-")
    return slug


def is_blocked(key, blocked):
    """Hidden by an admin: this very topic, or a phrase holding a hidden one ("x" hides "x y")."""
    if key in blocked:
        return True
    if key.startswith("link:"):
        return False
    padded = f" {key} "
    return any(f" {b} " in padded for b in blocked if not b.startswith("link:"))


def candidates(window, min_authors=MIN_AUTHORS):
    """Keys that enough different accounts posted about in the window."""
    authors = {}
    for entry in window:
        for key in entry.terms:
            authors.setdefault(key, set()).add(entry.post.author_id)
    return {key for key, who in authors.items() if len(who) >= min_authors}


def rank(window, baseline, now, min_authors=MIN_AUTHORS, blocked=(), top=TOP):
    """The topics of the moment, best first.

    window:   Entry rows of the last WINDOW_SECONDS
    baseline: Entry rows of the BASELINE_DAYS before that
    """
    keys = candidates(window, min_authors)
    if not keys:
        return []

    weight, posts, labels, kinds, points = {}, {}, {}, {}, Counter()
    for entry in window:
        post = entry.post
        fresh = freshness(now - post.created_utc)
        for key, (kind, label) in entry.terms.items():
            if key not in keys:
                continue
            by_author = weight.setdefault(key, {})
            by_author[post.author_id] = max(by_author.get(post.author_id, 0.0), fresh)   # an account counts once
            posts.setdefault(key, []).append((post.created_utc, post.id))
            labels.setdefault(key, Counter())[label] += 1
            if kind == NAME or key not in kinds:
                kinds[key] = kind
            points[key] += max(post.votes, 0) + 2 * post.comments

    usual, seen_in, total = {}, Counter(), 0
    for entry in baseline:
        total += 1
        day = entry.post.created_utc // 86400
        for key in entry.terms:
            if key in keys:
                usual.setdefault(key, set()).add((entry.post.author_id, day))
                seen_in[key] += 1

    topics = []
    for key in keys:
        if total >= COMMON_MIN_POSTS and seen_in[key] / total > COMMON_SHARE:
            continue
        count = len(weight[key])
        rise = burst(count, len(usual.get(key, ())) / BASELINE_DAYS)
        if rise <= 0:
            continue
        kind = kinds[key]
        kind_weight = WEIGHT[kind] if (kind != PHRASE or " " in key) else ONE_WORD_WEIGHT
        recency = 0.5 + 0.5 * sum(weight[key].values()) / count
        score = round(rise * recency * engagement(points[key]) * kind_weight, 4)
        label = sorted(labels[key].items(), key=lambda item: (-item[1], item[0]))[0][0]
        ids = [pid for _, pid in sorted(set(posts[key]), reverse=True)]
        topics.append(Topic(key, kind, label, score, count, ids))

    topics.sort(key=lambda t: (-t.score, -t.authors, t.key))
    # hidden topics go after tidying, so that hiding "etna" does not leave "mount" behind
    return [t for t in tidy(topics[:TIDY_LIMIT]) if not is_blocked(t.key, blocked)][:top]


def _inside(short, long):
    return short != long and f" {short} " in f" {long} "


def tidy(topics):
    """One topic where several say the same thing. `topics` best first; the order is kept."""
    alive = {t.key: t for t in topics}
    phrase_keys = [t.key for t in topics if t.kind != LINK]

    # a phrase inside a longer phrase: every post of the longer one has the shorter one too.
    # Keep the longer when it covers most of them ("world cup final"), else the shorter.
    for short in sorted(phrase_keys, key=len):
        for long in phrase_keys:
            if short not in alive or long not in alive or not _inside(short, long):
                continue
            a, b = alive[short], alive[long]
            if len(set(b.post_ids)) >= COVER * len(set(a.post_ids)):
                b.score = max(a.score, b.score)
                del alive[short]
            else:
                del alive[long]

    kept = []
    for topic in sorted(alive.values(), key=lambda t: (-t.score, -t.authors, t.key)):
        mine = set(topic.post_ids)
        if any(len(mine & set(other.post_ids)) >= COVER * min(len(mine), len(set(other.post_ids))) for other in kept):
            continue        # the same posts as a better topic (a link and the words of its title)
        kept.append(topic)
    return kept


def merge(lists, top=TOP):
    """Several scopes' lists as one (a viewer filtering by two regions): best score first,
    a topic once. Rows need `.slug` and `.score`."""
    best = {}
    for rows in lists:
        for row in rows:
            if row.slug not in best or row.score > best[row.slug].score:
                best[row.slug] = row
    return sorted(best.values(), key=lambda r: (-r.score, r.slug))[:top]
