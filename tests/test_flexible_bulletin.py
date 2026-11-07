import json
from dataclasses import replace
from nr_news.config import CONFIG
from nr_news.db import Database
from nr_news.pipeline import NewsPipeline
from nr_news.models import Candidate
from nr_news.curated_photos import select_curated_photo
from nr_news.util import iso


def setup_pipeline(tmp_path):
    root=tmp_path/'data'
    c=replace(CONFIG, db_path=root/'db.sqlite',data_dir=root,card_dir=root/'cards',audit_dir=root/'audit',media_dir=root/'media',snapshot_dir=root/'snapshots',dry_run=True,flexible_bulletins_enabled=True)
    c.ensure_dirs();db=Database(c.db_path);return NewsPipeline(c,db)


def stage(p, n, title, bullets, roman, photo=False, source='india_today'):
    c=Candidate(source=source,publisher='India Today',url=f'https://www.indiatoday.in/news/story/{n}',
        original_headline=title,published_at=iso(),updated_at=None,language='en',
        body_excerpt=title+' The report contains additional verified details.',category='politics',raw={})
    cid,_=p.db.insert_candidate(p.research._to_row(c,'hourly'))
    media=select_curated_photo(title,title) if photo else []
    content={'urdu_headline':title,'urdu_bullets':bullets,'roman_headline':'Khabar '+str(n),
             'roman_bullets':roman,'verification_notes':[],'source_urls':[c.url],
             'publication_time':iso(),'media':media}
    eid,_=p.db.insert_editorial(cid,None,content,status='bulletin_ready')
    p.db.set_editorial_status(eid,'bulletin_ready','auto_policy')
    p.db.set_setting('english_roman_verified:'+eid,'true')
    return cid,eid


def test_combine_three_verified_headlines_with_one_photo(tmp_path):
    p=setup_pipeline(tmp_path)
    stage(p,1,'Paraguay president faces impeachment', ['Paraguay lawmakers seek impeachment.'], ['Paraguay mein impeachment ki darkhwast.'],True)
    stage(p,2,'Telangana announces a new school policy', ['Telangana announced an education policy.'], ['Telangana ne taleemi policy ka elan kiya.'])
    stage(p,3,'Rajasthan announces a rail service', ['Rajasthan announced a new rail service.'], ['Rajasthan ne nai rail service ka elan kiya.'])
    p.publisher.publish=lambda *args,**kwargs: {'status':'dry_run'}
    result=p.assemble_flexible_bulletin()
    assert result['status']=='assembled' and result['stories']==3 and result['bullets']==3
    editorial=p.db.get_editorial(result['editorial_id']);members=json.loads(p.db.get_setting('bulletin_members:'+result['editorial_id']))
    assert len(members)==3 and len(json.loads(editorial['urdu_bullets_json']))==3
    assert len(json.loads(editorial['media_json']))==1
    assert 'Photo relates to:' in json.loads(editorial['media_json'])[0]['caption_disclosure']


def test_full_context_is_not_dropped_and_image_may_be_from_one_story(tmp_path):
    p=setup_pipeline(tmp_path)
    stage(p,1,'Paraguay president faces impeachment',['Paraguay lawmakers moved a motion.','A vote is expected tomorrow.','The president responded.'],['Motion pesh hua.','Kal voting mutawaqqa hai.','Sadar ne jawab diya.'],True)
    stage(p,2,'Telangana announces a rail policy',['Telangana announced a rail policy.'],['Telangana ne rail policy ka elan kiya.'])
    p.publisher.publish=lambda *args,**kwargs: {'status':'dry_run'}
    result=p.assemble_flexible_bulletin()
    assert result['stories']==2 and result['bullets']==4
    combined=p.db.get_editorial(result['editorial_id'])
    assert 'The president responded.' in json.loads(combined['urdu_bullets_json'])


def test_no_photo_and_insufficient_single_headline_do_not_publish(tmp_path):
    p=setup_pipeline(tmp_path)
    stage(p,1,'Telangana announces a rail policy',['Telangana announced a rail policy.'],['Telangana ne rail policy ka elan kiya.'])
    p.publisher.publish=lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError('must not publish'))
    assert p.assemble_flexible_bulletin()['reason']=='no_relevant_reviewed_photo'
    stage(p,2,'Paraguay president faces impeachment',['Paraguay lawmakers moved a motion.'],['Motion pesh hua.'],True)
    assert p.assemble_flexible_bulletin()['reason']=='awaiting_more_verified_headlines'


