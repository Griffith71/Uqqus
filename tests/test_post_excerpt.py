"""The first lines of a post's body on its feed card (helpers/post_excerpt.py): plain text only, a whole word at the
end, and a flag that says when the full body holds more than the card shows."""
from ruqqus.helpers.post_excerpt import LIMIT, SHOWS_TEXT, SHOWS_WITH_MEDIA, excerpt


def test_no_body_is_no_snippet():
    assert excerpt(None) == ("", False)
    assert excerpt("") == ("", False)
    assert excerpt("   \n ") == ("", False)


def test_tags_go_and_paragraphs_run_together_with_a_space():
    text, more = excerpt("<p>First <strong>line</strong>.</p><p>Second line.</p><ul><li>one</li><li>two</li></ul>")
    assert text == "First line. Second line. one two"
    assert not more


def test_pictures_code_and_tables_are_left_to_the_full_body():
    text, more = excerpt('<p>Look:</p><img src="/media/x.png"><pre><code>rm -rf /</code></pre>'
                         "<table><tr><td>cell</td></tr></table><p>after</p>")
    assert text == "Look: after"
    assert more        # the card can't show what was left out, so "Show more" is drawn


def test_a_long_body_is_cut_on_a_whole_word():
    words = " ".join(["photograph"] * 200)
    text, more = excerpt(f"<p>{words}</p>")
    assert more and text.endswith("…") and len(text) <= LIMIT + 1
    assert text[:-1].split(" ")[-1] == "photograph"          # never half a word


def test_a_long_unbroken_string_is_still_cut():
    text, more = excerpt("<p>" + "x" * 5000 + "</p>")
    assert more and len(text) == LIMIT + 1


def test_a_body_that_fits_its_lines_has_no_show_more():
    body = "<p>" + " ".join(["word"] * 40) + "</p>"            # 199 characters
    assert excerpt(body, shows=SHOWS_TEXT)[1] is False          # four lines hold it
    assert excerpt(body, shows=SHOWS_WITH_MEDIA)[1] is True     # two lines above a picture don't


def test_right_to_left_text_is_kept_as_it_is():
    text, _ = excerpt("<p>نص تجريبي باللغة العربية</p>")
    assert text == "نص تجريبي باللغة العربية"


def test_markup_in_the_text_comes_out_as_text_to_be_escaped():
    text, _ = excerpt("<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>")
    assert text == "<script>alert(1)</script>"      # plain text: the template escapes it (never | safe)
