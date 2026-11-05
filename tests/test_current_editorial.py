from unittest.mock import Mock
from types import SimpleNamespace
from dataclasses import replace
from pathlib import Path
import pytest
from nr_news.agents.media import MediaAgent
from nr_news.agents.publisher import PublishingAgent
from nr_news.agents.verification import editorial_exclusion_text
from nr_news.config import CONFIG
from nr_news.db import Database
from nr_news.models import Candidate, EditorialContent, VerificationResult
from nr_news.pipeline import NewsPipeline
from nr_news.renderer import CardRenderer, LayoutError
from nr_news.util import iso


def cfg(tmp_path):
    root=tmp_path/'data'
    out=replace(CONFIG,db_path=root/'db.sqlite',data_dir=root,card_dir=root/'cards',
                audit_dir=root/'audit',media_dir=root/'media',snapshot_dir=root/'snapshots',dry_run=True)
    out.ensure_dirs()
    return out


def test_rejects_old_urdu_and_preserves_gold_layout(tmp_path):
    c=EditorialContent(urdu_headline='اردو سرخی', urdu_bullets=['ایک','دو','تین'],
        roman_headline='Roman headline',roman_bullets=['Ek','Do','Teen'],verification_notes=[],source_urls=[],media=None)
    with pytest.raises(LayoutError): CardRenderer(cfg(tmp_path)).render_cards(c,'old')
    c.urdu_headline='Government announces education plan'
    c.urdu_bullets=['A plan was announced.','The plan concerns schools.','Further details are awaited.']
    cards=CardRenderer(cfg(tmp_path)).render_cards(c,'new')
    assert cards[0].visual_status=='passed' and cards[0].audit['byline']==''
    assert cards[0].audit['heading_box'][3]-cards[0].audit['heading_box'][1]==60


def test_unreviewed_stock_search_never_selected(tmp_path):
    db=Mock();stock=Mock();stock.available=True
    agent=MediaAgent(db=db,pixabay_client=stock,config=cfg(tmp_path))
    assert agent.select_many({'id':'test'})==[]
    stock.search.assert_not_called()


def test_legacy_cards_cannot_be_posted(tmp_path):
    db=Mock();db.get_editorial.return_value={'status':'approved','approved_by':'auto_policy'}
    db.get_setting.return_value=None;graph=Mock()
    p=PublishingAgent(db=db,graph=graph,config=cfg(tmp_path))
    out=p.publish('e',{'visual_status':'passed','sha256':'old'},dry_run=False)
    assert out['reason']=='new_format_verification_required'
    graph.publish_photo.assert_not_called()


def test_allows_protests_and_vote_theft_to_reach_verification():
    assert not editorial_exclusion_text('India bloc protest in Delhi')
    assert not editorial_exclusion_text('Party demands release of protesters')
    assert not editorial_exclusion_text('Vote theft claims')
    assert not editorial_exclusion_text('Telangana cabinet approves new water project')
    assert not editorial_exclusion_text('Mentoring by established institutions')
    assert not editorial_exclusion_text('Stabbing reported')


def test_original_article_unavailable_holds_even_verified_story(tmp_path):
    conf=cfg(tmp_path); db=Database(conf.db_path);p=NewsPipeline(conf,db)
    c=Candidate(source='the_hindu',publisher='The Hindu',url='https://www.thehindu.com/news/policy',
        original_headline='Telangana cabinet approves education policy', published_at=iso(),updated_at=None,
        language='en',body_excerpt='The Telangana cabinet announced an education policy in Hyderabad. '*3,category='politics')
    cid,_=db.insert_candidate(p.research._to_row(c,'hourly'))
    p._read_original_article=Mock(return_value='')
    p.research.summarize_urdu=Mock()
    res=p.generate_from_verified(cid,VerificationResult(True,[],[], 'high'))
    assert res['reason']=='original_article_unreadable_or_mismatched'
    p.research.summarize_urdu.assert_not_called()


def test_denied_source_is_not_processed(tmp_path):
    conf=cfg(tmp_path);db=Database(conf.db_path);p=NewsPipeline(conf,db)
    c=Candidate(source='unknown',publisher='Unknown',url='https://example.com/story',
        original_headline='Telangana cabinet approves education policy',published_at=iso(),updated_at=None,
        language='en',body_excerpt='The Telangana cabinet announced an education policy in Hyderabad.',category='politics')
    cid,_=db.insert_candidate(p.research._to_row(c,'hourly'))
    assert p.process_job('verify_story',{'candidate_id':cid})['reason']=='source_not_in_operator_list'


