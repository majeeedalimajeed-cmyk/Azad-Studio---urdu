"""Manual-dispatch smoke test. Synthetic story, temp DB, no live publication."""
from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import replace
from nr_news.config import CONFIG
from nr_news.db import Database
from nr_news.pipeline import NewsPipeline
from nr_news.models import Candidate, VerificationResult
from nr_news.util import iso

with TemporaryDirectory() as tmp:
    p=Path(tmp)
    conf=replace(CONFIG,db_path=p/'db.sqlite3',data_dir=p,card_dir=p/'cards',audit_dir=p/'audit',
                 media_dir=p/'media',snapshot_dir=p/'snapshots',dry_run=True,
                 auto_approve_verified=False,auto_post_enabled=False)
    conf.ensure_dirs(); db=Database(conf.db_path); desk=NewsPipeline(conf,db)
    candidate=Candidate(source='the_hindu',publisher='The Hindu',url='https://www.thehindu.com/news/synthetic-smoke-test',
        original_headline='Telangana cabinet approves synthetic school improvement plan',
        published_at=iso(),updated_at=None,language='en',category='politics',
        body_excerpt='Synthetic test only: the Telangana cabinet approved a school improvement plan in Hyderabad. The plan covers classroom maintenance and teacher training.')
    cid,_=db.insert_candidate(desk.research._to_row(candidate,'hourly'))
    paragraph=('Synthetic test only. The Telangana cabinet approved a school improvement plan during a meeting in Hyderabad. '
        'The plan concerns maintenance of school classrooms and training for teachers. '
        'Officials said the education department would coordinate implementation with district administrations. '
        'The cabinet asked the education department to prepare detailed implementation guidelines. '
        'The decision was announced after the cabinet meeting in Hyderabad. '
        'The education department will work with districts on the implementation plan. ')
    desk._read_original_article=lambda row: paragraph*2
    original_call=desk.research.llm.chat_json
    def debug_call(**kwargs):
        output=original_call(**kwargs)
        print('SYNTHETIC MODEL KEYS:', sorted(output.keys()), 'facts type:', type(output.get('facts')).__name__)
        return output
    desk.research.llm.chat_json=debug_call
    verification=VerificationResult(True,[],['Synthetic test source supplied for local probe.'],'high')
    result=desk.generate_from_verified(cid,verification)
    print('SYNTHETIC TEST RESULT:',result.get('status'),result.get('reason'),result.get('errors'))
    if result.get('status') != 'rendered' or not result.get('cards'):
        raise SystemExit('Smoke test did not generate a fully reviewed card')
    print('Synthetic card generated and independently checked; nothing posted.')
