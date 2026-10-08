"""The guild limit has to be asked everywhere an account gains a guild, and the pages must quote the one number.
These checks keep the entry points, the copy and the docs agreeing with helpers/guild_limits.py."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


BOARDS = read("ruqqus", "routes", "boards.py")
ADMIN = read("ruqqus", "routes", "admin.py")
USER = read("ruqqus", "classes", "user.py")
MODULE = read("ruqqus", "helpers", "guild_limits.py")


def view(source, name):
    head = source.split(f"def {name}(")[0].rsplit("\n\n\n", 1)[-1]
    return head + f"def {name}(" + source.split(f"def {name}(")[1].split("\n@app.")[0]


def test_every_way_of_gaining_a_guild_asks_the_limit():
    assert "v.gm_limit_refusal()" in view(BOARDS, "create_board_get")
    assert "v.gm_limit_refusal()" in view(BOARDS, "create_board_post")
    assert "user.gm_limit_refusal()" in view(BOARDS, "mod_invite_username")          # the invitee, not the inviter
    assert "v.gm_limit_refusal()" in view(BOARDS, "mod_accept_board")
    assert "v.gm_limit_refusal()" in BOARDS.split("# check guild count")[1].split("# Cannot siege banned guilds")[0]
    assert "user.gm_limit_refusal()" in ADMIN.split("# check guild count")[1].split("# Can't siege if exiled")[0]


def test_a_siege_of_a_guild_you_already_lead_is_still_allowed_past_the_limit():
    assert "if guild not in v.boards_modded else None" in BOARDS
    assert "if guild not in user.boards_modded else None" in ADMIN


def test_nothing_new_creates_a_leader_unnoticed():
    # every row that makes someone a guildmaster: create, invite (pending), siege, the admin siege, an admin
    # modding themself (level 4, exempt). A sixth creator has to be looked at for the limit.
    found = 0
    for route in sorted((ROOT / "ruqqus" / "routes").glob("*.py")):
        found += route.read_text(encoding="utf-8").count("ModRelationship(")
    assert found == 5


def test_the_cheap_per_person_check_is_what_every_page_pays_for():
    body = USER.split("def can_join_gms(self):")[1].split("def gm_limit_refusal")[0]
    assert "< guild_limits.PER_PERSON" in body and "< 10" not in body
    assert "guild_limits.check(g.db, self)" in USER.split("def gm_limit_refusal(self):")[1].split("@property")[0]
    # the address query is never reached from a property that pages read
    assert "guild_limits.check" not in USER.split("def can_make_guild(self):")[1].split("def can_join_gms")[0]


def test_the_module_only_reads_and_the_pages_quote_the_one_number():
    assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", MODULE.split('"""', 2)[2])
    assert "GUILD_LIMIT_PER_PERSON=guild_limits.PER_PERSON" in BOARDS
    for name in ("home.html", "help/siege.html"):
        html = read("ruqqus", "templates", *name.split("/"))
        assert "GUILD_LIMIT_PER_PERSON" in html, name
        assert "10 guilds" not in html and "9 or fewer" not in html, name
    assert "10 guilds" not in BOARDS and "guilds." in BOARDS


def test_claude_md_describes_the_limit_and_its_known_risk():
    doc = read("CLAUDE.md")
    assert "## Guild limits (legacy app)" in doc
    assert "shared network" in doc and "guild_limits.check(g.db, user)" in doc


def test_every_refusal_page_of_the_create_form_is_given_the_viewer():
    # the form draws {{ v.formkey }}: a render without v is a 500 (it was, for someone who could not make a guild)
    body = view(BOARDS, "create_board_post")
    pages = body.split('render_template("make_board.html",')[1:]
    assert len(pages) >= 5
    for page in pages:
        assert "v=v" in page[:80], page[:80]


def test_admins_pass_the_cheap_check_too():
    # can_make_guild reads can_join_gms, so an admin who leads many guilds must not be stopped there first
    body = USER.split("def can_join_gms(self):")[1].split("def gm_limit_refusal")[0]
    assert "if self.admin_level >= guild_limits.ADMIN_LEVEL:\n            return True" in body