def test_story_without_reviewed_photo_is_held_before_generation(tmp_path):
    conf=cfg(tmp_path);db=Database(conf.db_path);p=NewsPipeline(conf,db)
    c=Candidate(source='the_hindu',publisher='The Hindu',url='https://www.thehindu.com/news/policy',
        original_headline='Telangana cabinet approves education policy',published_at=iso(),updated_at=None,
        language='en',body_excerpt='The Telangana cabinet approved a new education policy in Hyderabad. '*3,category='politics')
    cid,_=db.insert_candidate(p.research._to_row(c,'hourly'))
    article=('Telangana cabinet approves education policy. The Telangana government announced a new education policy for schools in Hyderabad. ' * 9)
    p._read_original_article=Mock(return_value=article)
    p.research.summarize_urdu=Mock(return_value={
        'urdu_headline':'Telangana cabinet approves education policy',
        'urdu_bullets':['The cabinet approved an education policy.','The policy concerns schools in Telangana.','The announcement was made in Hyderabad.'],
        'facts':{},'verification_notes':[],'requires_human_review':False})
    p.translator.translate=Mock(return_value={'roman_headline':'Telangana cabinet ne taleemi policy manzoor ki',
        'roman_bullets':['Cabinet ne taleemi policy manzoor ki.','Policy Telangana ke schools ke liye hai.','Elan Hyderabad mein kiya gaya.']})
    p.validator.validate=Mock(return_value={'ok':True,'errors':[],'warnings':[],'model_review':{'consistent':True}})
    p.research.llm.chat_json=Mock(return_value={'supported':True,'important':True,'current_news':True,'straight_news':True,'issues':[]})
    result=p.generate_from_verified(cid,VerificationResult(True,[],['source checked'],'high'))
    assert result['status']=='held'
    assert result['reason']=='reviewed_photo_unavailable'
    p.research.summarize_urdu.assert_not_called()


def test_factual_review_failure_prevents_rendering(tmp_path, monkeypatch):
    monkeypatch.setattr('nr_news.pipeline.select_curated_photo', lambda *args: [{'illustrative': True}])
    conf=cfg(tmp_path);db=Database(conf.db_path);p=NewsPipeline(conf,db)
    c=Candidate(source='the_hindu',publisher='The Hindu',url='https://www.thehindu.com/news/policy',
        original_headline='Telangana cabinet approves education policy',published_at=iso(),updated_at=None,
        language='en',body_excerpt='The Telangana cabinet approved an education policy in Hyderabad. '*3,category='politics')
    cid,_=db.insert_candidate(p.research._to_row(c,'hourly'))
    p._read_original_article=Mock(return_value=('Telangana cabinet approves education policy in Hyderabad. '*20))
    p.research.summarize_urdu=Mock(return_value={'urdu_headline':'Cabinet approves education policy',
       'urdu_bullets':['One policy point','Another policy point','Third policy point'], 'facts':{},'verification_notes':[]})
    p.translator.translate=Mock(return_value={'roman_headline':'Taleemi policy','roman_bullets':['Ek baat','Doosri baat','Teesri baat']})
    p.validator.validate=Mock(return_value={'ok':True,'errors':[],'warnings':[],'model_review':{'consistent':True}})
    p.research.llm.chat_json=Mock(return_value={'supported':False,'issues':['unsupported claim']})
    p.cards.render=Mock()
    assert p.generate_from_verified(cid,VerificationResult(True,[],[],'high'))['reason']=='fact_review_not_approved'
    p.cards.render.assert_not_called()


def test_politics_first_and_named_leader_priority():
    from nr_news.agents.research import ResearchAgent
    def sample(title, category='general'):
        return Candidate(source='india_today',publisher='India Today',url='https://www.indiatoday.in/story',
            original_headline=title,published_at=iso(),updated_at=None,language='en',body_excerpt=title,category=category)
    regional_nonpolitics=sample('Hyderabad education department announces teacher training')
    other_politics=sample('Parliament passes a policy bill')
    priority_politics=sample('Asaduddin Owaisi speaks in Parliament about policy')
    assert ResearchAgent.priority(priority_politics)>ResearchAgent.priority(other_politics)>ResearchAgent.priority(regional_nonpolitics)
    assert ResearchAgent.priority(sample('Rahul Gandhi speaks on government policy')) > ResearchAgent.priority(other_politics)
    assert ResearchAgent.priority(sample('Revanth Reddy opens new school in Hyderabad')) > ResearchAgent.priority(other_politics)

