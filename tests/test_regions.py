"""Guard the region layout: country membership, centroids, and the seed rows."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _literal(name):
    # regions.py imports the whole app (db models, gevent), so read the plain
    # dict literals from the source instead of importing the module.
    tree = ast.parse((ROOT / "ruqqus" / "helpers" / "regions.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.targets[0].id == name:
            return ast.literal_eval(node.value)
    raise KeyError(name)


COUNTRY_TO_REGION = _literal("COUNTRY_TO_REGION")
REGION_CENTROIDS = _literal("REGION_CENTROIDS")
REGION_COUNTRIES = {}
for _cc, _region in COUNTRY_TO_REGION.items():
    REGION_COUNTRIES.setdefault(_region, []).append(_cc)

AFRICA_HORN_AND_SAHEL = {
    "west_africa": {"ML"},
    "sahel": {"SD", "TD", "NE", "MR", "EH"},
    "equatorial_nile": {"KE", "UG", "SS"},
    "horn_of_africa": {"ER", "ET", "SO", "DJ"},
}


def test_target_countries_are_in_their_regions():
    for region, countries in AFRICA_HORN_AND_SAHEL.items():
        for cc in countries:
            assert COUNTRY_TO_REGION[cc] == region, f"{cc} should be in {region}"


def test_sahel_and_horn_have_exactly_the_listed_countries():
    for region in ("sahel", "equatorial_nile", "horn_of_africa"):
        assert set(REGION_COUNTRIES[region]) == AFRICA_HORN_AND_SAHEL[region]


def test_every_region_has_a_centroid():
    assert set(COUNTRY_TO_REGION.values()) <= set(REGION_CENTROIDS)


def test_seed_has_a_row_for_every_region():
    seed = (ROOT / "seed-db.sql").read_text(encoding="utf-8")
    codes = set(re.findall(r"INSERT INTO public\.regions VALUES \(\d+, '([a-z_]+)'", seed))
    assert set(REGION_CENTROIDS) == codes
