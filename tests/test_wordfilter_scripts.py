"""The word filter in every script (ruqqus/helpers/wordfilter.py, the "native" path), the built-in words for other
languages (helpers/wordfilter_intl.json, read by helpers/wordfilter_seed.py) and the store's table side
(helpers/word_filter_store.py: listed_entries, add_builtin) on SQLite.

The promises: a word listed in another script matches text in that script and never through the Latin look-alike
folding (so a Cyrillic entry never fires on an English word, nor an English one on a Cyrillic word that merely
transliterates to it); English keeps every dodge it caught; scripts written without spaces are found inside the text
and an allowed compound protects the word in it; the version moves with the list."""
import hashlib
import importlib.util
import json
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "ruqqus" / "helpers" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wordfilter = _load("wordfilter")
seed = _load("wordfilter_seed")
WordFilter = wordfilter.WordFilter


def _e(word, severity=1, mode="word", variants=(), suffixes=(), language=None):
    return {"word": word, "severity": severity, "mode": mode, "variants": list(variants),
            "suffixes": None if suffixes is None else list(suffixes), "language": language}


# Innocent stand-ins ("dog", "fir tree", "pea"...): the script's own rules are the point, and like test_wordfilter.py
# this file keeps offensive words out of itself.
NATIVE = [
    _e("собака", variants=["собачка"], suffixes=["ми"], language="ru"),
    _e("ёлка", language="ru"),
    _e("сон", severity=2, language="ru"),            # its Latin skeleton is "con": must never fire on English
    _e("кошка", mode="anywhere", language="ru"),
    _e("λόγος", language="el"),
    _e("كلب", language="ar"),
    _e("قط", language="ar"),                         # two letters: never reached by taking a proclitic off
    _e("כלב", language="he"),
    _e("कुत्ता", severity=2, language="hi"),
    _e("หมาบ้า", mode="anywhere", language="th"),
    _e("笨蛋", mode="anywhere", variants=["笨旦"], language="zh"),
    _e("笨蛋树", severity=0, language="zh"),          # an allowed compound protects the word inside it
    _e("狗", mode="anywhere", language="zh"),         # one character: never used
    _e("いぬ", mode="anywhere", language="ja"),
    _e("강아지", mode="anywhere", language="ko"),
    _e("горох", severity=2, language="ru"),
    _e("горохов", severity=0, language="ru"),        # allowed in its own script
]
FILTER = WordFilter(seed.ENTRIES + NATIVE, seed.ALLOW_PHRASES + ["сон разума"])
ENGLISH = WordFilter(seed.ENTRIES, seed.ALLOW_PHRASES)


@pytest.mark.parametrize("text, base", [
    ("собака", "собака"),
    ("СОБАКА", "собака"),
    ("Собаааака", "собака"),
    ("ну собака!", "собака"),
    ("собачка", "собака"),               # a variant reports the base word
    ("собаками", "собака"),              # an ending of its own
    ("с о б а к а", "собака"),           # spelled out
    ("с.о.б.а.к.а", "собака"),
    ("соба\u200bка", "собака"),          # a zero-width space
    ("ёлка", "елка"),
    ("елка", "елка"),                    # ё is е
    ("ЁЛКА", "елка"),
    ("большаякошкадома", "кошка"),       # "anywhere" inside a longer token
    ("λόγος", "λογοσ"),
    ("ΛΟΓΟΣ", "λογοσ"),                  # capitals and the final sigma
    ("λογος", "λογοσ"),                  # accents left off
    ("كلب", "كلب"),
    ("كَلْب", "كلب"),                     # tashkeel
    ("كلـــب", "كلب"),                   # tatweel
    ("والكلب", "كلب"),                   # and + the
    ("بالكلب", "كلب"),                   # with + the
    ("للكلب", "كلب"),                    # for + the
    ("فكلب", "كلب"),
    ("قط", "قط"),
    ("כלב", "כלב"),
    ("כֶּלֶב", "כלב"),                     # niqqud
    ("कुत्ता", "कुत्ता"),
])
def test_a_word_in_another_script_matches_its_own_script(text, base):
    assert [(b, s) for _, b, s in FILTER.explain(text)][:1] == [(base, FILTER.severity(text))]
    assert FILTER.severity(text) >= 1


