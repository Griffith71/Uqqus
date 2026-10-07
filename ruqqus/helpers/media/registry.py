"""The providers this site has, by name. See base.py for how to add one."""
from . import dev, gdrive, google_oauth

PROVIDERS = {
    "dev": dev.DevProvider(),
    "gdrive": gdrive.DriveProvider(),
}
# which kind of linked account each provider belongs to
ACCOUNT_OF = {"dev": "dev", "gdrive": "google", "youtube": "google"}


def get(name):
    """The provider object for an asset's `provider`, or None if this site no longer has it."""
    provider = PROVIDERS.get(name)
    if provider is None:
        return None
    if name == "dev" and not dev.enabled():
        return None
    if ACCOUNT_OF.get(name) == "google" and not google_oauth.configured():
        return None
    return provider


def account_kinds():
    """The kinds of account a member can link on this site, in the order to offer them."""
    kinds = []
    if google_oauth.configured():
        kinds.append("google")
    if dev.enabled():
        kinds.append("dev")
    return kinds
