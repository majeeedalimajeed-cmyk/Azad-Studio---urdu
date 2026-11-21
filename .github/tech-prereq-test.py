"""Non-publishing readiness checks. Never print credentials or API payloads."""
import os,requests

def report(label,r):
 print(label,'HTTP',r.status_code,flush=True)
 try:
  e=r.json().get('error',{})
  if e: print('  error status:',e.get('status') or e.get('code'),flush=True)
 except Exception: pass

key=os.environ.get('GEMINI_KEY','')
if key:
 for name,text in [('EN','Welcome to Azaad Studio.'),('HI','नमस्कार, आज़ाद स्टूडियो में आपका स्वागत है।')]:
  payload={'contents':[{'parts':[{'text':text}]}],'generationConfig':{'responseModalities':['AUDIO'],'speechConfig':{'voiceConfig':{'prebuiltVoiceConfig':{'voiceName':'Kore'}}}}}
  try:
   r=requests.post('https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent',headers={'x-goog-api-key':key},json=payload,timeout=60);report('TTS '+name,r)
   if r.ok:
    parts=r.json().get('candidates',[{}])[0].get('content',{}).get('parts',[]);print('  audio returned:',any(p.get('inlineData',{}).get('data') for p in parts),flush=True)
  except Exception as e:print('TTS',name,'failed',type(e).__name__,flush=True)
else:print('TTS: no configured key')
px=os.environ.get('PIXABAY_KEY','')
if px:
 try:
  r=requests.get('https://pixabay.com/api/',params={'key':px,'q':'iphone duo','image_type':'photo','safesearch':'true','per_page':3},timeout=30);report('Pixabay image query',r)
  if r.ok:print('  image hits:',r.json().get('totalHits'),flush=True)
 except Exception as e:print('Pixabay check failed',type(e).__name__,flush=True)
else: print('Pixabay: no configured key')
tok=os.environ.get('FB_ACCESS','');pid=os.environ.get('FB_PAGE_ID','')
if tok and pid:
 try:
  h={'Authorization':'Bearer '+tok};r=requests.get('https://graph.facebook.com/v26.0/me',headers=h,params={'fields':'id,name'},timeout=30);report('Meta token',r)
  if r.ok:
   obj=r.json();pt=tok if obj.get('id')==pid else None
   if not pt:
    a=requests.get('https://graph.facebook.com/v26.0/me/accounts',headers=h,params={'fields':'id,access_token','limit':100},timeout=30);report('Meta page lookup',a)
    if a.ok:
     match=next((x for x in a.json().get('data',[]) if x.get('id')==pid),None);pt=(match or {}).get('access_token')
   if pt:
    r=requests.get('https://graph.facebook.com/v26.0/'+pid,headers={'Authorization':'Bearer '+pt},params={'fields':'instagram_business_account'},timeout=30);report('Instagram linked account lookup',r)
    if r.ok:print('  Instagram linked:',bool(r.json().get('instagram_business_account',{}).get('id')),flush=True)
   else:print('Meta: configured Page unavailable',flush=True)
 except Exception as e:print('Meta check failed',type(e).__name__,flush=True)
else:print('Meta: missing credential or Page ID')