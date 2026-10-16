"""Which pages may ask for the microphone and the camera (pure, no I/O).

Every page says "no" to both, except the pages that hold a call or a Space: their own window (`/call/<room>`) and
the Space page (`/space/<id>`). They are kept in a window of their own because the site reloads on every page, so
a call can't live inside the chat page or the chat side panel, and neither needs a microphone. A frame can never
get more than the page around it allows, so a page that says "no" also covers every frame inside it.

`__main__.after_request` sends both headers: `Feature-Policy` (older browsers) and `Permissions-Policy`.
"""

CALL_PREFIXES = ("/call/", "/space/")

# the features every page turns off; the call pages turn microphone and camera back on for this site only
_OFF = ("geolocation", "midi", "notifications", "push", "sync-xhr", "microphone", "camera", "magnetometer",
        "gyroscope", "vibrate", "fullscreen", "payment")
_CALL_FEATURES = ("microphone", "camera")


def is_call_page(path):
    """A call's or a Space's own page: the prefix and something after it."""
    return any(path.startswith(prefix) and len(path) > len(prefix) for prefix in CALL_PREFIXES) if path else False


def feature_policy(path):
    allow = _CALL_FEATURES if is_call_page(path) else ()
    return " ".join(f"{name} '{'self' if name in allow else 'none'}';" for name in _OFF)


# Permissions-Policy names the features the browsers know under this header (vibrate, push and notifications are
# not policy-controlled features there)
_PERMISSIONS = ("geolocation", "midi", "sync-xhr", "microphone", "camera", "magnetometer", "gyroscope",
                "fullscreen", "payment")


def permissions_policy(path):
    allow = _CALL_FEATURES if is_call_page(path) else ()
    return ", ".join(f"{name}=(self)" if name in allow else f"{name}=()" for name in _PERMISSIONS)
