# DocAgent 使用说明

2026-10-29整合补充：ResearchDesk已提供浏览器入口，见`../ResearchDesk/README.md`。本项目新增受控`docagent.service_bridge`文件请求接口，支持最近消息、偏好、资料范围、主题摘录和指定DocBase存储；旧ask命令兼容。真实deepseek-flash单轮问答已由用户此前运行成功，本文后续“Key留空/未联调”为初次构建记录，不代表用户当前配置。

DocAgent 使用 `deepseek-flash` 调用 DocBase，完成检索、上下文补取和带引用的中文回答。AGENTS.md 由运行器实际读取并注入模型请求；它不是仅供展示的文件。

代码和独立 Conda 环境已构建。DeepSeek Key 留空；真实模型联调和论文问答质量验收尚未完成。已有本地测试包括真实 DocBase 检索与模拟模型生成的闭环，模拟结果不代表 DeepSeek 实测能力。

## 1. 填写 Key

编辑本目录 `.env`：

```dotenv
DOCAGENT_API_KEY="你的DeepSeek API Key"
```

接入地址为 `https://api.deepseek.com`，模型为 `deepseek-flash`。此 Key 与 doc2md、DocBase 的 Gitee Key 分开保存。现有 Claude Code 配置不用修改，也不要求运行 Claude Code 才能问答。

配置文件的相对路径按项目目录解析；启动脚本默认使用 `DocAgent\.conda`。三个项目环境互相独立。若需要重建环境，运行 `setup.cmd`，已有 `.env` 不会被覆盖。

## 2. 从 CMD / Anaconda Prompt 使用

```bat
cd /d "C:\Users\Lenovo\Desktop\研一\大模型软件开发\DocAgent"
run.cmd doctor
run.cmd probe
```

doctor 只检查本地环境和配置，`key_configured: true`不等于模型已经验证可用。probe 使用合成文本完成一次真实工具调用往返，会产生少量 API 消耗。

先测试默认示例库：

```bat
run.cmd ask --collection default --question "测试文档中的版本号是什么？"
```

人工核对答案应为1729且有真实来源。再查询33篇Zotero论文：

```bat
run.cmd ask --question "CLIP-Adapter如何调整预训练视觉语言模型？"
run.cmd ask --question "这些论文中有哪些提示学习方法？请比较其主要差异，并分别提供证据。"
```

默认collection为`zotero_third_20261028`，profile为`bm25-only`。如需显式指定：

```bat
run.cmd ask --collection zotero_third_20261028 --profile bm25-only --question "你的问题"
```

路径和参数在CMD中使用双引号。`bm25-only`表示检索不调用Embedding/Reranker；问答仍会调用DeepSeek并产生消耗。当前Gitee检索模型账户资源问题不会因为填写DeepSeek Key而自动解决。

无需Key，只看检索：

```bat
run.cmd inspect --question "vision language model"
```

inspect返回候选和来源，不生成自然语言答案。`ask`还支持`--category`、`--index-version`、`--storage`；同一问答内的版本不会随CURRENT更新而变化。

## 3. 查看结果

每次ask都会打印以下绝对路径：

- `answer_md_path`：阅读中文回答、引用和来源链接。
- `answer_json_path`：结构化答案、来源、限制和真实usage记录。
- `trace_path`：查询、工具调用、证据ID和错误诊断。

产物位于 `DocAgent\storage\runs\<run_id>\`，另有 `evidence.json` 和 `policy_snapshot.md`。Markdown中来源链接指向DocBase不可变原文快照，可用VS Code Markdown预览打开；PDF链接的页码片段是否直接跳转取决于查看器。

引用显示的页数是PDF文件页序号，从1开始，不保证与论文印刷页码相同。`[E1]`对应本次证据登记表。每个关键事实提交时须附真实证据ID和连续短引文；程序核对文件存在及原文快照哈希。

PDF引用允许空白、换行及英文行末断词连字符的排版差异，例如`com-`换行`bining`与`combining`。匹配后保存的quote仍恢复为原始连续文本，并记录quote_span、submitted_quote、quote_match；不会容忍数字、否定词或同义改写的差异。参数结构错误与引用错误分别计一次修复，trace中的validation_failure记录具体字段或证据ID，便于诊断。

`answered`表示结构和引用校验通过，语义是否支持仍需人工核查；它不是正确率或置信度。`insufficient_evidence`表示本次范围内证据不足，`failed`表示技术故障。退出码分别为0、2、1，取消为130。

## 4. 检索质量调整与索引更新

本次在DocBase增加：

- `context`只读接口，补取登记来源中的相邻有效块。
- `status --index-version`，不产生模型请求即可验证指定版本。
- 可选`context-v2`切片策略（当前配置指纹为context-v2.1），过滤明显数字页码/arXiv假标题，识别独立References段落，只合并同页同节、位置相邻的正文段落，保留各块来源和字符范围。
- `query --prefer-body`，BM25优先正文并降低参考文献/孤立短标签优先级。Agent默认启用，检索参考文献时模型可将search_knowledge的prefer_body设为false。

已用新策略重建Zotero集合；旧索引仍保留，默认示例集合未更换。普通DocBase query的默认排序保持原行为；要使用正文优先，应加`--prefer-body`。

以后新增/更新论文后，先按原工作流完成doc2md解析和DocBase select/scan，再从DocBase目录构建：

```bat
cd /d "C:\Users\Lenovo\Desktop\研一\大模型软件开发\DocBase"
run.cmd build --collection zotero_third_20261028 --catalog "storage\catalog_zotero_third_20261028.json" --env-file "..\DocAgent\configs\docbase-context.env" --incremental
```

这里使用独立构建配置，没有覆盖DocBase/.env或API Key。语料仍为partial，待完成的图像释义不因重切片而补齐。

## 5. 测试与评测

无Key自动测试：

```bat
.conda\python.exe -m pytest -q
```

填Key后可用合成示例题集做真实联调：

```bat
run.cmd evaluate --collection default --dataset "eval\dev.jsonl"
```

evaluate逐题调用模型，会产生消耗；报告位于storage/evaluations。它记录执行状态、时延和usage，答案正确率与引用语义支持率留给人工标注。该题集不代表33篇论文的质量测试。冻结论文题集尚需人工标注，没有生成虚构的正确率。

`scripts/verify_integration.py`用于开发验证，真实调用本地DocBase、重建Zotero集合、模拟模型回答，不调用任何模型API；报告在storage/integration。它会更新CURRENT，不是普通只读测试命令。

## 6. 当前能力范围

本次交付文本/已有图像释义的问答闭环。原图模型核验、Mermaid自动渲染、40题冻结论文评测、后台Service与Web均尚未完成。若模型提交Mermaid，保存为明确标注“未验证”的草稿；不能声称已查看原图。

默认最多6次逻辑模型调用、3次搜索、10次检索工具调用，总时限180秒；HTTP临时故障最多重试2次并记录物理请求。原始证据总字符预算24000，单条超过预算时不暗中截断后冒充完整证据。

Runner支持取消事件与进度回调，检索子进程超时/取消会终止。同步HTTP调用期间，取消需等当前请求返回或达到请求超时，再被Runner处理。

知识库片段只作为资料，模型没有任意文件读取、shell和写库工具。提交给DeepSeek的内容包括问题、选中证据和必要上下文；不要把本地调试报告当作无需访问控制的公共文件。
