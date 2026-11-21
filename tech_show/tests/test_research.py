import datetime as dt
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research import parse_feed,date_parse

NOW=dt.datetime(2026,10,29,12,tzinfo=dt.timezone.utc)
RSS=b'''<rss><channel>
<item><title>Acme unveils new Galaxy phone</title><link>https://news.samsung.com/in/galaxy</link><pubDate>Wed, 28 Oct 2026 08:00:00 GMT</pubDate><description>launch</description></item>
<item><title>Acme announces new Galaxy phone</title><link>https://news.samsung.com/in/future</link><pubDate>Sun, 29 Nov 2026 08:00:00 GMT</pubDate></item>
<item><title>Acme unveils new Pixel phone</title><link>https://evil.example/in/phone</link><pubDate>Wed, 28 Oct 2026 08:00:00 GMT</pubDate></item>
<item><title>Galaxy software update launches</title><link>https://news.samsung.com/in/software</link><pubDate>Wed, 28 Oct 2026 08:00:00 GMT</pubDate></item>
</channel></rss>'''
ATOM=b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Apple introduces new iPhone</title><link href="https://www.apple.com/in/newsroom/2026/10/iphone/"/><updated>2026-10-28T10:00:00Z</updated><summary>Example.</summary></entry></feed>'''
class ResearchTests(unittest.TestCase):
 def test_rss_only_recent_official_hardware(self):
  x=parse_feed(RSS,'Samsung India',{'news.samsung.com'},NOW);self.assertEqual(len(x),1);self.assertEqual(x[0]['status'],'unverified_lead');self.assertIn('galaxy',x[0]['url'])
 def test_atom(self):
  x=parse_feed(ATOM,'Apple India',{'www.apple.com'},NOW);self.assertEqual(len(x),1);self.assertEqual(x[0]['source'],'Apple India')
 def test_requires_aware_time(self):
  with self.assertRaises(ValueError):parse_feed(RSS,'test',{'news.samsung.com'},dt.datetime(2026,10,29))
 def test_bad_dates(self):
  self.assertIsNone(date_parse('nonsense'));self.assertIsNone(date_parse('2026-10-20'))
 def test_no_userinfo(self):
  x=ATOM.replace(b'https://www.apple.com/',b'https://someone@www.apple.com/');self.assertEqual(parse_feed(x,'Apple',{'www.apple.com'},NOW),[])
if __name__=='__main__':unittest.main()
