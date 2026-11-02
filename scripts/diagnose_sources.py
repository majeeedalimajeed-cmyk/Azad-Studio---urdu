"""Manual diagnostics: identify source-access and article-extraction failures."""
from bs4 import BeautifulSoup
from nr_news.pipeline import NewsPipeline
p=NewsPipeline()
for r in p.source_monitor.monitor_all('breaking'):
    print('SOURCE',r.source,'ok=',r.ok,'count=',r.item_count,flush=True)
    for c in r.candidates[:2]:
        fetched=p.evidence.client.get(c.url)
        soup=BeautifulSoup(fetched.text,'html.parser') if fetched.ok else None
        tags={}
        if soup:
            for name,selector in [('article','article'),('main','main'),('p','p'),('storybody','[class*="storyBody"], [class*="story-body"], [class*="story_content"]'),('jsonld','script[type="application/ld+json"]')]:
                tags[name]=len(soup.select(selector))
        parsed=p._read_original_article({'url':c.url})
        print('  EXAMPLE',c.url,'fetch=',fetched.ok,'status=',fetched.status_code,'message=',fetched.message,
              'html_chars=',len(fetched.text),'tags=',tags,'extracted_chars=',len(parsed),flush=True)
