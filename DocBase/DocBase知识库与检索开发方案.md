# DocBase 知识库与检索工具独立开发方案

编制日期：2026-10-26。

模型选型更新：2026-10-28，依据用户提供的免费模型截图明确默认模型及 hybrid/full 模式，详见 3.4。

工程目标目录：`C:\Users\Lenovo\Desktop\研一\大模型软件开发\DocBase`，与现有 `doc2md` 并列。

**本文件保留开发设计基线。2026-10-28 已按后续授权构建代码及独立 Conda 环境；实际支持的命令、实现差异、测试与模型联调状态以 README.md、验证记录.md 为准。** 方案依据现有课件、四阶段实验方案，以及已核对的 doc2md 源代码和样例产物制定。

## 一、开发目标与边界

DocBase 接收 doc2md 已经完成的图文解析结果，构建本地持久化知识库。用户以自然语言、技术名词或接口名查询，工具通过三路检索、融合和精排返回相关文本及其原文出处。

完整检索流程：

**doc2md 解析产物 → 产物登记与质量检查 → 图文切片 → 稠密向量 / BM25 / BGE-M3 稀疏三路召回 → RRF 融合 → Reranker 精排 → Top-K 证据及来源。**

本阶段实现：多文档与分类管理、build/query/test 三种主要运行模式、索引持久化、增量处理、检索质量评估、可供 Agent 调用的 JSON 接口。开发中可先使用 hybrid 双路加精排；课件的三路完整验收仍以 full 为准。

阶段边界：

- doc2md 继续负责 PDF/Office/图片解析、OCR 和 Qwen 图片释义，DocBase 不重新解析原始文档。
- DocBase 输出证据，不直接承担最终答案生成，也不在本阶段强制接入 Chat 模型。
- AGENTS.md 阶段负责 Agent 如何检索、补检、回答及引用；第四阶段负责后台目录监控与 Web。
- 工具本身由普通 Python 程序执行，不要求 Claude Code 常驻或逐条决定检索操作。

## 二、与当前 doc2md 的真实交接方式

### 2.1 已核对的输入结构

当前 doc2md 的单文档产物结构为：

```text
doc2md/output/<doc_id>/<revision>/<config_fingerprint>/
  manifest.json
  blocks.jsonl
  document.md
  parse.log.jsonl
  images/
  source/
```

`manifest.json` 中已有：`schema_version、tool_version、doc_id、revision、config_fingerprint、status、source_path、source_snapshot、rendered_pdf、mode、page_count、block_count、unfinished_blocks、warnings、created_at` 等字段。

`blocks.jsonl` 中已有：

| 字段 | 当前含义 | DocBase 的使用方式 |
|---|---|---|
| doc_id | 按输入来源路径生成的文档 ID | 文档身份，不等于文件内容哈希 |
| revision | 原文件 SHA256 | 原文版本 |
| block_id | 解析块序号标识 | 追踪证据；不能单独代表所有解析配置 |
| type | 内容块类型 | 决定是否入库及如何切片 |
| text | 原生文字、表格或图片释义 | 候选检索文本 |
| heading_path | 当前恢复的标题路径 | 上下文前缀及切片边界；不假定标题层级完全准确 |
| category | 解析时设置的类别 | 检索过滤；默认“未分类” |
| source | 原路径、快照、页/幻灯片、锚点、坐标、图片及图序 ID | 建立完整溯源链 |
| status | ok / pending / failed | 内容质量过滤 |
| vision、model 等可选字段 | 图片结构及解释模型 | 图片知识审查与来源属性 |

`source` 中已有 `original_path、snapshot_path、rendered_pdf_path、page_index、printed_page_label、slide_number、anchor、bbox、image_path、figure_id`。其中相对文件路径以该文档产物目录为根；对外页序从 1 开始；原生 DOCX 后备解析的页码可能为空，必须使用段落锚点，不补造页码。

### 2.2 入库必须处理的实际问题

**问题一：不能把状态提示作为知识。**

默认只索引 `status=ok` 的有效内容。入库规则如下：

| type / 状态 | 处理规则 |
|---|---|
| heading、paragraph，且 ok | 保留为结构或正文；空白页提示不作正文知识 |
| table，且 ok | 保留表头、单元与来源，按表格结构切片 |
| ocr_text、vision_transcription，且 ok | 作为文字证据，标记内容来自 OCR/模型转录 |
| figure_description，且 ok | 作为图片语义证据，保留模型来源和不确定项 |
| image | 作为图片与来源附件，不把“文档图像”“扫描页”等占位文字编码为知识 |
| pending / failed | 排除出检索正文，计入未入库清单与缺失图像报告 |

文档整体 `status=partial` 不意味着所有块都不可用。默认允许导入其中有效块，但必须在文档和查询结果中显示质量警告；正式评估可用 `--quality strict` 只接受整体 ok 的文档。整篇没有有效知识块时报告空语料，不生成“成功但不可查询”的知识库。

