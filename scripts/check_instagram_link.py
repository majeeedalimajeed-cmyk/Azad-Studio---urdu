"""Read-only Meta diagnostics: never create or publish media."""
import os, requests
base='https://graph.facebook.com/'+os.getenv('NR_GRAPH_API_VERSION','v26.0')
page=os.environ['NR_FB_PAGE_ID']; token=os.environ['NR_FB_PAGE_ACCESS_TOKEN']; h={'Authorization':'Bearer '+token}
def get(path,params=None):
 try:
  r=requests.get(base+'/'+path,params=params,headers=h,timeout=20)
  payload=r.json()
  if r.status_code!=200:
   err=payload.get('error',{}); print('READ',path.split('/')[-1], 'HTTP',r.status_code,'code',err.get('code'),'message',str(err.get('message',''))[:350]);return None
  return payload
 except Exception as e:
  print('READ error',type(e).__name__);return None
p=get(page,{'fields':'name,instagram_business_account{id,username}'})
if p is not None:
 acc=p.get('instagram_business_account') or {}; print('LINKED_ACCOUNT',bool(acc.get('id')),'USERNAME',acc.get('username','not returned'))
 if acc.get('id'):
  a=get(str(acc['id']),{'fields':'username,media_count'}); print('ACCOUNT_READ_OK',bool(a),'ACCOUNT_TYPE',(a or {}).get('account_type','not returned'))
permissions=get('me/permissions')
if permissions is not None:
 available={x.get('permission'):x.get('status') for x in permissions.get('data',[])}
 print('IG_PERMISSIONS', {x:available.get(x,'not listed') for x in ['instagram_basic','instagram_content_publish','pages_read_engagement']})
accounts=get('me/accounts',{'fields':'id,access_token','limit':100})
if accounts:
 for entry in accounts.get('data',[]):
  if str(entry.get('id')) == page and entry.get('access_token'):
   h={'Authorization':'Bearer '+entry['access_token']}; print('DERIVED_PAGE_TOKEN',True);break
postid=page+'_122101386051530990'
attachments=get(postid+'/attachments')
if attachments is not None:
 print('PUBLISHED_FB_IMAGE_URL_AVAILABLE',any(((x.get('media') or {}).get('image') or {}).get('src') for x in attachments.get('data',[])))
