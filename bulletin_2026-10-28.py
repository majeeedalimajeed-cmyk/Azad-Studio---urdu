"""AZAD STUDIO premium Urdu political bulletin — 28 October 2026.

Ranked, verified political stories only (government decisions, council
elections, party developments, policy). Every story carries a spoken
source credit; report-level items are labelled as reports, not confirmed
appointments. Facts below are drawn from the named sources on the dates
shown in each story's verification notes — nothing is invented.

Run:  python bulletin_2026-10-28.py
Out:  bulletin_output/2026-10-28/bulletin.mp4 (+ voiceover + script)
"""
from __future__ import annotations

import json
from pathlib import Path

from nr_news.config import CONFIG
from nr_news.models import EditorialContent
from nr_news.shorts import build_bulletin

DATE_URDU = "اٹھائیس اکتوبر دو ہزار چھبیس"

STORIES: list[EditorialContent] = [
    # 1 — National: MLC results across 4 states (India Today + Indian Express, 28 Oct 2026)
    EditorialContent(
        urdu_headline="کونسل انتخابات: چار ریاستوں میں کانگریس کو ایک بھی نشست نہ ملی",
        urdu_bullets=[
            "اتر پردیش، بہار، مہاراشٹر اور کرناٹک کی اٹھائیس نشستوں کے نتائج میں کانگریس کو ایک بھی نشست نہ مل سکی",
            "کرناٹک کی چاروں نشستیں بی جے پی اور جے ڈی ایس اتحاد نے جیت لیں",
            "بنگلورو ٹیچرز نشست پر جے ڈی ایس کے اے پی رنگاناتھ نے کانگریس کے پٹنا کو نو سو آٹھ ووٹوں سے شکست دی",
            "کانگریس رہنما پرینک کھرگے نے کہا کہ پارٹی عوامی فیصلے کو قبول کرتی ہے",
        ],
        roman_headline="Council elections: Congress wins zero in four states",
        roman_bullets=[],
        verification_notes=[
            "India Today, 28 Oct 2026: '28 MLC polls in 4 states: Congress wins zero'",
            "Indian Express live, 28 Oct 2026: Congress lost all 4 Karnataka LC seats to BJP+JD(S)",
            "Balanced reaction included: Priyank Kharge (Congress) accepts the verdict",
        ],
        source_urls=["https://www.indiatoday.in", "https://indianexpress.com"],
    ),
    # 2 — Telangana: all-party meeting on El Niño/drought (The Hans India, 28 Oct 2026)
    EditorialContent(
        urdu_headline="خشک سالی اور ایل نینو کے خدشات: تلنگانہ میں کل جماعتی اجلاس",
        urdu_bullets=[
            "حیدرآباد سکریٹریٹ میں نائب وزیر اعلیٰ بھٹی وکرمارکا کی صدارت میں کل جماعتی اجلاس ہوا",
            "مجلس، بی آر ایس، بی جے پی، سی پی آئی، سی پی ایم اور ٹی جے ایس کے نمائندوں نے شرکت کی",
            "پینے کے پانی، آبپاشی کے لیے بجلی کی فراہمی اور متبادل فصلوں پر تبادلہ خیال ہوا",
        ],
        roman_headline="Telangana all-party meet on drought and El Nino",
        roman_bullets=[],
        verification_notes=["The Hans India, 28 Oct 2026: all-party meeting chaired by Dy CM Bhatti Vikramarka"],
        source_urls=["https://www.thehansindia.com"],
    ),
    # 3 — Hyderabad drinking water: CM's directions (The Hans India / Telugu360, 28 Oct 2026)
    EditorialContent(
        urdu_headline="حیدرآباد میں پانی کی قلت روکنے کے لیے وزیر اعلیٰ کے احکامات",
        urdu_bullets=[
            "وزیر اعلیٰ ریونت ریڈی نے پانی کی قلت روکنے کے لیے افسران کو واضح ہدایات جاری کیں",
            "سیوریج ٹریٹمنٹ پلانٹس سو فیصد صلاحیت پر چلائے جائیں گے اور تعمیراتی کاموں میں صاف شدہ پانی کا استعمال لازمی ہوگا",
            "یکم نومبر سے اقدامات پر عمل درآمد ہوگا اور کمانڈ کنٹرول مرکز سے نگرانی کی جائے گی",
        ],
        roman_headline="CM orders steps to prevent Hyderabad water shortage",
        roman_bullets=[],
        verification_notes=["The Hans India / Telugu360, 28 Oct 2026: CM Revanth Reddy's directions on drinking water"],
        source_urls=["https://www.thehansindia.com"],
    ),
    # 4 — Telangana drought declaration (IndToday, 27–28 Oct 2026)
    EditorialContent(
        urdu_headline="تلنگانہ کے تین سو پینسٹھ منڈل خشک سالی متاثرہ قرار",
        urdu_bullets=[
            "ریاستی حکومت کے مطابق ایک سو گیارہ منڈل شدید اور دو سو چون منڈل معتدل خشک سالی متاثرہ قرار دیے گئے ہیں",
        ],
        roman_headline="Telangana declares 365 mandals drought-affected",
        roman_bullets=[],
        verification_notes=["IndToday, 27-28 Oct 2026: 365 mandals drought-affected (111 severe, 254 moderate)"],
        source_urls=["https://indtoday.com"],
    ),
    # 5 — Andhra Pradesh local-body poll reschedule (IndToday, 28 Oct 2026)
    EditorialContent(
        urdu_headline="آندھرا پردیش میں مقامی انتخابات کے شیڈول میں تبدیلی",
        urdu_bullets=[
            "ریاستی الیکشن کمیشن نے ایم پی ٹی سی اور زیڈ پی ٹی سی انتخابات کی پولنگ کی تاریخ بدل دی ہے",
            "نئے شیڈول کے مطابق پولنگ اب پانچ نومبر کو ہوگی",
        ],
        roman_headline="Andhra Pradesh local body polling moved to November 5",
        roman_bullets=[],
        verification_notes=["IndToday, 28 Oct 2026: AP SEC rescheduled MPTC/ZPTC polling to 5 Nov"],
        source_urls=["https://indtoday.com"],
    ),
    # 6 — BRS party development (IndToday, 28 Oct 2026)
    EditorialContent(
        urdu_headline="بی آر ایس کا گریجویٹ ووٹر اندراج کی مدت بڑھانے کا مطالبہ",
        urdu_bullets=[
            "بی آر ایس نے الیکشن حکام سے گریجویٹ ووٹرز کے اندراج کی آخری تاریخ میں توسیع کا مطالبہ کیا ہے",
        ],
        roman_headline="BRS seeks extension of graduate voter registration",
        roman_bullets=[],
        verification_notes=["IndToday, 28 Oct 2026: BRS sought extension of graduate voter registration deadline"],
        source_urls=["https://indtoday.com"],
    ),
    # 7 — Telangana HC Chief Justice recommendation — REPORT level (IndToday, 28 Oct 2026)
    EditorialContent(
        urdu_headline="تلنگانہ ہائی کورٹ کے چیف جسٹس کے لیے نئے نام کی سفارش کی رپورٹ",
        urdu_bullets=[
            "رپورٹس کے مطابق جسٹس تاپابراتا چکرابورتی کے نام کی سفارش کی گئی ہے",
            "فی الحال یہ سفارش سے متعلق رپورٹ ہے، تصدیق شدہ تقرری نہیں",
        ],
        roman_headline="Report: name recommended for Telangana HC Chief Justice",
        roman_bullets=[],
        verification_notes=[
            "IndToday, 28 Oct 2026: CJ recommendation item (Justice Tapabrata Chakraborty)",
            "Presented on air strictly as a report, not a confirmed appointment",
        ],
        source_urls=["https://indtoday.com"],
    ),
]


def main() -> None:
    out_dir = Path(__file__).parent / "bulletin_output" / "2026-10-28"
    # Relevant freely-licensed story photos (fetch_bulletin_photos.py);
    # any story without a fetched photo keeps the branded fallback.
    photo_dir = out_dir / "photos"
    for i, story in enumerate(STORIES, 1):
        photo = photo_dir / f"story{i}.jpg"
        if photo.exists():
            story.media = [{"local_path": str(photo)}]
    result = build_bulletin(
        STORIES, out_dir, config=CONFIG,
        closing_credit=("آج کے بلیٹن کی خبریں انڈیا ٹوڈے، انڈین ایکسپریس، "
                        "دی ہنس انڈیا اور انڈ ٹوڈے سے لی گئی ہیں، " + DATE_URDU))
    (out_dir / "bulletin_meta.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in
                      ("video_path", "duration_seconds", "voice", "rate", "pitch", "story_count")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
