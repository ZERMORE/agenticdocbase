import importlib.util
from pathlib import Path
import pytest
from docbase.chunker import make_chunks
from docbase.config import Settings

def test_human_correction_not_merged_into_native_prose():
    source={'page_index':1,'bbox':[20,20,300,80]}
    def block(key,text,origin=None):
        b={'block_id':key,'text':text,'type':'paragraph','category':'test','source':dict(source),'heading_path':[]}
        if origin:b['content_origin']=origin
        return b
    artifact={'artifact_id':'a','bundle_id':'b','manifest':{'doc_id':'doc_test','revision':'r','status':'ok','warnings':[]},
              'blocks':[block('b1','The original native paragraph is long enough to be considered useful prose. ' * 2),
                        block('b2','A corrected paragraph which has enough words for comparison. '*2,'human_correction')]}
    chunks=make_chunks([artifact],Settings(chunk_strategy='context-v2'))
    assert any(c['content_origin']==['human_correction'] for c in chunks)
    assert not any(len(c['block_ranges'])>1 for c in chunks)

def test_freeze_refuses_unreviewed_candidates(tmp_path):
    import json
    path=Path(__file__).resolve().parents[2]/'workflows/quality_eval.py'
    spec=importlib.util.spec_from_file_location('quality_eval',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    cats={'fact':20,'explanation':15,'cross_document':10,'figure':5,'unanswerable':10}
    data=[]
    for category,count in cats.items():
        for i in range(count):
            data.append({'question_id':str(len(data)), 'question':category+str(i),'category':category,
                         'allowed_doc_ids':['d1'],'review':{'status':'pending'},'answerability':'answerable'})
    for name,items in [('dev',data[:20]),('test',data[20:])]:
        (tmp_path/(name+'.jsonl')).write_text(''.join(json.dumps(x)+'\n' for x in items),encoding='utf-8')
    with pytest.raises(ValueError,match='尚未人工审核'):module.freeze(tmp_path)
    assert not (tmp_path/'frozen.json').exists()