@pytest.mark.parametrize("text", [
    "sobaka", "SOBAKA",                   # a transliteration is not the word
    "con", "Con", "icon", "conman",       # "сон" folds to "con": the English words are clean
    "сонный",                             # whole word only
    "собаковод",                          # an ending nobody listed
    "कत्ता",                               # Devanagari vowel signs are letters: without them it is another word
    "كلبة اليوم",                          # a longer word
    "والقط", "وقط",                        # a two-letter word is never reached through a proclitic
    "горохов",                            # allowed in its own script
    "сон разума",                         # an allowed phrase in Cyrillic
    "привет, как дела?", "Доброе утро", "مرحبا بكم", "שלום עולם", "नमस्ते", "สวัสดีครับ", "你好世界", "こんにちは", "안녕하세요",
])
def test_innocent_text_in_any_script_is_clean(text):
    assert FILTER.explain(text) == []


LOOK_ALIKES = {"a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у"}


def test_a_cyrillic_entry_never_reaches_english_and_english_never_reaches_it():
    # "сон" in Latin look-alikes is "con"
    assert FILTER.severity("con") == 0
    assert FILTER.severity("сон") == 2
    # an English word written with Cyrillic look-alike letters is still the English word (the dodge folding)
    english = next(e["word"] for e in seed.ENTRIES
                   if e["severity"] == 1 and any(c in LOOK_ALIKES for c in e["word"][1:]))
    dodged = english[0] + "".join(LOOK_ALIKES.get(c, c) for c in english[1:])
    assert dodged != english
    assert FILTER.severity(dodged) == 1 and ENGLISH.severity(dodged) == 1


@pytest.mark.parametrize("technique", ["plain", "upper", "repeated letters", "digits for letters",
                                       "dots between letters", "spelled out", "look-alike alphabet", "fullwidth",
                                       "zero-width joiners"])
def test_english_dodges_are_unchanged_with_other_scripts_listed(technique):
    spec = importlib.util.spec_from_file_location("wordfilter_cases", ROOT / "tests" / "test_wordfilter.py")
    cases = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cases)
    for entry in cases.LISTED:
        assert FILTER.severity(cases.EVASIONS[technique](entry["word"])) >= entry["severity"]


@pytest.mark.parametrize("text, base", [
    ("他是笨蛋", "笨蛋"),
    ("你笨旦吗", "笨蛋"),
    ("笨 蛋", "笨蛋"),                    # a space between two characters is read through
    ("笨.蛋", "笨蛋"),
    ("笨・蛋", "笨蛋"),
    ("笨笨蛋", "笨蛋"),                    # a repeated character
    ("笨\u200b蛋", "笨蛋"),
    ("あのいぬが", "いぬ"),
    ("강아지야", "강아지"),                 # Korean particles attach to the word
    ("ไอ้หมาบ้าเลย", "หมาบ้า"),
])
def test_scripts_without_spaces_are_found_inside_the_text(text, base):
    assert [b for _, b, _ in FILTER.explain(text)] == [base]


@pytest.mark.parametrize("text", [
    "笨\n蛋",                              # a line break is never read through
    "笨。蛋糕",                            # nor a full stop
    "这棵笨蛋树很高",                       # the allowed compound masks the word inside it
    "狗",                                  # a one-character entry is never used
    "笨重", "หมา", "강아",
])
def test_scripts_without_spaces_keep_their_boundaries(text):
    assert FILTER.explain(text) == []


def test_halfwidth_katakana_and_html_blocks():
    f = WordFilter([_e("イヌ", mode="anywhere", language="ja")])
    assert f.severity("ｲﾇ") == 1                                    # halfwidth, NFKC
    assert FILTER.severity_html("<p>笨<b>蛋</b></p>") == 1          # inline tags are transparent
    assert FILTER.severity_html("<p>笨</p><p>蛋</p>") == 0          # separate blocks are not joined


def test_severity_and_explain_mix_scripts():
    assert FILTER.severity("собака and 笨蛋 and कुत्ता") == 2
    found = FILTER.explain("well собака, 笨蛋")
    assert ("собака,", "собака", 1) in found and ("笨蛋", "笨蛋", 1) in found


