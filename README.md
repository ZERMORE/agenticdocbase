# 研知 Research Desk

研知是 Windows 上的本地文献工作台。将 PDF 或 Zotero 附件整理进知识库，限定资料范围后检索或提问，再沿回答中的引用查看原句和 PDF 页码。会话可分支、收藏、归档并导出为 Markdown 或 JSON。

## 安装与启动

已有 Windows x64 安装包时，双击 `ResearchDesk-Setup-x64.exe` 安装，再从开始菜单打开研知。构建方法和数据位置见 [Windows 打包说明](packaging/windows/README.md)。

### 从源码运行

需要 Windows、可用的 Conda 和 Node.js 22.12 或更新版本（含 npm）。安装依赖时需要访问 Conda、Python 和 npm 软件源。建议从 Anaconda Prompt 进入项目根目录执行：

```bat
git clone https://github.com/ZERMORE/agenticdocbase.git
cd agenticdocbase
setup.cmd
start.cmd
```

打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。`setup.cmd` 安装四个独立的 Conda 环境、构建前端并初始化工作台。升级后可停止服务，重跑安装，再启动。已有模型配置和工作台数据保留。

## 五分钟体验

1. 在「知识库」中创建一个库并上传仓库自带的 [sample.pdf](doc2md/examples/sample.pdf)，保留「仅离线文本解析」。
2. 到「任务中心」查看解析和索引任务。文档状态变为「可检索」后，在知识库内搜索 `1729`，可找到原文片段。导入、本地文本解析和 BM25 检索可在未填写模型 Key 时使用。
3. 想试带引用问答时，到「个性化设置 → 模型配置」填写问答模型的接口地址、模型名称和 Key，再为目标库开启外部模型处理。详见 [API Key 配置](#api-key-配置)。
4. 在研究对话中选择该库，问「文档版本号是什么？」点击回答中的引用编号，核对原句和来源页。

## API Key 配置

本地文本解析、Zotero 导入和 BM25Plus 检索可以直接使用。带引用的模型问答和可选的多模态解析需要你自己的 API Key。

### 网页工作台

进入「个性化设置 → 模型配置」，按需选择角色并保存。Key 直接粘贴到输入框即可。

| 角色 | 默认接口地址 | 默认模型 | 什么时候填写 |
| --- | --- | --- | --- |
| 问答 Agent | https://api.deepseek.com | deepseek-flash | 使用研究对话生成带引用的回答 |
| 文档解析 | https://ai.gitee.com/v1 | Qwen3.6-Plus | 用多模态模型辅助解析图像 |

可以点击「测试已保存配置」检查连接。使用问答或在线解析前，还需在对应知识库开启「允许外部模型处理」。问答会把问题和选中的证据发送给所配置的模型服务。网页凭据通过 Windows DPAPI 加密保存，更换 Key 时请在这个页面更新。

### 命令行工具

在各组件目录下编辑本机的 `.env`。文件不存在时，复制同目录的 `.env.example` 后再填写。普通 Key 直接写在等号后即可，也可用双引号括起。

| 配置文件 | 要填写的变量 |
| --- | --- |
| `doc2md/.env` | `DOC2MD_API_KEY` |
| `DocAgent/.env` | `DOCAGENT_API_KEY` |
| `DocBase/.env` | `DOCBASE_EMBED_API_KEY`、`DOCBASE_RERANK_API_KEY`，仅在使用相应的混合检索能力时填写 |

网页的模型角色可以在首次初始化时从 `.env` 读取默认值。网页已有配置时，请在网页更新。真实 Key 只填入本机配置，提交到 Git 的模板应保持空值。

## Zotero 联动

Zotero 可以保存论文题录、分类和 PDF。研知通过导出 JSON 读取题录和附件路径，再从本机复制 PDF 到所选知识库并离线解析。

1. **导出一个分类。** 在 Zotero 左栏右键目标分类，选择「导出分类」。可使用 Better BibTeX 插件提供的 **BetterBibTeX JSON** 格式。导出的条目需带有 `attachments`，附件中应有 `path` 或 `localPath`。普通 CSL JSON 通常只含引文信息。每个分类可单独导出为一个 JSON，分别连接到对应的研知知识库。 [Zotero 导出说明](https://www.zotero.org/support/kb/exporting) · [Better BibTeX 导出格式](https://retorque.re/zotero-better-bibtex/installation/bundled-translators/)
2. **找到附件目录。** 在 Zotero 的「设置 → 高级 → 文件和文件夹」点击「显示数据目录」，使用其中的 `storage` 子目录。Windows 默认通常为 `%USERPROFILE%\Zotero\storage`。确认需要导入的 PDF 已下载到本机。 [Zotero 数据目录说明](https://www.zotero.org/support/zotero_data)
3. **预览并导入。** 打开研知中的目标知识库，在底部「Zotero 同步」填入 JSON 文件的完整路径和 `storage` 目录，点击「添加同步来源 → 预览变更」。确认附件匹配后点击「应用本次预览」，在「任务中心」查看进度。
4. **更新分类。** 把更新后的分类重新导出到同一个 JSON 文件，再预览并应用。启用「自动同步」后，研知会每分钟检查该 JSON。源附件暂时缺失时，研知保留已经入库的资料。


## 仓库结构

| 目录 | 用途 |
| --- | --- |
| [ResearchDesk](ResearchDesk/) | React 前端、FastAPI 服务、任务 Worker 和本地业务数据 |
| [doc2md](doc2md/) | 文档解析、质量检查和原文定位 |
| [DocBase](DocBase/) | BM25 检索、版本化索引和来源快照 |
| [DocAgent](DocAgent/) | 模型问答、证据收集和引用校验 |
| [docs](docs/) | 独立宣传页及公开演示素材，适合 GitHub Pages 的 `/docs` 发布方式 |
| [workflows](workflows/) 与 [evaluations](evaluations/) | 可选的导入和检索评测工具 |
| [plans](plans/) | 迭代方案与历史验证记录 |

[技术架构](ARCHITECTURE.md) · [doc2md CLI](doc2md/README.md) · [DocBase CLI](DocBase/README.md) · [DocAgent CLI](DocAgent/README.md)

## 运行与维护

| 命令 | 用途 |
| --- | --- |
| `status.cmd` | 查看本地进程及 Worker 心跳 |
| `stop.cmd` | 停止工作台和后台进程 |
| `backup.cmd` | 备份应用数据与引用所需的文件 |
| `verify.cmd` | 运行四个 Python 组件的测试并检查前端构建 |

业务数据保存在 `ResearchDesk/data/`，索引和来源快照保存在 `DocBase/storage/`。出现故障时优先查看「任务中心」或 `ResearchDesk/data/logs/`。端口被占用时，先确认占用该端口的程序，再启动工作台。恢复备份的步骤见 [操作指南](ResearchDesk/README.md#备份与恢复)。

当前工作台面向本机单用户，使用 BM25Plus。命令行的混合检索需要另建对应索引并配置可用的供应商服务。测试缓存、个人导入文件、模型凭据和运行数据均在本机管理。
