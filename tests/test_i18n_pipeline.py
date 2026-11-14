"""The interface's catalogues (scripts/i18n_catalogs.py) and their machine translation by the site's LibreTranslate
(scripts/i18n_translate.py; docs/i18n.md): what a translation must keep to be drawn, how a sentence is sent and read
back, plurals, the catalogue kept up to date and never overwriting a human fix, and the committed catalogues and
script tables themselves."""
import json
import re
import subprocess
from pathlib import Path

import pytest
from babel.messages.catalog import Catalog
from babel.messages.pofile import read_po, write_po

from scripts import i18n_catalogs as cat
from scripts import i18n_translate as tx

ROOT = Path(__file__).resolve().parent.parent


# --- what a translation must keep -----------------------------------------------------------------------------------------

def test_a_translation_keeps_the_values_the_tags_and_nothing_unsafe():
    assert cat.problems("Hello %(name)s", "Hola %(name)s") == []
    assert cat.problems("Hello %(name)s", "Hola") == ["placeholders differ"]
    assert cat.problems("Hello %(name)s", "Hola %(nombre)s") == ["placeholders differ"]
    english = 'Read the <a href="/help/rules">rules</a>.'
    assert cat.problems(english, 'Lee las <a href="/help/rules">reglas</a>.') == []
    assert "tag <a href=\"/evil\"> is not the English's" in cat.problems(english, 'Lee <a href="/evil">x</a>.')
    assert cat.problems("Save", "<script>alert(1)</script>Guardar") != []
    assert cat.problems("Save", '<img src=x onerror="alert(1)">') != []
    assert cat.problems("100%% sure", "100%% seguro") == []
    assert cat.problems("Sure", "100% seguro") == ["a lone % (write %%)"]


def test_a_plural_form_may_say_one_without_the_number():
    entry = type("M", (), {"id": ("%(num)s comment", "%(num)s comments"), "string": ("un comentario", "%(num)s comentarios")})()
    assert cat.entry_problems(entry) == []
    entry.string = ("un comentario", "comentarios")
    assert cat.entry_problems(entry) == ["placeholders differ"]


# --- sending a sentence --------------------------------------------------------------------------------------------------

def test_text_mode_keeps_values_names_brands_and_links_as_tokens():
    masked, tokens = tx.text_encode("Hi %(name)s, read the [rules](/help/rules) on YouTube, 100%% sure, @alice in +pets.")
    assert masked == "Hi {2}, read the [rules]({1}) on {3}, 100{4} sure, {5} in {6}."
    assert tx.text_decode("Hola {2}, lee las [reglas]({1}) en {3}, 100{4} seguro, {5} en {6}.", tokens) == \
        "Hola %(name)s, lee las [reglas](/help/rules) en YouTube, 100%% seguro, @alice en +pets."
    assert tx.text_decode("Hola, lee las reglas", tokens) is None                  # a token lost
    assert tx.text_decode("Hola {2} {2} {1} {3} {4} {5} {6}", tokens) is None       # a token twice
    assert tx.text_encode('Read the <a href="/x">rules</a>') is None               # tags: HTML mode
    assert tx.text_encode("Winter Interface")[1] == {}                             # brand names are whole words


def test_html_mode_keeps_tags_and_puts_their_attributes_back():
    english = 'Read the <a href="%(url)s" class="x">rules</a> **now**, %(name)s<br>'
    sent, restore = tx.html_encode(english)
    assert 'href' not in sent and '<x id=' in sent and '<a id="t' in sent
    answer = sent.replace("Read the", "Lee las").replace("rules", "reglas").replace("now", "ya")
    assert tx.html_decode(answer, restore, english) == 'Lee las <a href="%(url)s" class="x">reglas</a> **ya**, %(name)s<br>'
    assert tx.html_decode(answer.replace('<x id="p5"></x>', ""), restore, english) is None