def test_word_script():
    assert wordfilter.word_script("hello") == "Latin"
    assert wordfilter.word_script("straße") == "Latin"
    assert wordfilter.word_script("ｆｕｃｋ") == "Latin"
    assert wordfilter.word_script("собака") == "Cyrillic"
    assert wordfilter.word_script("笨蛋") == "Han"
    assert wordfilter.word_script("いぬ") == "Hiragana"
    assert wordfilter.word_script("강아지") == "Hangul"
    assert wordfilter.word_script("123!") is None
    assert WordFilter.clean_word("Sch.eiße!") == "scheisse"
    assert WordFilter.clean_word("Соба-ка!") == "собака"
    assert WordFilter.clean_word("كَلْب") == "كلب"
    assert WordFilter.clean_word("1234") == ""


def test_latin_words_of_other_languages_fold_accents():
    f = WordFilter([_e("straße", language="de"), _e("niño", suffixes=["s"], language="es")])
    for text in ("straße", "STRASSE", "strasse", "Stráße", "niños", "NINO"):
        assert f.severity(text) == 1, text
    assert f.severity("ninot") == 0
    # an explicit empty list of endings never takes the English ones
    assert f.severity("straßes") == 0 and f.severity("ninoing") == 0


def _old_version(entries, phrases):
    """The digest exactly as the engine made it before it read other scripts (ENGINE_REVISION 1)."""
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda e: (e["word"], e.get("severity", 0))):
        base = WordFilter._letters(entry["word"])
        if not base:
            continue
        variants = [WordFilter._letters(v) for v in (entry.get("variants") or [])]
        suffixes = entry.get("suffixes")
        if suffixes is None:
            suffixes = wordfilter.DEFAULT_SUFFIXES
        digest.update(repr((base, int(entry.get("severity", 0)), entry.get("mode") or "word", variants,
                            tuple(suffixes))).encode())
    for phrase in phrases:
        words = tuple(WordFilter._letters(w) for w in phrase.split())
        if len(words) > 1:
            digest.update(repr(words).encode())
    return digest.hexdigest()[:12]


def test_the_engine_revision_moves_every_version_and_every_other_change_moves_it():
    # the matching rules changed with other scripts, so even an English-only list gets a new version once and its
    # stored ratings are made again by the re-scan
    assert wordfilter.ENGINE_REVISION >= 2
    assert ENGLISH.version != _old_version(seed.ENTRIES, seed.ALLOW_PHRASES)
    assert ENGLISH.version == WordFilter(list(reversed(seed.ENTRIES)), seed.ALLOW_PHRASES).version
    assert FILTER.version != ENGLISH.version
    base = WordFilter(NATIVE)
    for change in (NATIVE + [_e("дерево")],
                   [dict(e, severity=2) if e["word"] == "собака" else e for e in NATIVE],
                   [dict(e, language="uk") if e["word"] == "собака" else e for e in NATIVE],
                   [dict(e, variants=[]) if e["word"] == "笨蛋" else e for e in NATIVE],
                   [e for e in NATIVE if e["word"] != "笨蛋树"]):
        assert WordFilter(change).version != base.version
    assert WordFilter(NATIVE, ["сон разума"]).version != base.version
    assert WordFilter(list(reversed(NATIVE))).version == base.version


# ------------------------------------------------------------------ dodges and allowed words (review, 2026-10-29)
BUILTIN = WordFilter(seed.BUILTIN_ENTRIES, seed.BUILTIN_ALLOW_PHRASES)
CGJ, VS16, VS17, FILLER, HALF_FILLER, CHOSEONG_FILLER, STRESS = "\u034f", "\ufe0f", "\U000e0100", "\u3164", "\uffa0", \
    "\u115f", "\u0301"


@pytest.mark.parametrize("text", [
    "соба" + CGJ + "ка", "собака" + CGJ, "соба" + VS16 + "ка", "со" + STRESS + "бака", "собака\u20dd",
    "笨" + CGJ + "蛋", "笨" + VS16 + "蛋", "笨" + VS17 + "蛋", "笨" + FILLER + "蛋", "笨" + HALF_FILLER + "蛋",
    "笨" + CHOSEONG_FILLER + "蛋", "강아" + CGJ + "지", "강아" + FILLER + "지",
])
def test_invisible_marks_and_fillers_inside_a_word_in_another_script_change_nothing(text):
    assert FILTER.severity(text) >= 1, text


