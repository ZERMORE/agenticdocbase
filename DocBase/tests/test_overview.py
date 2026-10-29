import json
import pytest
from test_docbase import create_artifact,cfg
from docbase.common import DocBaseError
from docbase.ingest import scan_artifacts
from docbase.engine import build_index
from docbase.overview import overview

def build(tmp_path,cfg):
    root=tmp_path/'parsed';catalog=tmp_path/'catalog.json';storage=tmp_path/'store'
    create_artifact(root,doc_id='english_a',text=['Abstract. We propose feature adaptation for pretrained vision language models using residual bottleneck modules. '*10])
    create_artifact(root,doc_id='english_b',text=['Abstract. We investigate process reward models and step level verification for reasoning and search. '*10])
    create_artifact(root,doc_id='chinese_report',text=['本项目介绍研究内容与主要工作，研究背景涉及论文总结和研究方向，包含手语识别模型、唇形生成和网站开发。'*10])
    scan_artifacts(root,catalog)
    result=build_index(catalog,storage,cfg,reporter=lambda _:None)
    return storage,result['index_version']

def test_overview_is_language_independent_and_document_balanced(tmp_path,cfg):
    storage,version=build(tmp_path,cfg)
    r=overview(storage,index_version=version,budget=900)
    assert r['coverage']['represented_documents']==3
    assert {x['doc_id'] for x in r['results']}=={'english_a','english_b','chinese_report'}
    assert sum(len(x['text']) for x in r['results'])<=900
    for item in r['results']:
        assert item['chunk_id'] is None
        bundle=storage/'source_store'/item['sources'][0]['source_bundle_id']
        blocks=[json.loads(x) for x in (bundle/'blocks.jsonl').read_text().splitlines()]
        block=next(b for b in blocks if b['block_id']==item['block_ranges'][0]['block_id'])
        span=item['block_ranges'][0]
        assert block['text'][span['start']:span['end']]==item['text']

def test_overview_obeys_empty_and_selected_scope(tmp_path,cfg):
    storage,version=build(tmp_path,cfg)
    assert not overview(storage,index_version=version,allowed_doc_ids=[])['results']
    r=overview(storage,index_version=version,allowed_doc_ids=['english_b'])
    assert {x['doc_id'] for x in r['results']}=={'english_b'}
    assert not overview(storage,index_version=version,category='absent')['results']

def test_overview_reports_budget_omissions_and_checks_source(tmp_path,cfg):
    storage,version=build(tmp_path,cfg)
    r=overview(storage,index_version=version,budget=120)
    assert r['coverage']['eligible_documents']==3 and r['coverage']['omitted_documents']==2
    source=storage/'source_store'/r['results'][0]['sources'][0]['source_bundle_id']/'blocks.jsonl'
    source.write_text(source.read_text()+'\n')
    with pytest.raises(DocBaseError):overview(storage,index_version=version)
