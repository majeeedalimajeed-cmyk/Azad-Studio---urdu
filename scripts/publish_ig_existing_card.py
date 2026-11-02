"""ONE story, Instagram only. Single-use durable claim; never retries ambiguity."""
import base64,json,os,time,requests
page=os.environ['NR_FB_PAGE_ID'];configured=os.environ['NR_FB_PAGE_ACCESS_TOKEN']; version=os.getenv('NR_GRAPH_API_VERSION') or 'v26.0'
base='https://graph.facebook.com/'+version; repo=os.environ['GITHUB_REPOSITORY']; gh=os.environ['NR_GITHUB_LEDGER_TOKEN']; path='instagram-once-firecrackers-20261102.json';ghurl=f'https://api.github.com/repos/{repo}/contents/{path}'
s=requests.Session()
def call(method,path,token,**kw):
 r=s.request(method,base+'/'+path,headers={'Authorization':'Bearer '+token},timeout=60,**kw)
 try:j=r.json()
 except Exception:j={}
 if not r.ok:
  e=j.get('error',{});raise RuntimeError(f'Meta {path.split("/")[-1]} HTTP {r.status_code} code {e.get("code")} subcode {e.get("error_subcode")}')
 return j
# The secret slot can contain a user token. Derive the real page token.
accounts=call('GET','me/accounts',configured,params={'fields':'id,access_token','limit':100})
token=next((x['access_token'] for x in accounts.get('data',[]) if str(x.get('id'))==page and x.get('access_token')),None)
if not token: raise RuntimeError('Could not obtain confirmed Page token; no action taken')
account=call('GET',page,token,params={'fields':'instagram_business_account{id,username}' }).get('instagram_business_account') or {}
if account.get('username','').lower()!='azadstudioofficial' or not account.get('id'):raise RuntimeError('Linked Instagram account differs; no action taken')
ig=account['id']; marker='c6407f9a46ab'
recent=call('GET',ig+'/media',token,params={'fields':'id,caption,permalink','limit':100})
if any(marker in (x.get('caption') or '') for x in recent.get('data',[])):
 print('HELD: matching Instagram caption already exists');raise SystemExit(0)
headers={'Authorization':'Bearer '+gh,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}
r=requests.get(ghurl,params={'ref':'publishing-ledger'},headers=headers,timeout=25)
if r.status_code==200:
 print('HELD: durable Instagram claim already exists; manual reconciliation required');raise SystemExit(0)
if r.status_code!=404: raise RuntimeError('Could not read durable Instagram claim; no upload')
record={'version':1,'status':'reserved','purpose':'one-time Instagram copy of verified Facebook story','marker':marker,'run_id':os.getenv('GITHUB_RUN_ID')}
payload={'message':'Reserve one-time Instagram cross-post','branch':'publishing-ledger','content':base64.b64encode(json.dumps(record).encode()).decode()}
r=requests.put(ghurl,headers=headers,json=payload,timeout=25)
if r.status_code not in (200,201): raise RuntimeError('Durable Instagram claim could not be reserved; no upload')
print('Durable one-time claim reserved')
caption=('Supreme Court permits green firecrackers in Delhi-NCR on November 7 and 8.\n'
 'The court set bursting hours of 6–7 am and 8–10 pm each day. The bench urged enforcement and suggested public awareness efforts.\n\n'
 'Roman Urdu: Delhi-NCR mein 7–8 November ko green firecrackers ki ijazat hai. Patakhe jalane ka waqt subah 6–7 aur raat 8–10 baje hai. Bench ne qawaneen par amal aur awami agahi ki baat ki.\n\n'
 'Photo is an illustrative stock image of firecrackers; it does not identify the fireworks permitted by the court.\n'
 'AZAD STUDIO ref: '+marker)
try:
 # Host the approved design as an unpublished Page photo; this does not make a Facebook post.
 with open('one_time_ig/firecrackers_preview.png','rb') as f:
  obj=call('POST',page+'/photos',token,data={'published':'false'},files={'source':('card.png',f,'image/png')})
 photo=obj.get('id')
 if not photo: raise RuntimeError('Meta did not return the unpublished photo ID')
 details=call('GET',str(photo),token,params={'fields':'images'})
 images=details.get('images') or []
 image_url=next((x.get('source') for x in images if x.get('source')),None)
 if not image_url:raise RuntimeError('No accessible image URL available for Instagram')
 r=requests.get(image_url,stream=True,timeout=30);accessible=r.status_code==200 and r.headers.get('Content-Type','').startswith('image/');r.close()
 if not accessible:raise RuntimeError('Unpublished photo is not publicly retrievable for Instagram')
 creation=call('POST',ig+'/media',token,data={'image_url':image_url,'caption':caption}).get('id')
 if not creation:raise RuntimeError('Instagram container ID missing')
 status=''
 for _ in range(18):
  status=call('GET',creation,token,params={'fields':'status_code'}).get('status_code','')
  if status in ('FINISHED','ERROR','EXPIRED'): break
  time.sleep(5)
 if status!='FINISHED':raise RuntimeError('Instagram container not ready: '+status)
 media=call('POST',ig+'/media_publish',token,data={'creation_id':creation}).get('id')
 if not media:raise RuntimeError('Instagram publication returned no ID')
 record.update(status='confirmed',media_id=media)
 print('INSTAGRAM_PUBLISHED media_id',media)
 try:
  link=call('GET',media,token,params={'fields':'permalink'}).get('permalink');print('INSTAGRAM_PERMALINK',link or 'not available');record['permalink']=link
 except Exception:print('Instagram permalink unavailable; media ID was confirmed')
except Exception as exc:
 record.update(status='uncertain',reason=str(exc)[:300]);print('HELD_OR_UNKNOWN',str(exc)[:300])
finally:
 try:
  existing=requests.get(ghurl,params={'ref':'publishing-ledger'},headers=headers,timeout=25)
  sha=existing.json().get('sha'); payload={'message':'Record one-time Instagram outcome','branch':'publishing-ledger','sha':sha,'content':base64.b64encode(json.dumps(record).encode()).decode()}
  w=requests.put(ghurl,headers=headers,json=payload,timeout=25); print('CLAIM_OUTCOME_RECORDED',w.status_code==200)
 except Exception: print('Claim outcome could not be updated; original reservation still blocks retries')
