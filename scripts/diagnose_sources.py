"""Manual diagnostic: actual source reading + editorial models, never publish."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
from nr_news.config import CONFIG
from nr_news.db import Database
from nr_news.pipeline import NewsPipeline
from nr_news.agents.research import ResearchAgent
from nr_news.agents.verification import editorial_exclusion_text
from nr_news.util import utcnow,parse_iso

with TemporaryDirectory() as tmp:
    root=Path(tmp)
    cfg=replace(CONFIG,db_path=root/'db.sqlite3',data_dir=root,card_dir=root/'cards',audit_dir=root/'audit',
        media_dir=root/'media',snapshot_dir=root/'snapshots',dry_run=True,auto_post_enabled=False,
        auto_approve_verified=False,video_enabled=False,instagram_enabled=False)
    cfg.ensure_dirs();db=Database(cfg.db_path);p=NewsPipeline(cfg,db)
    results=p.source_monitor.monitor_all('breaking')
    pool=[]
    for r in results:
        print('SOURCE',r.source,'ok=',r.ok,'count=',r.item_count,flush=True)
        for c in r.candidates:
            when=parse_iso(c.published_at)
            if when and -2 < (utcnow()-when).total_seconds()/3600 < 36 and not editorial_exclusion_text(c.original_headline,c.body_excerpt):
                pool.append(c)
    pool.sort(key=ResearchAgent.priority,reverse=True)
    rendered=False
    for c in pool[:14]:
        cid,_=db.insert_candidate(p.research._to_row(c,'test'))
        out=p.process_job('verify_story',{'candidate_id':cid})
        print('ATTEMPT',c.source,'priority=',ResearchAgent.priority(c),'url=',c.url,
            'status=',out.get('status'),'reason=',out.get('reason'),'reasons=',out.get('reasons'),flush=True)
        if out.get('status')=='rendered':
            dest=Path('diagnostic-results');dest.mkdir(exist_ok=True)
            shutil.copy(out['cards'][0],dest/'preview.png')
            for a in cfg.audit_dir.glob('*.json'):shutil.copy(a,dest/'audit.json')
            print('SUCCESS: One real, source-read bilingual card rendered in an isolated temporary database; no publication.',flush=True)
            rendered=True;break
    if not rendered:
        print('No current candidate passed all editorial and rendering checks in this diagnostic sample.',flush=True)