def test_the_marks_a_script_writes_its_letters_with_are_kept():
    # a Devanagari vowel sign is a letter: without it the word is another word
    assert FILTER.severity("कुत्ता") == 2 and FILTER.severity("कत्त") == 0
    assert FILTER.severity("หมาบ้า") == 1


@pytest.mark.parametrize("text, severity", [
    ("fu\u0640ck", 1), ("f\u0640u\u0640c\u0640k", 1), ("nig\u0640ger", 2), ("ni\u0640gger", 2), ("cunt\u0640s", 1),
    ("fu\u0559ck", 1), ("nig\u0559ger", 2), ("fu\u10fcck", 1), ("fu\u07fack", 1),     # modifier letters of other scripts
    ("fuбck", 1),                                                                 # one foreign letter in Latin ones
])
def test_decoration_from_another_script_inside_a_latin_word_is_read_through(text, severity):
    assert BUILTIN.severity(text) == severity, text


def test_a_word_mostly_in_another_script_is_not_read_as_latin():
    assert BUILTIN.severity("сообщили") == 0 and BUILTIN.severity("Об этом сообщили в пресс-службе") == 0
    assert BUILTIN.explain("κύκλος") == []


@pytest.mark.parametrize("text, severity", [
    ("nigger.pikk", 2), ("nigger-pikk", 2), ("nigger-kukk", 2), ("cunt-negeer", 1), ("nigger-فينيك", 2),
    ("fuck,فينيك", 1), ("pikk-fuck", 1), ("assess-cunt", 1),
])
def test_an_allowed_word_never_excuses_a_listed_word_beside_it(text, severity):
    assert BUILTIN.severity(text) == severity, text


def test_an_allowed_word_still_excuses_itself():
    for text in ("pikk", "pikk.", "فينيك", "فينيك!", "assess", "kút", "szár", "горохов"):
        assert BUILTIN.severity(text) == 0 or FILTER.severity(text) == 0, text
    assert FILTER.severity("горохов") == 0 and FILTER.severity("горох") == 2


def test_an_accented_allowed_word_never_excuses_an_extreme_listed_one():
    allowed = WordFilter(seed.ENTRIES + [_e("fág", severity=0, language="ga"), _e("kút", severity=0, language="hu")])
    assert allowed.severity("fág") == 2 and allowed.severity("FÁG!") == 2       # its skeleton is the extreme slur
    assert allowed.severity("kút") == 0                                          # nothing extreme: allowed as written
    assert allowed.listed_severity("fág") == 2 and allowed.listed_severity("hello") == 0
    assert WordFilter.allowed_form("Kút") == "kút" and WordFilter.allowed_form("pikk") == "pikk"
    assert WordFilter.allowed_form("кошка") == WordFilter.clean_word("кошка")


@pytest.mark.parametrize("text, severity", [
    ("씨발명", 1), ("씨발송", 1),                       # an allowed word that only shares a syllable
    ("笨蛋树", 0),                                       # an allowed compound that holds the word
    ("他妈妈的生日", 0),                                 # the listed word only reaches it by a doubled character
])
def test_an_allowed_compound_protects_only_what_it_holds(text, severity):
    assert BUILTIN.severity(text) == severity if not text.startswith("笨") else FILTER.severity(text) == severity


def test_hostile_input_and_speed_in_other_scripts():
    for nasty in ["笨" * 50_000, "б " * 20_000, "ال" * 20_000, "ـ" * 10_000, "<p>" + "我" * 10_000]:
        FILTER.severity(nasty)
        FILTER.severity_html(nasty)
    comments = ["<p>" + " ".join(["Это обычный комментарий ни о чём, со ссылкой"] * 10) + "</p>",
                "<p>" + "这是一条普通的评论，没有什么特别的内容。" * 10 + "</p>",
                "<p>" + " ".join(["هذا تعليق عادي لا شيء فيه"] * 10) + "</p>"]
    start = time.perf_counter()
    for _ in range(100):
        for comment in comments:
            assert FILTER.severity_html(comment) == 0
    assert time.perf_counter() - start < 5


