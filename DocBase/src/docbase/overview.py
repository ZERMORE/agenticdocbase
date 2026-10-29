"""Document-balanced overview; independent of the question's language and term overlap."""
from collections import defaultdict
from pathlib import Path
import re
from .common import DocBaseError,inside,name_checked
from .config import ROOT
from .engine import load_index,resolve_sources
from .ingest import inspect_artifact

def overview(storage=None,*,collection='default',index_version=None,allowed_doc_ids=None,category=None,budget=24000):
    if type(budget) is not int or not 120<=budget<=1000000:
        raise DocBaseError('CONFIG','概览字符预算应为120–1000000')
    storage=Path(storage or ROOT/'storage').resolve()
    _,manifest,chunks=load_index(storage,collection,index_version)
    scope=set(allowed_doc_ids) if allowed_doc_ids is not None else None
    groups=defaultdict(list)
    for c in chunks:
        if (scope is None or c['doc_id'] in scope) and (category is None or c['category']==category):
            groups[c['doc_id']].append(c)
    documents=[];results=[];remaining=budget
    limit=min(len(groups),120,budget//120)
    per_doc=min(1600,budget//max(limit,1))
    verified={}
    for number,(doc,values) in enumerate(sorted(groups.items())):
        first=values[0];source=first['sources'][0]
        entry={'doc_id':doc,'name':Path(source.get('original_path','')).name,'excerpted':False}
        documents.append(entry)
        if number>=limit:
            entry['reason']='概览预算不足，未取代表片段';continue
        bundle_id=name_checked(source['source_bundle_id'])
        if bundle_id not in verified:
            verified[bundle_id]=inspect_artifact(inside(storage/'source_store',bundle_id))
        artifact=verified[bundle_id]
        if artifact['artifact_id']!=first['artifact_id'] or artifact['bundle_id']!=bundle_id:
            raise DocBaseError('SOURCE_CORRUPT','概览来源与固定索引不一致')
        # Only blocks represented in this frozen index are eligible (including category filters).
        ids={r['block_id'] for c in values for r in c['block_ranges']}
        candidates=[b for b in artifact['blocks'] if b['block_id'] in ids
                    and b['type'] in {'paragraph','ocr_text','vision_transcription'} and len(b['text'].strip())>=60]
        if not candidates:
            entry['reason']='缺少适合概览的正文块';continue
        def rank(b):
            text=b['text'];page=b['source'].get('page_index') or 999
            abstract=bool(re.search(r'\babstract\b|摘要',' '.join(b.get('heading_path',[]))+' '+text[:200],re.I))
            reference=any(h.strip().lower() in {'references','bibliography','参考文献'} for h in b.get('heading_path',[]))
            noise=bool(re.search(r'@|all rights reserved|copyright|arxiv:',text[:150],re.I))
            return (reference,not abstract,page>2,noise,page,-min(len(text),600),b['block_id'])
        block=min(candidates,key=rank)
        text=block['text'];start=0
        marker=re.search(r'\bAbstract\b\s*[:.—-]?\s*|摘要\s*[:：]?\s*',text[:250],re.I)
        if marker:start=marker.end()
        end=min(len(text),start+min(per_doc,remaining))
        if end<len(text):
            stop=max(text.rfind('. ',start+max(60,(end-start)//2),end),text.rfind('。',start+max(60,(end-start)//2),end))
            if stop>start:end=stop+1
        segments=[(start,end)]
        if end<len(text) and len(text)-start>per_doc:
            head_end=min(len(text),start+max(60,per_doc//3))
            tail_start=max(head_end,len(text)-(per_doc-(head_end-start)))
            segments=[(start,head_end),(tail_start,len(text))]
        excerpt=text[start:end]
        if not excerpt.strip():entry['reason']='代表片段为空';continue
        src=dict(block['source'],doc_id=doc,revision=artifact['manifest']['revision'],
                 source_bundle_id=bundle_id,source_block_id=block['block_id'])
        for left,right in segments:
            excerpt=text[left:right]
            row=dict(first,chunk_id=None,evidence_kind='overview_excerpt',text=excerpt,type=block['type'],
                     heading_path=block.get('heading_path',[]),
                     block_ranges=[{'block_id':block['block_id'],'start':left,'end':right}],sources=[src],
                     content_origin=[block.get('content_origin') or {'ocr_text':'ocr','vision_transcription':'model_transcription'}.get(block['type'],'native_text')])
            row['sources']=resolve_sources(row,storage)
            results.append(row);remaining-=len(excerpt)
        entry['excerpted']=True
        entry['page_index']=src.get('page_index')
    return {'status':'ok','index_version':manifest['index_version'],'query_id':'overview_'+manifest['index_version'],
            'results':results,'documents':documents,
            'coverage':{'mode':'document_overview','eligible_documents':len(documents),
                        'represented_documents':sum(d['excerpted'] for d in documents),'omitted_documents':sum(not d['excerpted'] for d in documents),
                        'excerpt_chars':budget-remaining,'method':'verified abstract/early-body head and contribution tail per document; exact spans kept separately'},
            'note':'这是逐文档摘要/正文代表片段，不是全文阅读。非论文材料也保留文件名，须分别说明。'}
