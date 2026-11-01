"""The Edit profile pop-up (CLAUDE.md "Profile header"): the button opens it in place, one Save sends one request, and the
route checks everything before it changes anything. These keep the template, the script, the stylesheet, the route and the
model agreeing; the checks themselves are tests/test_profile_edit.py, the route on the real app tests/pg/test_profile_edit.py
and the pop-up in a browser scripts/ui/profile_edit.spec.ts."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


HEADER = read("ruqqus", "templates", "partials", "profile_header.html")
USERPAGE = read("ruqqus", "templates", "userpage.html")
MODAL = read("ruqqus", "templates", "partials", "profile_edit_modal.html")
SCRIPT = read("ruqqus", "assets", "js", "profile_edit.js")
ROUTE = read("ruqqus", "routes", "profile_edit_pages.py")
HELPER = read("ruqqus", "helpers", "profile_edit.py")
SETTINGS = read("ruqqus", "routes", "settings.py")
USER = read("ruqqus", "classes", "user.py")
SCREENS = read("ruqqus", "assets", "style", "_screens.scss")


def func(source, name, nxt="\n\n\n"):
    return source.split(f"def {name}(")[1].split(nxt)[0]


def in_order(source, *needles):
    positions = [source.index(n) for n in needles]
    assert positions == sorted(positions), needles


# --- the entry points ------------------------------------------------------------------------------------------------

def test_both_entry_points_open_the_pop_up_and_stay_links_to_settings_without_it():
    # the button and the "⋯" item: the script intercepts the click only when the pop-up is there (its own checks), so the
    # link is what a browser without script, a blocked Bootstrap or a side-panel frame follows
    assert '<a href="/settings/profile" class="btn profile-x-outline profile-x-pill" data-profile-edit>' in HEADER
    assert '<a class="dropdown-item" href="/settings/profile" data-profile-edit>' in HEADER
    assert HEADER.count("data-profile-edit") == 2
    assert 'href="/settings/profile#lists"' in HEADER                                                         # the lists link is untouched
    # nothing opens before the first branch the other tests count on
    assert HEADER.index("{% if own %}") < HEADER.index("data-profile-edit")


def test_the_pop_up_is_included_only_on_your_own_profile_and_outside_a_side_panel_frame():
    line = USERPAGE.split('{% if v and v.id == u.id and not request.args.get(\'embed\') %}')[1].split("{% endif %}")[0]
    assert '{% include "partials/profile_edit_modal.html" %}' in line
    assert "asset_url('js/profile_edit.js')" in line                          # never a hand-written /assets/ path
    assert '{% if v and v.id != u.id %}{% include "partials/report_account_modal.html" %}{% endif %}' in USERPAGE      # unchanged


# --- the template ------------------------------------------------------------------------------------------------------

def test_the_pop_up_is_a_bootstrap_dialog_that_becomes_a_sheet_on_a_phone():
    assert 'class="modal fade modal-sm-bottom" id="profileEditModal"' in MODAL
    assert 'role="dialog" aria-labelledby="profileEditTitle"' in MODAL
    assert "modal-dialog-scrollable modal-dialog-centered" in MODAL
    # the form is inside the scrolling body and the header's buttons reach it with form="": a form around the whole card
    # would stop modal-dialog-scrollable from scrolling its body
    assert '<form id="profile-edit-form" action="/settings/profile/edit" method="post" enctype="multipart/form-data"' in MODAL
    assert 'form="profile-edit-form" class="btn btn-primary btn-sm" id="profile-edit-save" disabled' in MODAL
    assert MODAL.index('class="modal-header') < MODAL.index('<form id="profile-edit-form"') < MODAL.index('class="modal-footer')


def test_nothing_in_the_template_is_raw_html_or_a_surface_of_its_own():
    assert "| safe" not in MODAL and "|safe" not in MODAL
    assert "bg-white" not in MODAL and "bg-light" not in MODAL                    # the glass shows only as a border on an opaque panel
    assert "style=" not in MODAL
    for icon in re.findall(r'class="(?:fas|far|fab) (fa-[a-z-]+)', MODAL):
        assert icon in {"fa-times", "fa-camera", "fa-user", "fa-cog"}, icon


def test_the_pop_up_carries_every_field_the_header_draws_with_the_same_limits():
    for needle in ('name="display_name"', 'data-weighted-max="50"', 'autocomplete="nickname"',
                   'name="bio"', 'data-weighted-max="256"', "{{ v.bio or '' }}",
                   'name="profile_job" maxlength="60"', 'name="profile_location" maxlength="60"',
                   'name="profile_website" maxlength="200"', 'name="profile"', 'name="banner"'):
        assert needle in MODAL, needle
    # prefilled from the account: without these the pop-up opens blank and a save would send the blanks as edits
    for needle in ('value="{{ v.display_name or \'\' }}"', 'value="{{ v.profile_job or \'\' }}"', 'value="{{ v.profile_location or \'\' }}"',
                   'value="{{ v.profile_website or \'\' }}"', "{{ v.bio or '' }}</textarea>", 'src="{{ v.banner_url }}"', 'src="{{ v.profile_url }}"'):
        assert needle in MODAL, needle
    # the same names Settings > Profile and the route read
    settings = read("ruqqus", "templates", "settings_profile.html")
    for name in ("display_name", "bio", "profile_job", "profile_location", "profile_website"):
        assert f'name="{name}"' in settings, name


def test_the_pictures_have_real_buttons_names_and_the_reason_when_there_is_no_camera():
    assert MODAL.count('data-pe-pick="') == 2 and MODAL.count("data-pe-remove=") >= 2
    assert 'aria-labelledby="pe-change-text pe-avatar-label"' in MODAL and 'aria-labelledby="pe-change-text pe-banner-label"' in MODAL
    # the file inputs are hidden from sight but are never display: none, and a button (not the label) opens them
    assert MODAL.count('type="file" class="sr-only" tabindex="-1" aria-hidden="true"') == 2
    assert "{% if v.can_upload_avatar %}" in MODAL and "{% if v.can_upload_banner %}" in MODAL
    assert "You must have a verified email address to upload images via Tor." in MODAL
    assert "Rep." not in MODAL and "Additionally" not in MODAL        # no Rep is asked for a picture (the owner, 2026-10-30)
    assert "data-too-big=\"{{ _('That file is too big. The limit for image files is %(size)s MB.', size=16) }}\"" in MODAL


def test_a_field_error_has_a_place_beside_each_field_and_the_picture():
    for name in ("display_name", "bio", "profile_job", "profile_location", "profile_website", "profile", "banner"):
        assert f'id="pe-error-{name}"' in MODAL, name
    assert 'id="profile-edit-status" role="status" aria-live="polite"' in MODAL
    assert 'id="profile-edit-discard" role="alert" hidden' in MODAL


def test_the_pop_up_reuses_the_words_the_site_already_has():
    # a new msgid in the interface catalogue lowers every language's share (READY_SHARE, 95%): the pop-up's template adds none. Its
    # words are Settings' and the header's, plus two sentences other uploads already say (routes/media.py, helpers/media/rules.py)
    elsewhere = {"That file is not a picture.", "That file is too big. The limit for image files is %(size)s MB."}
    settings = read("ruqqus", "templates", "settings_profile.html")
    header = read("ruqqus", "templates", "partials", "profile_header.html")
    found = [text for _quote, text in re.findall(r"\{\{ _\((['\"])(.*?)\1[,)]", MODAL)]
    assert len(found) > 15
    for text in found:
        assert text in elsewhere or text in settings or text in header or text in {"Close", "Edit profile", "Settings"} \
            or text.startswith("Tell the"), text
    # and the Python side says them with the existing words too
    assert 'tr("That file is too big. The limit for image files is %(size)s MB.", size=megabytes)' in HELPER
    assert 'tr("That file is too big.")' in HELPER and 'tr("Error. Please try again.")' in ROUTE


# --- the script --------------------------------------------------------------------------------------------------------

def test_the_script_draws_text_only_and_never_asks_with_the_browsers_own_pop_up():
    assert "innerHTML" not in SCRIPT and "outerHTML" not in SCRIPT and "insertAdjacentHTML" not in SCRIPT
    assert "window.confirm" not in SCRIPT and "confirm(" not in SCRIPT                     # it asks in a row of its own
    assert "document.write" not in SCRIPT and "eval(" not in SCRIPT
    # it loads before jQuery: nothing may touch $ or jQuery at the top level
    top_level = SCRIPT.split("(function () {")[1]
    for line in top_level.splitlines():
        if line.startswith("  ") and not line.startswith("   "):          # a statement at the IIFE's own level
            assert "$(" not in line and "jQuery" not in line or line.lstrip().startswith(("//", "*")), line


def test_the_script_sends_one_request_to_the_one_route_with_the_form_key_and_the_text_unchanged():
    assert SCRIPT.count("fetch(") == 1
    assert "fetch('/settings/profile/edit', { method: 'POST', credentials: 'same-origin', body: body, signal:" in SCRIPT
    assert "body.append('formkey', formkey());" in SCRIPT
    # only what this pop-up changed is sent: a tab opened before another one saved must not send the old text back as an edit
    assert "if (newlines(field(name).value) !== baseline[name]) body.append(name, field(name).value);" in SCRIPT
    # a text left as it was is not held to today's limit: an older, longer bio must not stop a save of something else
    assert "if (newlines(input.value) === baseline[name]) input.setCustomValidity('');" in SCRIPT
    assert SCRIPT.index("input.setCustomValidity('')") < SCRIPT.index("form.checkValidity()")
    assert "SAVE_TIMEOUT" in SCRIPT and "abort.abort()" in SCRIPT                    # a hung save can be given up on
    assert "field(name).readOnly = busy" in SCRIPT
    assert "new FormData()" in SCRIPT                                       # built by hand: a picture is the staged File, not an input
    assert "res.redirected" in SCRIPT and "application/json" in SCRIPT      # a session that ended answers with the sign-in page


def test_closing_with_changes_asks_in_a_row_of_the_pop_up_and_a_save_blocks_every_way_out():
    assert "if (busy) { event.preventDefault(); return; }" in SCRIPT
    assert "if (leaving) return;" in SCRIPT and "if (!dirty()) return;" in SCRIPT
    assert "if (!discardRow.hidden) { event.preventDefault(); keepEditing(); return; }" in SCRIPT      # Esc while it is asked: keep editing
    # the footer's link to Settings asks the same way, and Keep editing goes back to the field the member was in
    assert "afterDiscard" in SCRIPT and "lastFocus" in SCRIPT and "'.modal-footer a[href]'" in SCRIPT
    assert "tr('Discard changes?')" in SCRIPT and "tr('Keep editing')" in SCRIPT
    assert "'hide.bs.modal'" in SCRIPT                                       # Esc, the X and a tap outside all arrive there
    assert "closeButton.disabled = busy" in SCRIPT and "saveButton.disabled = busy || !dirty()" in SCRIPT


def test_the_link_stays_a_link_when_the_pop_up_cannot_open():
    clicks = SCRIPT.split("var link = target.closest('[data-profile-edit]');")[1].split("return;\n    }")[0]
    assert "event.metaKey || event.ctrlKey || event.shiftKey" in clicks       # a modified click opens the link
    assert "!window.jQuery.fn.modal" in clicks                                # no Bootstrap: the link goes to Settings
    assert clicks.index("!window.jQuery.fn.modal") < clicks.index("event.preventDefault()")


def test_the_pop_up_is_moved_out_of_the_profile_headers_stacking_context_and_gives_the_focus_back():
    assert "document.body.appendChild(modal)" in SCRIPT
    assert "document.getElementById('profile-x-more')" in SCRIPT and "opener.focus()" in SCRIPT
    assert "updateWeightedLimit" in SCRIPT                                    # a counter is redrawn after a reset, which is not typing
    assert "URL.revokeObjectURL" in SCRIPT


# --- the stylesheet ----------------------------------------------------------------------------------------------------

def test_the_rules_are_scoped_by_id_and_made_of_tokens():
    block = SCREENS.split("#profileEditModal {")[1].split("// --- the chat page")[0]
    assert "!important" not in block and "backdrop-filter" not in block and "filter:" not in block
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block), "a hex colour"
    assert not re.search(r"font-size:\s*\d", block)                           # type is a token or px2rem()
    assert "transition: all" not in block
    assert "background-color: var(--glass-chip);" in block and "color: var(--on-brand);" in block
    # every length is a token or a hairline: the 4px grid
    for length in re.findall(r"(?<![\w-])(\d+)px", block):
        assert length in {"1", "2", "576", "600"}, length
    assert SCREENS.index("// --- Profile header") < SCREENS.index("#profileEditModal {") < SCREENS.index("// --- the chat page")


# --- the route ---------------------------------------------------------------------------------------------------------

def test_the_route_has_the_decorators_in_their_order_and_none_that_would_hold_the_account_out():
    head = ROUTE.split("def settings_profile_edit_post(")[0].rsplit("\n\n\n", 1)[1]
    in_order(head, '@app.post("/settings/profile/edit")', "@limiter.limit(", "@auth_required", "@validate_formkey")
    assert "member_limit_key" in head
    assert "not_quarantined" not in ROUTE.replace("A held account", "")       # a held account may change its own profile
    assert "from .profile_edit_pages import *" in read("ruqqus", "routes", "__init__.py")
    # the module and its view are named apart from the helper: the star import would replace a helper of the same name
    assert not (ROOT / "ruqqus" / "routes" / "profile_edit.py").exists()
    assert "def profile_edit(" not in ROUTE


def test_the_route_checks_everything_before_it_changes_anything():
    body = ROUTE.split("def settings_profile_edit_post(")[1]
    in_order(body, "profile_edit.lock_account(g.db, v.id)", "g.db.refresh(v)",
             "profile_edit.clean_display_name(", "profile_edit.clean_details(", "profile_edit.check_bio(", "profile_edit.check_image(",
             "if errors:", "v.bio, v.bio_html = bio.text, bio.html", "apply_user_severity(v)", "g.db.flush()",
             "profile_edit.looks_like_link_spam(", "v.set_profile(picture)", "v.set_banner(picture)", "gevent.spawn(check_csam_url")
    assert body.count("apply_user_severity(v)") == 1                          # re-stamped once, after every assignment
    checking, after = body.split("if errors:", 1)
    for assignment in ("v.bio, v.bio_html", "setattr(v", "v.set_", "v.del_", "v.display_name =", "g.db.add(v)"):
        assert assignment not in checking, assignment                          # nothing is assigned before every check has passed
    refusal = after.split("# --- 5")[0]
    assert 'v.ban(days=30, reason=N_("Digitally malicious content is not allowed."))' in refusal and "return _refused(" in refusal
    assert after.index("g.db.flush()") < after.index("v.set_profile(picture)")     # a column that won't fit fails before the bucket is touched


def test_an_unchanged_field_is_never_checked_again():
    body = ROUTE.split("def settings_profile_edit_post(")[1]
    # a value sent exactly as it is stored is not cleaned, filtered or banned for: what an earlier day's rules let through
    # must not stop a save of something else
    assert 'if form.get("display_name", v.display_name or "") != (v.display_name or ""):' in body
    assert "touched = [field for field in profile_edit.DETAIL_FIELDS if sent[field] != stored[field]]" in body
    assert "elif cleaned[field] != stored[field]:" in body
    assert 'if "profile_website" in new:' in body and 'profile_edit.website_refusal(new["profile_website"])' in body
    assert 'if bio.code == "unchanged":' in body                            # check_bio compares with the stored text before it renders


def test_pictures_are_gated_like_the_old_routes_and_a_removal_is_not_an_upload():
    body = ROUTE.split("def settings_profile_edit_post(")[1]
    gate = func(ROUTE, "_gate")
    assert "v.is_suspended" in gate and "v.can_upload_avatar" in gate and "v.can_upload_banner" in gate
    assert gate.count('tr("You must have a verified email address to upload images via Tor.")') == 2
    assert "reputation" not in gate
    assert "getattr(v, has_field)" in body                                    # a removal only runs when there is a picture to remove
    assert body.index("_gate(v, kind)") < body.index("profile_edit.check_image(sent)")
    assert "gated = True" in body and "403 if gated else 400" in body


def test_a_picture_that_fails_leaves_the_old_one_and_is_reported_for_itself():
    body = ROUTE.split("def settings_profile_edit_post(")[1]
    assert body.count("except Exception:\n            traceback.print_exc()") == 2
    assert 'answer["errors"] = errors' in body and "return jsonify(answer), 409" in body      # not a 5xx: a proxy may swap its body for a page
    assert '"saved": changed' in body and '"values": _values(v)' in body


def test_a_new_picture_is_stored_before_the_old_one_goes_and_the_old_one_goes_after_the_commit():
    for name, key in (("set_profile", "profile"), ("set_banner", "banner")):
        method = func(USER, name, "\n    def " if name == "set_profile" else "\n    @staticmethod")
        assert f"new_nonce = self.{key}_nonce + 1" in method
        in_order(method, f"aws.upload_file(name=f\"uid/{{self.base36id}}/{key}-{{new_nonce}}.png\"", f"self.{key}_nonce = new_nonce", "self._drop_stray(old_key)")
        assert f"self.del_{key}()" not in method                              # it used to delete first and upload after
    drop = func(USER, "_drop_stray", "\n    def ")
    # queued, not run: the account may still point at the old object until the commit, and a failed commit must not leave it pointing at nothing
    assert "g.after_commit.append(lambda: aws.delete_file(name=key))" in drop and "aws.delete_file(name=key)\n" not in drop.replace("g.after_commit.append(lambda: aws.delete_file(name=key))", "")
    main = read("ruqqus", "__main__.py")
    after = main.split("def after_request(response):")[1].split("response.headers.add('Access-Control-Allow-Headers'")[0]
    assert "g.db.commit()" in after and "else:" in after and 'getattr(g, "after_commit", ())' in after
    assert after.index("g.db.commit()") < after.index('getattr(g, "after_commit", ())')          # only once the commit has gone through
    assert "gevent.spawn(_run_after_commit, later)" in after and "traceback.print_exc()" in main.split("def _run_after_commit(later):")[1].split("@app.after_request")[0]


def test_a_picture_png_cannot_hold_is_converted_instead_of_failing_the_save():
    aws = read("ruqqus", "helpers", "aws.py")
    assert 'PNG_MODES = ("1", "L", "LA", "I", "I;16", "P", "RGB", "RGBA")' in aws
    assert "if i.mode not in PNG_MODES:" in aws.split("def crop_and_resize(")[1].split("def upload_file(")[0]


# --- one copy of every rule ---------------------------------------------------------------------------------------------

def test_settings_and_the_bots_call_the_helper_and_the_link_spam_rule_has_one_copy():
    post = func(SETTINGS, "settings_profile_post")
    for call in ("profile_edit.clean_display_name(", "profile_edit.display_name_refusal(", "profile_edit.clean_details(",
                 "profile_edit.website_refusal(", "profile_edit.check_bio(", "profile_edit.looks_like_link_spam(v, check.html"):
        assert call in post, call
    assert "BeautifulSoup" not in SETTINGS and "mistletoe.Document" not in post and "filter_comment_html(" not in post
    bots = read("ruqqus", "routes", "bot_accounts.py")
    assert "profile_edit.check_image(" in func(bots, "_image") and "profile_edit.display_name_refusal(" in func(bots, "_display_name")
    assert "Image.open" not in bots
    assert "BeautifulSoup" in HELPER and "BeautifulSoup" not in ROUTE


def test_the_helper_assigns_nothing_and_imports_no_app():
    for forbidden in ("from ruqqus.__main__", "from flask", "import flask", "g.db", "request.", "setattr(", "db.add(", "db.commit("):
        assert forbidden not in HELPER, forbidden


def test_claude_md_describes_the_pop_up():
    claude = read("CLAUDE.md")
    section = claude.split("## Profile header (legacy app)")[1].split("\n## ")[0]
    for part in ("profile_edit_modal.html", "profile_edit.js", "POST /settings/profile/edit", "helpers/profile_edit.py",
                 "tests/test_profile_edit.py", "profile_edit.spec.ts"):
        assert part in section, part
