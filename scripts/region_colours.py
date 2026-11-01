"""The colours of the regions on /regions (regions.color; the map and the cards draw them).

The owner, 2026-10-31: "more of an upbeat colour scheme for the countries ... a good diversity of colours, and that similar colours
aren't too close to each other". Two steps, both stdlib only:

    python scripts/region_colours.py neighbours --map world.js   # how far apart the regions are on the map -> tests/data/region_neighbours.json
    python scripts/region_colours.py colours                     # 32 colours and who gets which -> a migration's VALUES, and a report

`world.js` is the map the page loads (https://cdn.jsdelivr.net/npm/jsvectormap@1.5.3/dist/maps/world.js). The neighbours file holds, for every
two regions less than 250 map units apart (the map is 900 wide), the distance between their outlines, and each region's share of the land,
and a fingerprint of which country is in which region (ruqqus/helpers/regions.py COUNTRY_TO_REGION): tests/test_region_colours.py fails
when a country moves, because the distances are then out of date. Run `neighbours` again and then `colours`.

How the colours are chosen:
1. **The palette**: bright colours only (OKLCH chroma 0.145-0.185, lightness 0.64-0.90; no browns or muddy olives, yellows and limes only
   when light; calm aqua so cyan and teal don't pile up; none within 14 of the map's grey land or of white), 40 of them picked to be as
   far from each other as a search for it finds (the smallest gap between any two is as large as that search makes it; "gap" below is
   OKLab distance times 100: 2 is just visible, 10 plainly different, 30 very different). The 32 that are used are the 8 more than
   that, taken out where they suit the map least, so the smallest gap among them is a little larger still.
2. **Who gets which** (32 of the 40, simulated annealing): regions that touch are pushed hard to differ by 20 or more (a penalty, not
   a rule), and regions near each other by what a target asks: it falls off with the distance between their outlines, 46 at a border, 33
   at 15 map units, 20 at 40, 9 at 100 and nothing from 250. A gap is the smallest of: the plain one, the one under deutan and protan colour blindness (a little looser, since
   they lose range), and the chromatic one, so a lighter pink beside a pink is still a pink. The three largest regions (North America,
   Russia, East Asia...) get the calmer colours: a big region in a loud colour shouts.

The result depends on Python's random numbers; another version may give other, equally good colours. The committed ones came from an earlier
version of this search whose running cost drifted from the true cost (fixed since: `assign` is exact now and can score any assignment with
`start=` and `iterations=0`); on the exact cost they come to 57,646, a little better than six fresh runs of this version (57,730 to
60,940), so they were kept. What counts is what tests/test_region_colours.py checks.
"""
import argparse
import ast
import collections
import hashlib
import json
import math
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NEIGHBOURS = ROOT / "tests" / "data" / "region_neighbours.json"
REGIONS_PY = ROOT / "ruqqus" / "helpers" / "regions.py"
LAND = "#d1dbdd"                                             # the map's land that belongs to no region (templates/regions.html)
ALIASES = {"_0": "CY", "_1": "XK", "_2": "SO"}               # the map's three shapes with no code of their own (routes/regions.py)
RADIUS = 250.0


# --- colour: OKLab, the colour-blind view ------------------------------------------------------------------------------------

def _to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(c):
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def hex_to_linear(text):
    text = text.lstrip("#")
    return [_to_linear(int(text[i:i + 2], 16) / 255) for i in (0, 2, 4)]


def linear_to_hex(rgb):
    return "#" + "".join(f"{round(_to_srgb(c) * 255):02x}" for c in rgb)


def oklab(rgb):
    r, g, b = rgb
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = (math.copysign(abs(x) ** (1 / 3), x) for x in (l, m, s))
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def oklch_to_linear(lightness, chroma, hue):
    a, b = chroma * math.cos(math.radians(hue)), chroma * math.sin(math.radians(hue))
    l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s]


def lch(text):
    """(lightness, chroma, hue in degrees) of a #rrggbb colour."""
    lightness, a, b = oklab(hex_to_linear(text))
    return lightness, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


# Machado, Oliveira and Fernandes (2009), full severity, on linear sRGB
_COLOUR_BLIND = {
    "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
    "protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
}


def seen_by(text, kind):
    """The colour as OKLab sees it with deutan or protan colour vision."""
    rgb = hex_to_linear(text)
    return oklab([max(0.0, min(1.0, sum(row[j] * rgb[j] for j in range(3)))) for row in _COLOUR_BLIND[kind]])