def test_a_hint_is_cut_and_a_plural_number_put_back():
    job = tx.Job("Post", hint="button")
    assert job.sent() == "Post (button)" and job.finish("Publicar (botón)") == "Publicar"
    assert tx.Job("Post", hint="button").finish("投稿（ボタン）") == "投稿"
    job = tx.Job("%(num)s comments", number=5)
    assert job.sent() == "5 comments" and job.finish("5 comentarios") == "%(num)s comentarios"
    assert tx.Job("%(num)s comments", number=5).finish("٥ تعليقات") == "%(num)s تعليقات"     # Arabic-Indic digits
    assert tx.Job("%(num)s comments", number=5).finish("cinco comentarios") is None
    assert tx.Job("Hello %(name)s").finish("Hola") is None                                  # the checks apply


def test_one_example_number_for_each_plural_form():
    assert tx.plural_examples(Catalog(locale="ar")) == [0, 1, 2, 3, 100, 11]
    assert tx.plural_examples(Catalog(locale="ru")) == [1, 2, 0]
    assert tx.plural_examples(Catalog(locale="ja")) == [0]
    assert tx.plural_examples(Catalog(locale="es"))[:2] == [1, 0]


# --- a catalogue ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.setattr(cat, "OUT", tmp_path)
    template = Catalog(domain="messages")
    template.add("Save")
    template.add("Hello %(name)s")
    template.add(("%(num)s comment", "%(num)s comments"))
    template.add("Post", context="button")
    template.add("%(num)s")
    with (tmp_path / "messages.pot").open("wb") as f:
        write_po(f, template)
    return tmp_path


def fake_spanish(texts, target, fmt):
    words = {"Save": "Guardar", "Hello": "Hola", "comment": "comentario", "comments": "comentarios",
             "Post (publish a message)": "Publicar (publicar un mensaje)", "Save (keep for later)": "Guardar (guardar para después)"}
    out = []
    for text in texts:
        for english, spanish in words.items():
            text = text.replace(english, spanish)
        out.append(text)
    return out


def test_a_catalogue_is_translated_flagged_and_never_overwrites_a_human_fix(tree, monkeypatch):
    monkeypatch.setattr(tx, "call", fake_spanish)
    result = tx.translate_catalog("es", "messages", "es", workers=1)
    assert result["translated"] == 5 and result["failed"] == 0
    po = tree / "es" / "LC_MESSAGES" / "messages.po"
    catalog = read_po(po.open("rb"))
    assert catalog.get("Save").string == "Guardar" and "machine" in catalog.get("Save").flags
    assert catalog.get("Hello %(name)s").string == "Hola %(name)s"
    assert catalog.get(("%(num)s comment", "%(num)s comments")).string == ("%(num)s comentario", "%(num)s comentarios")
    assert catalog.get("Post", context="button").string == "Publicar"
    assert catalog.get("%(num)s").string == "%(num)s"                   # nothing to translate
    # someone corrects one by hand: a new run leaves it, and only new entries are sent
    catalog.get("Save").string = "Guardar cambios"
    catalog.get("Save").flags.discard("machine")
    with po.open("wb") as f:
        write_po(f, catalog)
    sent = []
    monkeypatch.setattr(tx, "call", lambda texts, target, fmt: sent.extend(texts) or fake_spanish(texts, target, fmt))
    assert tx.translate_catalog("es", "messages", "es", workers=1)["to_do"] == 0 and sent == []
    assert read_po(po.open("rb")).get("Save").string == "Guardar cambios"


def test_what_fails_the_checks_stays_english_and_is_reported(tree, monkeypatch):
    monkeypatch.setattr(tx, "call", lambda texts, target, fmt: ["Hola" for _t in texts])     # drops every value
    result = tx.translate_catalog("es", "messages", "es", workers=1)
    assert "Hello %(name)s" in result["failed_strings"]
    catalog = read_po((tree / "es" / "LC_MESSAGES" / "messages.po").open("rb"))
    assert not catalog.get("Hello %(name)s").string


