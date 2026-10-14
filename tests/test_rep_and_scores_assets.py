"""Rep and scores: Rep is the upvotes from others, coloured by the share of upvotes with the counts on hover; each
member chooses at sign-up how scores read (upvotes coloured, the default; or upvotes minus downvotes); downvotes are
red. helpers/vote_colour.py, helpers/rep_store.py, helpers/jinja2.py score_mode."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_rep_is_upvotes_from_others_and_is_shown_with_its_colour_and_counts():
    user = read("ruqqus", "classes", "user.py")
    true_score = user.split("    def true_score(self):")[1].split("\n    @property")[0]
    assert "self.stored_karma = self.rep_upvotes" in true_score
    assert "rep_store.counts(g.db, self.id)" in user
    for path in (("ruqqus", "templates", "default.html"), ("ruqqus", "templates", "header.html"), ("ruqqus", "templates", "userpage.html")):
        src = read(*path)
        for who in re.findall(r"\{\{ (\w)\.true_score \}\}</span>", src):
            assert f'<span class="rep-count {{{{ {who}.rep_class }}}}" title="{{{{ {who}.rep_label }}}}">' in src, path
        assert src.count("rep-count") >= 2, path


def test_the_downvote_is_red_and_the_ratio_shades_exist_in_both_stylesheets():
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        assert re.search(r"^\$downvote: #e8452c;", css, re.M) and "#38B2AC;" not in css.split("// red")[0], sheet
        assert "@for $i from 0 through 10 {" in css and "mix($upvote, $downvote, $i * 10%)" in css, sheet
        assert ".score-display-option {" in css, sheet


def test_every_score_carries_its_counts_mode_and_colour():
    for name in ("submission_listing.html", "submission.html", "comments.html"):
        src = read("ruqqus", "templates", name)
        assert "{% set score = ups if score_mode_now == 0 else ups-downs %}" in src, name
        assert "{% set score_class = ratio_class(ups, downs) if score_mode_now == 0 else '' %}" in src, name
        spans = re.findall(r'<span id="(?:post|comment)-[^"]*score[^"]*"[^>]*>', src)
        assert spans, name
        for span in spans:
            assert 'data-ups="{{ ups }}" data-downs="{{ downs }}" data-mode="{{ score_mode_now }}"' in span, name
            assert "{% if score_class %} {{ score_class }}{% endif %}" in span, name


def test_the_vote_script_redraws_by_mode_and_undo_restores_the_counts():
    js = read("ruqqus", "assets", "js", "all_js.js")
    assert "function voteCounts(el, dUp, dDown, fallback) {" in js
    assert js.count("voteCounts(thisScoreText,") == 6
    assert "if (s.ups !== undefined) { s.score.dataset.ups = s.ups; s.score.dataset.downs = s.downs; }" in js


def test_the_choice_at_sign_up_and_in_settings_takes_only_two_values():
    signup = read("ruqqus", "templates", "sign_up.html")
    assert 'name="score_display" value="0" checked' in signup and 'name="score_display" value="1">' in signup
    login = read("ruqqus", "routes", "login.py")
    assert 'score_display=1 if request.form.get("score_display") == "1" else 0,' in login
    settings = read("ruqqus", "routes", "settings.py")
    route = settings.split("def settings_score_display(v):")[1].split("\n@app.")[0]
    assert 'if choice not in ("0", "1"):' in route and "@validate_formkey\ndef settings_score_display(" in settings
    jinja = read("ruqqus", "helpers", "jinja2.py")
    assert "return SCORE_NET if v is not None and getattr(v, \"score_display\", 0) == SCORE_NET else SCORE_UPVOTES" in jinja


def test_schema_and_migration_agree():
    assert "ADD COLUMN IF NOT EXISTS score_display smallint NOT NULL DEFAULT 0;" in read("scripts", "migrations", "2026-10-14_score_display.sql")
    assert "    score_display smallint DEFAULT 0 NOT NULL\n);" in read("schema.sql")
    assert "score_display = Column(SmallInteger, default=0)" in read("ruqqus", "classes", "user.py")
