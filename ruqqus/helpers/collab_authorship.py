"""Who typed what in a shared draft (pure, no I/O).

A field's authorship is a list of runs `[user_id, length]` covering its text exactly (lengths in UTF-16 units, as the
ops count). Every op committed by the server updates it with the same walk that changes the text: a kept stretch keeps
its runs, an insert becomes a run of the person who sent it, a delete takes its characters (and their credit) away.
Pasted text counts as typed by whoever pasted it. A fork copies the runs as they are, so the original typists stay
credited.
"""
from ruqqus.helpers import collab_ot as ot


def empty():
    return []


def for_text(text, user_id):
    """The runs of a text one person wrote (a new draft's starting text)."""
    n = ot.units(text)
    return [[int(user_id), n]] if n else []


def length(runs):
    return sum(n for _, n in runs)


def _push(out, uid, n):
    if n <= 0:
        return
    if out and out[-1][0] == uid:
        out[-1][1] += n
    else:
        out.append([uid, n])


def apply(runs, op, user_id):
    """The runs after `op`, sent by `user_id`. `op` must fit the text the runs cover."""
    if ot.base_length(op) != length(runs):
        raise ot.OpError("the authorship doesn't fit the op")
    out, i, left = [], 0, (runs[0][1] if runs else 0)
    for c in op:
        if isinstance(c, str):
            _push(out, int(user_id), ot.units(c))
            continue
        n = abs(c)
        while n:
            while left == 0:
                i += 1
                left = runs[i][1]
            take = min(n, left)
            if c > 0:
                _push(out, runs[i][0], take)
            left -= take
            n -= take
    return out


def clean(value):
    """Runs read back from JSON: ints only, positive lengths, neighbours merged."""
    out = []
    for item in value or []:
        if (isinstance(item, (list, tuple)) and len(item) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in item)
                and item[1] > 0):
            _push(out, item[0], item[1])
    return out


def shares(runs):
    """[(user_id, units, percent)] largest first; percents are whole numbers that add up to 100 (or nothing)."""
    total = length(runs)
    if not total:
        return []
    by_user = {}
    order = []
    for uid, n in runs:
        if uid not in by_user:
            order.append(uid)
            by_user[uid] = 0
        by_user[uid] += n
    people = sorted(order, key=lambda u: (-by_user[u], order.index(u)))
    exact = [by_user[u] * 100 / total for u in people]
    whole = [int(x) for x in exact]
    # largest remainders get the leftover points, so the shown numbers add up to 100
    for k in sorted(range(len(people)), key=lambda k: -(exact[k] - whole[k]))[:100 - sum(whole)]:
        whole[k] += 1
    return [(u, by_user[u], whole[k]) for k, u in enumerate(people)]


def pieces(text, runs):
    """[(user_id, piece of text)] for drawing the text tinted by who typed it. Runs that don't fit the text (they
    always should) give one piece credited to nobody (user id None)."""
    data = text.encode("utf-16-le", "surrogatepass")
    if length(runs) != len(data) // 2:
        return [(None, text)] if text else []
    out, pos = [], 0
    for uid, n in runs:
        out.append((uid, data[pos * 2:(pos + n) * 2].decode("utf-16-le", "surrogatepass")))
        pos += n
    return out
