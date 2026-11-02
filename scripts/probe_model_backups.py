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
        return label,'HTTP '+str(r.status_code)
    except Exception as exc:return label,'error '+type(exc).__name__
items=[('OpenAI',os.getenv('OPENAI_TEST_KEY'), 'https://api.openai.com/v1','gpt-4o-mini'),
       ('Gemini',os.getenv('GEMINI_TEST_KEY'),'https://generativelanguage.googleapis.com/v1beta/openai','gemini-2.5-flash')]
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 for label,result in pool.map(lambda x:probe(*x),items):print('BACKUP MODEL',label,result,flush=True)
