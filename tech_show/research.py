"""Discover unverified tech-product leads from official feeds. Never publishes.

A lead is not an approved product: launch status, specific model, specifications,
price, authenticity and media rights must all be checked independently before use.
"""
from __future__ import annotations
import argparse
import datetime as dt
import email.utils
import html
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

IST = ZoneInfo('Asia/Kolkata')
FEEDS = (
    ('Apple India','https://www.apple.com/in/newsroom/rss-feed.rss',{'www.apple.com','apple.com'}),
    ('Samsung India','https://news.samsung.com/in/feed',{'news.samsung.com'}),
    ('Google Pixel','https://blog.google/products/pixel/rss',{'blog.google'}),
)
HARDWARE = re.compile(r'\b(iphone|ipad|macbook|mac mini|apple watch|airpods|galaxy|pixel|smartphone|phone|tablet|laptop|notebook|earbuds?|headphones?|smartwatch|gaming handheld|foldable|wearables?)\b', re.I)
LAUNCH = re.compile(r'\b(unveil\w*|introduc\w*|announc\w*|launch\w*|debut\w*|new|reveal\w*)\b', re.I)
SOFTWARE_ONLY = re.compile(r'\b(software update|pixel drop|security update|developer beta|how to|tips and tricks)\b',re.I)
MAX_FEED = 5 * 1024 * 1024

def clean(value: str | None) -> str:
    value = html.unescape(re.sub(r'<[^>]*>',' ',value or ''))
    return re.sub(r'\s+',' ',value).strip()

def date_parse(s: str | None) -> dt.datetime | None:
    try:
        x=dt.datetime.fromisoformat((s or '').replace('Z','+00:00'))
    except ValueError:
        try:x=email.utils.parsedate_to_datetime(s or '')
        except (TypeError,ValueError,OverflowError):return None
    if x.tzinfo is None:return None
    return x.astimezone(dt.timezone.utc)

def parse_feed(data: bytes, source: str, hosts: set[str], as_of: dt.datetime, lookback_days: int = 60) -> list[dict]:
    if len(data)>MAX_FEED:raise ValueError('feed too large')
    if as_of.tzinfo is None:raise ValueError('as_of must include a timezone')
    root=ET.fromstring(data); items=[];atom='{http://www.w3.org/2005/Atom}'
    entries=root.findall('.//'+atom+'entry')
    if entries:
        rows=[]
        for e in entries:
            l=e.find(atom+'link'); link=l.get('href','') if l is not None else ''
            rows.append((e.findtext(atom+'title'),link,e.findtext(atom+'published') or e.findtext(atom+'updated'),e.findtext(atom+'summary')))
    else:
        rows=[(e.findtext('title'),e.findtext('link'),e.findtext('pubDate'),e.findtext('description')) for e in root.findall('.//item')]
    for title,url,date,summary in rows:
        title=clean(title);url=(url or '').strip();when=date_parse(date)
        parsed=urllib.parse.urlsplit(url)
        if parsed.scheme!='https' or (parsed.hostname or '').lower() not in hosts or parsed.username or parsed.password:continue
        if not when or when>as_of.astimezone(dt.timezone.utc):continue
        age=as_of.astimezone(dt.timezone.utc)-when
        if age>dt.timedelta(days=lookback_days):continue
        if not HARDWARE.search(title) or not LAUNCH.search(title) or SOFTWARE_ONLY.search(title):continue
        score=(20 if 'India' in source else 0)+(10 if 'india' in title.casefold() else 0)+max(0,30-age.days)
        items.append({'title':title,'url':url,'published_utc':when.isoformat(),'source':source,'summary':clean(summary)[:500],'score':score,'status':'unverified_lead'})
    return items

def collect(as_of: dt.datetime) -> tuple[list[dict],list[dict]]:
    results=[]; checks=[]
    s=requests.Session();s.headers.update({'User-Agent':'AZADStudioTechResearch/1.0 (editorial discovery)'})
    for source,url,hosts in FEEDS:
        try:
            r=s.get(url,timeout=25,allow_redirects=True);r.raise_for_status()
            host=(urllib.parse.urlsplit(r.url).hostname or '').lower()
            if host not in hosts: raise ValueError('unexpected redirect host')
            found=parse_feed(r.content,source,hosts,as_of)
            results.extend(found);checks.append({'source':source,'ok':True,'count':len(found)})
        except (requests.RequestException,ValueError,ET.ParseError) as exc:
            checks.append({'source':source,'ok':False,'reason':type(exc).__name__})
    dedup={i['url']:i for i in results};ranked=sorted(dedup.values(),key=lambda x:(x['score'],x['published_utc']),reverse=True)
    return ranked,checks

def main() -> None:
    p=argparse.ArgumentParser();p.add_argument('--as-of',help='ISO-8601 date/time with timezone; default current IST');p.add_argument('--out',default='tech-candidates.json');args=p.parse_args()
    now=date_parse(args.as_of) if args.as_of else dt.datetime.now(IST)
    if not now:raise SystemExit('invalid --as-of')
    candidates,checks=collect(now);out={'as_of':now.astimezone(IST).isoformat(),'status':'research_only_no_publication','checks':checks,'candidates':candidates[:20]}
    Path(args.out).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':out['status'],'candidates':len(out['candidates']),'feeds':checks},ensure_ascii=False))
if __name__=='__main__':main()