def test_india_today_source_is_allowed(tmp_path):
    conf=cfg(tmp_path);db=Database(conf.db_path);desk=NewsPipeline(conf,db)
    c=Candidate(source='india_today',publisher='India Today',url='https://www.indiatoday.in/news/story/123',
        original_headline='National sports tournament announces schedule',published_at=iso(),updated_at=None,
        language='en',body_excerpt='A national sports tournament announced a schedule in an official statement.'*2,category='sports')
    cid,_=db.insert_candidate(desk.research._to_row(c,'hourly'))
    desk.verification.verify=Mock(return_value=VerificationResult(False,['verification held'],[],'low'))
    desk._read_original_article=Mock(return_value=('National sports tournament announces schedule. The official sporting event is scheduled for the coming week. '*10))
    out=desk.process_job('verify_story',{'candidate_id':cid})
    assert out['status']=='held' and out['reasons']==['verification held']
    desk.verification.verify.assert_called_once()


def test_only_three_requested_publisher_domains_are_allowed():
    from nr_news.source_policy import source_allowed
    assert source_allowed({'source':'the_hindu','url':'https://www.thehindu.com/news/x'})
    assert source_allowed({'source':'india_today','url':'https://www.indiatoday.in/news/x'})
    assert source_allowed({'source':'the_wire','url':'https://thewire.in/news/x'})
    assert not source_allowed({'source':'indian_express','url':'https://indianexpress.com/article/x'})
    assert not source_allowed({'source':'the_hindu','url':'https://thehindu.com.evil.example/news/x'})


def test_monitor_calls_only_three_operator_sources(tmp_path):
    from nr_news.sources import SourceMonitor
    from nr_news.models import SourceResult
    conf=cfg(tmp_path);db=Database(conf.db_path);monitor=SourceMonitor(db,config=conf)
    for method,name in [('_hindu','the_hindu'),('_india_today','india_today'),('_the_wire','the_wire')]:
        setattr(monitor,method,Mock(return_value=SourceResult(name,True,0,'ok')))
    monitor._indian_express=Mock()
    assert [x.source for x in monitor.monitor_all()]==['the_hindu','india_today','the_wire']
    monitor._indian_express.assert_not_called()


def test_public_jsonld_article_extraction_checks_url_and_paywall(tmp_path):
    import json
    from types import SimpleNamespace
    conf=cfg(tmp_path); desk=NewsPipeline(conf,Database(conf.db_path))
    url='https://thewire.in/politics/article'
    article={'@type':'NewsArticle','mainEntityOfPage':url,
             'headline':'Parliament adopts education policy',
             'articleBody':'Parliament adopted an education policy for public schools after the session. '*12}
    def page(obj, final=url):
        return SimpleNamespace(ok=True,url=final,text='<html><script type="application/ld+json">'+json.dumps(obj)+'</script></html>')
    desk.evidence.client.get=Mock(return_value=page(article))
    assert len(desk._read_original_article({'url':url}))>600
    article['isAccessibleForFree']=False
    desk.evidence.client.get.return_value=page(article)
    assert desk._read_original_article({'url':url})==''
    article['isAccessibleForFree']=True;article['mainEntityOfPage']='https://thewire.in/politics/other-story'
    desk.evidence.client.get.return_value=page(article)
    assert desk._read_original_article({'url':url})==''
    desk.evidence.client.get.return_value=page(article,final='https://untrusted.example/article')
    assert desk._read_original_article({'url':url})==''


def test_full_article_is_read_before_verification(tmp_path):
    conf=cfg(tmp_path);db=Database(conf.db_path);desk=NewsPipeline(conf,db)
    c=Candidate(source='india_today',publisher='India Today',url='https://www.indiatoday.in/news/story/123',
        original_headline='National sports tournament announces schedule',published_at=iso(),updated_at=None,
        language='en',body_excerpt='Short feed excerpt',category='sports')
    cid,_=db.insert_candidate(desk.research._to_row(c,'hourly'))
    full='National sports tournament announces schedule. The tournament will take place next week. '*10
    desk._read_original_article=Mock(return_value=full)
    desk.verification.verify=Mock(return_value=VerificationResult(False,['held'],[],'low'))
    desk.process_job('verify_story',{'candidate_id':cid})
    assert desk.verification.verify.call_args.kwargs['original_article_text']==full
    assert db.get_candidate(cid)['body_excerpt']=='Short feed excerpt'