def test_the_scripts_table_is_made_from_the_js_catalogue():
    catalog = Catalog(locale="es", domain="js")
    catalog.add("Saved.", "Guardado.")
    catalog.add(("%(num)s item", "%(num)s items"), ("%(num)s elemento", "%(num)s elementos"))
    catalog.add("Broken %(x)s", "Roto")                                   # fails the checks: left out
    catalog.add("Fuzzy", "Borroso", flags=["fuzzy"])
    source = cat.js_source("es", catalog)
    table = json.loads(source.split("window.I18N.messages = ")[1].split(";\n")[0])
    assert table == {"Saved.": "Guardado.", "%(num)s item\u0000%(num)s items": ["%(num)s elemento", "%(num)s elementos"]}
    assert "window.I18N.plural = function (n) { return Number((n != 1)); };" in source


# --- the committed catalogues ----------------------------------------------------------------------------------------------

def test_every_committed_translation_is_safe_to_draw():
    bad = []
    for folder in cat.language_folders():
        for po in sorted((folder / "LC_MESSAGES").glob("*.po")):
            for message in read_po(po.open("rb")):
                if message.id and not message.fuzzy and cat.entry_problems(message):
                    bad.append((po.relative_to(ROOT).as_posix(), message.id, cat.entry_problems(message)))
    assert bad == []


def test_the_scripts_tables_are_in_step_with_the_catalogues():
    from ruqqus.helpers import i18n
    for folder in cat.language_folders():
        po = folder / "LC_MESSAGES" / "js.po"
        if not po.exists():
            continue
        made = cat.JS_OUT / f"{folder.name}.js"
        catalog = cat.read(po, locale=i18n.babel_code(folder.name), domain="js")
        assert made.exists() and made.read_text(encoding="utf-8") == cat.js_source(folder.name, catalog), folder.name


def test_every_catalogue_language_is_one_the_site_offers():
    from ruqqus.helpers import i18n
    for folder in cat.language_folders():
        assert folder.name in i18n.interface_keys() and folder.name != "en", folder.name


def test_the_check_command_runs():
    out = subprocess.run([str(ROOT / ".venv" / "bin" / "python"), "scripts/i18n_catalogs.py", "check"],
                         cwd=ROOT, capture_output=True, text=True, env={"PYTHONPATH": str(ROOT)}, timeout=300)
    assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-2000:]


def test_a_sentence_about_angle_brackets_has_no_tags():
    assert cat.tags("%(field)s can't contain < or >.") == []
    assert cat.problems("%(field)s can't contain < or >.", "%(field)s no puede contener < ni >.") == []


def test_a_plural_form_before_the_last_may_say_its_number_in_words():
    assert tx.Job("%(num)s day ago", number=1, optional=True).finish("hace un día") == "hace un día"
    assert tx.Job("%(num)s days ago", number=5).finish("hace cinco días") is None            # the last form keeps it
    assert tx.Job("%(num)s day ago", number=1, optional=True).finish("hace 3 días") is None   # never another number


def test_entities_and_words_must_survive():
    assert cat.problems("a &middot; b", "a &middot } b") == ["entities differ"]
    assert cat.problems("%(role)s: read", "%(role)s:") == ["no words left"]
    assert cat.problems("%(num)s", "%(num)s") == []                        # nothing to read, nothing lost
    assert tx.text_encode("a &middot; b")[0] == "a {1} b"


def test_the_sites_shorthand_is_sent_as_plain_english():
    # the owner, 2026-10-25: shortened words and the site's own lingo reach LibreTranslate written out ("Rep" became
    # Japanese for "request"); the catalogue keeps the site's English
    assert tx.plain("%(score)s Rep") == "%(score)s Reputation"
    assert tx.plain("Mod Log") == "Moderation Log" and tx.plain("%(num)s min read") == "%(num)s minute read"
    assert tx.plain("%(num)s coin/mo") == "%(num)s coin per month"
    assert tx.plain("Please take a sec to verify") == "Please take a moment to verify"
    assert tx.plain("%(who)s added a flair") == "%(who)s added a label"
    assert tx.plain("Siege against +%(guild)s Failed") == "Takeover claim against +%(guild)s Failed"
    # never inside a value, a name, an address or code
    assert tx.plain("%(min)s and +flair and [flair](/help/flair) and `rep`") == "%(min)s and +flair and [label](/help/flair) and `rep`"
    assert tx.Job("%(score)s Rep").sent() == "%(score)s Reputation"
    assert tx.Job("Space", hint=tx.SHORT_HINTS["Space"]).sent() == "Space (a live audio room)"
    assert tx.text_encode("Upgrade to Premium for 64 MB")[0] == "Upgrade to {1} for 64 {2}"   # kept as they are