**问题二：不能把所有历史目录都当成当前知识。**

同一 doc_id 可能有多个 revision 和 config_fingerprint；即使二者都没变化，用户补填 Key、重跑解析，也可能使 blocks.jsonl 从 pending 变成完整释义。

因此建立输入登记清单 `catalog.json`，显式选择每份逻辑文档的当前有效产物。初版按以下原则实现：

1. `scan` 仅发现候选产物、状态和版本，不自动把全部历史版本纳入正式集合。
2. 单个 doc_id 只有一个已登记候选时可直接选中；有多个候选时要求登记选择，或报版本冲突，不静默按目录名/mtime 选“最新”。
3. 一份集合中每个 doc_id 默认只启用一个原文版本及解析配置；跨版本检索以后以显式集合实现。
4. 校验 manifest、blocks.jsonl 和来源文件的完整性，只读取已发布目录；忽略 `.work-*`、`.previous-*` 和缓存、环境目录。原文档有解析锁时跳过本次读取并报告。
5. 读取前后核对 manifest/blocks 的摘要，发现正在变化就中止该产物导入并重试，避免读到重跑替换中的混合结果。

建议产物身份：

```text
artifact_id = SHA256(doc_id + revision + config_fingerprint + blocks_sha256)
```

`block_id` 结合 artifact_id 才成为唯一的源块键。不能仅用 doc_id+revision 判断是否需要更新，也不能仅凭当前 block_id 跨解析配置去重。

**问题三：不要只保存指向可变产物目录的链接。**

初版导入时将所选 manifest、blocks、Markdown 及引用所需的图片/源文件复制到 DocBase 的内容寻址 source store，并校验哈希；索引保存其 source_bundle_id。这样 doc2md 重跑、同目录被替换后，旧回答仍能解释其所用证据。源文件可以按内容哈希复用，避免每个索引版本重复复制整份 PDF。

删除原始输入文件不会自动撤回已登记快照。阶段二通过 catalog 的停用/移除操作更新有效集合；第四阶段的目录监控再显式触发该操作。

## 三、所需资源与工具

### 3.1 基础环境与依赖

| 资源/工具 | 用途 | 初版安排 |
|---|---|---|
| Windows + Conda + Python 3.11 | 与当前使用方式一致 | DocBase 单独 `.conda` 环境，不修改 doc2md 环境 |
| NumPy | 稠密矩阵保存与精确检索 | 小规模实验先做精确点积搜索 |
| sqlite3 / SQLite | 文档、切片、来源及快照清单 | 使用 Python 对应环境提供的 SQLite 支持，doctor 检查 |
| rank_bm25 | BM25 相关性计算 | 固定版本并保留分词语料 |
| jieba + 术语词典 | 中文分词、技术词保留 | 文档和查询使用相同规则 |
| httpx、python-dotenv | 模型服务和独立配置 | 使用 DOCBASE_* 前缀，不覆盖 DOC2MD_* |
| tokenizer 资源 | 估算实际模型长度、切片和截断预算 | 优先固定所选 Embedding tokenizer 版本，仅下载 tokenizer 不要求下载整个模型 |
| pytest、人工题集 | 正确性、质量和更新检验 | Mock 测协议，真实模型测质量，两者分开 |

