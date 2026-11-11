# DocAgent 使用说明

DocAgent 使用 deepseek-flash 调用 DocBase 完成检索、上下文补取和带引用的回答。运行器会将 AGENTS.md 中的规则注入模型请求。网页入口见 [ResearchDesk 使用手册](../ResearchDesk/README.md)。

## 1. 填写 Key

编辑本目录 `.env`：

```dotenv
DOCAGENT_API_KEY="你的DeepSeek API Key"
```

接入地址为 `https://api.deepseek.com`，模型为 `deepseek-flash`。此 Key 与 doc2md、DocBase 的 Gitee Key 分开保存。现有 Claude Code 配置不用修改，也不要求运行 Claude Code 才能问答。

配置文件的相对路径按项目目录解析；启动脚本默认使用 `DocAgent\.conda`。三个项目环境互相独立。若需要重建环境，运行 `setup.cmd`，已有 `.env` 不会被覆盖。

## 2. 从 CMD 或 Anaconda Prompt 使用

先用 [DocBase 离线示例](../DocBase/README.md#2-离线示例) 建立 default 知识库，再从仓库根目录执行：

```bat
cd DocAgent
run.cmd doctor
run.cmd inspect --collection default --profile bm25-only --question "1729"
```

inspect 返回候选及来源。填写 DeepSeek Key 后，可运行 probe 检查模型工具调用，再用同一知识库验证问答：

```bat
run.cmd probe
run.cmd ask --collection default --profile bm25-only --question "测试文档中的版本号是什么？"
```

预期答案为 1729，并附原文引文。probe 和 ask 都会调用 DeepSeek。使用其他知识库时，通过 --collection 指定集合。还可用 --storage 和 --index-version 指定存储和索引版本。

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

DocBase 提供以下检索能力：

- `context`只读接口，补取登记来源中的相邻有效块。
- `status --index-version`，不产生模型请求即可验证指定版本。
- 可选`context-v2`切片策略（当前配置指纹为context-v2.1），过滤明显数字页码/arXiv假标题，识别独立References段落，只合并同页同节、位置相邻的正文段落，保留各块来源和字符范围。
- `query --prefer-body`，BM25优先正文并降低参考文献/孤立短标签优先级。Agent默认启用，检索参考文献时模型可将search_knowledge的prefer_body设为false。

新增或更新文档后，先运行 doc2md，再按 DocBase 文档扫描、选择产物并重建相应集合。需要正文优先排序时，可为 DocBase query 添加 --prefer-body。

## 5. 测试与评测

无Key自动测试：

```bat
.conda\python.exe -m pytest -q
```

填Key后可用合成示例题集做真实联调：

```bat
run.cmd evaluate --collection default --dataset "eval\dev.jsonl"
```

evaluate逐题调用模型，会产生消耗；报告位于storage/evaluations。它记录执行状态、时延和usage，答案正确率与引用语义支持率留给人工标注。示例题集用于流程检查。评估研究资料时，请使用人工审核的题集。

`scripts/verify_integration.py`用于开发验证，真实调用本地DocBase、重建Zotero集合、模拟模型回答，不调用任何模型API；报告在storage/integration。它会更新CURRENT，不是普通只读测试命令。

## 6. 当前能力范围

问答使用文档正文和已有图像释义。若模型提交 Mermaid，系统保存为标注“未验证”的草稿。通过网页打开来源面板，可直接核对原图和原文。

默认最多6次逻辑模型调用、3次搜索、10次检索工具调用，总时限180秒；HTTP临时故障最多重试2次并记录物理请求。原始证据总字符预算24000，单条超过预算时不暗中截断后冒充完整证据。

Runner支持取消事件与进度回调，检索子进程超时/取消会终止。同步HTTP调用期间，取消需等当前请求返回或达到请求超时，再被Runner处理。

知识库片段只作为资料，模型没有任意文件读取、shell和写库工具。提交给DeepSeek的内容包括问题、选中证据和必要上下文；不要把本地调试报告当作无需访问控制的公共文件。
