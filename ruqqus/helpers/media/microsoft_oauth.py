"""Linking a member's Microsoft account, for OneDrive (helpers/media/onedrive.py).

Set MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET: an app registration in Microsoft Entra
(free), "Web" platform, with <site>/settings/media/microsoft/callback as its redirect address.
MICROSOFT_TENANT is `consumers` by default (personal Microsoft accounts: Outlook, Hotmail,
Xbox, with 5 GB of free OneDrive); `common` also lets work and school accounts in.

Scope: Files.ReadWrite.AppFolder, a folder of the member's OneDrive that belongs to this site
("Apps/<app name>"). The site can see nothing else in the member's OneDrive.
"""
from os import environ

from .oauth import Service, id_token_subject

SCOPE_APPFOLDER = "Files.ReadWrite.AppFolder"
SCOPES = ("openid", "offline_access", SCOPE_APPFOLDER)


def _tenant():
    return environ.get("MICROSOFT_TENANT", "").strip() or "consumers"


def _identity(token_data):
    return id_token_subject(token_data.get("id_token", ""))


SERVICE = Service(
    name="microsoft",
    label="Microsoft",
    id_env="MICROSOFT_CLIENT_ID",
    secret_env="MICROSOFT_CLIENT_SECRET",
    # the tenant is read when the address is made, so a change of setting needs no restart
    auth_url=lambda: f"https://login.microsoftonline.com/{_tenant()}/oauth2/v2.0/authorize",
    token_url=lambda: f"https://login.microsoftonline.com/{_tenant()}/oauth2/v2.0/token",
    wants={"storage": SCOPES},
    required=SCOPE_APPFOLDER,
    identity=_identity,
    auth_params={"prompt": "select_account", "response_mode": "query"},
    refresh_scope=" ".join(SCOPES),
)
