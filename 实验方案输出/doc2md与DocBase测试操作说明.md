# doc2md 与 DocBase 测试操作说明

适用终端：**CMD / Anaconda Prompt**。路径使用英文双引号，启动使用 `run.cmd`。

**当前状态（2026-10-28 更新）：两个工具均已有代码和各自独立的 Conda 环境，可执行 doc2md → DocBase BM25 联合测试。真实 Embedding/Reranker 联调返回 HTTP 400，其中 Embedding 已确认为账户没有可用计费资源；混合模型模式需要平台资源可用后复验。**

## 1. 测试 doc2md

### 第一步：检查环境和模型

```bat
cd /d "C:\Users\Lenovo\Desktop\研一\大模型软件开发\doc2md"
run.cmd doctor
run.cmd probe
```

预期：doctor 返回 JSON，解释器指向项目 `.conda\python.exe`，`api_key_configured` 为 true；probe 返回 `status: ok`，能识别 `INPUT A → OUTPUT B`。probe 会调用模型并产生用量。

### 第二步：先处理小样本

```bat
run.cmd parse ".\examples\sample.pdf" --output ".\output\smoke"
```

等到出现 `-> ok / partial / failed` 和最终 JSON 摘要，才算本次处理结束。根据摘要中的 `markdown` 路径打开 `document.md`，检查：

- 中文正文与数字 `1729` 正确。
- 表格中的 Item、Value、Version、1729 保留。
- 原图可显示，模型释义的方向为 INPUT A → OUTPUT B。
- 来源链接可以打开相应的文件快照。

`ok` 表示本次完整执行；`partial` 表示有待处理/失败图像或质量警告，查看 manifest.json、parse.log.jsonl；`failed` 表示文件处理失败。图片释义为 pending/failed 时，不能认为多模态测试已通过。

### 第三步：处理真实文档

本目录上一级已有该 PDF，可执行：

```bat
run.cmd parse "..\DNX16-AN126-PUB.pdf" --output ".\output\manual"
```

抽查至少一页正文、一张表和一幅图，确认内容、图片关系、页码和来源链接一致。大文档会逐张请求模型，期间可能暂时没有新进度；PyMuPDF 的布局建议不是失败结论。

### 可选：本地自动测试（不调用真实模型）

```bat
.\.conda\python.exe -m pytest -q
```

预期所有测试通过。若只想测试离线提取，在 parse 命令后加 `--offline`；图片释义待处理是该模式的预期结果。

## 2. doc2md + DocBase 联合工作流

完成第一节的样例解析后，在 CMD 执行以下真实命令（不调用检索模型 API）：

```bat
cd /d "C:\Users\Lenovo\Desktop\研一\大模型软件开发\DocBase"
run.cmd doctor
run.cmd scan --input "..\doc2md\output\smoke"
run.cmd build --profile bm25-only
run.cmd query --profile bm25-only --question "1729" --top-k 5
run.cmd query --profile bm25-only --question "INPUT OUTPUT TRANSFER" --top-k 5
run.cmd test --profile bm25-only --dataset ".\eval\smoke.jsonl"
```

预期查询返回正确正文/表格或图像释义，sources 中的快照及图片完整路径可打开。JSON 的 report_path 指向保存的结果。小样本评测仅证明链路连通，不代表真实大文档质量。

资源可用后先分别执行 `run.cmd probe --capability embedding` 和 `run.cmd probe --capability reranker`；都成功后再使用 `--profile hybrid` 建库、查询及评测。若要课件三路 full，仍需额外 sparse 配置。

| 步骤 | 操作 | 通过标准 |
|---|---|---|
| 1 准备资料 | 完成上述真实文档解析，记录成功产物目录 | manifest.json、blocks.jsonl、图片和来源快照齐全，关键图像释义已完成 |
| 2 登记入库 | DocBase 选择该文档唯一有效版本；排除 pending/failed 内容 | 不重复导入历史版本，不把错误提示当知识 |
| 3 建库 | 先运行 BM25 基线，模型资源可用后再测试 hybrid；full 另需稀疏模型 | 模式和能力准确，来源完整；不把两路称为三路 |
| 4 查询 | 人工先从原文选定答案，再分别用精确名词、语义改写、图像关系三类问题查询 | Top-5 包含正确证据，文档/页/图与人工标注一致 |
| 5 取证 | 打开命中证据对应的源文件和图像 | 证据真正支持问题，模型释义与原图关系一致 |
| 6 重启更新 | 重载索引；修改文档或补齐释义后重新入库 | 重载可查，新内容生效，当前结果不混用旧版本 |

完整三路测试前，需要确认 **Embedding、BGE-M3 稀疏输出和 Reranker** 的可用接口；现有视觉模型和 DeepSeek Chat 接口不自动等于这三种能力已配置。

DocBase 本阶段返回检索证据，最终自然语言答案和多轮补检属于后续 AGENTS.md/Agent 阶段。Claude Code 无需参与上述 doc2md 测试；以后可作为调用 DocBase 的问答入口。
