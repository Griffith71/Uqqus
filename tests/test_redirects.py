"""Where a browser may be sent after signing in (ruqqus/helpers/redirects.py, bug hunt 2026-10 H5): only a path on
this site, never another site, however it is written."""
import pytest

from ruqqus.helpers.redirects import same_site


@pytest.mark.parametrize("target", ["/", "/settings/profile", "/@alice/post/12?sort=new#c3", "/+pets", "/search?q=a%2Fb"])
def test_a_path_on_this_site_is_kept(target):
    assert same_site(target) == target


@pytest.mark.parametrize("target", [
    "https://evil.example/x", "http://evil.example", "//evil.example/x", "///evil.example", "/\evil.example",
    "\\evil.example", "https:evil.example", "javascript:alert(1)", "data:text/html,x", " https://evil.example",
    "/\tx", "/\nx", "/x\r\nSet-Cookie: a=b", "evil.example", "settings", "", None, 5,
])
def test_anything_else_falls_back(target):
    assert same_site(target) == "/"
    assert same_site(target, default="/home") == "/home"


def test_no_route_sends_the_browser_straight_to_a_value_from_the_request():
    """Bug hunt H5: a redirect target from a form or a link goes through same_site() first."""
    import re
    from pathlib import Path
    routes = Path(__file__).resolve().parents[1] / "ruqqus" / "routes"
    raw = re.compile(r"redirect\(\s*request\.(args|form|values|headers|referrer)")
    found = [f"{p.name}:{n}" for p in routes.glob("*.py")
             for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if raw.search(line)]
    assert found == []
