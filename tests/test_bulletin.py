"""Bulletin structure tests: ranked stories, one at a time, intro first."""
from nr_news.models import EditorialContent
from nr_news.shorts import INTRO_SECONDS, bulletin_segments


def _story(headline: str, bullets: list[str]) -> EditorialContent:
    return EditorialContent(
        urdu_headline=headline, urdu_bullets=bullets,
        roman_headline="Roman", roman_bullets=[], verification_notes=[],
        source_urls=[], publication_time=None, media=None)


def test_bulletin_segments_intro_then_stories_in_rank_order():
    stories = [_story("پہلی خبر", ["نکتہ ایک", "نکتہ دو"]),
               _story("دوسری خبر", ["نکتہ تین"])]
    pairs = bulletin_segments(stories)
    kinds = [seg["kind"] for _, seg in pairs]
    assert kinds[0] == "intro"
    assert kinds.count("headline") == 2
    headlines = [seg for _, seg in pairs if seg["kind"] == "headline"]
    assert [s["story_index"] for s in headlines] == [1, 2]
    # Story 1's bullets all play before story 2's headline (one at a time).
    first_headline_2 = kinds.index("headline", 1)
    assert all(k != "headline" for k in kinds[1:first_headline_2])


def test_bulletin_intro_duration_is_broadcast_length():
    assert 3.0 <= INTRO_SECONDS <= 5.0


def test_bulletin_segments_empty():
    assert bulletin_segments([]) == []


def test_source_credit_lines_are_never_narrated():
    from nr_news.shorts import bulletin_segments, narration_segments

    story = _story("خبر", ["اصل نکتہ", "ماخذ: انڈ ٹوڈے، آج"])
    pairs = bulletin_segments([story])
    texts = [seg["text"] for _, seg in pairs]
    assert any("اصل نکتہ" in t for t in texts)
    assert not any(t.strip().startswith("ماخذ") for t in texts)

    segs = narration_segments(story)
    assert not any(s["text"].strip().startswith("ماخذ") for s in segs)
