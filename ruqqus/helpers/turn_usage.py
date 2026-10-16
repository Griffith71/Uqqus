"""The relay's monthly allowance (pure, no I/O).

Calls and Spaces send their audio and video through the site's own TURN relay (coturn), so members never learn
each other's IP addresses. What the relay sends counts against the server's included bandwidth, and **live media
must cost the site nothing** (the owner's rule), so it has a monthly allowance in GB (`TURN_MONTHLY_GB`):

- under 80% of it, calls are as they are (`OPEN`);
- from 80%, video is off and calls are audio only (`AUDIO_ONLY`);
- from 100%, no call or Space starts until the first of next month (`PAUSED`). Text is never affected.

It fails closed, on cost: no allowance set, or no reading of the relay for `STALE_SECONDS` (the counting job,
`scripts/turn_usage.py`, is down), means `PAUSED`.

The job reads coturn's own counters (`--prometheus`): the bytes it sent to clients and to peers since it started.
They start again from 0 when coturn restarts, which `advance` treats as a new count rather than a negative one.
The store is `helpers/turn_usage_store.py`; tests are `tests/test_turn_usage.py`.
"""

import calendar
import datetime
import re

OPEN = "open"
AUDIO_ONLY = "audio_only"
PAUSED = "paused"

AUDIO_ONLY_AT = 0.8
STALE_SECONDS = 30 * 60
GB = 1024 ** 3

# what the relay sends out: to the browsers, and on to the other side of a relayed connection
SENT_METRICS = ("turn_total_traffic_sentb", "turn_total_traffic_peer_sentb")

_SAMPLE = re.compile(r"^([A-Za-z_:][A-Za-z0-9_:]*)(?:\{[^}]*\})?\s+([0-9.eE+-]+)(?:\s+-?\d+)?\s*$")

UNAVAILABLE = "Calls are unavailable right now. Try again later."


def sent_bytes(metrics_text):
    """The bytes the relay has sent since it started, from its Prometheus page. None when the page is not
    coturn's (neither counter is named), 0 before any traffic (coturn names a counter before it has a value)."""
    if not metrics_text:
        return None
    named = any(f"# TYPE {name} " in metrics_text for name in SENT_METRICS)
    total = 0
    seen = False
    for line in metrics_text.splitlines():
        match = _SAMPLE.match(line.strip())
        if not match or match.group(1) not in SENT_METRICS:
            continue
        try:
            total += int(float(match.group(2)))
            seen = True
        except ValueError:
            continue
    if not named and not seen:
        return None
    return total


def advance(last_counter, counter):
    """How much was sent since the last reading. A first reading counts everything (on cost, what was sent
    before the job ever ran is counted rather than forgotten); a counter that went down means coturn restarted
    and started again from 0."""
    if counter is None or counter < 0:
        return 0
    if last_counter is None or counter < last_counter:
        return counter
    return counter - last_counter


def month_of(ts):
    return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).strftime("%Y-%m")


def next_month_start(ts):
    day = datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc)
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return calendar.timegm((year, month, 1, 0, 0, 0))


def allowance_bytes(text):
    """`TURN_MONTHLY_GB` as bytes; None when it is not set or not a positive number."""
    try:
        gb = float((text or "").strip())
    except ValueError:
        return None
    if not 0 < gb < float("inf"):       # also refuses nan
        return None
    return int(gb * GB)


def state(used, allowance, last_reading_utc, now):
    if allowance is None or not last_reading_utc or now - last_reading_utc > STALE_SECONDS:
        return PAUSED
    if used >= allowance:
        return PAUSED
    if used >= allowance * AUDIO_ONLY_AT:
        return AUDIO_ONLY
    return OPEN


def paused_message(now):
    day = datetime.datetime.fromtimestamp(next_month_start(now), datetime.timezone.utc)
    return f"Calls are paused until 1 {day.strftime('%B')}. Messages still work."


def refusal(current_state, used, allowance, now):
    """What a join answers: None to go ahead, otherwise the message. Used by the call and Space routes."""
    if current_state != PAUSED:
        return None
    if allowance is not None and used >= allowance:
        return paused_message(now)
    return UNAVAILABLE


def video_allowed(current_state):
    return current_state == OPEN