# ------------------------------------------------------------------ the built-in list for other languages
def test_the_intl_file_has_the_agreed_shape():
    data = json.loads((ROOT / "ruqqus" / "helpers" / "wordfilter_intl.json").read_text(encoding="utf-8"))
    for key in ("source", "url", "license", "license_url", "fetched", "adapted", "entries", "allow_phrases"):
        assert key in data
    assert data["license"] == "CC BY 4.0"
    languages = _languages()
    for entry in data["entries"]:
        assert set(entry) == {"word", "severity", "mode", "variants", "suffixes", "language"}, entry
        assert entry["severity"] in (0, 1, 2) and entry["mode"] in ("word", "anywhere")
        assert isinstance(entry["suffixes"], list) and isinstance(entry["variants"], list)
        assert entry["language"] in languages, entry
    for phrase in data["allow_phrases"]:
        assert set(phrase) == {"phrase", "language"} and phrase["language"] in languages
    assert len(seed.INTL_ENTRIES) == len(data["entries"])
    assert seed.BUILTIN_ENTRIES == seed.ENTRIES + seed.INTL_ENTRIES


def _languages():
    source = (ROOT / "ruqqus" / "helpers" / "languages.py").read_text(encoding="utf-8")
    block = source.split("LANGUAGE_NAMES = {", 1)[1].split("}", 1)[0]
    import re
    return set(re.findall(r'"([a-z]{2,3})":', block))


def test_every_built_in_word_is_caught_by_the_built_in_filter():
    builtin = WordFilter(seed.BUILTIN_ENTRIES, seed.BUILTIN_ALLOW_PHRASES)
    for entry in seed.INTL_ENTRIES:
        if entry["severity"] == 0:
            continue
        script = wordfilter.word_script(entry["word"])
        if script in wordfilter.SPACELESS_SCRIPTS and len(WordFilter.clean_word(entry["word"])) < wordfilter.MIN_SPACELESS:
            continue
        assert builtin.severity(entry["word"]) >= entry["severity"], entry["word"]
    for phrase in seed.BUILTIN_ALLOW_PHRASES:
        assert builtin.explain(phrase) == [], phrase


