"""Offline V1 corpus selection, review/freeze gate, version-pinned retrieval evaluation.

Run using DocBase/.conda/python.exe. Does not call answer-generating models.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'DocBase/src'))
DATASET = ROOT/'evaluations/datasets/v1'

def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def rows(path):return [json.loads(x) for x in Path(path).read_text(encoding='utf-8-sig').splitlines() if x.strip()]

def seed(destination=DATASET):
    destination=Path(destination)
    if destination.exists() and any(destination.iterdir()): raise ValueError('题集目录非空，不覆盖已有标注')
    db=sqlite3.connect(ROOT/'ResearchDesk/data/app.sqlite');db.row_factory=sqlite3.Row
    source=[]
    for row in db.execute("SELECT d.name,m.artifact,k.collection,k.index_version FROM members m JOIN docs d ON d.id=m.doc JOIN kbs k ON k.id=m.kb WHERE k.deleted=0 AND m.active=1 AND k.name LIKE 'Zotero%' AND m.artifact IS NOT NULL"):
        item=dict(row);root=Path(item['artifact']);m=read(root/'manifest.json')
        blocks=rows(root/'blocks.jsonl')
        prose=[b for b in blocks if b['type']=='paragraph' and b['status']=='ok' and 150<len(b['text'])<2500
               and not any('references'==h.lower().strip() for h in b.get('heading_path',[]))]
        if not prose:continue
        item.update(doc_id=m['doc_id'],revision=m['revision'],pages=m.get('page_count') or 0,
                    manifest_sha256=digest(root/'manifest.json'),blocks_sha256=digest(root/'blocks.jsonl'),
                    partial=m['status']=='partial',image_blocks=sum(b['type']=='image' for b in blocks),
                    prose=prose,blocks=blocks,layout='pending_manual_review')
        source.append(item)
    db.close()
    source.sort(key=lambda x:(x['pages'],x['doc_id']))
    chosen=[]
    clip=next((x for x in source if 'CLIP-Adapter' in x['name']),None)
    if clip:chosen.append(clip)
    for i in range(12):
        candidate=source[round(i*(len(source)-1)/11)]
        if candidate not in chosen:chosen.append(candidate)
    for item in source:
        if len(chosen)>=12:break
        if item not in chosen:chosen.append(item)
    chosen=chosen[:12]
    if len(chosen)<12:raise ValueError('可用论文不足12篇')
    corpus=[{k:v for k,v in x.items() if k not in {'blocks','prose'}} for x in chosen]
    save(destination/'corpus.json',corpus)
    categories=[('fact',20,7),('explanation',15,5),('cross_document',10,3),('figure',5,2),('unanswerable',10,3)]
    output={'dev':[],'test':[]};number=0
    for category,count,dev_count in categories:
        for i in range(count):
            item=chosen[(number+i)%len(chosen)]
            related=[item]
            if category=='cross_document':related.append(chosen[(chosen.index(item)+1)%len(chosen)])
            gold=[]
            for doc in related:
                candidates=doc['prose']
                if category=='figure':
                    captions=[b for b in candidates if any(w in b['text'].lower() for w in ('figure','table','fig.'))]
                    candidates=captions or candidates
                block=candidates[i%min(len(candidates),8)]
                gold.append({'doc_id':doc['doc_id'],'revision':doc['revision'],'source_block_id':block['block_id'],
                             'page_index':block['source'].get('page_index'),'quote':block['text'][:200],
                             'review_status':'candidate'})
            title=item['name'].removesuffix('.pdf')
            stems={'fact':['研究对象与问题是什么？','提出了哪些关键组成部分？','使用了什么评价指标？','实验采用了哪些数据集？','核心模型以什么作为输入？','方法的输出形式是什么？','有哪些需要训练的参数？','预训练模块如何处理？','损失函数用于什么目的？','采用什么训练设置？','基线方法有哪些？','消融实验考察什么因素？','推理阶段需要什么资料？','对数据标注有什么要求？','主要假设是什么？','采用了什么编码器？','作者列出的贡献是什么？','实验使用什么划分？','算法包含哪些阶段？','给出了哪些计算成本信息？'],
                   'explanation':['为什么提出该方法，其动机是什么？','关键机制如何工作？','方法的适用条件与局限是什么？','如何处理监督信息不足？','关键模块怎样相互配合？','为什么采用该评价设置？','消融结果支持什么判断？','如何避免表征退化？','数据假设如何影响结论？','方法在训练和推理时有何差异？','现有方法的不足是什么？','如何利用预训练知识？','实验条件对比较有何限制？','作者如何解释主要结果？','哪些结论需要额外证据？'],
                   'figure':['定位一幅解释方法流程的图，并说明其主要关系。','定位一个结果表，说明比较条件及能得出的结论。']}
            if category=='cross_document':
                question=f'比较《{title}》与《{related[1]["name"].removesuffix(".pdf")}》在{["问题与方法","数据和监督条件","评价协议","局限与适用范围","计算与训练设置"][i%5]}方面的异同，证据不足处说明。'
            elif category=='unanswerable':
                question=f'仅依据《{title}》，'+['给出论文未披露的训练集每个样本的完整SHA256。','报告该方法在2100年新增真实数据上的实测准确率。','给出作者未公开的后续实验日志全文。','列出论文未包含的每个随机种子的逐样本预测。','证明该方法在所有可能数据分布上都最优。','给出与本库未收录方法的完整同条件实测比较。','恢复论文未公布的原始受试者私密记录。','提供作者未来十年的所有研究结论。','给出本文未披露硬件上经过实测的精确能耗。','保证该模型在任何未知任务上的错误率为零。'][i]
                # Out-of-corpus candidates are deliberately labelled pending, not proven negatives.
                gold=[]
            else:question=f'《{title}》'+stems[category][i%len(stems[category])]
            number+=1
            row={'question_id':f'v1_{number:03}','question':question,'category':category,
                 'allowed_doc_ids':[x['doc_id'] for x in related], 'gold_evidence':gold,'answer_points':[],
                 'answerability':'unanswerable' if category=='unanswerable' else 'answerable',
                 'review':{'status':'pending','reviewer':None,'notes':'自动候选：核对问题、原文证据、答案要点和不可回答性后才能冻结。'}}
            output['dev' if i<dev_count else 'test'].append(row)
    for name,items in output.items():
        (destination/(name+'.jsonl')).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in items),encoding='utf-8')
    save(destination/'status.json',{'status':'draft','counts':{k:len(v) for k,v in output.items()},
         'note':'60道自动候选，未人工审核，尚无冻结金标准；不能报告正式准确率。'})
    return {'status':'draft','corpus':len(corpus),'dev':len(output['dev']),'test':len(output['test'])}

def validate(directory, reviewed=False):
    directory=Path(directory);allrows=rows(directory/'dev.jsonl')+rows(directory/'test.jsonl')
    if len(allrows)!=60 or len({r['question_id'] for r in allrows})!=60:raise ValueError('需要60个唯一问题')
    if len(rows(directory/'dev.jsonl'))!=20:raise ValueError('开发/测试应为20/40')
    expected={'fact':20,'explanation':15,'cross_document':10,'figure':5,'unanswerable':10}
    if dict(Counter(r['category'] for r in allrows))!=expected:raise ValueError('题型分布不符')
    # Avoid question/paraphrase leakage: human reviewer must verify paraphrase groups as well.
    if len({r['question'] for r in allrows})!=60:raise ValueError('存在重复问题')
    for r in allrows:
        if reviewed:
            review=r['review']
            if review.get('status')!='approved' or not review.get('reviewer'):raise ValueError(f"{r['question_id']} 尚未人工审核")
            if r['answerability']=='answerable' and (not r['gold_evidence'] or not r['answer_points']):raise ValueError('有答案题缺少证据或要点')
        if not r['allowed_doc_ids']:raise ValueError('资料范围不能为空')
    if reviewed:
        corpus={x['doc_id']:x for x in read(directory/'corpus.json')}
        sources={}
        for doc,item in corpus.items():
            root=Path(item['artifact'])
            if digest(root/'manifest.json')!=item['manifest_sha256'] or digest(root/'blocks.jsonl')!=item['blocks_sha256']:
                raise ValueError('标注来源快照已变化')
            sources[doc]={b['block_id']:b for b in rows(root/'blocks.jsonl')}
        for question in allrows:
            if not set(question['allowed_doc_ids']).issubset(corpus):raise ValueError('题目范围超出登记语料')
            if question['answerability'] not in {'answerable','unanswerable'}:raise ValueError('无效answerability')
            for gold in question['gold_evidence']:
                doc=gold['doc_id'];block=sources.get(doc,{}).get(gold.get('source_block_id'))
                if doc not in question['allowed_doc_ids'] or not block:raise ValueError('金标准来源块不存在或不在题目范围内')
                if gold.get('revision')!=corpus[doc]['revision'] or not gold.get('quote') or gold['quote'] not in block['text']:
                    raise ValueError('金标准版本或原文不匹配')
                if gold.get('page_index')!=block['source'].get('page_index'):raise ValueError('金标准页码不一致')
    return allrows

def freeze(directory):
    directory=Path(directory)
    validate(directory,reviewed=True)
    file=directory/'frozen.json'
    if file.exists():raise ValueError('此版本已冻结；修订需新建数据集版本')
    save(file,{'schema_version':'eval-v1','frozen_at':time.time(),
               'files':{n:digest(directory/n) for n in ['dev.jsonl','test.jsonl','corpus.json']}})
    return {'status':'frozen'}

def baseline(directory,split='test',allow_draft=False):
    from docbase.config import Settings
    from docbase.engine import query
    from docbase.evaluator import matches
    directory=Path(directory)
    validate(directory,reviewed=not allow_draft)
    frozen=directory/'frozen.json'
    if not allow_draft:
        if not frozen.is_file():raise ValueError('未冻结题集；仅可显式 --allow-draft 做候选诊断')
        for name,sha in read(frozen)['files'].items():
            if digest(directory/name)!=sha:raise ValueError('冻结题集发生变化')
    corpus=read(directory/'corpus.json');version=corpus[0]['index_version'];collection=corpus[0]['collection']
    cfg=Settings.load(ROOT/'DocAgent/configs/docbase-context.env')
    cfg.profile='bm25-only'
    started=time.time();records=[]
    for r in rows(directory/(split+'.jsonl')):
        t=time.monotonic()
        try:
            response=query(r['question'],ROOT/'DocBase/storage',cfg,collection=collection,index_version=version,
                           profile='bm25-only',top_k=10,prefer_body=True,allowed_doc_ids=r['allowed_doc_ids'])
            covered=[i for i,gold in enumerate(r['gold_evidence']) if any(matches(hit,gold) for hit in response['results'])]
            records.append({'question_id':r['question_id'],'status':'ok','hit':bool(covered),
                            'covered':len(covered),'gold_count':len(r['gold_evidence']),
                            'seconds':time.monotonic()-t,'query_id':response['query_id'],
                            'ranked_chunk_ids':[x['chunk_id'] for x in response['results']],
                            'answerable':r['answerability']=='answerable','human_correct':None,'human_support':None})
        except Exception as exc:
            records.append({'question_id':r['question_id'],'status':'failed','error_type':type(exc).__name__,
                            'answerable':r['answerability']=='answerable','hit':False,'covered':0,'gold_count':len(r['gold_evidence'])})
        print(f"{len(records)}: {r['question_id']} {records[-1]['status']}",file=sys.stderr,flush=True)
    scored=[r for r in records if r['answerable']]
    git=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True)
    result={'status':'provisional' if allow_draft else 'evaluated','split':split,'index_version':version,
            'collection':collection,'profile':'bm25-only','code_commit':git.stdout.strip() or None,
            'working_tree_dirty':bool(subprocess.run(['git','status','--porcelain'],cwd=ROOT,capture_output=True,text=True).stdout.strip()),
            'dataset_sha256':digest(directory/(split+'.jsonl')),'started':started,'seconds':time.time()-started,
            'records':records,'metrics':{'question_hit_at_10':sum(r['hit'] for r in scored)/len(scored) if scored else None,
                 'evidence_recall_at_10':sum(r['covered'] for r in scored)/sum(r['gold_count'] for r in scored) if sum(r['gold_count'] for r in scored) else None,
                 'answerable_count':len(scored),'failed':sum(r['status']=='failed' for r in records)},
            'note':'候选诊断不是人工金标准成绩。无答案题不由检索命中判断拒答；未执行外部模型问答。' if allow_draft else '仅检索评估；回答正确性、拒答与支持率需要另行模型运行及人工标注。'}
    folder=ROOT/'evaluations/runs'/('retrieval_'+uuid.uuid4().hex[:16])
    save(folder/'report.json',result)
    return {'report':str(folder/'report.json'),'status':result['status'],'metrics':result['metrics']}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['seed','validate','freeze','baseline'])
    p.add_argument('--dataset',type=Path,default=DATASET)
    p.add_argument('--split',choices=['dev','test'],default='test')
    p.add_argument('--allow-draft',action='store_true')
    a=p.parse_args()
    try:
        if a.command=='seed':result=seed(a.dataset)
        elif a.command=='validate':result={'count':len(validate(a.dataset))}
        elif a.command=='freeze':result=freeze(a.dataset)
        else:result=baseline(a.dataset,a.split,a.allow_draft)
    except (ValueError,OSError,KeyError) as exc:raise SystemExit(str(exc))
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