def gap(a, b):
    """The distance between two OKLab colours, times 100."""
    return 100 * math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def gaps(first, second):
    """(plain, colour-blind: the smaller of deutan and protan) between two #rrggbb colours."""
    plain = gap(oklab(hex_to_linear(first)), oklab(hex_to_linear(second)))
    blind = min(gap(seen_by(first, kind), seen_by(second, kind)) for kind in _COLOUR_BLIND)
    return plain, blind


# --- the map ------------------------------------------------------------------------------------------------------------------

def country_regions():
    """{country: region code} from ruqqus/helpers/regions.py, read without importing the app."""
    for node in ast.parse(REGIONS_PY.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "COUNTRY_TO_REGION":
            return ast.literal_eval(node.value)
    raise SystemExit("COUNTRY_TO_REGION not found in ruqqus/helpers/regions.py")


def fingerprint(countries):
    return hashlib.sha256(json.dumps(sorted(countries.items())).encode()).hexdigest()[:16]


def outlines(map_text):
    """{shape code: [polygon, ...]} from jsvectormap's world map (only absolute M, relative l and Z are used)."""
    shapes = {}
    for code, path in re.findall(r'(\w+):\{path:"([^"]+)"', map_text):
        polygons, current, x, y = [], [], 0.0, 0.0
        for command, args in re.findall(r"([MlZ])([^MlZ]*)", path):
            if command == "M":
                if current:
                    polygons.append(current)
                x, y = map(float, args.split(",")[:2])
                current = [(x, y)]
            elif command == "l":
                numbers = [float(n) for n in re.split(r"[ ,]+", args.strip()) if n]
                for i in range(0, len(numbers) - 1, 2):
                    x += numbers[i]
                    y += numbers[i + 1]
                    current.append((x, y))
            elif current:
                polygons.append(current)
                current = []
        if current:
            polygons.append(current)
        shapes[code] = polygons
    return shapes


def _points(polygon, step):
    for (x0, y0), (x1, y1) in zip(polygon, polygon[1:] + polygon[:1]):
        pieces = max(1, int(math.hypot(x1 - x0, y1 - y0) / step))
        for k in range(pieces):
            yield x0 + (x1 - x0) * k / pieces, y0 + (y1 - y0) * k / pieces


def _closest(points_by_region, radius):
    cells = collections.defaultdict(list)
    for region, points in points_by_region.items():
        for x, y in points:
            cells[(int(x // radius), int(y // radius))].append((region, x, y))
    best = {}
    for (cx, cy), items in cells.items():
        around = [p for dx in (-1, 0, 1) for dy in (-1, 0, 1) for p in cells.get((cx + dx, cy + dy), ())]
        for region, x, y in items:
            for other, x2, y2 in around:
                if other > region:
                    d = math.hypot(x - x2, y - y2)
                    if d < radius and d < best.get((region, other), 1e9):
                        best[(region, other)] = d
    return best


def measure(map_text):
    """The neighbours file: the distance between the outlines of every two regions closer than RADIUS map units (the fine pass finds the
    very close ones exactly, the coarse pass the rest), each region's share of the land, and the fingerprint of the countries."""
    countries = country_regions()
    owner = dict(countries)
    owner.update({shape: countries.get(code) for shape, code in ALIASES.items()})
    shapes = outlines(map_text)
    fine, coarse = collections.defaultdict(list), collections.defaultdict(list)
    area = collections.Counter()
    for code, polygons in shapes.items():
        region = owner.get(code)
        if not region:
            continue
        for polygon in polygons:
            fine[region].extend(_points(polygon, 1.0))
            coarse[region].extend(_points(polygon, 6.0))
            area[region] += abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(polygon, polygon[1:] + polygon[:1]))) / 2
    distances = _closest(coarse, RADIUS)
    distances.update(_closest(fine, 14.0))
    total = sum(area.values())
    return {"map": "jsvectormap 1.5.3 world (900 units wide)", "radius": RADIUS, "countries": fingerprint(countries),
            "area_share": {r: round(a / total, 4) for r, a in sorted(area.items())},
            "distances": {f"{a}|{b}": round(d, 3) for (a, b), d in sorted(distances.items())}}


# --- the palette ----------------------------------------------------------------------------------------------------------------

def candidates():
    """Bright colours as (hex, OKLab), hue every 3 degrees."""
    land, white = oklab(hex_to_linear(LAND)), oklab([1, 1, 1])
    found = []
    for hue in range(0, 360, 3):
        for lightness in (round(0.64 + 0.02 * i, 2) for i in range(14)):
            for chroma in (round(0.145 + 0.005 * i, 3) for i in range(9)):
                if 35 <= hue < 75 and lightness < 0.74:
                    continue                                   # oranges must be light, or they turn brown
                if 75 <= hue <= 140 and (lightness < 0.84 or chroma < 0.165):
                    continue                                   # yellows and limes: light and strong, or they turn mustard and olive
                if 125 <= hue <= 175 and lightness < 0.70:
                    continue                                   # no dull dark greens
                if 130 <= hue <= 165 and lightness > 0.84:
                    continue                                   # a minty near-white
                if 160 <= hue <= 215 and (chroma > 0.16 or lightness > 0.84):
                    continue                                   # aqua and cyan stay calm: electric ones sit on top of each other
                if 15 <= hue < 75 and chroma < 0.15:
                    continue                                   # red-oranges need chroma or they are brick
                if (hue >= 335 or hue <= 25) and lightness < 0.66:
                    continue
                rgb = oklch_to_linear(lightness, chroma, hue)
                if min(rgb) < -0.002 or max(rgb) > 1.002:
                    continue                                   # outside what a screen shows
                text = linear_to_hex(rgb)
                lab = oklab(hex_to_linear(text))
                if gap(lab, land) >= 14 and gap(lab, white) >= 14:
                    found.append((text, lab))
    return found


def palette(size, seed=2):
    """`size` of the candidates as far from each other as possible: farthest-point picks from ten starts, the best one improved by
    replacing a colour of the closest pair with whichever colour leaves the largest smallest gap."""
    pool = candidates()

    def smallest(chosen):
        return min(gap(pool[a][1], pool[b][1]) for i, a in enumerate(chosen) for b in chosen[i + 1:])

    best = None
    for start in range(10):
        rnd = random.Random(start)
        chosen = [rnd.randrange(len(pool))]
        nearest = [gap(c[1], pool[chosen[0]][1]) for c in pool]
        while len(chosen) < size:
            nxt = max(range(len(pool)), key=lambda i: nearest[i])
            chosen.append(nxt)
            nearest = [min(nearest[i], gap(pool[i][1], pool[nxt][1])) for i in range(len(pool))]
        if best is None or smallest(chosen) > best[0]:
            best = (smallest(chosen), chosen)
    chosen, rnd, top = best[1], random.Random(seed), best[0]
    for _ in range(800):
        closest = min(((gap(pool[a][1], pool[b][1]), a, b) for i, a in enumerate(chosen) for b in chosen[i + 1:]))
        victim = rnd.choice(closest[1:])
        rest = [c for c in chosen if c != victim]
        pick = max((c for c in rnd.sample(range(len(pool)), 400) if c not in chosen),
                   key=lambda c: min(gap(pool[c][1], pool[r][1]) for r in rest))
        if smallest(rest + [pick]) >= top:
            chosen, top = rest + [pick], smallest(rest + [pick])
    return [pool[i][0] for i in chosen]


# --- who gets which -------------------------------------------------------------------------------------------------------------

def _loud(text):
    """How loud a colour is on a big region: chroma past a calm fill's, and lightness near white."""
    lightness, chroma, _hue = lch(text)
    return max(0.0, chroma - 0.15) * 400 + max(0.0, lightness - 0.82) * 120


def _target(distance):
    return 34 * math.exp(-distance / 32) + 12 * max(0.0, 1 - distance / RADIUS)


def assign(colours, data, iterations, seed, loud_weight=5.0, start=None):
    """(cost, {region: colour}): annealing over which of the colours each region gets, from a random start or from `start` (a
    {region: colour}: with iterations=0 that is its cost). The cost is exact: each region's loudness once, and every pair of regions
    twice (each sees the other), so a move's change is the change of that sum."""
    regions = sorted(data["area_share"])
    n, m = len(regions), len(colours)
    index = {r: i for i, r in enumerate(regions)}
    plain = [[0.0] * m for _ in range(m)]
    blind = [[0.0] * m for _ in range(m)]
    effective = [[0.0] * m for _ in range(m)]
    labs = [oklab(hex_to_linear(c)) for c in colours]
    for a in range(m):
        for b in range(m):
            if a != b:
                plain[a][b], blind[a][b] = gaps(colours[a], colours[b])
                chromatic = 100 * math.hypot(labs[a][1] - labs[b][1], labs[a][2] - labs[b][2])
                effective[a][b] = min(plain[a][b], 1.6 * blind[a][b], 1.5 * chromatic)
    loudness = [_loud(c) for c in colours]
    near = [[] for _ in range(n)]
    for key, d in data["distances"].items():
        a, b = (index[r] for r in key.split("|"))
        near[a].append((b, _target(d), d < 1.2, d))
        near[b].append((a, _target(d), d < 1.2, d))
    weight = [math.sqrt(data["area_share"][r]) * loud_weight for r in regions]

    def loud(i, c):
        return weight[i] * loudness[c] ** 2

    def pairs(i, c, held, skip=-1):
        """What region i in colour c costs against its neighbours (but `skip`), as one end of each pair."""
        total = 0.0
        for k, target, touching, d in near[i]:
            if k == skip:
                continue
            other, e = held[k], effective[c][held[k]]
            if e < target:
                total += (target - e) ** 2
            if touching and e < 20.0:
                total += 6 * (20.0 - e) ** 2                       # regions that share a border are never alike
            if d < 35 and blind[c][other] < 8.0:
                total += 3 * (8.0 - blind[c][other]) ** 2          # nor for someone who can't tell red from green
        return total

    rnd = random.Random(seed)
    held = [colours.index(start[r]) for r in regions] if start else rnd.sample(range(m), n)
    used = set(held)
    current = sum(loud(i, held[i]) + pairs(i, held[i], held) for i in range(n))
    best, best_held = current, held[:]
    for step in range(iterations):
        heat = 120.0 * (0.1 / 120.0) ** (step / iterations)
        if rnd.random() < 0.55:
            i, j = rnd.sample(range(n), 2)
            ci, cj = held[i], held[j]
            # the pair (i, j) itself does not change when they trade places; every other pair of theirs is counted from both ends
            change = (loud(i, cj) + loud(j, ci) - loud(i, ci) - loud(j, cj)
                      + 2 * (pairs(i, cj, held, skip=j) + pairs(j, ci, held, skip=i)
                             - pairs(i, ci, held, skip=j) - pairs(j, cj, held, skip=i)))
            if change <= 0 or rnd.random() < math.exp(-change / heat):
                held[i], held[j] = cj, ci
                current += change
        else:
            i, new = rnd.randrange(n), rnd.randrange(m)
            if new in used:
                continue
            old = held[i]
            change = loud(i, new) - loud(i, old) + 2 * (pairs(i, new, held) - pairs(i, old, held))
            if change <= 0 or rnd.random() < math.exp(-change / heat):
                held[i] = new
                used.discard(old)
                used.add(new)
                current += change
        if current < best:
            best, best_held = current, held[:]
    return best, {regions[i]: colours[best_held[i]] for i in range(n)}


# --- the command line -------------------------------------------------------------------------------------------------------------

def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("neighbours", help="measure the map and write tests/data/region_neighbours.json")
    one.add_argument("--map", required=True, help="the jsvectormap world.js file")
    two = sub.add_parser("colours", help="choose the colours and print the migration's rows")
    two.add_argument("--palette", type=int, default=40, help="how many colours to choose from (at least one per region)")
    two.add_argument("--iterations", type=int, default=3_000_000)
    two.add_argument("--restarts", type=int, default=2)
    two.add_argument("--seed", type=int, default=30)
    args = parser.parse_args(argv)
    if args.command == "neighbours":
        data = measure(Path(args.map).read_text(encoding="utf-8"))
        NEIGHBOURS.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        print(f"{len(data['distances'])} pairs of regions within {RADIUS:g} map units -> {NEIGHBOURS.relative_to(ROOT)}")
        return 0
    data = json.loads(NEIGHBOURS.read_text(encoding="utf-8"))
    if data["countries"] != fingerprint(country_regions()):
        print("the countries of the regions changed since the neighbours file was made: run `neighbours` first")
        return 1
    if args.palette < len(data["area_share"]):
        print(f"--palette must be at least {len(data['area_share'])}: a colour for every region")
        return 2
    colours = palette(args.palette)
    runs = [assign(colours, data, args.iterations, args.seed + k) for k in range(args.restarts)]
    cost, chosen = min(runs, key=lambda run: run[0])
    print(f"-- cost {cost:.0f} of {[round(r[0]) for r in runs]}")
    print(",\n".join(f"    ('{region}', '{colour}')" for region, colour in sorted(chosen.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
