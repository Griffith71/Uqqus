"""The sign-up form's captcha: hCaptcha or Cloudflare Turnstile, chosen by `CAPTCHA_PROVIDER` (the signup guard,
helpers/signup_risk.py).

* `CAPTCHA_PROVIDER=hcaptcha` with `HCAPTCHA_SITEKEY` / `HCAPTCHA_SECRET`, or `turnstile` with `TURNSTILE_SITEKEY` /
  `TURNSTILE_SECRET`. Unset with an hCaptcha site key is hCaptcha (what the site did before); `none`, or a provider
  without both keys, is no captcha (local development).
* `widget(config)` is what `sign_up.html` draws; `verify(config, token, address)` asks the provider, once, with a
  timeout, and never raises: "ok", "failed" (the provider said no), "missing" (the form carried no answer) or
  "unavailable" (the provider could not be asked or answered nonsense). The signup guard refuses "missing" and
  "failed" and quarantines "unavailable", so an outage neither shuts sign-ups nor lets bots through.
* The two siteverify addresses are fixed first-party hosts, so plain `requests` is used (helpers/safe_fetch.py is
  for addresses a member typed). The address is sent as `remoteip`, as both providers ask.
tests/test_captcha.py.
"""
from dataclasses import dataclass

TIMEOUT = 5


@dataclass(frozen=True)
class Provider:
    name: str
    script: str
    widget_class: str
    field: str
    verify_url: str
    sends_sitekey: bool


PROVIDERS = {
    "hcaptcha": Provider("hcaptcha", "https://js.hcaptcha.com/1/api.js", "h-captcha", "h-captcha-response",
                         "https://api.hcaptcha.com/siteverify", True),
    "turnstile": Provider("turnstile", "https://challenges.cloudflare.com/turnstile/v0/api.js", "cf-turnstile",
                          "cf-turnstile-response", "https://challenges.cloudflare.com/turnstile/v0/siteverify", False),
}


def keys(config, name):
    prefix = name.upper()
    return (config.get(f"{prefix}_SITEKEY") or "").strip(), (config.get(f"{prefix}_SECRET") or "").strip()


LABELS = {"hcaptcha": "hCaptcha", "turnstile": "Cloudflare Turnstile"}


def label(config):
    """The service's name as the privacy page writes it, or None when there is no captcha."""
    return LABELS.get(chosen(config))


def chosen(config):
    """The configured provider's name, or None when there is no captcha."""
    name = (config.get("CAPTCHA_PROVIDER") or "").strip().lower()
    if not name:
        name = "hcaptcha" if keys(config, "hcaptcha")[0] else ""
    if name not in PROVIDERS:
        return None
    sitekey, secret = keys(config, name)
    return name if sitekey and secret else None


def widget(config):
    """What the sign-up page draws, or None."""
    name = chosen(config)
    if not name:
        return None
    provider = PROVIDERS[name]
    return {"provider": name, "sitekey": keys(config, name)[0], "script": provider.script,
            "widget_class": provider.widget_class}


def answer_of(config, form):
    name = chosen(config)
    return (form.get(PROVIDERS[name].field) or "").strip() if name else ""


def verify(config, token, address, post=None):
    name = chosen(config)
    if not name:
        return "off"
    if not token:
        return "missing"
    provider = PROVIDERS[name]
    sitekey, secret = keys(config, name)
    data = {"secret": secret, "response": token, "remoteip": address or ""}
    if provider.sends_sitekey:
        data["sitekey"] = sitekey
    if post is None:
        import requests
        post = requests.post
    try:
        answer = post(provider.verify_url, data=data, timeout=TIMEOUT)
        if answer.status_code != 200:
            return "unavailable"
        body = answer.json()
    except Exception:
        return "unavailable"
    if not isinstance(body, dict) or not isinstance(body.get("success"), bool):
        return "unavailable"
    return "ok" if body["success"] else "failed"