def test_two_bullet_story_can_publish_alone(tmp_path):
    p=setup_pipeline(tmp_path)
    stage(p,1,'Paraguay president faces impeachment',['Paraguay lawmakers moved a motion.','A vote is expected tomorrow.'],['Motion pesh hua.','Kal voting mutawaqqa hai.'],True)
    p.publisher.publish=lambda *args,**kwargs: {'status':'dry_run'}
    out=p.assemble_flexible_bulletin()
    assert out['stories']==1 and out['bullets']==2


def make_multi(p):
    stage(p,1,'Paraguay president faces impeachment',['Paraguay lawmakers moved a motion.'],['Paraguay mein motion pesh hua.'],True)
    stage(p,2,'Telangana announces a rail policy',['Telangana announced a rail policy.'],['Telangana ne rail policy ka elan kiya.'])
    stage(p,3,'Rajasthan announces new schools',['Rajasthan announced new schools.'],['Rajasthan ne naye schoolon ka elan kiya.'])
    publisher=p.publisher
    p.publisher=type('Capture',(),{'publish':lambda *args,**kwargs: {'status':'dry_run'}})()
    result=p.assemble_flexible_bulletin()
    p.publisher=publisher
    return result


class Graph:
    def __init__(self): self.calls=[]
    def confirmed_page_id(self):return 'page'
    def publish_photo(self,*args):self.calls.append('facebook');return {'id':'photo','post_id':'page_post'}
    def instagram_account_id(self,*args):return 'ig'
    def post_image_url(self,*args):return 'https://cdn.example/card.png'
    def ig_create_container(self,*args):self.calls.append('ig_create');return 'container'
    def ig_container_status(self,*args):return 'FINISHED'
    def ig_publish_container(self,*args):self.calls.append('ig_publish');return 'media'
    def ig_permalink(self,*args):return 'https://www.instagram.com/p/test/'

class Ledger:
    def __init__(self):self.claims=[];self.finished=[]
    def reserve_many(self, **kw):self.claims.append(kw)
    def reserve(self, **kw):raise AssertionError('must reserve every story atomically')
    def finish(self, key, **kw):self.finished.append((key,kw))


def live_publisher(p, result):
    p.publisher.config=replace(p.config,dry_run=False,publish_enabled=True,publish_safety_switch=True,
        operator_approved=True,verify_approved=True,auto_post_enabled=True,instagram_enabled=True,facebook_page_token='test-token',expected_page_name='AZAD STUDIO')
    p.publisher.graph=Graph();p.publisher.ledger=Ledger()
    card=p.db.one('SELECT * FROM cards WHERE editorial_id=? ORDER BY created_at DESC LIMIT 1',(result['editorial_id'],))
    return card


def test_real_publisher_reserves_every_story_before_facebook_and_crossposts(tmp_path):
    p=setup_pipeline(tmp_path);out=make_multi(p);card=live_publisher(p,out)
    p.publisher.graph.publish_photo=lambda *args: (p.publisher.ledger.claims and p.publisher.graph.calls.append('facebook') or {'id':'photo','post_id':'page_post'})
    result=p.publisher.publish(out['editorial_id'],card,dry_run=False)
    assert result['status']=='success' and result['instagram']['status']=='success'
    assert len(p.publisher.ledger.claims)==1 and len(p.publisher.ledger.claims[0]['candidates'])==3
    assert p.publisher.graph.calls==['facebook','ig_create','ig_publish']
    for member in json.loads(p.db.get_setting('bulletin_members:'+out['editorial_id'])):
        assert p.db.get_editorial(member['editorial_id'])['status']=='published'


def test_malformed_members_and_missing_verification_fail_closed(tmp_path):
    p=setup_pipeline(tmp_path);out=make_multi(p);card=live_publisher(p,out);key='bulletin_members:'+out['editorial_id']
    members=p.db.get_setting(key)
    for bad in ['{bad', '', 'null', '[]']:
        p.db.set_setting(key,bad)
        assert p.publisher.publish(out['editorial_id'],card,dry_run=False)['reason']=='invalid_bulletin_members'
    p.db.set_setting(key,members)
    first=json.loads(members)[0]['editorial_id'];p.db.set_setting('english_roman_verified:'+first,'false')
    assert p.publisher.publish(out['editorial_id'],card,dry_run=False)['reason']=='bulletin_story_not_verified'
    assert not p.publisher.ledger.claims and not p.publisher.graph.calls


