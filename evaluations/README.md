# V1 质量评估

从项目根目录的 CMD 运行：

```bat
DocBase\.conda\python.exe -X utf8 workflows\quality_eval.py seed
DocBase\.conda\python.exe -X utf8 workflows\quality_eval.py validate
DocBase\.conda\python.exe -X utf8 workflows\quality_eval.py baseline --split dev --allow-draft
```

seed 拒绝覆盖已有目录。当前 v1 是 12 篇真实快照、60 道自动编制候选，开发20/测试40，不能当人工金标准。自动候选证据只是供审阅的线索，可能不充分支持问题，必须核对并替换。版式类型待人工登记。

## 人工审核与冻结

审阅 `datasets/v1/corpus.json` 和 dev/test.jsonl：对照原文填写 answer_points，修正 gold_evidence（doc_id/revision/source_block_id/page_index/quote；此处 page_index 沿用来源1起始页码），确认资料范围和不可回答性。跨文档题要覆盖所有必需论文。图表题若离线缺失语义可标不可回答并调整题型配额，不能凭模型描述猜测。

将每题 review.status 设为 approved、review.reviewer 填实际审阅者，并填写 notes。不要直接批量改为 approved。无答案题应多样化，替换模板式口令问题，核验库外、未披露条件、超出资料范围和相矛盾信息；同义改写应保持在同一划分。

```bat
DocBase\.conda\python.exe -X utf8 workflows\quality_eval.py freeze
DocBase\.conda\python.exe -X utf8 workflows\quality_eval.py baseline --split test
```

冻结保存数据集 SHA，修改后拒绝正式评测，需创建新版本。固定索引来自 corpus.json，不跟随 CURRENT。正式评估前补充人工审阅的规范金标准、题型覆盖、否定题与错误引用样本；候选诊断不得用于调参后再宣称同集泛化成绩。

报告记录全部失败、分母、耗时、提交ID和未提交状态、配置和索引版本。question_hit_at_10 是有答案题中至少找到一处标注证据的比例；evidence_recall_at_10 是全部金标准证据条目的覆盖比例。它们不等于回答正确率或拒答率。

DocAgent 原有 evaluate 可运行模型问答，但真实评估需明确预算、库级外部处理授权和人工支持性打分；本轮构建默认只跑离线检索，不发送论文到供应商。推荐三次重复记录模型返回名、预算、支持检查模式、人工正确性与支持性，报告均值、范围及错例。
