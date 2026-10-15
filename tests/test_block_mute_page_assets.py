"""Block & Mute is one settings page (templates/settings_blocks.html, routes/settings.py): an Accounts segment (Blocked,
Muted) and a Guilds segment (Muted), kept in ?show=, working without script. The old Muted Guilds page redirects to it."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = read("ruqqus", "routes", "settings.py")
PAGE = read("ruqqus", "templates", "settings_blocks.html")
NAV = read("ruqqus", "templates", "settings.html")


def func(source, name):
    return source.split(f"def {name}(")[1].split("\n\n\n")[0]


def test_the_old_muted_guilds_page_is_a_redirect_to_the_guilds_segment():
    assert not (ROOT / "ruqqus" / "templates" / "settings_guildfilter.html").exists()
    old = func(ROUTES, "settings_blockedguilds")
    assert 'return redirect("/settings/blocks?show=guilds")' in old and "render_template" not in old


def test_the_segment_is_read_from_the_address_and_anything_else_is_accounts():
    page = func(ROUTES, "settings_blockedpage")
    assert 'show = "guilds" if request.args.get("show") == "guilds" else "accounts"' in page
    assert 'muted=muted_accounts(v) if show == "accounts" else []' in page     # only the drawn segment is read


def test_the_segments_are_links_so_they_work_without_script():
    for segment in ("accounts", "guilds"):
        assert f'href="/settings/blocks?show={segment}"' in PAGE
        assert f"{{% if show == '{segment}' %}} aria-current=\"page\"{{% endif %}}" in PAGE
    assert '<nav class="btn-group btn-group-sm block-mute-segments mb-4" aria-label="Block and mute">' in PAGE


def test_accounts_has_blocked_and_muted_and_guilds_has_muted():
    sections = PAGE.split("</nav>", 1)[1].split("<!-- Mute guild dialog -->")[0]
    assert sections.lstrip().startswith("{% if show == 'guilds' %}")
    guilds, accounts = sections.split('id="blocked-accounts"', 1)
    accounts = 'id="blocked-accounts"' + accounts
    assert 'id="muted-guilds"' in guilds and "Guilds you have muted" in guilds and "/settings/unmute_guild?board=" in guilds
    assert 'id="blocked-accounts"' in accounts and 'id="muted-accounts"' in accounts
    assert "/settings/unblock?username=" in accounts and "/settings/unmute?username=" in accounts
    assert accounts.index('id="blocked-accounts"') < accounts.index('id="muted-accounts"')


def test_each_segment_draws_only_its_own_dialog():
    """Both dialogs use the ids all_js.js's block_user() and filter_guild() read (exile-username, exile-form)."""
    dialogs = PAGE.split("<!-- Mute guild dialog -->")[1]
    guild_dialog, account_dialog = dialogs.split("{% else %}", 1)
    assert 'id="HideGuildModal"' in guild_dialog and 'onclick="filter_guild()"' in guild_dialog
    assert 'id="guildmasterExileModal"' in account_dialog and 'onclick="block_user()"' in account_dialog
    assert PAGE.count('id="exile-username"') == 2 and PAGE.count('id="exile-form"') == 2


def test_the_form_key_is_never_put_in_an_address():
    """post() in all_js.js sends it in the form; an address ends up in logs and the browser history."""
    assert "formkey={{" not in PAGE


def test_the_nav_has_one_entry_for_block_and_mute_and_no_muted_guilds():
    assert "Muted Guilds" not in NAV and "/settings/filters" not in NAV
    assert NAV.count('href="/settings/blocks"') == 2                                     # desktop and phone
    assert ">Block &amp; Mute</a>" in NAV
    assert 'href="/settings/blocks" title="Block and mute" aria-label="Block and mute"' in NAV


def test_every_phone_settings_icon_has_a_name():
    phone = NAV.split("<!-- Mobile -->")[1].split("</ul>")[0]
    for link in phone.split('<a class="nav-link')[1:]:
        assert "aria-label=" in link.split(">")[0], link[:80]
