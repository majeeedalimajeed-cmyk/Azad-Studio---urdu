from nr_news.curated_photos import select_curated_photo


def test_curated_photo_matching_and_metadata():
    result=select_curated_photo('Supreme Court permits green crackers for Diwali', 'Green crackers allowed')
    assert len(result)==1
    assert result[0]['illustrative'] is True
    assert result[0]['curated_topic']=='firecrackers'
    assert result[0]['license_ref']=='https://pixabay.com/service/terms/'
    assert result[0]['sha256'] and result[0]['rights_reviewed_by']


def test_no_unrelated_or_sensitive_photos():
    assert select_curated_photo('Political leader speaks in Telangana','Leader speaks') == []
    assert select_curated_photo('Crackers recipe for Diwali meal','Bakery opens') == []
    assert select_curated_photo('Salt crackers gain popularity','Salt crackers') == []
    assert select_curated_photo('Firecrackers blast injures workers','Firecrackers explosion') == []
    assert select_curated_photo('Rupee strengthens against dollar','Rupee strengthens','Counterfeit notes were seized.') == []


def test_two_other_precise_categories():
    assert select_curated_photo('Rupee rises against the dollar','Rupee rises')[0]['curated_topic']=='rupee'
    assert select_curated_photo('Medical college admissions open','Medical college opens')[0]['curated_topic']=='medical_books'


def test_story_text_risk_and_missing_file_are_fail_closed(monkeypatch, tmp_path):
    import nr_news.curated_photos as cp
    assert not select_curated_photo('Firecrackers allowed', 'Firecrackers allowed', 'A blast injured three workers.')
    monkeypatch.setattr(cp, 'ASSETS', tmp_path)
    assert not cp.select_curated_photo('Court allows green firecrackers', 'Green firecrackers')


def test_illustrative_notice_in_caption_not_on_photo(tmp_path):
    import json
    from nr_news.agents.publisher import PublishingAgent
    from nr_news.config import CONFIG
    from nr_news.db import Database
    from dataclasses import replace
    cfg=replace(CONFIG,db_path=tmp_path/'test.sqlite3')
    agent=PublishingAgent(db=Database(cfg.db_path),config=cfg)
    ed={'urdu_headline':'News','urdu_bullets_json':json.dumps(['One','Two','Three']),
        'roman_headline':'Khabar','roman_bullets_json':json.dumps(['Ek','Do','Teen']),
        'source_urls_json':'[]','media_json':json.dumps([{'illustrative':True}])}
    caption=agent.build_caption(ed,'abc123')
    assert 'illustrative stock image' in caption
    ed['media_json']='[]'
    assert 'illustrative stock image' not in agent.build_caption(ed,'abc123')