def test_related_html_article_is_never_mistaken_for_original(tmp_path):
    from types import SimpleNamespace
    conf=cfg(tmp_path);desk=NewsPipeline(conf,Database(conf.db_path))
    url='https://www.indiatoday.in/india/story/original'
    related=('A completely different related article on the same subject and with similar names. '*12)
    html='<html><h1>Original headline</h1><article><p>'+related+'</p></article></html>'
    desk.evidence.client.get=Mock(return_value=SimpleNamespace(ok=True,url=url,text=html))
    assert desk._read_original_article({'url':url})==''


def test_current_video_page_is_not_used_as_news_article(tmp_path):
    conf=cfg(tmp_path);db=Database(conf.db_path);desk=NewsPipeline(conf,db)
    c=Candidate(source='india_today',publisher='India Today',url='https://www.indiatoday.in/india/video/123',
        original_headline='Politician speaks at event',published_at=iso(),updated_at=None,
        language='en',body_excerpt='A politician spoke at an event today.',category='politics')
    cid,_=db.insert_candidate(desk.research._to_row(c,'hourly'))
    desk._read_original_article=Mock()
    assert desk.process_job('verify_story',{'candidate_id':cid})['reason']=='video_page_not_a_news_article'
    desk._read_original_article.assert_not_called()


def test_continuation_cards_are_held_as_a_whole(tmp_path, monkeypatch):
    monkeypatch.setattr('nr_news.pipeline.select_curated_photo', lambda *args: [{'illustrative': True}])
    conf=cfg(tmp_path);db=Database(conf.db_path);p=NewsPipeline(conf,db)
    c=Candidate(source='the_hindu',publisher='The Hindu',url='https://www.thehindu.com/news/policy',
        original_headline='Telangana cabinet approves education policy',published_at=iso(),updated_at=None,
        language='en',body_excerpt='The Telangana cabinet approved a new education policy.'*3,category='politics')
    cid,_=db.insert_candidate(p.research._to_row(c,'hourly'))
    p._read_original_article=Mock(return_value='Telangana cabinet approves education policy in Hyderabad. '*20)
    p.research.summarize_urdu=Mock(return_value={'urdu_headline':'Cabinet approves education policy',
        'urdu_bullets':['The cabinet approved a policy.','The policy concerns education.','The cabinet met in Hyderabad.'],
        'facts':{},'verification_notes':[],'requires_human_review':False})
    p.translator.translate=Mock(return_value={'roman_headline':'Taleemi policy','roman_bullets':['Ek','Do','Teen']})
    p.validator.validate=Mock(return_value={'ok':True,'errors':[],'warnings':[],'model_review':{'consistent':True}})
    p.research.llm.chat_json=Mock(return_value={'supported':True,'important':True,'current_news':True,'straight_news':True,'issues':[]})
    p.cards.render=Mock(return_value=[SimpleNamespace(page_count=2),SimpleNamespace(page_count=2)])
    p.publisher.publish=Mock()
    out=p.generate_from_verified(cid,VerificationResult(True,[],[],'high'))
    assert out['reason']=='single_card_required'
    assert db.get_editorial(out['editorial_id'])['status']=='pending_review'
    p.publisher.publish.assert_not_called()


def test_opinion_or_historical_review_holds_card(tmp_path, monkeypatch):
    monkeypatch.setattr('nr_news.pipeline.select_curated_photo', lambda *args: [{'illustrative': True}])
    conf=cfg(tmp_path);db=Database(conf.db_path);p=NewsPipeline(conf,db)
    c=Candidate(source='the_wire',publisher='The Wire',url='https://thewire.in/news/story',
        original_headline='Parliament reviews education policy',published_at=iso(),updated_at=None,
        language='en',body_excerpt='Parliament reviews education policy.'*5,category='politics')
    cid,_=db.insert_candidate(p.research._to_row(c,'hourly'))
    p._read_original_article=Mock(return_value='Parliament reviews education policy in India. '*20)
    p.research.summarize_urdu=Mock(return_value={'urdu_headline':'Education policy review',
        'urdu_bullets':['Parliament reviewed policy.','The review involved education.','The debate was in India.'],
        'facts':{},'verification_notes':[],'requires_human_review':False})
    p.translator.translate=Mock(return_value={'roman_headline':'Taleemi policy','roman_bullets':['Ek','Do','Teen']})
    p.validator.validate=Mock(return_value={'ok':True,'errors':[],'warnings':[],'model_review':{'consistent':True}})
    p.research.llm.chat_json=Mock(return_value={'supported':True,'important':True,'current_news':False,'straight_news':False,'issues':['Historical opinion']})
    p.cards.render=Mock()
    out=p.generate_from_verified(cid,VerificationResult(True,[],[],'high'))
    assert out['reason']=='not_current_straight_news'
    p.cards.render.assert_not_called()