def test_an_older_machine_entry_the_plain_rules_change_is_made_again(tree, monkeypatch):
    po = tree / "es" / "LC_MESSAGES" / "messages.po"
    po.parent.mkdir(parents=True)
    old = Catalog(locale="es", domain="messages")
    old.add("Save", "Guardar", flags=["machine"])                       # in SHORT_HINTS: made again
    old.add("Hello %(name)s", "Hola %(name)s", flags=["machine"])       # nothing to change: kept
    with po.open("wb") as f:
        write_po(f, old)
    sent = []
    monkeypatch.setattr(tx, "call", lambda texts, target, fmt: sent.extend(texts) or fake_spanish(texts, target, fmt))
    tx.translate_catalog("es", "messages", "es", workers=1)
    assert "Save (keep for later)" in sent and not any(t.startswith("Hello") for t in sent)
    catalog = read_po(po.open("rb"))
    assert {"machine", tx.PLAIN_FLAG} <= set(catalog.get("Save").flags)


def test_signing_out_reaches_libretranslate_as_disconnecting():
    # 2026-11-12: "signed out" came back as signed (a document), signed in or subscribed; "log out" as logging in
    assert tx.plain("This device will be signed out shortly.") == "This device will be disconnected shortly."
    assert tx.plain("Sign out %(device)s") == "Disconnect %(device)s"
    assert tx.plain("Changing your password signs out chat on your other devices.") == \
        "Changing your password disconnects chat on your other devices."
    assert tx.plain("Resetting your password signs chat out on all your devices") == \
        "Resetting your password disconnects chat on all your devices"
    assert tx.plain("This browser was signed out of chat from another device") == \
        "This browser was disconnected from chat from another device"
    assert tx.plain("You logged out everywhere from another device") == "You disconnected everywhere from another device"
    assert tx.plain("Logging out everywhere ends that sign-in.") == "Disconnecting everywhere ends that sign-in."
    # signing in is left as it is, and an address keeps its words
    assert tx.plain("Sign in again to carry on.") == "Sign in again to carry on."
    assert tx.plain("[log out everywhere](/settings/security#log-out-everywhere)") == \
        "[disconnect everywhere](/settings/security#log-out-everywhere)"


def test_an_entry_made_with_the_first_plain_rules_is_made_again_only_where_signing_out_changes_it(tree, monkeypatch):
    template = Catalog(domain="messages")
    for msgid in ("Save", "This device will be signed out shortly.", "Your account was logged out", "This one was signed out"):
        template.add(msgid)
    with (tree / "messages.pot").open("wb") as f:
        write_po(f, template)
    po = tree / "es" / "LC_MESSAGES" / "messages.po"
    po.parent.mkdir(parents=True)
    old = Catalog(locale="es", domain="messages")
    old.add("This device will be signed out shortly.", "Este dispositivo será firmado en breve.", flags=["machine", "plain1"])
    old.add("Save", "Guardar", flags=["machine", "plain1"])                 # the first rules already sent it right: kept
    old.add("Your account was logged out", "Tu cuenta fue registrada", flags=["machine"])   # no plain flag at all: made again
    old.add("This one was signed out", "Esta fue cerrada a mano", flags=[])  # someone's own correction: never touched
    with po.open("wb") as f:
        write_po(f, old)
    sent = []
    monkeypatch.setattr(tx, "call", lambda texts, target, fmt: sent.extend(texts) or fake_spanish(texts, target, fmt))
    tx.translate_catalog("es", "messages", "es", workers=1)
    assert "This device will be disconnected shortly." in sent and "Your account was disconnected" in sent
    assert not any(t.startswith(("Save", "This one")) for t in sent)
    catalog = read_po(po.open("rb"))
    remade = catalog.get("This device will be signed out shortly.")
    assert remade.string != "Este dispositivo será firmado en breve."
    assert {"machine", "plain2"} <= set(remade.flags) and "plain1" not in remade.flags
    assert catalog.get("Save").string == "Guardar" and "plain1" in catalog.get("Save").flags
    assert catalog.get("This one was signed out").string == "Esta fue cerrada a mano"


