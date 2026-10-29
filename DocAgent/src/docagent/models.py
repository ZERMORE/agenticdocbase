from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Search(StrictModel):
    query: str = Field(min_length=1, max_length=3000)
    top_k: int = Field(default=8, ge=1, le=10)
    prefer_body: bool = True


class Evidence(StrictModel):
    evidence_id: str = Field(pattern=r'^E[1-9][0-9]*$')


class Context(Evidence):
    before: int = Field(default=1, ge=0, le=2)
    after: int = Field(default=1, ge=0, le=2)


class Support(StrictModel):
    evidence_id: str = Field(pattern=r'^E[1-9][0-9]*$')
    quote: str = Field(min_length=2, max_length=1200)


class Claim(StrictModel):
    text: str = Field(min_length=1, max_length=3000)
    kind: Literal['direct', 'inference'] = 'direct'
    supports: list[Support] = Field(min_length=1, max_length=8)


class Submission(StrictModel):
    status: Literal['answered', 'insufficient_evidence']
    claims: list[Claim] = Field(max_length=30)
    limitations: list[str] = Field(max_length=20)
    mermaid: str | None = Field(default=None, max_length=12000)


TOOL_MODELS = {'search_knowledge': Search, 'get_evidence': Evidence, 'get_context': Context,
               'submit_answer': Submission}
DESCRIPTIONS = {
    'search_knowledge': '检索指定知识库；中英文关键词可补检。排名不是事实支持。',
    'get_evidence': '核验本次已返回证据的原始切片。',
    'get_context': '补取已返回证据前后有效块；不能任意打开文件。',
    'submit_answer': '提交中文答案：逐条claim附原文连续短引文；证据不足如实说明。',
}


def tool_definitions():
    return [{'type': 'function', 'function': {'name': name, 'description': DESCRIPTIONS[name],
            'parameters': model.model_json_schema()}} for name, model in TOOL_MODELS.items()]
