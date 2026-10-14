"""Only the author of an OAuth app may edit or delete it (ruqqus/routes/oauth.py). Deleting an app removes every grant
made through it, bot keys included (helpers/bots.py), so another member must never be able to do it."""
from pathlib import Path

OAUTH = (Path(__file__).resolve().parent.parent / "ruqqus" / "routes" / "oauth.py").read_text(encoding="utf-8")


def body(name):
    return OAUTH.split(f"def {name}(")[1].split("\n@app.")[0]


def test_deleting_an_app_checks_its_author_before_anything_is_removed():
    b = body("delete_oauth_app")
    assert b.index("if app.author_id != v.id:") < b.index("g.db.delete(auth)") < b.index("g.db.delete(app)")
    assert "abort(403)" in b and "if not app:\n        abort(404)" in b


def test_editing_an_app_checks_its_author_before_anything_is_changed():
    b = body("edit_oauth_app")
    assert b.index("if app.author_id != v.id:") < b.index("app.redirect_uri =")
    assert "abort(403)" in b and "if not app:\n        abort(404)" in b


def test_both_need_a_signed_in_member_and_the_form_key():
    for name in ("delete_oauth_app", "edit_oauth_app"):
        head = OAUTH.split(f"def {name}(")[0].rsplit("\n\n", 1)[1]
        assert "@is_not_banned" in head and "@validate_formkey" in head, name
