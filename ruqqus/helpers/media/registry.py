"""The providers this site has, by name. See base.py for how to add one."""
from . import dev

PROVIDERS = {
    "dev": dev.DevProvider(),
}


def get(name):
    """The provider object for an asset's `provider`, or None if this site no longer has it."""
    provider = PROVIDERS.get(name)
    if provider is not None and name == "dev" and not dev.enabled():
        return None
    return provider


def account_kinds():
    """The kinds of account a member can link on this site, in the order to offer them."""
    kinds = []
    if dev.enabled():
        kinds.append("dev")
    return kinds