# --- the glossary ---------------------------------------------------------------------------------------------------------

def test_the_glossary_gives_the_sites_own_words_and_never_overwrites_a_human_fix(tree, monkeypatch):
    # the owner, 2026-10-25: "Rep" should read as reputation, not as LibreTranslate's guess
    (tree / tx.GLOSSARY).write_text(json.dumps({"es": {"Post": {"button": "Publicar"}, "Save": {"": "Guardar"}}}),
                                    encoding="utf-8")
    sent = []
    monkeypatch.setattr(tx, "call", lambda texts, target, fmt: sent.extend(texts) or fake_spanish(texts, target, fmt))
    result = tx.translate_catalog("es", "messages", "es", workers=1)
    assert result["glossary"] == 2 and not any(t.startswith(("Post", "Save")) for t in sent)
    po = tree / "es" / "LC_MESSAGES" / "messages.po"
    catalog = read_po(po.open("rb"))
    assert catalog.get("Post", context="button").string == "Publicar"
    assert {"machine", "glossary"} <= set(catalog.get("Post", context="button").flags)
    # someone corrects one by hand; the glossary then changes: the correction stays, the machine entry follows it
    catalog.get("Save").string = "Guardar cambios"
    catalog.get("Save").flags.discard("machine")
    with po.open("wb") as f:
        write_po(f, catalog)
    (tree / tx.GLOSSARY).write_text(json.dumps({"es": {"Post": {"button": "Publica"}, "Save": {"": "Salvar"}}}),
                                    encoding="utf-8")
    tx.translate_catalog("es", "messages", "es", workers=1)
    catalog = read_po(po.open("rb"))
    assert catalog.get("Save").string == "Guardar cambios" and catalog.get("Post", context="button").string == "Publica"
    # a word taken out of the glossary is made again by the machine
    (tree / tx.GLOSSARY).write_text(json.dumps({"es": {}}), encoding="utf-8")
    sent.clear()
    tx.translate_catalog("es", "messages", "es", workers=1)
    catalog = read_po(po.open("rb"))
    assert "Post (publish a message)" in sent
    assert "glossary" not in catalog.get("Post", context="button").flags


def test_a_glossary_entry_that_loses_a_value_is_not_used(tree, monkeypatch):
    (tree / tx.GLOSSARY).write_text(json.dumps({"es": {"Hello %(name)s": {"": "Hola"}}}), encoding="utf-8")
    monkeypatch.setattr(tx, "call", fake_spanish)
    tx.translate_catalog("es", "messages", "es", workers=1)
    catalog = read_po((tree / "es" / "LC_MESSAGES" / "messages.po").open("rb"))
    assert catalog.get("Hello %(name)s").string == "Hola %(name)s"


def test_the_glossary_command_writes_without_libretranslate(tree, monkeypatch):
    monkeypatch.setattr(tx, "call", fake_spanish)
    tx.translate_catalog("es", "messages", "es", workers=1)
    (tree / tx.GLOSSARY).write_text(json.dumps({"es": {"Save": {"": "Guardar ya"}}}), encoding="utf-8")
    monkeypatch.setattr(tx, "call", lambda *a: pytest.fail("LibreTranslate was asked"))
    assert cat.main(["glossary"]) == 0
    assert read_po((tree / "es" / "LC_MESSAGES" / "messages.po").open("rb")).get("Save").string == "Guardar ya"


