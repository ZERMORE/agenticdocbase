import json
from pathlib import Path
import pymupdf as fitz
import pytest
from doc2md.config import Settings
from doc2md.extract import Extractor
from doc2md.pipeline import parse_document
from doc2md.revisions import revise
from doc2md.locator import locate

def make(path,rotation=0,crop=False):
    with fitz.open() as d:
        p=d.new_page(width=600,height=700)
        p.insert_text((80,100),'Version number is 1729.',fontsize=14)
        if crop:p.set_cropbox(fitz.Rect(30,40,580,670))
        p.set_rotation(rotation)
        p=d.new_page();p.insert_text((80,100),'The second page stays unchanged.',fontsize=14)
        d.save(path)

def blocks(folder):return [json.loads(x) for x in (Path(folder)/'blocks.jsonl').read_text(encoding='utf-8').splitlines()]
def hashes(folder):
    import hashlib
    return {str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(folder).rglob('*') if p.is_file()}

def test_quality_and_copy_on_write_edit(tmp_path):
    source=tmp_path/'source.pdf';make(source)
    cfg=Settings(offline=True)
    base=Path(parse_document(source,tmp_path/'out',cfg)['output']);before=hashes(base)
    original=blocks(base);block=original[0]
    child=Path(revise({'artifact':str(base),'action':'edit','block_id':block['block_id'],
                      'text':'Human correction: value 1730.','reason':'核对样本文字'},tmp_path/'revisions',cfg)['output'])
    assert hashes(base)==before
    revised=blocks(child)
    assert revised[0]['content_origin']=='human_correction'
    assert revised[1:]==original[1:]
    assert revised[0]['block_id']!=block['block_id']
    assert json.loads((child/'quality.json').read_text(encoding='utf-8'))['page_count']==2
    assert json.loads((child/'manifest.json').read_text(encoding='utf-8'))['correction']['lineage']

def test_one_page_reparse_preserves_other_page(tmp_path):
    source=tmp_path/'source.pdf';make(source);cfg=Settings(offline=True)
    base=Path(parse_document(source,tmp_path/'out',cfg)['output']);before=hashes(base)
    child=Path(revise({'artifact':str(base),'action':'page','page_index':0,'reason':'核对第一页'},tmp_path/'revisions',cfg)['output'])
    assert hashes(base)==before
    assert [b for b in blocks(child) if b['source']['page_index']==2]==[b for b in blocks(base) if b['source']['page_index']==2]
    assert all(b['source']['page_index'] in {1,2} for b in blocks(child))

@pytest.mark.parametrize('rotation',[0,90,180,270])
@pytest.mark.parametrize('crop',[False,True])
def test_rotated_crop_locator(tmp_path,rotation,crop):
    source=tmp_path/'source.pdf';make(source,rotation,crop)
    result=locate({'path':str(source),'page_index':0,'quote':'Version number is 1729.'},tmp_path/'page.png')
    assert result['kind']=='text_matches' and result['display_page']==1
    assert result['rects'] and all(0<=v<=1 for rect in result['rects'] for v in rect)
    assert (tmp_path/'page.png').is_file()

def test_locator_region_is_not_exact_text(tmp_path):
    source=tmp_path/'source.pdf';make(source)
    result=locate({'path':str(source),'page_index':0,'quote':'Unfindable correction','bbox':[40,50,200,150]},tmp_path/'page.png')
    assert result['kind']=='block_region' and '不是逐字' in result['note']
    with pytest.raises(ValueError):locate({'path':str(source),'page_index':0,'source_hash':'bad'},tmp_path/'wrong.png')

def test_pdf_page_checkpoint_resumes_only_completed_pages(tmp_path,monkeypatch):
    source=tmp_path/'source.pdf';make(source);cfg=Settings(offline=True)
    original=Extractor.add
    def interrupted(self,*args,**kw):
        if kw.get('page')==2:raise RuntimeError('simulated crash')
        return original(self,*args,**kw)
    monkeypatch.setattr(Extractor,'add',interrupted)
    with pytest.raises(RuntimeError):parse_document(source,tmp_path/'out',cfg)
    checkpoints=list((tmp_path/'out'/'.checkpoints').glob('*/checkpoint.json'))
    assert len(checkpoints)==1
    assert json.loads(checkpoints[0].read_text(encoding='utf-8'))['completed_pages']==[0]
    seen=[]
    def resumed(self,*args,**kw):
        seen.append(kw.get('page'));return original(self,*args,**kw)
    monkeypatch.setattr(Extractor,'add',resumed)
    result=parse_document(source,tmp_path/'out',cfg)
    assert 1 not in seen and 2 in seen
    assert len(blocks(result['output']))==2

def test_pending_rerun_keeps_previous_artifact(tmp_path):
    from PIL import Image
    source=tmp_path/'scan.pdf'
    with fitz.open() as d:
        p=d.new_page();image=tmp_path/'white.png';Image.new('RGB',(200,200),'white').save(image)
        p.insert_image(p.rect,filename=str(image));d.save(source)
    cfg=Settings(offline=True);one=parse_document(source,tmp_path/'out',cfg);before=hashes(Path(one['output']))
    two=parse_document(source,tmp_path/'out',cfg)
    assert one['output']!=two['output'] and hashes(Path(one['output']))==before


def test_cached_complete_artifact_rejects_changed_text(tmp_path):
    source=tmp_path/'source.pdf';make(source)
    cfg=Settings(offline=True);result=parse_document(source,tmp_path/'out',cfg)
    path=Path(result['output'])/'blocks.jsonl'
    path.write_text(path.read_text(encoding='utf-8').replace('1729','1730'),encoding='utf-8')
    with pytest.raises(ValueError,match='哈希不匹配'):parse_document(source,tmp_path/'out',cfg)


def test_cli_parse_stdout_is_one_json_document(tmp_path):
    import subprocess,sys
    source=tmp_path/'source.pdf';make(source)
    result=subprocess.run([sys.executable,'-X','utf8','-m','doc2md','parse',str(source),'--output',str(tmp_path/'out'),'--offline'],capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0
    assert json.loads(result.stdout)['results'][0]['status']=='ok'
