# Zotero 随机三分之一入库

来源目录：`C:\Users\Lenovo\Zotero\storage`。发现 100 个 PDF，按文件数向下取整随机抽取 33 个，不重复抽取；所选内容哈希均不同。合计 155.5 MiB、697 页。

本批使用 doc2md 离线解析与 DocBase BM25 入库，不调用模型 API；图片释义标为待补充，不能当成已完成全部多模态内容。原始 Zotero 文件仅被读取，不移动、不改名。

## 文件位置

- `selection.json`：候选总数、抽样规则、随机种子、选中的完整路径、文件大小及哈希。
- `selected_files.txt`：便于直接阅读的论文路径清单。
- `pdf_inventory.json`：所选文件的页数和可读取状态。
- `import_report.json`：逐文件解析结果、建库结果、查询检查及最终统计。
- `parse_*.log`：逐文件解析日志。
- `docbase_*.log`：扫描、建库及检索日志。
- Markdown：工程根目录 `doc2md/output/zotero_third_20261028`。
- 登记清单：`DocBase/storage/catalog_zotero_third_20261028.json`。
- 知识库集合：`zotero_third_20261028`，与原 `default` 集合分开。

## 查询（CMD / Anaconda Prompt）

```bat
cd /d "C:\Users\Lenovo\Desktop\研一\大模型软件开发\DocBase"
run.cmd query --collection zotero_third_20261028 --profile bm25-only --question "vision language model" --top-k 5
run.cmd query --collection zotero_third_20261028 --profile bm25-only --question "sign language recognition" --top-k 5
run.cmd status --collection zotero_third_20261028
```

应使用论文原文中的关键词；当前 BM25 模式不等于语义向量检索。结果中的 sources 给出原文名、页码、图片及来源快照完整路径，report_path 指向保存的查询结果。

## 原样重跑/中断恢复

从工程根目录执行：

```bat
doc2md\.conda\python.exe -u workflows\import_zotero.py
```

已有 selection.json 时沿用既定的 33 个文件，不再次抽样；复用报告中的已完成产物，检查原文件哈希，最后重新扫描并增量建库。新的随机批次请用不同的 `--batch` 名称，避免混淆现有抽样。

最终入库文档数以 import_report.json 的 summary 及 scan 的 rejected 为准。若某份 PDF 无法提取原生文字且未使用 OCR，可能不能形成有效知识块，应保留其被选中记录并报告，不能自动换选另一篇来掩盖缺失。

## 本次完成结果

33 个选中 PDF 全部入库，18,099 个检索片；原文总计 697 页。33 个解析结果均为 partial（离线图片释义未完成），没有失败或拒绝入库。

图片待释义数量：1175。模型请求次数：0。原始 Zotero 文件哈希核对一致。论文检索返回 3 条结果，抽查来源路径均有效。

可点击查看：[论文与 Markdown 目录](./论文目录.md)。
