"""Which pages may ask for the microphone and the camera (pure, no I/O).

Every page says "no" to both, except the pages that hold a call or a Space: their own window (`/call/<room>`) and
the Space page (`/space/<id>`). They are kept in a window of their own because the site reloads on every page, so
a call can't live inside the chat page or the chat side panel, and neither needs a microphone. A frame can never
get more than the page around it allows, so a page that says "no" also covers every frame inside it.

`__main__.after_request` sends `Permissions-Policy` (audit U-03: the older `Feature-Policy` duplicated it with
features browsers don't know, a console warning on every page).

Fullscreen is allowed on every page, for the page and the players it embeds (videos could not go full screen while it
was off): each player's frame still has to ask for it (`allow="fullscreen"`), and only vetted players are embedded
(helpers/embed.py).
"""

CALL_PREFIXES = ("/call/", "/space/")

_CALL_FEATURES = ("microphone", "camera")
_EVERYWHERE = ("fullscreen",)            # allowed on every page, for the page and its players' frames


def is_call_page(path):
    """A call's or a Space's own page: the prefix and something after it."""
    return any(path.startswith(prefix) and len(path) > len(prefix) for prefix in CALL_PREFIXES) if path else False


# the features every page turns off (the ones browsers know under this header); the call pages turn microphone and
# camera back on for this site only
_PERMISSIONS = ("geolocation", "midi", "sync-xhr", "microphone", "camera", "magnetometer", "gyroscope", "payment")


def permissions_policy(path):
    allow = _CALL_FEATURES if is_call_page(path) else ()
    return ", ".join([f"{name}=(self)" if name in allow else f"{name}=()" for name in _PERMISSIONS]
                     + [f"{name}=*" for name in _EVERYWHERE])
