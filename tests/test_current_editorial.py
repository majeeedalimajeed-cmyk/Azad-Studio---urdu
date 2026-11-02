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


def test_excludes_protest_and_vote_theft():
    assert editorial_exclusion_text('India bloc protest in Delhi')
    assert editorial_exclusion_text('Vote theft claims')
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


def test_verified_english_roman_card_has_no_unreviewed_photo(tmp_path):
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
    p.research.llm.chat_json=Mock(return_value={'supported':True,'important':True,'issues':[]})
    result=p.generate_from_verified(cid,VerificationResult(True,[],['source checked'],'high'))
    assert result['status']=='rendered'
    assert len(result['cards'])==1 and Path(result['cards'][0]).exists()
    assert db.get_setting('english_roman_verified:'+result['editorial_id'])=='true'
    assert db.get_editorial(result['editorial_id'])['media_json'] in ('null', None)


def test_factual_review_failure_prevents_rendering(tmp_path):
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

def test_india_today_source_is_allowed(tmp_path):
    conf=cfg(tmp_path);db=Database(conf.db_path);desk=NewsPipeline(conf,db)
    c=Candidate(source='india_today',publisher='India Today',url='https://www.indiatoday.in/news/story/123',
        original_headline='National sports tournament announces schedule',published_at=iso(),updated_at=None,
        language='en',body_excerpt='A national sports tournament announced a schedule in an official statement.'*2,category='sports')
    cid,_=db.insert_candidate(desk.research._to_row(c,'hourly'))
    desk.verification.verify=Mock(return_value=VerificationResult(False,['verification held'],[],'low'))
    out=desk.process_job('verify_story',{'candidate_id':cid})
    assert out['status']=='held' and out['reasons']==['verification held']
    desk.verification.verify.assert_called_once()
