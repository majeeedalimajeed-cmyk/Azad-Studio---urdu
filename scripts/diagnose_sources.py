"""Isolated real-news article-to-card test; never sends a Facebook post."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
from nr_news.config import CONFIG
from nr_news.db import Database
from nr_news.pipeline import NewsPipeline
from nr_news.models import Candidate

with TemporaryDirectory() as tmp:
    root=Path(tmp)
    cfg=replace(CONFIG,db_path=root/'db.sqlite3',data_dir=root,card_dir=root/'cards',audit_dir=root/'audit',
        media_dir=root/'media',snapshot_dir=root/'snapshots',dry_run=True,auto_post_enabled=False,
        auto_approve_verified=False,video_enabled=False,instagram_enabled=False,llm_max_retries=0)
    cfg.ensure_dirs();db=Database(cfg.db_path);p=NewsPipeline(cfg,db)
    candidate=Candidate(source='india_today',publisher='India Today',
        url='https://www.indiatoday.in/world/story/indian-tourists-rescued-nepal-shey-phoksundo-heavy-snowfall-ptag-3030318-2026-11-02',
        original_headline='Six Indian tourists rescued after snowfall strands them in Nepal',
        published_at='2026-11-02T04:56:00Z',updated_at=None,language='en',
        body_excerpt='Six Indian tourists were rescued after heavy snowfall in Nepal.',category='world')
    cid,_=db.insert_candidate(p.research._to_row(candidate,'test'))
    out=p.process_job('verify_story',{'candidate_id':cid})
    print('CURRENT STORY TEST',out.get('status'),out.get('reason'),out.get('reasons'),out.get('errors'),flush=True)
    if out.get('status')=='rendered':
        dest=Path('diagnostic-results');dest.mkdir(exist_ok=True)
        shutil.copy(out['cards'][0],dest/'preview.png')
        for a in cfg.audit_dir.glob('editorial-*.json'):shutil.copy(a,dest/'audit.json')
        print('SUCCESS: Real current-news card rendered in isolated temporary database; no publication.',flush=True)