def test_context_mismatch_fails_before_reservation(tmp_path):
    p=setup_pipeline(tmp_path);out=make_multi(p);card=live_publisher(p,out)
    members=json.loads(p.db.get_setting('bulletin_members:'+out['editorial_id']))
    p.db.execute('UPDATE editorials SET urdu_bullets_json=? WHERE id=?',(json.dumps(['Changed claim']),members[1]['editorial_id']))
    assert p.publisher.publish(out['editorial_id'],card,dry_run=False)['reason']=='bulletin_content_mismatch'
    assert not p.publisher.ledger.claims


def test_five_short_stories_fit_one_card(tmp_path):
    p=setup_pipeline(tmp_path)
    stage(p,1,'Paraguay president faces impeachment',['Paraguay lawmakers moved a motion.'],['Paraguay mein motion pesh hua.'],True)
    examples=[('Telangana opens a hospital','Telangana opened a hospital.','Telangana mein hospital khula.'),
              ('Rajasthan starts train service','Rajasthan started a train service.','Rajasthan mein train shuru hui.'),
              ('Odisha reports cyclone warning','Odisha issued a cyclone warning.','Odisha mein tufan ki warning jari hui.'),
              ('Karnataka holds assembly vote','Karnataka held an assembly vote.','Karnataka mein assembly vote hua.')]
    for n,(title,en,ru) in enumerate(examples,2):
        stage(p,n,title,[en],[ru])
    p.publisher.publish=lambda *args,**kwargs: {'status':'dry_run'}
    out=p.assemble_flexible_bulletin()
    assert out['status']=='assembled' and out['bullets']==5 and out['stories']==5
    assert len(out['cards'])==1


def test_renderer_accepts_one_to_five_corresponding_bullets(tmp_path):
    from nr_news.models import EditorialContent
    p=setup_pipeline(tmp_path)
    photo=select_curated_photo('Paraguay president faces impeachment','Paraguay president faces impeachment')
    for count in range(1,6):
        content=EditorialContent('Latest developments',['English news '+str(i)+'.' for i in range(count)],
            'Aham khabrein',['Roman khabar '+str(i)+'.' for i in range(count)],[],[],iso(),photo)
        cards=p.cards.renderer.render_cards(content,'number-'+str(count),media=photo)
        assert len(cards)==1 and cards[0].visual_status=='passed'
        assert cards[0].audit['media_count']==1


def test_interrupted_batch_creation_is_never_approved_before_members_are_saved(tmp_path):
    import pytest
    p=setup_pipeline(tmp_path)
    stage(p,1,'Paraguay president faces impeachment',['Paraguay lawmakers moved a motion.'],['Paraguay mein motion pesh hua.'],True)
    stage(p,2,'Telangana opens a hospital',['Telangana opened a hospital.'],['Telangana mein hospital khula.'])
    stage(p,3,'Odisha issues cyclone warning',['Odisha issued a cyclone warning.'],['Odisha mein tufan ki warning jari hui.'])
    original=p.db.set_setting
    def fail_during_members(key,value):
        if key.startswith('bulletin_members:'):raise RuntimeError('simulated crash')
        return original(key,value)
    p.db.set_setting=fail_during_members
    with pytest.raises(RuntimeError):p.assemble_flexible_bulletin()
    batch=p.db.query("SELECT * FROM editorials WHERE status='bulletin_building'")
    assert len(batch)==1 and not p.db.query("SELECT * FROM editorials WHERE status='approved'")
    p.db.set_setting=original
    p.publisher.publish=lambda *args,**kwargs: {'status':'dry_run'}
    result=p.assemble_flexible_bulletin()
    assert result['status']=='assembled' and result['stories']==3


def test_stale_member_fails_closed_at_publisher(tmp_path):
    p=setup_pipeline(tmp_path);out=make_multi(p);card=live_publisher(p,out)
    members=json.loads(p.db.get_setting('bulletin_members:'+out['editorial_id']))
    p.db.execute('UPDATE candidates SET published_at=? WHERE id=?',('2020-01-01T00:00:00+00:00',members[1]['candidate_id']))
    assert p.publisher.publish(out['editorial_id'],card,dry_run=False)['reason']=='bulletin_story_stale'
    assert not p.publisher.graph.calls and not p.publisher.ledger.claims


