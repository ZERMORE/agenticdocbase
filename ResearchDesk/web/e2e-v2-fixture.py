"""Local-only synthetic browser fixture. No worker and no provider HTTP requests."""
import json,tempfile,threading,time
from pathlib import Path
import uvicorn
from researchdesk.api import create_app
from researchdesk.store import Store,dump
from researchdesk.model_profiles import ModelProfiles,ROLES
s=Store(Path(tempfile.mkdtemp(prefix='researchdesk-v2-')))
app=create_app(s,seed=False)
now=time.time()
s.execute('INSERT INTO kbs VALUES(?,?,?,?,?,?,?,?,?)',('kb_fixture','V2 合成测试库','fixture',1,1,'idx_synthetic',1,0,now))
for i in range(10):
    s.execute('INSERT INTO docs VALUES(?,?,?,?,?,?)',('doc'+str(i),'Synthetic paper '+str(i)+'.pdf','sha'+str(i),str(s.data/('paper'+str(i)+'.pdf')),10,now))
    s.execute('INSERT INTO members(kb,doc,docbase_id,quality,status) VALUES(?,?,?,?,?)',('kb_fixture','doc'+str(i),'base'+str(i),'ok','ready'))
    from researchdesk.v2_store import set_metadata
    set_metadata(s,'kb_fixture','doc'+str(i),{'title':'合成论文 '+str(i),'authors':['Author '+str(i)],'year':2020+i%5,'tags':['vision' if i%2 else 'language']},'zotero')
for role in ROLES:ModelProfiles(s).save(role,{'base_url':'https://example.invalid/v1','model':'synthetic-model','api_key':'FAKE-NON-NETWORK-KEY','inherit':role=='support'})
def simulate():
    while True:
        for row in s.all("SELECT * FROM jobs WHERE state='queued' AND kind='chat'"):
            job=row['id'];s.execute("UPDATE jobs SET state='running',stage='model_response' WHERE id=?",(job,))
            for i in range(30):
                if s.one('SELECT cancel FROM jobs WHERE id=?',(job,))['cancel']:break
                text=f'合成草稿片段 {i:03d} · {int(time.time()*1000)}'
                s.execute('INSERT INTO message_drafts VALUES(?,?,?,?) ON CONFLICT(job) DO UPDATE SET text=excluded.text,sequence=excluded.sequence,updated=excluded.updated',(job,text,i+1,time.time()))
                s.event(job,'draft',{'text':text,'verified':False});time.sleep(.1)
            if s.one('SELECT cancel FROM jobs WHERE id=?',(job,))['cancel']:
                s.execute("UPDATE jobs SET state='cancelled' WHERE id=?",(job,));continue
            result={'status':'answered','claims':[{'text':'这是合成浏览器测试答案。','kind':'direct','supports':[]}],'citations':[],'model_returned':'synthetic-fixture','timings':{'total_seconds':3},'support_check':'disabled','model_snapshot':{},'request_snapshot':json.loads(row['payload'])['snapshot']}
            s.execute("UPDATE messages SET status='answered',content=?,result=? WHERE job=? AND role='assistant'",('这是合成浏览器测试答案。',dump(result),job))
            s.event(job,'answer_committed');s.execute("UPDATE jobs SET state='succeeded' WHERE id=?",(job,))
        time.sleep(.05)
threading.Thread(target=simulate,daemon=True).start()
uvicorn.run(app,host='127.0.0.1',port=8766,log_level='warning')
