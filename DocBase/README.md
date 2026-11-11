# DocBase：doc2md 图文知识库与检索

DocBase 将 doc2md 产物编入本地知识库，支持 BM25 检索、混合检索、上下文补取和原文定位。BM25 可离线使用。混合检索需要可用的 Embedding 和 Reranker 配置。

## 1. 环境隔离

| 项目 | Conda 环境 | 配置 | 数据 |
|---|---|---|---|
| doc2md | `doc2md\.conda` | `DOC2MD_*` / 自身 `.env` | 解析产物 |
| DocBase | `DocBase\.conda` | `DOCBASE_*` / 自身 `.env` | 本项目 `storage` |

两边通过 JSON 文件交接，不混装依赖。安装脚本保留已有 `.env`；不修改 doc2md 的代码、配置或环境。后台可分别调用两个 `run.cmd`，无需把全部库装进一个环境。

## 2. 离线示例

先在仓库根目录运行一次安装脚本。从根目录的 CMD 或 Anaconda Prompt 执行：

```bat
cd doc2md
run.cmd parse "examples\sample.pdf" --offline --output "output\smoke"
cd ..\DocBase
run.cmd doctor
run.cmd scan --input "..\doc2md\output\smoke"
run.cmd build --profile bm25-only --collection default
run.cmd query --profile bm25-only --collection default --question "1729" --top-k 5
```

命中结果包含原文快照和页码。单独安装 DocBase 时，在本目录运行 setup.cmd。PowerShell 里使用 .\run.cmd。

scan 输入也可换成其他 doc2md 输出根目录或单篇完整产物目录。有多个历史版本时编辑 `storage/catalog.json`，同一 doc_id 只启用一个 `active: true`；扫描不会擅自删除旧登记。停用文档时显式设 active 为 false，再建库。

后台或手动重跑后，可直接使用 `run.cmd select --artifact "doc2md 本次结果中的 output 完整路径"`，显式选择该 doc_id 的当前产物，不用手工修改 JSON。随后 build --incremental；其他文档不受影响。

## 3. 真实 hybrid 模型

已有 `.env` 保持不变，默认选择 Qwen3-Embedding-8B 与 Qwen3-Reranker-8B。对应配置：

```dotenv
DOCBASE_PROFILE=hybrid
DOCBASE_EMBED_BASE_URL=https://ai.gitee.com/v1
DOCBASE_EMBED_MODEL=Qwen3-Embedding-8B
DOCBASE_EMBED_API_KEY=你的Key
DOCBASE_RERANK_BASE_URL=https://ai.gitee.com/v1
DOCBASE_RERANK_MODEL=Qwen3-Reranker-8B
DOCBASE_RERANK_API_KEY=你的Key
```

base_url 默认追加 `/embeddings` 和 `/rerank`；填完整对应路径时不重复追加。可通过 `DOCBASE_EMBED_PATH` / `DOCBASE_RERANK_PATH` 自定义。Embedding 请求用 model/input，响应需 data[index,embedding]；Reranker 请求用 model/query/documents/top_n，响应需 results[index,relevance_score]。服务格式不同需适配客户端。

平台权限及资源可用后执行（会调用模型）：

```bat
run.cmd probe --capability embedding
run.cmd probe --capability reranker
run.cmd build --profile hybrid --incremental
run.cmd query --profile hybrid --question "资料中的版本号是什么？" --top-k 5
```

probe 绕过历史缓存，仅发送少量合成短句。不会打印 Key 或完整向量。MODEL_QUOTA 代表平台账户资源问题，不能用截图免费标价当成接口已可用的证据。

hybrid 查询前先成功构建 hybrid 快照；资源不足时请显式使用 `--profile bm25-only`，无须改写 `.env`。构建失败不覆盖旧版。

## 4. 模式和稀疏扩展

| profile | 执行内容 |
|---|---|
| bm25-only | 本地 BM25Plus |
| hybrid | 稠密向量 + BM25 → RRF → Reranker |
| full | 稠密向量 + BM25 + BGE-M3 sparse → RRF → Reranker |

full 不自动降级。默认 sparse 为 unconfigured，因此 full 会明确报未配置。

本地稀疏选项：设置 `DOCBASE_SPARSE_BACKEND=local`、`DOCBASE_SPARSE_MODEL=BAAI/bge-m3`，在该环境安装 `.[sparse]` 及权重。此选项代码已提供，但没有自动安装/下载，也未在本机真实推理验证。

