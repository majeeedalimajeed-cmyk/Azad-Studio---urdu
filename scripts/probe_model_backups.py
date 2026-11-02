"""Probe already-configured model credentials without printing any key."""
import os,requests,concurrent.futures

def probe(label,key,base,model):
    if not key:return label,'secret-empty'
    try:
        r=requests.post(base+'/chat/completions',headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},
          json={'model':model,'messages':[{'role':'system','content':'Return a JSON object with ok true.'},{'role':'user','content':'Perform the availability test.'}],
          'max_tokens':100,'response_format':{'type':'json_object'}},timeout=40)
        if r.status_code==200:
            data=r.json();content=data['choices'][0]['message'].get('content','');return label,'working' if content else 'empty-response'
        try:
            err=r.json().get('error',{})
            return label,'HTTP '+str(r.status_code)+' code='+str(err.get('code',''))+' status='+str(err.get('status',''))+' type='+str(err.get('type',''))
        except Exception:return label,'HTTP '+str(r.status_code)
    except Exception as exc:return label,'error '+type(exc).__name__
items=[('OpenAI',os.getenv('OPENAI_TEST_KEY'), 'https://api.openai.com/v1','gpt-4o-mini'),
       ('Gemini',os.getenv('GEMINI_TEST_KEY'),'https://generativelanguage.googleapis.com/v1beta/openai','gemini-2.5-flash')]
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 for label,result in pool.map(lambda x:probe(*x),items):print('BACKUP MODEL',label,result,flush=True)

key=os.getenv('GEMINI_TEST_KEY')
if key:
 try:
  response=requests.get('https://generativelanguage.googleapis.com/v1beta/models',headers={'x-goog-api-key':key},timeout=30)
  if response.ok:
   names=[m.get('name') for m in response.json().get('models',[]) if 'generateContent' in m.get('supportedGenerationMethods',[])]
   print('GEMINI GENERATIVE MODELS',names[:25],flush=True)
  else:
   print('GEMINI MODEL LIST HTTP',response.status_code,flush=True)
 except Exception as exc:print('GEMINI MODEL LIST',type(exc).__name__,flush=True)
