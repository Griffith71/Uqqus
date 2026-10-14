"""The providers this site has, by name. See base.py for how to add one."""
from . import dailymotion, dailymotion_oauth, dev, gdrive, google_oauth, microsoft_oauth, onedrive, rules, youtube

PROVIDERS = {
    "dev": dev.DevProvider(),
    "gdrive": gdrive.DriveProvider(),
    "youtube": youtube.YouTubeProvider(),
    "onedrive": onedrive.OneDriveProvider(),
    "dailymotion": dailymotion.DailymotionProvider(),
}
# which kind of linked account each provider belongs to
ACCOUNT_OF = {"dev": "dev", "gdrive": "google", "youtube": "google", "onedrive": "microsoft", "dailymotion": "dailymotion"}
# the accounts linked through helpers/media/oauth.py (Google has its own module, google_oauth.py)
SERVICES = {"microsoft": microsoft_oauth.SERVICE, "dailymotion": dailymotion_oauth.SERVICE}


def service_configured(kind):
    """Has this site been given the keys for this kind of linked account?"""
    if kind == "google":
        return google_oauth.configured()
    if kind == "dev":
        return dev.enabled()
    service = SERVICES.get(kind)
    return bool(service and service.configured())


def get(name):
    """The provider object for an asset's `provider`, or None if this site no longer has it."""
    provider = PROVIDERS.get(name)
    if provider is None:
        return None
    if not service_configured(ACCOUNT_OF.get(name)):
        return None
    return provider


def account_kinds():
    """The kinds of account a member can link on this site, in the order to offer them."""
    return [kind for kind in rules.SERVICE_ORDER if service_configured(kind)]
