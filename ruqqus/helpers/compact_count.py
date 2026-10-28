"""How a count reads where room is short (scores, the action row's counts): 999, 1.2k, 12k, 1.2M. Pure.

The same rule is `compactCount` in assets/js/all_js.js, which redraws a score after a vote; the exact numbers stay in
the score's tooltip and its data-ups / data-downs."""


def compact(count):
    count = int(count or 0)
    sign = "-" if count < 0 else ""
    n = abs(count)
    if n >= 1_000_000:
        text = f"{n // 100_000 / 10:.1f}".replace(".0", "") + "M"     # truncated, never rounded up to "1000k"
    elif n >= 10_000:
        text = f"{n // 1000}k"
    elif n >= 1000:
        text = f"{n // 100 / 10:.1f}".replace(".0", "") + "k"
    else:
        text = str(n)
    return sign + text