def test_the_loader_takes_an_unknown_or_broken_file_quietly(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert seed.load_intl(bad) == ({}, [], [])
    assert seed.load_intl(tmp_path / "missing.json") == ({}, [], [])
    odd = tmp_path / "odd.json"
    odd.write_text(json.dumps({"source": "x", "entries": [
        {"word": "ok", "severity": 1, "mode": "word", "variants": [], "language": "de"},     # no suffixes: []
        {"word": "", "severity": 1}, {"word": "x", "severity": 5}, {"word": "y", "severity": 1, "mode": "regex"}, 7,
    ], "allow_phrases": [{"phrase": "a b", "language": None}, {"phrase": ""}]}), encoding="utf-8")
    source, entries, phrases = seed.load_intl(odd)
    assert source == {"source": "x"}
    assert entries == [{"word": "ok", "severity": 1, "mode": "word", "variants": [], "suffixes": [], "language": "de"}]
    assert phrases == [{"phrase": "a b", "language": None}]


# ------------------------------------------------------------------ the table: migration and store
MIGRATION = "2026-12-07_word_filter_language.sql"


def test_the_migration_is_safe_to_run_twice_and_mirrored():
    sql = (ROOT / "scripts" / "migrations" / MIGRATION).read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS language character varying(16)" in sql
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
    table = schema.split("CREATE TABLE public.word_filter_entries (", 1)[1].split(");", 1)[0]
    assert "language character varying(16) DEFAULT NULL" in table
    assert f"('{MIGRATION}')" in schema.split("INSERT INTO public.schema_migrations", 1)[1]


def _db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("""CREATE TABLE word_filter_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, word VARCHAR(64) NOT NULL, severity SMALLINT DEFAULT 1 NOT NULL,
            mode VARCHAR(16) DEFAULT 'word' NOT NULL, variants VARCHAR(512) DEFAULT '', suffixes VARCHAR(512),
            enabled BOOLEAN DEFAULT 1 NOT NULL, note VARCHAR(256) DEFAULT '', created_utc INTEGER DEFAULT 0,
            language VARCHAR(16), CONSTRAINT word_filter_entries_word_key UNIQUE (word))""")
    return Session(engine)


@pytest.fixture
def store():
    from ruqqus.helpers import word_filter_store
    return word_filter_store


def test_an_empty_table_lists_the_built_in_words(store):
    db = _db()
    listed = store.listed_entries(db)
    assert listed["builtin"] is True
    assert len(listed["entries"]) == len(store.wordfilter_seed.BUILTIN_ENTRIES)
    assert {"word", "severity", "mode", "variants", "suffixes", "language"} == set(listed["entries"][0])
    assert listed["source"]["license"] == "CC BY 4.0"
    assert {"phrase": "maine coon", "language": None} in listed["allow_phrases"]
    languages = [e["language"] or "" for e in listed["entries"]]
    assert languages == sorted(languages)
    assert store._load(db).version == WordFilter(store.wordfilter_seed.BUILTIN_ENTRIES,
                                                 store.wordfilter_seed.BUILTIN_ALLOW_PHRASES).version


def test_adding_the_built_in_words_is_idempotent_and_never_changes_a_row(store):
    db = _db()
    intl = store.wordfilter_seed.INTL_ENTRIES
    taken = intl[0]["word"]
    db.execute(text("INSERT INTO word_filter_entries (word, severity, mode, enabled, note) "
                    "VALUES (:w, 2, 'word', 0, 'mine')"), {"w": taken})
    db.execute(text("INSERT INTO word_filter_entries (word, severity, mode, enabled) VALUES ('zzzexample', 1, 'word', 1)"))

    would = store.add_builtin(db, "intl", dry_run=True)
    assert taken not in would and len(would) == len(intl) - 1 + len(store.wordfilter_seed.INTL_ALLOW_PHRASES)
    assert db.execute(text("SELECT count(*) FROM word_filter_entries")).scalar() == 2

    added = store.add_builtin(db, "intl", now=1000)
    assert added == would
    assert store.add_builtin(db, "intl") == []                                   # a second run adds nothing
    row = db.execute(text("SELECT severity, enabled, note, language FROM word_filter_entries WHERE word = :w"),
                     {"w": taken}).one()
    assert tuple(row) == (2, 0, "mine", None)                                    # the admin's row is untouched
    langs = dict(db.execute(text("SELECT word, language FROM word_filter_entries")).fetchall())
    for entry in intl[1:]:
        assert langs[entry["word"]] == entry["language"]
    suffixes = db.execute(text("SELECT suffixes FROM word_filter_entries WHERE word = :w"),
                          {"w": intl[1]["word"]}).scalar()
    assert suffixes is not None                                                  # never NULL: no English endings

    listed = store.listed_entries(db)
    assert listed["builtin"] is False
    assert taken not in [e["word"] for e in listed["entries"]]                   # turned off, so not listed
    live = store._load(db)
    for entry in intl[1:]:
        if entry["severity"]:
            assert live.severity(entry["word"]) >= entry["severity"]


def test_a_built_in_word_already_on_the_list_in_another_spelling_is_not_added_again(store):
    # the admin form stores "maricon", "scheiss" and Arabic letters; turned off, they stay off
    db = _db()
    for word in ("maricon", "scheiss", "كير"):
        db.execute(text("INSERT INTO word_filter_entries (word, severity, mode, enabled) VALUES (:w, 1, 'word', 0)"),
                   {"w": word})
    added = store.add_builtin(db, "intl")
    for spelled in ("maricón", "scheiß", "کیر"):
        assert spelled not in added
    live = store._load(db)
    assert live.severity("maricón") == 0 and live.severity("کیر") == 0
    # an allowed word written with an accent is not the listed word its skeleton spells
    assert store._entry_key("kút", 0) != store._entry_key("kut", 1)
    assert store._entry_key("maricon", 0) == store._entry_key("maricón", 1)
    assert store._entry_key("Tapette à mouches", 0) == store._entry_key("tapette a mouches", 0)


def test_copying_the_whole_built_in_list(store):
    db = _db()
    added = store.add_builtin(db, "all")
    seedmod = store.wordfilter_seed
    assert len(added) == len(seedmod.ENTRIES) + len(seedmod.ALLOW_PHRASES) + len(seedmod.INTL_ENTRIES) \
        + len(seedmod.INTL_ALLOW_PHRASES)
    assert store.add_builtin(db, "all") == []
    assert store._load(db).version == WordFilter(seedmod.BUILTIN_ENTRIES, seedmod.BUILTIN_ALLOW_PHRASES).version
    english = db.execute(text("SELECT suffixes, language FROM word_filter_entries WHERE word = :w"),
                         {"w": seedmod.ENTRIES[0]["word"]}).one()
    assert tuple(english) == (None, None)                                        # the usual endings, no language
