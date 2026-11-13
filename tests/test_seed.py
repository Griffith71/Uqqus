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


def test_a_dev_seed_checks_the_database_the_app_will_really_write_to():
    """The app connects to DATABASE_CONNECTION_POOL_URL first (ruqqus/__main__.py), and docker-compose.yml sets it: a seed that checked
    only DATABASE_URL passed with an override naming ui_preview there and then wrote its accounts into the real dev database."""
    main = (ROOT / "ruqqus" / "__main__.py").read_text(encoding="utf-8")
    assert 'environ.get(\n    "DATABASE_CONNECTION_POOL_URL",\n    environ.get("DATABASE_URL"))' in main
    seeds = [ROOT / "scripts" / "seed_ui_fixtures.py"] + sorted((ROOT / "scripts" / "ui").glob("seed_*.py"))
    for path in seeds:
        text = path.read_text(encoding="utf-8")
        if "DATABASE_URL" not in text:
            continue
        assert 'os.environ.get("DATABASE_CONNECTION_POOL_URL", os.environ.get("DATABASE_URL", ""))' in text, path.name
        assert text.count('os.environ.get("DATABASE_URL"') == text.count('os.environ.get("DATABASE_CONNECTION_POOL_URL"'), path.name
