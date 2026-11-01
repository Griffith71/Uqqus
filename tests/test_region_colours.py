"""The regions' colours on /regions (the owner, 2026-10-31: "more of an upbeat colour scheme for the countries ... a good diversity of
colours, and that similar colours aren't too close to each other"). scripts/region_colours.py chooses them; this holds the committed
ones (seed-db.sql, scripts/migrations/2026-12-15_region_colours.sql) to what that asked for, with the map's own distances between the
regions (tests/data/region_neighbours.json). A gap is the OKLab distance times 100: 2 is just visible, 10 plainly different."""
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import region_colours as rc  # noqa: E402

# the newest migration that sets them: a later pass (a country moved) is a new file, and the seed follows it
MIGRATION = sorted(p.name for p in (ROOT / "scripts" / "migrations").glob("*region_colours*.sql"))[-1]
DATA = json.loads((ROOT / "tests" / "data" / "region_neighbours.json").read_text(encoding="utf-8"))


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def seed_colours():
    return dict(re.findall(r"INSERT INTO public\.regions VALUES \(\d+, '(\w+)', '[^']*', '[^']*', '(#[0-9a-f]{6})'\);", read("seed-db.sql")))


def migration_colours():
    return dict(re.findall(r"\('(\w+)', '(#[0-9a-f]{6})'\)", read("scripts", "migrations", MIGRATION)))


def test_the_seed_and_the_migration_carry_the_same_colours_one_per_region():
    seed = seed_colours()
    assert len(seed) == 32 and seed == migration_colours()
    assert len(set(seed.values())) == 32, "two regions share a colour"


def test_the_migration_is_last_in_the_ledger_and_safe_to_run_twice():
    schema = read("schema.sql")
    listed = re.findall(r"\('(\d{4}-\d{2}-\d{2}_[\w-]+\.sql)'\)", schema.split("INSERT INTO public.schema_migrations", 1)[1])
    assert listed == sorted(listed) and MIGRATION in listed and listed.index(MIGRATION) > listed.index("2026-12-14_log_public.sql")
    assert "r.color IS DISTINCT FROM v.color" in read("scripts", "migrations", MIGRATION)


def test_every_colour_is_bright_not_muddy_and_clear_of_the_maps_grey_land():
    land = rc.oklab(rc.hex_to_linear(rc.LAND))
    white = rc.oklab([1, 1, 1])
    for region, colour in seed_colours().items():
        lightness, chroma, hue = rc.lch(colour)
        assert chroma >= 0.14 and 0.63 <= lightness <= 0.91, (region, colour)         # no greys, pastels or near-blacks
        if 35 <= hue < 75:
            assert lightness >= 0.73, (region, colour, "a dark orange is brown")
        if 75 <= hue <= 140:
            assert lightness >= 0.83, (region, colour, "a dark yellow or lime is mustard or olive")
        lab = rc.oklab(rc.hex_to_linear(colour))
        assert rc.gap(lab, land) >= 12 and rc.gap(lab, white) >= 12, (region, colour)


def test_the_colours_are_the_ones_the_generator_may_choose():
    """The generator's own rules are the definition of "upbeat": a colour outside its candidates was made by hand."""
    allowed = {text for text, _lab in rc.candidates()}
    assert [r for r, c in seed_colours().items() if c not in allowed] == []


def test_no_two_regions_are_alike_even_far_apart():
    colours = seed_colours()
    regions = sorted(colours)
    tightest = min((rc.gaps(colours[a], colours[b])[0], a, b) for i, a in enumerate(regions) for b in regions[i + 1:])
    assert tightest[0] >= 5.5, tightest                      # 32 of 40 bright colours picked to be far apart: their smallest gap is about 5.8


def test_the_distances_are_those_of_todays_countries_and_regions():
    assert DATA["countries"] == rc.fingerprint(rc.country_regions()), (
        "a country moved to another region: run `python scripts/region_colours.py neighbours --map world.js` and then `colours`")
    assert set(DATA["area_share"]) == set(seed_colours()) == set(rc.country_regions().values())
    assert abs(sum(DATA["area_share"].values()) - 1) < 0.01


def test_regions_that_touch_or_are_near_each_other_are_told_apart():
    """The nearer two regions are on the map, the further apart their colours: by the plain gap, and by the gap a person who can't tell
    red from green has (deutan and protan). Distances are between the outlines, in map units (the map is 900 wide)."""
    colours = seed_colours()
    bands = [(0, 1.2, 15, 9), (1.2, 15, 8, 6), (15, 40, 6.5, 5)]            # from, to, smallest plain gap, smallest colour-blind gap
    for low, high, plain_min, blind_min in bands:
        pairs = [(key, d) for key, d in DATA["distances"].items() if low <= d < high]
        assert pairs, (low, high)
        for key, d in pairs:
            a, b = key.split("|")
            plain, blind = rc.gaps(colours[a], colours[b])
            assert plain >= plain_min and blind >= blind_min, (a, b, d, round(plain, 1), round(blind, 1))


def test_the_gaps_are_symmetric_and_zero_for_the_same_colour():
    assert rc.gaps("#52b862", "#52b862") == (0.0, 0.0)
    assert rc.gaps("#03e3fe", "#db687a") == rc.gaps("#db687a", "#03e3fe")
    # the old browns, olive, greys and near-blacks are exactly what the generator keeps out
    allowed = {text for text, _lab in rc.candidates()}
    assert not allowed & {"#8c510a", "#a09a40", "#897f70", "#d0b69f", "#023858", "#4d004b", "#00441b", "#a95c30"}


def test_the_generators_alias_table_is_the_pages():
    """The map has three shapes with no code of their own; the page colours them as the region they belong to, and the generator measures
    them as that region (a shape left out would leave Cyprus, Kosovo or Somalia out of the distances)."""
    tree = ast.parse(read("ruqqus", "routes", "regions.py"))
    page = next(ast.literal_eval(n.value) for n in tree.body
                if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "MAP_LIBRARY_TERRITORY_ALIASES")
    assert rc.ALIASES == page
    assert set(rc.ALIASES.values()) <= set(rc.country_regions())


def test_the_annealing_keeps_an_exact_cost_and_the_committed_assignment_is_a_good_one():
    """The search's running cost must be the cost of the assignment it holds (it was not: swaps and recolours weighed a pair differently,
    and the printed cost, the best snapshot and the choice between restarts all went by a drifting number), and the committed colours must
    cost about what the generator finds: six fresh searches of it cost 57,700 to 60,900, the committed assignment 57,646."""
    committed = seed_colours()
    extra = [text for text, _lab in rc.candidates() if text not in committed.values()][:4]
    colours = sorted(committed.values()) + extra                 # spare colours, so recolouring happens as well as swapping
    cost, same = rc.assign(colours, DATA, 0, 0, start=committed)
    assert same == committed and cost < 60000, cost
    tracked, found = rc.assign(colours, DATA, 4000, 1, start=committed)
    recomputed, _ = rc.assign(colours, DATA, 0, 0, start=found)
    assert abs(tracked - recomputed) < 1e-6 * recomputed, (tracked, recomputed)