HTTP 稀疏选项：设置 backend=http 及 sparse base_url/model/Key，可自定义 `DOCBASE_SPARSE_PATH`。约定请求 model/input/return_sparse，响应 `lexical_weights: [{"token_id": weight}, ...]`；这是明确的外部 BGE 服务契约，不假定普通 Gitee embedding 接口具有此功能。

## 5. 切片与参数

初版按标题和原始 block 结构切片；超长正文滑窗、长表格拆行并重复表头，保存字符范围和来源。尚不具备通用语义分段、复杂跨页表格自动恢复能力。

未配置 tokenizer 时默认 **800 字符/片、重叠上限 100 字符**，报告 `length_unit=characters`，不能称为 tokens。配置精确 tokenizer JSON、安装 `.[tokenizer]` 后可使用真实 token 预算。可选环境变量：

```text
DOCBASE_CHUNK_SIZE=800
DOCBASE_OVERLAP=100
DOCBASE_TOKENIZER_PATH=
DOCBASE_RECALL_K=30
DOCBASE_RRF_K=60
DOCBASE_RERANK_CANDIDATES=50
DOCBASE_BATCH_SIZE=16
DOCBASE_TIMEOUT=90
DOCBASE_RETRIES=2
DOCBASE_DICTIONARY_PATH=
DOCBASE_MODEL_REVISION=user-managed-v1
```

精排候选上限至少覆盖 top_k；模型/指令/分词/切片变化会要求重建相应索引。模型提供方滚动更新同名模型时，需主动更改 model_revision 以使缓存失效。词典可按 jieba 用户词典格式配置。

不使用固定的通用分数阈值判断无答案。dense 会返回最相似候选，非空不等于证据充分，相关性分数也不是答案准确率。

## 6. 输入、更新及历史证据

正式输入为 manifest.json + blocks.jsonl，不从 Markdown 的 HTML 标记猜来源。只索引 status=ok 的正文、表格、OCR/模型转录和图片释义；图片占位、pending 和错误提示排除。partial 文档可使用有效块并带警告，`build --quality strict` 只接收整体 ok。

artifact_id 包含 blocks 摘要，因此原文未改但图片释义补齐也会识别更新。source_store 复制并校验原文/图像快照，避免 doc2md 重跑导致旧引用失效。

```bat
run.cmd build --profile bm25-only --incremental
run.cmd get --chunk-id "真实chunk_id" --index-version "真实index_version"
run.cmd status
```

未变化返回 action=unchanged；变化时复用编码缓存并重建快照，发布时原子替换 CURRENT，保留旧索引和来源。查询固定同一版本。进程被强制结束后若出现 build.lock，确认原进程已退出再仅移除对应集合的锁。

## 7. 测试与评价

```bat
.\.conda\python.exe -m pytest -q
run.cmd test --profile bm25-only --dataset ".\eval\smoke.jsonl"
```

自动测试不调用真实 API。smoke 题集针对当前 sample.pdf，仅用于链路检查，不代替真实资料上的质量评估。自定义题集为 JSONL：

```json
{"qid":"q1","question":"版本号是多少？","answerable":true,"relevant":[{"doc_id":"真实doc_id","page_index":1,"quote":"1729"}]}
```

证据需 doc_id，并给出 quote 或 source_block_id+start/end；可加 revision、页、图等限制。无答案题 answerable=false，不进入可回答题指标。输出 Hit@K、Recall@K、MRR@K 及逐题报告。检索不代替下一阶段的 Agent 作答/拒答。

## 8. 运行产物及整合接口

`storage` 下包含 catalog、source_store、caches、indexes、collections/CURRENT 和 reports。完整 JSON 不含 Key；stdout 只输出 JSON，阶段进度写 stderr。

查询输出 query_id、index_version、profile、results、diagnostics、report_path；结果包含 chunk_id、正文、block_ranges、sources、质量警告与评分。`get` 支持按索引版本取证据。下一阶段 Agent/Service 可分别启动独立环境，再消费 JSON，不要求 Claude Code 参与本阶段运行。

整合示例及稳定文件交接方式见 `整合接口说明.md`。

Python API：`build_index(catalog, storage, settings, ...)`、`query(question, storage, settings, ...)`、`get_evidence(...)`、`eval_retrieval(...)`。

默认 storage 固定为本工程目录；显式相对参数以调用者当前目录为准。退出码：0 完整执行（含正常零命中）、1 输入/模型/索引错误、2 质量警告、130 中断。Conda 可能为非零返回追加自己的错误提示，具体原因看工具 JSON。
