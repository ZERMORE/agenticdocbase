# 本地导入文件

在研知网页的「知识库」中指定 Zotero 导出 JSON 与附件目录，预览后应用。此目录可以存放个人导出文件、批次清单和解析日志。

批量抽样导入是独立的实验工具。使用 DocBase 环境执行，并显式提供路径和批次名称：

```bat
DocBase\.conda\python.exe -X utf8 workflows\import_zotero.py --source "D:\Zotero\storage" --batch literature_sample
```
