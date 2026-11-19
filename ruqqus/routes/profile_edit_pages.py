"""The Edit profile pop-up's one save (templates/partials/profile_edit_modal.html, assets/js/profile_edit.js).

Settings > Profile saves a name, a bio, the details and each picture through separate forms; a pop-up that changes
several at once needs one answer. Every rule is the Settings page's own (helpers/profile_edit.py is the one copy), and
the order is what keeps a save from half happening:

1. one edit of an account at a time (an advisory lock, then the account is read again);
2. each field is compared with what is stored, on the server: a field that did not change is never checked again, so a
   rule added since can't refuse or ban someone for the name or website they already had;
3. everything is checked before anything is assigned: names, details, bio, and each picture opened and decoded in full;
4. a refusal answers 400 with a sentence per field and changes nothing (a bio that links the worst kind of banned site still
   puts the account on hold, as in Settings, so the pop-up is no way to probe the list);
5. the text is assigned and flushed (a column that would not fit fails here, before the bucket is touched) and rated by
   the word filter once;
6. the pictures last, each on its own: a new picture is stored before the old one goes (User.set_profile), so a failure
   leaves the old picture and is reported for that picture alone.

A suspended account may remove a picture and change its text but not upload one (the rule of the old routes). A held
account (helpers/quarantine.py) may change its own profile, and one the site's own checks put on hold (helpers/auto_quarantine.py) may too, with two
differences: it may not upload a picture or a banner (`_gate`: 403, the site's own words for a hold; removing one is allowed), and a bio that trips the
SEO-spam check is not kept, the account goes back to the one it had. Nothing in the answer says why a save put an account on hold.
"""
import time
import traceback

import gevent
from flask import g, jsonify, request

from ruqqus.helpers import auto_quarantine, profile_edit, quarantine
from ruqqus.helpers.aws import check_csam_url
from ruqqus.helpers.i18n_runtime import N_, tr
from ruqqus.helpers.word_filter_store import apply_user_severity
from ruqqus.helpers.wrappers import auth_required, qr_limited_answer, validate_formkey
from ruqqus.__main__ import app, limiter, member_limit_key, r

PICTURES = (("profile", "remove_profile", "has_profile"),
            ("banner", "remove_banner", "has_banner"))


def _values(v):
    """What the pop-up shows after a save: the text as stored."""
    return {"display_name": v.display_name or "", "bio": v.bio or "", "profile_job": v.profile_job or "",
            "profile_location": v.profile_location or "", "profile_website": v.profile_website or ""}


def _take_back_bio(v, kept):
    """The bio that tripped the SEO-spam check goes back to what it was, and the account is rated again (a held account's profile is public, and the links
    were what the check was for). The rest of the save, a name or a website, is left alone."""
    v.bio, v.bio_html = kept
    apply_user_severity(v)


def _refused(errors, status=400):
    return jsonify({"ok": False, "error": next(iter(errors.values())), "errors": errors}), status


def _gate(v, kind):
    """The sentence for why this account can't upload this kind of picture, else None."""
    if v.is_suspended:
        return tr("Your account is suspended.")
    held = quarantine.picture_refusal(v, r)      # a hold an automatic check placed: the site's own words for it, never the Tor sentence below
    if held:
        return held
    if kind == "profile" and not v.can_upload_avatar:
        return tr("You must have a verified email address to upload images via Tor.")
    if kind == "banner" and not v.can_upload_banner:
        return tr("You must have a verified email address to upload images via Tor.")
    return None


