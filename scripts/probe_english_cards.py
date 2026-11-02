"""Manual-dispatch smoke test. Verified example article, temp DB, no live publication."""
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
    candidate=Candidate(source='indian_express',publisher='The Indian Express',url='https://indianexpress.com/article/education/aicte-launches-four-schemes-2026-27-idea-lab-modrobs-margdarshan-details-10945006/',
        original_headline='AICTE launches four major schemes for technical education',
        published_at=iso(),updated_at=None,language='en',category='politics',
        body_excerpt='AICTE launched four schemes for 2026–27: IDEA Lab, MODROBS, Margdarshan and Grant for Organising Conference, covering practical education, infrastructure, mentoring and conferences.')
    cid,_=db.insert_candidate(desk.research._to_row(candidate,'hourly'))
    paragraph=("AICTE launches four major schemes for technical education. The All India Council for Technical Education launched four initiatives for the academic year 2026–27. "
        "The four initiatives are IDEA Lab, MODROBS, Margdarshan and Grant for Organising Conference. "
        "IDEA Lab focuses on hands-on learning and the practical application of science, technology, engineering and mathematics. "
        "MODROBS supports the modernisation of laboratories, workshops and computing facilities in technical institutions. "
        "Margdarshan focuses on academic mentoring, with guidance from established institutions. "
        "Grant for Organising Conference supports national and international academic conferences. "
        "AICTE said the initiatives are intended to widen participation by institutions across the country. "
        "Institutions should consult each scheme's guidelines for the relevant eligibility and assistance details.")
    desk._read_original_article=lambda row: paragraph*2
    original_call=desk.research.llm.chat_json
    def debug_call(**kwargs):
        output=original_call(**kwargs)
        print('SYNTHETIC MODEL KEYS:', sorted(output.keys()), 'facts type:', type(output.get('facts')).__name__)
        return output
    desk.research.llm.chat_json=debug_call
    verification=VerificationResult(True,[],['Verified example based on Indian Express article, provided for isolated smoke test.'],'high')
    result=desk.generate_from_verified(cid,verification)
    print('SYNTHETIC TEST RESULT:',result.get('status'),result.get('reason'),result.get('errors'))
    if result.get('status') != 'rendered' or not result.get('cards'):
        raise SystemExit('Smoke test did not generate a fully reviewed card')
    print('Synthetic card generated and independently checked; nothing posted.')