def test_the_committed_glossary_covers_the_sites_words_in_every_language_safely():
    from ruqqus.helpers import i18n
    from scripts.i18n_glossary_terms import CHROME, HEADINGS, LABELS, TERMS, budget, width
    data = json.loads((cat.OUT / tx.GLOSSARY).read_text(encoding="utf-8"))
    wanted = {(en, context) for en, context, _meaning in TERMS + HEADINGS + LABELS + CHROME}
    assert set(data) == set(i18n.interface_keys()) - {"en"}
    for key, by_msgid in data.items():
        have = {(msgid, context) for msgid, by_context in by_msgid.items() for context in by_context}
        assert have == wanted, (key, sorted(wanted ^ have)[:5])
        for msgid, by_context in by_msgid.items():
            for context, text in by_context.items():
                assert text.strip() and not cat.problems(msgid, text), (key, msgid, context, text)
                # the owner, 2026-10-25: a translation fits the design ("Rep" stays short)
                assert width(text) <= budget(msgid, context), (key, msgid, context, text, budget(msgid, context))


SIGN_OUT_LABELS = ("Sign out", "Sign out %(device)s", "Signed out", "Reload", "Log out everywhere", "Log Out Everywhere")
# what LibreTranslate made of signing out before PLAIN_SIGN_OUT and the glossary: signed (a document), signed up, subscribed,
# logged in, registered, "FAQ"
WRONG_SIGN_OUT = re.compile(r"(?i)\bsigné|firmad|firmat|\bfirma\b|подпис|imzalan|assinad|サインアップ|署名|签字|angemeldet|"
                            r"registrad|registrat|войти|accedi|자주 묻는|suscr|subscri")


def test_signing_out_is_said_as_signing_out_in_every_language():
    """2026-11-12: the security warnings about signing out said the opposite in many languages (French "sera signé", Russian
    "Войти везде" for "Log out everywhere", Japanese "サインアップ" for "Sign out"). The short labels are the glossary's in every
    language (or someone's own correction), every machine entry those rules change was made again with them (flag plain2, never
    plain1 alone), and the sentences that are only about signing out carry none of the old wrong senses."""
    glossary = json.loads((cat.OUT / tx.GLOSSARY).read_text(encoding="utf-8"))
    sentences = ("This device will be signed out shortly.", "Your other devices have been signed out.",
                 "That device couldn't be signed out right now.", "All other devices have been logged out",
                 "Two-factor authentication is on. Your other devices have been signed out.")
    for key in sorted(glossary):
        for domain in ("messages", "js"):
            catalog = cat.read(cat.OUT / key / "LC_MESSAGES" / f"{domain}.po")
            for message in catalog:
                if not message.id or not message.string:
                    continue
                msgid = message.id if isinstance(message.id, str) else message.id[0]
                texts = message.string if isinstance(message.string, (list, tuple)) else [message.string]
                if msgid in SIGN_OUT_LABELS and "machine" in message.flags:
                    assert texts[0] == glossary[key][msgid][""], (key, domain, msgid, texts[0])
                if "machine" in message.flags and any(tx.plain(t) != tx.plain(t, tx._PLAIN_BEFORE_SIGN_OUT)
                                                      for t in ([message.id] if isinstance(message.id, str) else message.id)):
                    assert tx.PLAIN_FLAG in message.flags, (key, domain, msgid, sorted(message.flags))
                if msgid in sentences:
                    assert not any(WRONG_SIGN_OUT.search(t) for t in texts), (key, domain, msgid, texts)


def test_a_hash_before_a_value_is_kept():
    # the label an anonymous person is drawn with in a thread: machines wrote "Anonyme3", "Anonimo!3", "Anonymous # 3" (2026-11-14)
    assert "a # before a value is dropped" in cat.problems("Anonymous #%(num)s", "Anonyme%(num)s")
    assert "a # before a value is dropped" in cat.problems("Anonymous #%(num)s", "Anonimo!%(num)s")
    assert "a # before a value is dropped" in cat.problems("post #%(id)s", "%(id)s nº")
    # one space between the mark and the value is a language's own spelling; the mark itself is the rule
    assert cat.problems("Anonymous #%(num)s", "Анонимен # %(num)s") == []
    assert cat.problems("Anonymous #%(num)s", "Anonyme #%(num)s") == []
    assert cat.problems("#%(channel)s was made.", "#%(channel)s wurde erstellt.") == []
    assert cat.problems("Save #1 first", "Zuerst #1 speichern") == [], "a number is not a value"