@app.post("/settings/profile/edit")
@limiter.limit("20/hour", key_func=member_limit_key)     # each picture is stored and scanned
@auth_required
@validate_formkey
def settings_profile_edit_post(v):

    if (request.content_length or 0) > profile_edit.REQUEST_MAX_BYTES:
        return _refused({"profile": profile_edit.picture_message("too_big", 16)}, 413)

    profile_edit.lock_account(g.db, v.id)
    g.db.refresh(v)                 # whatever a save that held the lock before this one committed

    form = request.form
    errors = {}
    gated = False

    # --- 2 and 3: what changed, and is it fine ---------------------------------------------------------------------
    new = {}                        # field -> value to store

    # a value sent exactly as it is stored is not looked at: what an earlier day's rules let through must not stop a save
    # of something else (and no rule added since may refuse or ban for it)
    if form.get("display_name", v.display_name or "") != (v.display_name or ""):
        name, problem = profile_edit.clean_display_name(form.get("display_name", ""))
        if not problem and name != (v.display_name or ""):
            problem = profile_edit.display_name_refusal(
                g.db, v.id, name, (app.config["SITE_NAME"], "ruqqus", "uqqus"), admin=v.admin_level >= 3)
            new["display_name"] = name
        if problem:
            errors["display_name"] = problem

    stored = {field: getattr(v, field) or "" for field in profile_edit.DETAIL_FIELDS}
    sent = {field: form.get(field, stored[field]) for field in profile_edit.DETAIL_FIELDS}
    touched = [field for field in profile_edit.DETAIL_FIELDS if sent[field] != stored[field]]
    if touched:
        # (an untouched field goes in empty: it cleans to nothing and its result is not read)
        job, location, website, problems = profile_edit.clean_details(
            *(sent[field] if field in touched else "" for field in profile_edit.DETAIL_FIELDS))
        cleaned = dict(zip(profile_edit.DETAIL_FIELDS, (job, location, website)))
        for field in touched:
            if field in problems:
                errors[field] = problems[field]
            elif cleaned[field] != stored[field]:
                new[field] = cleaned[field]
        if "profile_website" in new:
            problem = profile_edit.website_refusal(new["profile_website"])
            if problem:
                errors["profile_website"] = problem

    bio = None
    malicious = False
    if "bio" in form:
        bio = profile_edit.check_bio(form.get("bio", ""), v.bio)
        if bio.code == "unchanged":
            bio = None
        elif bio.code:
            errors["bio"] = bio.message
            malicious = bio.malicious
            bio = None

    uploads = {}
    removals = []
    for kind, remove_field, has_field in PICTURES:
        sent = request.files.get(kind)
        if sent is not None and sent.filename:
            problem = _gate(v, kind)
            if problem:
                errors[kind] = problem
                gated = True
                continue
            picture, code = profile_edit.check_image(sent)
            if code:
                errors[kind] = profile_edit.picture_message(code, 16)
            elif picture is not None:
                uploads[kind] = picture
        elif form.get(remove_field) == "1" and getattr(v, has_field):
            removals.append(kind)

    # --- 4: a refusal changes nothing ----------------------------------------------------------------------------------
    if errors:
        if malicious and not v.qr_limited:      # a quarantine outlives everything: a limited QR sign-in is only refused (helpers/qr_login.py)
            auto_quarantine.hold(g.db, v, "link_malicious", detail="a link to a banned site in a bio")
            g.db.commit()
            auto_quarantine.send_pending(g.db)
        return _refused(errors, 403 if gated else 400)

    # --- 5: the text -----------------------------------------------------------------------------------------------------
    changed = []
    kept_bio = (v.bio, v.bio_html)
    if bio is not None:
        v.bio, v.bio_html = bio.text, bio.html
        changed.append("bio")
    for field, value in new.items():
        setattr(v, field, value)
        changed.append(field)
    if changed:
        apply_user_severity(v)
        g.db.add(v)
        g.db.flush()

        if bio is not None and profile_edit.looks_like_link_spam(v, bio.html, int(time.time())):
            if v.qr_limited:                # no hold on a limited QR sign-in's word, and none of this save stays (helpers/qr_login.py)
                g.db.rollback()
                return qr_limited_answer(v)
            # the account is put on hold (helpers/auto_quarantine.py) and the save goes on as for any other: a hold is not a suspension, so the pictures
            # of the same save are still taken, and the answer says nothing of it. Not committed here: the account's lock is held until the whole save is
            # done, and the person is told once the request's commit has gone through
            auto_quarantine.hold(g.db, v, "seo_spam", detail="links in the bio of a new account")
            if v.is_auto_quarantined:       # held now or before (an admin, a bot or a suspended account is not held, and keeps what it wrote)
                _take_back_bio(v, kept_bio)

    # --- 6: the pictures, each on its own ----------------------------------------------------------------------------
    for kind, picture in uploads.items():
        try:
            if kind == "profile":
                v.set_profile(picture)
                remove, url = v.del_profile, v.profile_url
            else:
                v.set_banner(picture)
                remove, url = v.del_banner, v.banner_url
        except Exception:
            traceback.print_exc()
            errors[kind] = tr("Error. Please try again.")
            continue
        gevent.spawn(check_csam_url, url, v, remove)
        changed.append(kind)

    for kind in removals:
        try:
            v.del_profile() if kind == "profile" else v.del_banner()
        except Exception:
            traceback.print_exc()
            errors[kind] = tr("Error. Please try again.")
            continue
        changed.append(kind)

    answer = {"ok": not errors, "changed": bool(changed), "saved": changed, "values": _values(v),
              "profile_url": v.profile_url, "banner_url": v.banner_url,
              "has_profile": bool(v.has_profile), "has_banner": bool(v.has_banner)}
    if errors:
        answer["errors"] = errors
        answer["error"] = next(iter(errors.values()))
        return jsonify(answer), 409         # not a 5xx: a proxy may replace an origin's 5xx body with its own page, and the saved part would be lost
    return jsonify(answer)
