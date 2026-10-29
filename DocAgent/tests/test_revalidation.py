import copy,json
from pathlib import Path
import pytest
from test_docagent import row,answer
from docagent.common import AgentError
from docagent.config import Settings
import docagent.revalidate as module

def fixture_job(tmp_path,row):
    folder=tmp_path/'job';run=folder/'runs'/'run_original';run.mkdir(parents=True)
    record=dict(copy.deepcopy(row),chunk_id=None,evidence_kind='overview_excerpt')
    (run/'evidence.json').write_text(json.dumps({'E1':record}),encoding='utf-8')
    (run/'policy_snapshot.md').write_text('test policy',encoding='utf-8')
    (run/'trace.jsonl').write_text(json.dumps({'event':'tool_call','name':'submit_answer','arguments':answer()})+'\n',encoding='utf-8')
    (folder/'request.json').write_text(json.dumps({'allowed_doc_ids':['doc_a']}),encoding='utf-8')
    (folder/'result.json').write_text(json.dumps({'run_id':'run_original','status':'failed','question':'version',
        'collection':'default','index_version':'idx_fixed','trace_path':str(run/'trace.jsonl'),
        'usage':{'model_calls':1},'timings':{},'model_returned':'test'}),encoding='utf-8')
    return folder,record

def patch_local_adapter(monkeypatch,record):
    class Adapter:
        def __init__(self,*args):pass
        def pin(self,version):assert version=='idx_fixed'
        def overview(self,budget):return {'results':[record],'coverage':{'eligible_documents':1}}
    monkeypatch.setattr(module,'DocBaseAdapter',Adapter)
    monkeypatch.setattr(module.Settings,'load',lambda:Settings())

def test_offline_replay_keeps_original_and_makes_no_model_calls(tmp_path,row,monkeypatch):
    folder,record=fixture_job(tmp_path,row);patch_local_adapter(monkeypatch,record)
    before=(folder/'result.json').read_bytes()
    result=module.revalidate(folder)
    assert result['status']=='answered' and result['usage']['provider']['requests']==0
    assert result['support_check']=='unchecked' and not result['claim_checks']
    assert result['revalidation']['source_run_id']=='run_original'
    assert (folder/'result.json').read_bytes()==before

def test_offline_replay_rejects_tampered_evidence(tmp_path,row,monkeypatch):
    folder,record=fixture_job(tmp_path,row);patch_local_adapter(monkeypatch,record)
    file=next(folder.rglob('evidence.json'));evidence=json.loads(file.read_text());evidence['E1']['text']='Altered text 1730.'
    file.write_text(json.dumps(evidence),encoding='utf-8')
    with pytest.raises(AgentError) as error:module.revalidate(folder)
    assert error.value.code=='REVALIDATE'
