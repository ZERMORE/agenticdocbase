import json
from fastapi.testclient import TestClient
from researchdesk.api import create_app,public_result
from researchdesk.store import Store

def test_default_and_scope_specific_support_preferences(tmp_path):
 s=Store(tmp_path);c=TestClient(create_app(s,seed=False),base_url='http://127.0.0.1')
 c.headers['X-CSRF-Token']=c.get('/api/bootstrap').json()['csrf']
 assert c.get('/api/preferences').json()['support_check_level']=='advisory'
 assert c.patch('/api/preferences',json={'language':'English'}).status_code==200
 assert c.patch('/api/preferences',json={'support_check_level':'balanced'}).status_code==200
 assert c.get('/api/preferences').json()['language']=='English'
 assert c.patch('/api/preferences',json={'support_check_level':'invented'}).status_code==422
 kb=s.create_kb('test');chat=c.post('/api/conversations',json={'kb':kb['id']}).json()
 assert c.patch(f"/api/conversations/{chat['id']}/preferences",json={'support_check_level':'off'}).status_code==200
 assert json.loads(s.one('SELECT preferences FROM conversations')['preferences'])=={'support_check_level':'off'}
 s.init(seed=False)
 assert c.get('/api/preferences').json()['support_check_level']=='balanced'
 assert public_result({'claims':[],'citations':[],'support_check_level':'off'})['support_check_level']=='off'
