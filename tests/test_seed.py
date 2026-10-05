"""Guard: a fresh docker-compose database must contain the reserved profile
guild, or every POST /submit 404s (submit_post calls get_guild on it)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_seed_creates_the_reserved_profile_guild():
    boards_py = (ROOT / "ruqqus" / "classes" / "boards.py").read_text(encoding="utf-8")
    name = re.search(r'^PROFILE_BOARD_NAME\s*=\s*"([^"]+)"', boards_py, re.M).group(1)
    seed = (ROOT / "seed-db.sql").read_text(encoding="utf-8")
    assert re.search(rf"INSERT INTO public\.boards.*?'{re.escape(name)}'", seed, re.S), (
        f"seed-db.sql must insert the '{name}' guild")