这里选择 SQLite + NumPy，是面向初版小规模、便于审查和复现的工程取舍，并非已测得它能满足任意规模。SQLite 可无需独立数据库服务运行；NumPy 支持矩阵点积。[Python 3.11 sqlite3 文档](https://docs.python.org/3.11/library/sqlite3.html)、[NumPy dot 文档](https://numpy.org/doc/stable/reference/generated/numpy.dot.html)

BM25 本身不替应用完成分词、大小写及术语归一化；这些策略需要统一并随索引版本记录。[rank_bm25 官方仓库](https://github.com/dorianbrown/rank_bm25)

规模估算先统计 N（chunk 数）和 D（实际向量维度）。float32 向量原始存储约为 `4×N×D` 字节。例如假定 N=10,000、D=4,096，矩阵约 156.25 MiB，另需元数据、稀疏索引及进程内开销；这里 D 只是容量计算示例，不代表已确认服务输出维度。超出预算后再评估分块扫描、内存映射或向量索引后端。

### 3.2 模型能力与当前资源状态

| 能力 | 课件参考配置 | 当前是否已确认 | 需要补齐什么 |
|---|---|---|---|
| 稠密 Embedding | Qwen3-Embedding-8B | 尚未确认可用服务 | 模型标识、API 格式、权限、维度、批量与长度限制、查询指令 |
| 学习型稀疏表示 | BGE-M3 | 尚未确认稀疏服务 | token ID/权重输出；或可运行的本地 FlagEmbedding 环境 |
| Reranker | Qwen3-Reranker-8B | 尚未确认可用服务 | 成对或列表请求、分数及原文索引映射、长度与批量限制 |
| 文档视觉理解 | Gitee Qwen3.6-Plus | 上一阶段 probe 已有成功反馈 | 沿用 doc2md，不由 DocBase 重复调用 |
| Chat 推理 | Claude Code 使用的 DeepSeek API | 用户已有配置 | 本阶段非必需，留给后续 Agent 问答 |

**不能假定上一阶段的 Qwen3.6-Plus Chat 接口同时提供上述三种检索模型能力，也不能默认其 API Key 对这些模型拥有权限。** 可以使用同一服务商，但每个能力应单独 probe、单独记录结果。

Qwen 官方分别发布了 Embedding 与 Reranker 用法；具体托管服务的地址、鉴权及请求格式应以实际服务为准，不能把官方本地示例当成某供应商已支持的 API。[Qwen Embedding 模型说明](https://huggingface.co/Qwen/Qwen3-Embedding-8B)、[Qwen Reranker 模型说明](https://huggingface.co/Qwen/Qwen3-Reranker-8B)

BGE-M3 官方示例使用 `return_sparse=True` 获取 `lexical_weights`，用于学习型词项匹配。普通 dense embedding 返回值不能代替它；文档与查询还必须使用相同 token ID 空间。[BGE-M3 模型说明](https://huggingface.co/BAAI/bge-m3)

### 3.3 资源选择顺序

推荐优先接可获得的远端 Embedding/Reranker；BGE-M3 有稀疏服务时调用服务，没有时再评估本地运行。基础环境不默认安装 PyTorch/CUDA 或下载完整模型权重，本地模型依赖作为独立 extras 或单独推理服务管理。

先取得一小批真实调用结果：2～3 个短文本的 embedding、一个查询与候选的 rerank、一个句子的 sparse 权重。确认能力之后再确定客户端字段和依赖版本。

资源暂未到位时仍可实现输入、切片、来源、BM25 和 Mock 协议测试，但应明确标为 bm25-only 或开发验证；不能报告“三路混合检索已完成”。

### 3.4 按免费模型列表明确默认选型

**选型时尚未实现运行代码；后续已构建，默认配置仍为下表。** 真实接口当前遇到账户资源限制，具体以验证记录为准，不能将选型表当成真实模型联调成功的证据。

| 用途 | 默认选择 | 选择依据 |
|---|---|---|
| 文本及图片释义向量化 | Qwen3-Embedding-8B | 用户截图标为免费；模型用途为文本嵌入，匹配现有 doc2md 文本输入 |
| 候选相关性精排 | Qwen3-Reranker-8B | 用户截图标为免费；用于查询与文本候选的相关性排序 |
| 精确关键词召回 | BM25 | 本地算法，不调用模型 API |
| BGE 稀疏通路 | 单独配置，未取得接口前不启用 | 截图中没有展示 BGE-M3，不能用其他 dense 模型冒充 sparse 输出 |

这两个默认模型名称与原课件方案一致，已作为截图免费列表中的选型写入 `.env.example`。实际 Key、权限和可用资源由对应平台账户决定。

Qwen3-Embedding-8B 官方说明为文本嵌入，最多支持 4096 维；托管端实际维度仍通过 probe 确认。Qwen3-Reranker-8B 为文本重排，两类接口不能互换。[Embedding 官方说明](https://huggingface.co/Qwen/Qwen3-Embedding-8B)、[Reranker 官方说明](https://huggingface.co/Qwen/Qwen3-Reranker-8B)

当前 doc2md 已将原图解析为转录、节点、边和自然语言释义，因此文本检索模型可以覆盖这部分图像知识。初版不选 VL Embedding；若以后要直接对原图做向量检索，再单独评估截图同样标为免费的 Qwen3-VL-Embedding。截图中的 VL Reranker 标有费用，未纳入本次免费方案。

备用候选是截图中免费的 Qwen3-Embedding-0.6B、Qwen3-Reranker-0.6B，或 Reranker-4B。只有在真实调用延迟、限流或质量对照需要时才切换；不根据参数量直接保证哪个远端服务更快，也不保证 8B 在用户题集上必然最好。

默认运行模式拟为 `hybrid`：**稠密向量 + BM25 → RRF → Reranker**。`full` 继续代表加入 BGE-M3 稀疏通路后的三路完整模式，满足课件实验要求时需补齐这项资源。

免费判断仅依据用户提供截图，尚未登录其账户验证免费额度、并发、模型权限及实时计费规则；不是无限量免费承诺。配置模板暂沿用用户已有 Gitee base_url，具体 Embedding/Reranker 的实际请求地址、请求体和鉴权仍以对应模型调用示例核实，不直接复用 Chat Completions 请求体。

## 四、环境与配置设计

### 4.1 环境组织

工程使用独立 `environment.yml`；Conda 管理 Python/pip，应用依赖由该环境中的 pip 按版本约束安装。默认前缀：

```text
C:\Users\Lenovo\Desktop\研一\大模型软件开发\DocBase\.conda
```

同时提供 `setup.cmd / run.cmd` 和 PowerShell 入口。鉴于当前用户使用 CMD/Anaconda Prompt，README 的主示例采用 `.cmd` 与英文双引号。脚本保留用户已有 `.env`，不自动调用真实模型，也不修改 Claude Code 配置。

### 4.2 配置示例

以下只是设计模板，不是可直接调用的已确认配置；真实 Key 均留空：

```dotenv
DOCBASE_PROFILE=hybrid
DOCBASE_EMBED_BASE_URL=https://ai.gitee.com/v1
DOCBASE_EMBED_MODEL=Qwen3-Embedding-8B
DOCBASE_EMBED_API_KEY=
DOCBASE_RERANK_BASE_URL=https://ai.gitee.com/v1
DOCBASE_RERANK_MODEL=Qwen3-Reranker-8B
DOCBASE_RERANK_API_KEY=
DOCBASE_SPARSE_BACKEND=unconfigured
DOCBASE_SPARSE_BASE_URL=
DOCBASE_SPARSE_MODEL=
DOCBASE_SPARSE_API_KEY=
DOCBASE_TOKENIZER_PATH=
```

使用检索配置文件记录非密钥参数：模式、超时、批量、重试、切片预算、Top-K、RRF 常数、精排候选数、质量策略、词典版本和缓存位置。实际向量维度经 probe 得到并写入索引，不擅自用空向量补齐失败项。

`hybrid` 为选定免费 Embedding/Reranker 接口可用后的默认模式，要求稠密、BM25 及精排；`full` 要求额外具备 BGE-M3 稀疏通路；`bm25-only` 用于早期基线。每个模式缺少其必需组件时默认失败，只有显式允许降级时才能返回 degraded，并列出缺失通路。主动选择 hybrid 不是意外降级，但也不是 full 三路模式。网络故障不伪装成零命中。

## 五、数据模型与输出契约

### 5.1 Document / Artifact / Chunk

| 对象 | 主要字段 |
|---|---|
| Document | doc_id、原文名称、原文来源、category、active |
| Artifact | artifact_id、doc_id、revision、config_fingerprint、blocks_sha256、解析质量、source_bundle_id |
| Chunk | chunk_id、artifact_id、source_block_ids、block_ranges、正文、检索文本、token_count、标题路径、来源列表、content_hash |
| IndexManifest | schema_version、index_version、collection、启用通路、全部配置指纹、源集合摘要、行数/维度、构建状态、组件校验和 |

`chunk_id` 根据 artifact_id、切片规则版本、块/文本范围和内容摘要稳定生成。不同来源的相同文本可复用编码缓存，但必须保留各自出处，不能为了去重丢掉文档关系。

`block_ranges` 记录一个 chunk 在源块中的起止位置。切片可以带标题前缀，但原文范围只描述真正来源，不把人工补充标题误记为原文正文。

### 5.2 查询结果

对后续 Agent 的最小输出约定：

```json
{
  "schema_version": "1",
  "query_id": "q_example",
  "index_version": "idx_example",
  "status": "ok",
  "profile": "full",
  "query": "用户问题",
  "results": [
    {
      "chunk_id": "chunk_example",
      "artifact_id": "artifact_example",
      "text": "可引用的完整证据文本",
      "content_origin": ["native_text", "model_description"],
      "scores": {"dense_rank": 2, "bm25_rank": 1, "sparse_rank": 3, "rrf": 0.0484, "rerank": 0.91},
      "sources": [
        {
          "doc_id": "doc_example",
          "revision": "完整原文哈希",
          "source_block_id": "原始块ID",
          "source_bundle_id": "bundle_example",
          "page_index": 12,
          "slide_number": null,
          "anchor": null,
          "figure_id": "figure_000005",
          "snapshot_path": "存储内对应的源文件路径",
          "image_path": "存储内对应的图片路径",
          "bbox": [50, 100, 500, 450]
        }
      ],
      "quality_warnings": []
    }
  ],
  "diagnostics": {"active_routes": ["dense", "bm25", "sparse"], "degraded": false}
}
```

上述分数和页码只是结构示例，不是实测结果。精排分数的范围取决于模型/服务，不能统一解释为答案正确概率；原始分数应保留但不直接跨通路相加。

正常返回空 results 是“当前检索未命中”，不自动等于“全库确定无答案”。输入无效、索引未就绪、模型故障使用独立错误码；后续 Agent 再判断是否补检。

## 六、具体开发流程

### B1：工程初始化与输入检查

**输入：** doc2md 产物目录或登记清单。

实现 schema 检查、源集合扫描、版本冲突检查、有效块过滤、partial 质量策略和来源文件校验。统计发现、选中、跳过、冲突的文档数及可索引块数。

**预期效果：** 正文、表格和已成功的图片释义可入库；pending 提示不进入索引；历史产物不重复加入。

**检验：** 使用含多个原文版本、离线/在线配置、pending 图片和缺文件的固定样例，逐项检查登记结果。文档重跑、仅 blocks 改变但 revision 未变时应被识别为新 artifact。

### B2：结构与语义切片

以标题、段落、表格和图片释义的结构边界优先切片，初始目标约 600 tokens、重叠约 100 tokens；实际参数在调参集确定后冻结。优先在同页/同章节合并短块，跨页时保留多个来源。短内容无需凑满目标长度。

- 标题作为上下文附在检索文本中，不单独制造大量只有标题的片。
- 表格保留表头；超长表格按行组拆分，每片重复表头并注明行范围，不切断单元事实。
- 同一 figure_id 的转录、释义和原图组成图像组；避免把共享转录重复编码两遍。超长图组可按结构字段拆分，保留共同父图及子范围。
- 文字规范化只处理必要空白和换行；接口名、数字、单位、下划线和大小写原貌保存在原始 text 中。
- 模型转录、OCR 和模型释义记录 content_origin；模型指出的不确定项保留，不自动改写成确定事实。
- tokenizer 不可用时可提供明确的字符预算开发模式，但不能把字符计数称为 tokens，也不能混入正式 token 切片评估。

**预期效果：** 每片可独立理解且能回到全部证据来源，图片知识不脱离正文。

**检验：** 短段落、超长段落、超长图组、长表格、多页片均有固定样例；检查长度上限、覆盖率、重叠范围、来源及同输入 ID 稳定性。对用户示例问题人工检查是否保留完整关键条件。

### B3：BM25 基线，先跑通可用链路

先使用无需模型的 BM25 验证“输入 → 切片 → 索引 → 查询 → 来源”完整链路。文档和查询使用同一 jieba/术语词典及拉丁字符规则。保留 SD5993、FE_HEART_Start 一类完整标识符；中文分词 token 和术语词不能无规则重复加入而人为增加词频。

初版对每个类别的有效子语料确定一致的 BM25 统计方式，过滤在 Top-N 选择前执行；各对照实验使用相同口径。记录空查询、纯标点和未登录术语行为。

**预期效果：** 在没有远端模型时即可检索精确名词及正文事实，证明管线和来源可用。

**检验：** 人工构造名词、缩写、带下划线命令、中文短问和类别隔离用例；检查词典变化触发 BM25 索引更新。

### B4：接入稠密向量检索

实现独立 EmbeddingClient，分别封装 `encode_documents` 和 `encode_queries`，遵守所选模型的查询指令和向量处理方式。批量请求、长度限制、返回 index 和输入的对应关系必须核对。

保存 float32 矩阵及 row_id→chunk_id 映射。采用同一空间下归一化向量的点积排序；拒绝 NaN、Infinity、零向量和维度不一致。类别筛选先得到有效行号，再在该范围计算/选 Top-N。

编码缓存键包含服务/模型及版本标识、输出维度、编码角色/指令、规范化规则、文本摘要和客户端编码版本；修改查询/文档编码策略不能混用旧缓存。服务没有固定模型版本时记录返回标识及探测时间，显式更换模型要重建相关索引。

**预期效果：** 不出现原文关键词的语义改写也能召回证据。

**检验：** Mock 检查请求与返回乱序映射；真实小样本检查维度和同义问题；重启后同配置结果可复现；不能用随机或手工“假向量”作为真实质量结果。

### B5：接入 BGE-M3 稀疏检索

实现 SparseClient，输出稀疏 token ID→权重，建立倒排表。文档和查询使用同一模型/tokenizer 版本，不能将 jieba 的词字符串当成 BGE token ID。对共同 token 计算权重乘积之和作为该通路分数。

过滤非数值、不合法 token 和非有限权重；词项裁剪若引入近似，应作为独立参数冻结并评估，初版优先保存完整有效输出。严格区分 BM25 统计词频检索与学习型稀疏表示。

**预期效果：** 形成真实的第三路候选，为词项及语义相关召回补充证据。

**检验：** 小型稀疏向量可手工算分；检查无共享 token、重复 token、错误模型版本、类别过滤及空输出。真实服务未具备 sparse 输出时，本步骤不验收为完成。

### B6：RRF 融合与去重

三路初始各召回 30 条，在同一有效语料范围内排名。先按 chunk_id 合并，再计算：

```text
RRF(d) = Σ_route 1 / (60 + rank_route(d))
```

排名从 1 开始，未被该通路召回的项贡献为 0。RRF 常数 60 是初始实验参数。并列分数采用稳定 ID 次序，保存每路候选和排名。

完全相同 chunk 只保留一项；同页多个不同证据不能因页码相同而删除。重叠片的近重复控制在检索后处理独立实现，保留所有 source 链；不以“同一文件”作为去重条件。

**预期效果：** 兼顾精确匹配与语义召回，不受三路原始分数尺度差异直接影响。

**检验：** 手工候选排名表验证融合结果、缺失通路、重复候选与稳定并列顺序；已知相关证据在每路均未召回时，明确属于召回失败，不归咎精排。

### B7：Reranker 精排与 Top-K 输出

融合后初始最多 50 条送入 Reranker，最终 K 默认为 5，可设为 8 等值。RerankerClient 按真实服务适配输入；精排返回的候选索引/ID必须与原候选对应，检查缺项、重复、乱序和服务输出分数方向。

长候选按照模型真实预算处理，不能无声截掉答案关键条件。必要时使用结构子片参与打分，并将结果映射回明确的证据子范围。记录实际提交的精排文本，便于解释分数。

不默认硬编码一个通用“相关性分数阈值”来判断无答案；如需阈值，只能在调参集上按当前模型校准。模型故障默认报告失败；显式降级时可返回融合结果，并标记 rerank 未执行。

**预期效果：** 相关证据在最终 Top-K 中优先出现，同时完整保留来源。

**检验：** 用固定候选测试映射正确性、截断和失败模式；真实题集比较融合与精排后指标，退化如实报告，不预设精排一定提升。

### B8：持久化、增量与快照发布

初版采用“增量解析/编码缓存复用 + 索引快照重建”，不承诺全部组件具备原位增量算法。

建议存储：

```text
DocBase/storage/
  catalog.json
  source_store/<bundle_id>/...
  caches/embedding/...
  caches/sparse/...
  indexes/<collection>/<index_version>/
    metadata.sqlite
    dense.npy
    rows.json
    bm25_corpus.jsonl
    sparse_postings.json
    index_manifest.json
  collections/<collection>/CURRENT.json
  reports/...
```

1. 读取登记集合，比较 artifact_id 和配置，识别新增、内容变化、重新释义及停用文档。
2. 复用未变化切片的编码；同文不同来源可复用编码但保留多份来源登记。
3. 重建当前有效集合的 BM25 语料统计、矩阵映射及稀疏倒排；报告实际重建成本。
4. 在独立 staging 目录完成组件，校验数量、来源和文件摘要。
5. 以单集合写锁保证只有一个 builder；成功后发布不可变索引版本，原子替换 CURRENT 指针文件。
6. 查询启动时读取一次 CURRENT 并固定该快照，整次查询不跨版本；失败/中断不覆盖旧版。
7. `--sync` 才按登记集合显式停用缺失项；输入目录不可达不解释为“所有资料已删除”。

源文件复制期间重新核对摘要。源库与历史快照不自动删除，后续清理必须避开被有效索引/引用使用的 bundle。初版不实现自动垃圾回收。

**预期效果：** 重启可查询，重跑不重复，文档增改删可控，旧证据可追溯。

**检验：** 在编码失败、文件损坏、构建中断和并发查询下验证 CURRENT 与旧索引仍可用；新增、更新和停用之后检查有效结果。清单应证明发生了哪些变化及复用了多少编码。

### B9：检索自测、日志与阶段进度

每次构建输出：发现/选中/跳过产物数，有效/排除块数，切片数，编码批次与缓存命中，三路索引完成状态、验证和发布结果。进度至少到“阶段+批次”，不能只打印文件名后长时间无反馈。

查询输出三路耗时、候选数、融合/精排耗时和模式；完整 JSON 结果写 stdout，进度写 stderr，测试不得混入第三方提示破坏 JSON。

API Key 不入日志或索引；模型用量仅记录接口真实返回值，没有 usage 则留空，不把估算当实测。

## 七、拟实现命令与 Python 接口

以下是未来命令示例，**当前尚不可执行**。CMD 中路径使用英文双引号：

```bat
cd /d "C:\Users\Lenovo\Desktop\研一\大模型软件开发\DocBase"
setup.cmd
run.cmd doctor

rem 发现 doc2md 候选产物，随后检查/选择 catalog
run.cmd scan --input "..\doc2md\output" --catalog ".\storage\catalog.json"

rem 优先跑通无需远端模型的基线
run.cmd build --catalog ".\storage\catalog.json" --profile bm25-only
run.cmd query --question "分组转发是怎样进行的？" --profile bm25-only --top-k 5 --format json

rem 单独验证检索模型接口；填写相应配置后再执行
run.cmd probe --capability embedding
run.cmd probe --capability sparse
run.cmd probe --capability reranker

rem 三路与精排完成后的正式模式
run.cmd build --catalog ".\storage\catalog.json" --profile full --incremental
run.cmd query --question "分组转发是怎样进行的？" --profile full --category "技术资料" --top-k 5 --format json
run.cmd test --dataset ".\eval\test.jsonl" --profile full --top-k 5
run.cmd get --chunk-id "真实证据ID" --index-version "真实索引版本"
run.cmd status
```

按当前免费模型默认方案，取得 Embedding/Reranker 接口后可先使用 `--profile hybrid` 建库和查询；上述 full 示例仅在额外 BGE-M3 稀疏通路也已就绪时使用。

profile 必须与目标快照支持的能力一致；不得用 full 查询去掩盖一个实际只有 BM25 的快照。主要运行模式仍是课件要求的 build/query/test，其他命令用于输入、诊断和后续 Agent 取证。

Python 接口草案：

```python
scan_artifacts(input_dir, catalog_path) -> ScanReport
build_index(catalog, storage, profile, incremental=False, quality="usable") -> BuildReport
query(index, question, top_k=5, category=None, profile="hybrid") -> QueryResult
get_evidence(index_version, chunk_id, include_neighbors=False) -> EvidenceResult
eval_retrieval(index, test_set, top_k=5) -> EvaluationReport
```

建议状态码：0 为本次请求完整执行；1 为输入/配置/服务故障；2 为显式允许的降级或构建质量警告。正常零命中不是程序崩溃，JSON status=ok、results=[] 并保留诊断；部分文档不可用的构建报告列出实际纳入内容，不能含糊写“全部成功”。

## 八、工程目录设计

```text
大模型软件开发/
  doc2md/                         # 已有，保持独立
  DocBase/
    DocBase知识库与检索开发方案.md  # 当前文档
    README.md
    environment.yml
    pyproject.toml
    requirements.txt
    requirements.lock.txt
    .env.example                  # 各能力 Key 留空
    .env                          # 后续创建，不复制 doc2md 密钥
    setup.cmd / setup.ps1
    run.cmd / run.ps1
    configs/retrieval.toml
    dictionaries/terms.txt
    src/docbase/
      cli.py
      config.py
      models.py                   # 统一输入/证据/结果契约
      ingest.py                   # 产物扫描、登记、质量、来源复制
      chunker.py
      clients/embedding.py
      clients/sparse.py
      clients/reranker.py
      indexers/dense.py
      indexers/bm25.py
      indexers/sparse.py
      fusion.py
      retriever.py
      storage.py
      evaluator.py
    tests/
    eval/dev.jsonl
    eval/test.jsonl
    storage/                      # 本地运行产物，不提交密钥和大索引
```

初版固定单进程 builder，查询只读快照；模块通过 models.py 的契约传递，不直接依赖其他模块内部存储格式。依赖锁定在实际安装并测试后生成，不在方案阶段虚构“已验证版本”。

## 九、实验数据、评价方法与验收标准

### 9.1 先验数据准备

先用现有 doc2md 合成样例校验输入/来源，再使用 DNX16 等经过用户选择的真实技术文档；不自动将目录下个人账单等无关资料入库。

建议 12～20 份文档，60 道人工标注题：20 道调参、40 道冻结测试。开发题和测试题均覆盖精确术语、语义改写、图片关系、多处证据，以及无答案问法；开发与测试题避免仅改几个字的近重复。

每题记录 qid、问题、类别、是否可回答、标准要点、人工相关证据单元及其文档版本/页/图/段落范围。证据必须从原文人工确认，不能只让检索系统自己生成“标准答案”。

若真实题所需图片在 doc2md 中尚为 pending，先报告前置知识缺失，保留端到端失败；另外单列“仅在证据已完整入库题目上的检索指标”，不要把解析缺失悄悄删掉以抬高检索分数。

### 9.2 指标定义

| 指标 | 统计方式 | 用途 |
|---|---|---|
| Hit@K | 可回答题中 Top-K 至少命中一个人工相关证据的比例 | 看能否找到相关证据 |
| Recall@K | 每题已覆盖的相关证据单元数 / 人工相关证据单元总数，再按题平均 | 看多证据是否完整 |
| MRR@K | Top-K 第一个相关结果排名倒数，未命中为 0，再按题平均 | 看首个相关证据位置 |
| 来源定位成功率 | 可准确定位快照、页/锚点/图的结果数 / 被检查结果数 | 检查可追溯性 |
| 入库覆盖 | 选中语料的有效块/排除块和图像未完成数 | 防止把缺知识误当召回问题 |
| 延迟/资源 | 构建耗时、峰值内存、存储量；查询总耗时及各阶段耗时 | 判断成本与可用性 |

来源判定以真实证据内容及范围为准；只落在同一页、或碰巧引用到同一个长 block，不自动算命中。重叠 chunk 命中同一证据只计一次。MRR 按实际返回 chunk 排名计算，另报告重复证据占用 Top-K 的情况。

图片节点、关系、条件的正确性属于解析/人工核验口径；DocBase 的命中不能证明 VL 释义本身正确。无答案题不混入可回答题召回分母，也不在本阶段声称测得 Agent 拒答率。

### 9.3 对照组

| 分组 | 能力 | 观察目标 |
|---|---|---|
| G0 | BM25 | 精确术语基线及无模型可用性 |
| G1 | 稠密向量 | 语义改写基线 |
| G2 | 稠密 + BM25 + RRF | 双路互补 |
| G3 | 三路 + RRF | BGE 稀疏增加的收益/代价 |
| G4 | 三路 + RRF + Reranker | 精排收益及延迟 |

另在 G4 上对比“仅原生/OCR 正文”和“加入成功图片释义”。固定语料版本、分词/切片规则、最终 K 及模型配置；各组共同使用同一题集，记录每路召回预算，避免把候选规模变化混同为算法收益。

### 9.4 功能和质量验收

| 验收项 | 操作 | 完成标准 |
|---|---|---|
| 输入契约 | ok/partial/pending、历史多版本、缺源文件混合 | 选中与排除原因明确，失败提示不入库 |
| 重新释义更新 | 原文 revision 不变，blocks 从待处理变为成功 | 识别 artifact 改变，索引增加真实图片知识 |
| 切片来源 | 抽查至少 20 片，含长表格、图片组和跨页内容 | 文本范围与所有引用匹配，不伪造页码 |
| 三路真实性 | 分别单独运行并检查编码/候选 | 三路来源不同，稀疏实际输出词项权重 |
| 融合精排 | 人工小样本与固定候选 | RRF 与候选映射正确，错误/截断可见 |
| 重启与更新 | 重载、增文档、改原文、仅改释义、停用文档 | 无重复，当前有效版本一致，旧快照可解释 |
| 原子发布 | 构建过程中模拟模型失败/进程中断 | 不替换有效 CURRENT，旧版仍可查询 |
| 类别隔离 | 类别语料不重叠的查询 | 候选从指定有效类别中选取 |
| 命令可用 | 从 CMD 执行三种主模式 | JSON 可解析、进度持续可见、退出状态准确 |
| 检索质量 | 冻结测试题和人工证据 | 建议 Hit@5 ≥85%，必须同时报告样本数、分题型结果和其他指标 |

85% 是拟采用的起始目标，不是课件硬性指标或已有成绩。样本量不足时先报告实际分子/分母；失败或退化如实分析，不为了达标在测试集继续调参。

## 十、CodeAgent / Claude Code 的角色

用户已经配置 Claude Code + DeepSeek API，可以用于以下开发任务：

1. 根据模块需求实现输入校验、数据模型、CLI 和检索模块。
2. 生成固定的小型测试、审查实现是否符合契约，排查失败日志。
3. 下一阶段由项目约定指导其调用 DocBase 的 query/get，并根据证据作答。

**本阶段运行时不引入 Agent 决策循环。** 建库和检索使用确定的 Python 流程，便于复现、测试和控制耗时；DeepSeek Chat 配置不替代 Embedding、Reranker 或稀疏模型。

后续给 Claude Code 的工具只需稳定的 JSON CLI 或 Python 接口。执行方式与其实际支持的项目约定入口在第三阶段确认，本阶段不改写现有 Claude Code 的模型、Key 或全局指令文件。

## 十一、开发任务顺序、成果与开始条件

| 里程碑 | 工作 | 阶段交付与检验 |
|---|---|---|
| M0 资源与接口 | 确认模型能力、选样本、约定 schema/profile | 资源清单，三个能力 probe 设计，不能把未取得接口写成已可用 |
| M1 无模型基线 | Conda/CMD 入口、输入登记、来源、切片、BM25 | 可用 query/get、可追溯结果、输入和分词测试 |
| M2 语义检索 | Embedding 客户端、缓存、矩阵映射、双路融合 | 真实语义题结果与 G1/G2 基线 |
| M3 三路与精排 | BGE sparse、倒排、三路融合、Reranker | 完整 profile 与协议/质量验证，缺通路时不通过完整验收 |
| M4 持久化更新 | source store、版本快照、更新计划、写锁与发布 | 重载、增改停用、重跑释义及失败恢复记录 |
| M5 自测交接 | 评测题集、对照组、报告、文档 | build/query/test、Agent 接口说明、可复现实验结果 |

交付物应包含源代码、Conda 声明及锁定依赖、空 Key 配置模板、CMD/PowerShell 启动脚本、数据契约、源集合清单、索引重建方法、逐题结果、失败分析和运行说明。

真正开始编码前需要落实的外部信息：Embedding/Reranker 的实际提供方与调用示例、BGE-M3 稀疏输出来源、可获得的硬件（若要本地推理）、准备纳入的 doc2md 完整产物及测试文档。拿不到全部接口时可以先执行 M1，但完整三路实验仍以上述真实能力就绪为前提。

建议实施主线：**先让正确的内容以正确出处进入知识库，再加入语义召回、三路融合和精排，最后验证质量、更新和后续 Agent 调用。**