def test_text_only_bulletin_publishes_and_crossposts_when_user_allows(tmp_path):
    p=setup_pipeline(tmp_path);p.config=replace(p.config,allow_text_only=True)
    stage(p,1,'Telangana opens a hospital',['Telangana opened a hospital.'],['Telangana mein hospital khula.'])
    stage(p,2,'Odisha issues cyclone warning',['Odisha issued a cyclone warning.'],['Odisha mein tufan ki warning jari hui.'])
    stage(p,3,'Rajasthan announces rail service',['Rajasthan announced a rail service.'],['Rajasthan mein rail service ka elan hua.'])
    original=p.publisher;p.publisher=type('Capture',(),{'publish':lambda *args,**kwargs:{'status':'dry_run'}})()
    out=p.assemble_flexible_bulletin();p.publisher=original
    assert out['status']=='assembled' and out['bullets']==3
    card=live_publisher(p,out)
    assert p.db.get_setting('card_explicit_text_only:'+card['sha256'])=='true'
    result=p.publisher.publish(out['editorial_id'],card,dry_run=False)
    assert result['status']=='success' and result['instagram']['status']=='success'
    assert len(p.publisher.ledger.claims[0]['candidates'])==3


def test_text_only_stays_blocked_without_opt_in_and_forged_photo_is_rejected(tmp_path):
    p=setup_pipeline(tmp_path);p.config=replace(p.config,allow_text_only=True)
    stage(p,1,'Telangana opens a hospital',['Telangana opened a hospital.'],['Telangana mein hospital khula.'])
    p.publisher.publish=lambda *args,**kwargs:{'status':'dry_run'}
    out=p.assemble_flexible_bulletin();assert out['status']=='assembled'
    from nr_news.agents.publisher import PublishingAgent
    p.publisher=PublishingAgent(p.db,config=replace(p.config,allow_text_only=False))
    card=live_publisher(p,out)
    p.publisher.config=replace(p.publisher.config,allow_text_only=False)
    assert p.publisher.publish(out['editorial_id'],card,dry_run=False)['reason']=='reviewed_photo_required'
    p.publisher.config=replace(p.publisher.config,allow_text_only=True)
    p.db.execute('UPDATE editorials SET media_json=? WHERE id=?',(json.dumps([{'url':'not-reviewed'}]),out['editorial_id']))
    assert p.publisher.publish(out['editorial_id'],card,dry_run=False)['reason']=='reviewed_photo_required'
    assert not p.publisher.graph.calls


def test_known_bad_queued_draft_is_quarantined(tmp_path):
    p=setup_pipeline(tmp_path);p.config=replace(p.config,allow_text_only=True)
    cid,eid=stage(p,1,'Supreme Court issues contempt notices',
        ['The post targeted the Chief Justice before being deleted on October 12.'],['Post pehle October 12 ko delete hui.'])
    stage(p,2,'Telangana opens a hospital',['Telangana opened a hospital.'],['Telangana mein hospital khula.'])
    p.publisher.publish=lambda *args,**kwargs:{'status':'dry_run'}
    out=p.assemble_flexible_bulletin()
    assert out['status']=='assembled'
    assert p.db.get_editorial(eid)['status']=='pending_review'
    combined=p.db.get_editorial(out['editorial_id'])
    assert 'deleted on October 12' not in combined['urdu_bullets_json']


def test_photo_still_preferred_when_text_only_allowed(tmp_path):
    p=setup_pipeline(tmp_path);p.config=replace(p.config,allow_text_only=True)
    stage(p,1,'Paraguay president faces impeachment',['Paraguay lawmakers moved a motion.'],['Paraguay mein motion pesh hua.'],True)
    stage(p,2,'Telangana opens a hospital',['Telangana opened a hospital.'],['Telangana mein hospital khula.'])
    p.publisher.publish=lambda *args,**kwargs:{'status':'dry_run'}
    out=p.assemble_flexible_bulletin();ed=p.db.get_editorial(out['editorial_id'])
    assert json.loads(ed['media_json']) and out['status']=='assembled'