def test_date_components_from_iso_inventory_do_not_cause_false_hold():
    from nr_news.agents.research import ResearchAgent
    data={'urdu_headline':'Rescue completed on Nov 1',
          'urdu_bullets':['Rescue happened on Nov 1.','Officials coordinated the rescue.','Those rescued are safe.'],
          'verification_notes':[],'requires_human_review':False,
          'facts':{'names':[],'dates':['2026-11-01'],'figures':[],'locations':[]}}
    ResearchAgent._validate_output(data)
    assert data['requires_human_review'] is False
    data['urdu_bullets'][0]='Rescue happened on Nov 9.'
    ResearchAgent._validate_output(data)
    assert data['requires_human_review'] is True


def test_mixed_new_and_completed_projects_require_clear_foundation_status(tmp_path, monkeypatch):
    monkeypatch.setattr('nr_news.pipeline.select_curated_photo', lambda *args: [{'illustrative': True}])
    conf=cfg(tmp_path);db=Database(conf.db_path);p=NewsPipeline(conf,db)
    c=Candidate(source='india_today',publisher='India Today',url='https://www.indiatoday.in/news/project',
        original_headline='Minister opens and launches new projects',published_at=iso(),updated_at=None,
        language='en',body_excerpt='Minister opens and launches new projects.'*4,category='politics')
    cid,_=db.insert_candidate(p.research._to_row(c,'hourly'))
    article='Minister opens and launches new projects. The minister inaugurated completed works and laid foundation stones for new projects. '*9
    p._read_original_article=Mock(return_value=article)
    p.research.summarize_urdu=Mock(return_value={'urdu_headline':'Minister launches new projects',
        'urdu_bullets':['Minister opened completed works.','The minister launched new projects.','The projects concern local facilities.'],
        'facts':{},'verification_notes':[],'requires_human_review':False})
    p.translator.translate=Mock(return_value={'roman_headline':'Naye projects ka iqtataah',
        'roman_bullets':['Mukammal kaam khole.','Naye projects ka iqtataah kiya.','Yeh local facilities hain.']})
    p.validator.validate=Mock(return_value={'ok':True,'errors':[],'warnings':[],'model_review':{'consistent':True}})
    p.research.llm.chat_json=Mock(return_value={'supported':True,'important':True,'current_news':True,'straight_news':True,'issues':[]})
    p.cards.render=Mock()
    out=p.generate_from_verified(cid,VerificationResult(True,[],[],'high'))
    assert out['reason']=='project_status_ambiguous'
    p.cards.render.assert_not_called()


def test_legacy_manual_story_dedup_checks_full_articles():
    from nr_news.pipeline import _already_posted_manually
    assert _already_posted_manually('AICTE announces four new schemes for institutes')
    assert _already_posted_manually('AICTE launches MODROBS with IDEA Lab and Margdarshan')
    assert _already_posted_manually('Revanth Reddy says treated wastewater should be used')
    assert not _already_posted_manually('Telangana ministers discuss a new school policy')


def test_live_publisher_holds_existing_text_only_cards(tmp_path):
    from nr_news.util import sha256_file
    conf=replace(cfg(tmp_path),dry_run=False)
    db=Database(conf.db_path);g=Mock()
    eid='existing-text-card';p=Path(tmp_path/'card.png');p.write_bytes(b'text-only card')
    digest=sha256_file(p)
    db.set_setting('english_roman_verified:'+eid,'true')
    db.set_setting('card_current_layout:'+digest,'true')
    db.get_editorial=Mock(return_value={'status':'approved'})
    out=PublishingAgent(db=db,graph=g,config=conf).publish(eid,{'visual_status':'passed','file_path':str(p),'sha256':digest,'page_count':1},dry_run=False)
    assert out['reason']=='reviewed_photo_required'
    g.publish_photo.assert_not_called()
