"""No-publishing, low-token model availability probe."""
import os,requests,concurrent.futures
options=[('gemini-3.1-flash-lite',os.getenv('TEST_GEMINI_KEY'),'https://generativelanguage.googleapis.com/v1beta/openai'),('gemini-3.5-flash-lite',os.getenv('TEST_GEMINI_KEY'),'https://generativelanguage.googleapis.com/v1beta/openai'),('gemini-3-flash-preview',os.getenv('TEST_GEMINI_KEY'),'https://generativelanguage.googleapis.com/v1beta/openai'),('llama-3.3-70b-versatile',os.getenv('TEST_GROQ_KEY'),'https://api.groq.com/openai/v1')]
def check(item):
 model,key,base=item
 if not key:return model,'missing key'
 try:
  r=requests.post(base+'/chat/completions',headers={'Authorization':'Bearer '+key},json={'model':model,'messages':[{'role':'system','content':'Return a JSON object only.'},{'role':'user','content':'Return {"ok":true}'}], 'max_tokens':256,'response_format':{'type':'json_object'}},timeout=25)
  if r.ok:
   try:
    import json
    obj=json.loads(r.json()['choices'][0]['message']['content']);return model,'OK' if obj.get('ok') is True else 'invalid result'
   except Exception:return model,'empty or invalid JSON'
  return model,'HTTP '+str(r.status_code)
 except Exception as e:return model,type(e).__name__
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as x:
 for model,status in x.map(check,options):print('MODEL',model,status,flush=True)
