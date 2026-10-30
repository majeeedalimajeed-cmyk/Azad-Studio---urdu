"""Upload the two leader footage videos to YouTube as UNLISTED.

Operator will check them on YouTube first; nothing goes public
until they approve. Run once per video:
  python3 upload_youtube.py revanth
  python3 upload_youtube.py Owaisi
"""
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
oauth = json.loads(
    pathlib.Path("/home/user/.config/nr_news/youtube_oauth.json").read_text())
os.environ["NR_YOUTUBE_CLIENT_ID"] = oauth["client_id"]
os.environ["NR_YOUTUBE_CLIENT_SECRET"] = oauth["client_secret"]
os.environ["NR_YOUTUBE_REFRESH_TOKEN"] = oauth["refresh_token"]
# Review uploads default to unlisted; pass "public" as 2nd CLI arg
# only when the operator has approved and said to post.
os.environ["NR_YOUTUBE_PRIVACY"] = sys.argv[2] if len(sys.argv) > 2 else "unlisted"

sys.path.insert(0, str(HERE.parent))
from nr_news.config import CONFIG  # noqa: E402
from nr_news.shorts import ShortUnavailable, YouTubeClient  # noqa: E402

VIDEOS = {
    "revanth": {
        "file": "revanth_global_summit_2026-10-29.mp4",
        "title": "Revanth Reddy: سرمایہ کاروں کو ذاتی ضمانت | Telangana $1 Trillion 2034 | AZAD STUDIO",
        "description": (
            "تلنگانہ کے وزیر اعلیٰ اے۔ Revanth Reddy کا انویسٹ تلنگانہ گلوبل سمٹ 2026 کے "
            "کرٹن ریزر سے خطاب — نئی دہلی، 29 اکتوبر 2026۔\n\n"
            "اصل انگریزی تقریر، اردو ترجمہ سب ٹائٹلز کے ساتھ۔ وزیر اعلیٰ نے سرمایہ کاروں کو "
            "ذاتی ضمانت دی اور تلنگانہ رائزنگ 2047 کا ہدف پیش کیا: 2034 تک ایک ٹریلین ڈالر "
            "اور 2047 تک تین ٹریلین ڈالر کی معیشت۔\n\n"
            "Invest Telangana Global Summit: 7–9 December 2026, Bharat Future City, Hyderabad.\n\n"
            "AZAD STUDIO — اصل آواز، تصدیق شدہ خبر۔"
        ),
        "tags": ["Revanth Reddy", "Telangana", "Invest Telangana Global Summit 2026",
                 "Hyderabad", "Urdu news", "Telangana Rising 2047"],
    },
    "Owaisi": {
        "file": "owaisi_kairana_2026-10-26.mp4",
        "title": "Asaduddin Owaisi: کیرانہ میں تعلیم، کالج اور بجلی کا سوال | Kairana Speech | AZAD STUDIO",
        "description": (
            "اے آئی ایم آئی ایم کے صدر اسد الدین اویسی کا کیرانہ (شاملی، اتر پردیش) کے جلسۂ عام "
            "سے خطاب — 26 اکتوبر 2026۔\n\n"
            "اصل اردو تقریر، اردو سب ٹائٹلز کے ساتھ۔ اویسی نے کیرانہ میں شرح خواندگی، ڈگری کالج، "
            "بچیوں کے کالج، کھیل اور بجلی کے پاور اسٹیشن کے فقدان پر سوال اٹھایا۔ تمام اعداد و شمار "
            "اور دعوے اسد الدین اویسی کے اپنے بیان کے طور پر پیش کیے گئے ہیں۔\n\n"
            "AZAD STUDIO — اصل آواز، تصدیق شدہ خبر۔"
        ),
        "tags": ["Asaduddin Owaisi", "AIMIM", "Kairana", "Uttar Pradesh",
                 "Urdu news", "Owaisi speech"],
    },
}


def main() -> None:
    key = sys.argv[1]
    spec = VIDEOS[key]
    out_path = HERE / f"upload_{key}_result.json"
    try:
        result = YouTubeClient(CONFIG).upload_short(
            HERE / spec["file"], title=spec["title"],
            description=spec["description"], tags=spec["tags"])
        payload = {"status": "success", "video": key,
                   "video_id": result["video_id"],
                   "watch_url": f"https://www.youtube.com/watch?v={result['video_id']}",
                   "privacy": os.environ["NR_YOUTUBE_PRIVACY"]}
    except ShortUnavailable as exc:
        payload = {"status": "failed", "video": key, "reason": str(exc)[:300]}
    except Exception as exc:  # network/API errors: record, don't crash silently
        payload = {"status": "failed", "video": key,
                   "reason": f"{type(exc).__name__}: {str(exc)[:250]}"}
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
