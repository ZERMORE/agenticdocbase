"""Opt-in deterministic terminology addition; never modifies scope or replaces the question."""
TERMS={'少样本':'few-shot','零样本':'zero-shot','视觉语言':'vision language','对比学习':'contrastive learning','提示学习':'prompt learning','图像分类':'image classification','过程监督':'process supervision','推理加速':'inference acceleration','病理':'pathology'}
def expand_query(query,mode='off'):
    if mode!='glossary':return query
    additions=[term for key,term in TERMS.items() if key in query and term.casefold() not in query.casefold()]
    return (query+'\n'+' '.join(additions))[:12000] if additions else query