# --- a machine's loops and padding ---------------------------------------------------------------------------------------

def test_a_translation_that_loops_or_pads_is_refused():
    # the owner, 2026-10-25: translations long enough to break the design; LibreTranslate's own loops, as it wrote them
    assert "repeats a word" in cat.problems("After %(date)s", "After " * 40 + "%(date)s")
    assert "repeats a word" in cat.problems("ended", "sonunda sona er sona er sona er sona erdi")
    assert "repeats a word" in cat.problems("moved from <a href=\"%(url)s\">+%(guild)s</a>",
                                            "からの移動 <a href=\"%(url)s\">" + "+ " * 30 + "%(guild)s</a>")
    assert "repeats a word" in cat.problems('Example: cats dogs', "例:猫犬・" + "犬・" * 30)
    assert "much longer than the English" in cat.problems(
        'by <a href="/@%(name)s">@%(name)s</a>',
        'par <a href="/@%(name)s">À l\'annexe I du règlement (UE) no 1308/2013 du Parlement européen @%(name)s</a>')
    assert "subtitle codes" in cat.problems("#%(channel)s was made.", "{\\fn方正粗倩简体\\fs12}%(channel)s 已经做了。")
    # what a language really does is fine: a word twice, a long word, the English's own repeats
    assert cat.problems("Each account counts once", "Cada cuenta cuenta una vez") == []
    assert cat.problems("Tip", "Pourboire") == []
    assert cat.problems("Ha ha ha", "Ja ja ja") == []
    # a short label repeating a run of words; a long sentence may (Spanish "navegador a navegador a través")
    assert "repeats a word" in cat.problems("Paused", "Pa used Pa used")
    assert "repeats a word" in cat.problems("Posting to %(guilds)s", "Posting to posting to posting %(guilds)s")
    assert cat.problems("Voice goes browser to browser through the site's relay, so nobody sees anyone's address.",
                        "La voz va navegador a navegador a través del relé del sitio, así que nadie ve la dirección.") == []


def test_marks_the_machine_doubled_are_written_once():
    assert tx.tidy("Already forwarded to +%(guild)s.", "已转发给 +%(guild)s。 。 。") == "已转发给 +%(guild)s。"
    assert tx.tidy("%(who)s comments on %(title)s - %(site)s",
                   "%(who)s コメント %(title)s - - - %(site)s") == "%(who)s コメント %(title)s - %(site)s"
    assert tx.tidy("Done.", "हो गया।.") == "हो गया।"
    assert tx.tidy("Delete this story?", "تحذف هذه القصة؟?") == "تحذف هذه القصة؟"
    assert tx.tidy("Delete this story?", "删除这个故事？?") == "删除这个故事？"
    assert tx.tidy("Saved.", "保存しました。.") == "保存しました。"
    assert tx.tidy("Loading...", "Chargement...") == "Chargement..."           # the English's own
    assert tx.tidy("Loading…", "Cargando...") == "Cargando…" and tx.tidy("Loading…", "Cargando.") == "Cargando…"
    catalog = Catalog(locale="zh", domain="messages")
    catalog.add("Saved.", "已保存。 。", flags=["machine"])
    catalog.add("Blocked.", "已屏蔽。 。")                                       # someone's own: left alone
    assert tx.tidy_catalog(catalog) == 1
    assert catalog.get("Saved.").string == "已保存。" and catalog.get("Blocked.").string == "已屏蔽。 。"


def test_a_plurals_example_number_is_found_in_any_script():
    # LibreTranslate answers Bengali in Bengali digits ("৫ মন্তব্য"): every Bengali plural failed
    assert tx.ascii_digits("৫ মন্তব্য") == "5 মন্তব্য" and tx.ascii_digits("٥ تعليقات") == "5 تعليقات"
    assert tx.ascii_digits("५ टिप्पणियाँ") == "5 टिप्पणियाँ" and tx.ascii_digits("๕ ความคิดเห็น") == "5 ความคิดเห็น"
    assert tx.Job("%(num)s comments", number=5).finish("৫ মন্তব্য") == "%(num)s মন্তব্য"
