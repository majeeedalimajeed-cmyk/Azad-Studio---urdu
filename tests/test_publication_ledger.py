import base64
import json
from unittest.mock import Mock
import pytest
import requests
from nr_news.publication_ledger import GitHubPublicationLedger, LedgerDuplicate, LedgerUnavailable
from nr_news.util import iso


class Response:
    def __init__(self, code, data=None):
        self.status_code, self.data = code, data or {}
    def json(self): return self.data

class FakeGitHub:
    def __init__(self):
        self.data={'version':1,'records':[]}; self.revision=1; self.mode='ok'; self.puts=0
    def request(self, method, url, **kwargs):
        if method == 'GET':
            if self.mode=='read_fail': return Response(403)
            return Response(200,{'sha':str(self.revision),'encoding':'base64',
                'content':base64.b64encode(json.dumps(self.data).encode()).decode()})
        self.puts+=1
        if self.mode=='write_fail':return Response(403)
        payload=kwargs['json']
        if payload['sha']!=str(self.revision):return Response(409)
        self.data=json.loads(base64.b64decode(payload['content']))
        self.revision+=1
        if self.mode=='lost_write_response':
            raise requests.Timeout('response lost after commit')
        return Response(200)


def ledger(session):return GitHubPublicationLedger(token='fake',repo='owner/repo',session=session)

def candidate(url='https://thehindu.com/news/story',headline='Telangana cabinet approves school policy'):
    return {'url':url,'original_headline':headline}

def reserve(l, key='key1', item=None):
    l.reserve(key=key,candidate=item or candidate(),editorial_id='e1',page_id='page',caption_marker='mark')


def test_claim_is_committed_then_success_is_recorded():
    gh=FakeGitHub(); l=ledger(gh); reserve(l)
    assert gh.data['records'][0]['status']=='reserved'
    l.finish('key1',status='confirmed',post_id='post123')
    assert gh.data['records'][0]['status']=='confirmed' and gh.data['records'][0]['post_id']=='post123'
    with pytest.raises(LedgerDuplicate):reserve(l,'otherkey',candidate('https://www.thehindu.com/news/story?utm_source=x'))


def test_unknown_and_confirmed_claims_survive_process_restart():
    gh=FakeGitHub();l=ledger(gh);reserve(l);l.finish('key1',status='uncertain')
    restarted=ledger(gh)
    with pytest.raises(LedgerDuplicate):reserve(restarted,'new-key')
    assert gh.data['records'][0]['status']=='uncertain'


def test_committed_but_timed_out_reservation_never_uploads_on_retry():
    gh=FakeGitHub();gh.mode='lost_write_response';l=ledger(gh)
    with pytest.raises(LedgerUnavailable):reserve(l)
    assert len(gh.data['records'])==1
    gh.mode='ok'
    with pytest.raises(LedgerDuplicate):reserve(ledger(gh),'different-key')


def test_read_write_and_corruption_failure_closed():
    gh=FakeGitHub(); l=ledger(gh)
    gh.mode='read_fail'
    with pytest.raises(LedgerUnavailable):reserve(l)
    gh.mode='write_fail'
    with pytest.raises(LedgerUnavailable):reserve(l)
    gh.mode='ok';gh.data={'version':1,'records':[{'key':'broken','status':'confirmed'}]}
    with pytest.raises(LedgerUnavailable):reserve(l)


def test_near_identical_cross_source_headline_is_held():
    gh=FakeGitHub(); l=ledger(gh);reserve(l)
    same=candidate('https://www.indiatoday.in/article/1','Telangana cabinet approves new school policy')
    with pytest.raises(LedgerDuplicate):reserve(l,'other',same)


def test_compare_and_swap_conflict_rereads_and_blocks_race():
    class Race(FakeGitHub):
        def request(self,method,url,**kwargs):
            if method=='PUT' and self.puts==0:
                self.puts+=1
                self.data['records'].append({'key':'competing','status':'reserved','created_at':iso(),
                    'url':candidate()['url'],'headline':candidate()['original_headline']})
                self.revision+=1
                return Response(409)
            return super().request(method,url,**kwargs)
    gh=Race()
    with pytest.raises(LedgerDuplicate):reserve(ledger(gh))
    assert len(gh.data['records'])==1 and gh.data['records'][0]['key']=='competing'


def test_probe_does_not_create_story_claim():
    gh=FakeGitHub();ledger(gh).probe()
    assert gh.data['records']==[] and gh.data['last_write_probe']['at']
