"""Linking a member's Dailymotion account, for their videos (helpers/media/dailymotion.py).

Set DAILYMOTION_API_KEY and DAILYMOTION_API_SECRET: a free API key made in the Dailymotion
account's developer settings, with <site>/settings/media/dailymotion/callback as its redirect.

Scope: manage_videos (upload to the member's own channel). Dailymotion's limits are the
member's, not the site's: on a standard account 15 videos and 10 hours a day, each video up
to 2 hours and 4 GB.
"""
from . import oauth
from .oauth import Service

API = "https://api.dailymotion.com"
SCOPE_VIDEOS = "manage_videos"


def _identity(token_data):
    """Dailymotion gives no id token: ask who the token belongs to."""
    token = token_data.get("access_token")
    if not token:
        return ""
    r = oauth._http("GET", f"{API}/me", params={"fields": "id"}, headers={"Authorization": f"Bearer {token}"})
    return str(oauth._json(r).get("id") or "") if r.status_code == 200 else ""


def _revoke(service, account):
    """Dailymotion's logout ends the token it is called with."""
    service.api(account, "GET", f"{API}/logout")


SERVICE = Service(
    name="dailymotion",
    label="Dailymotion",
    id_env="DAILYMOTION_API_KEY",
    secret_env="DAILYMOTION_API_SECRET",
    auth_url="https://www.dailymotion.com/oauth/authorize",
    token_url=f"{API}/oauth/token",
    wants={"video": (SCOPE_VIDEOS,)},
    required=SCOPE_VIDEOS,
    identity=_identity,
    auth_params={"display": "popup"},
    revoke=_revoke,
)
