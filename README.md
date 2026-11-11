# 研知 Research Desk

研知是 Windows 上的本地文献工作台。将 PDF 或 Zotero 附件整理进知识库，限定资料范围后检索或提问，再沿回答中的引用查看原句和 PDF 页码。会话可分支、收藏、归档并导出为 Markdown 或 JSON。

## 安装与启动

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
3. 想试带引用问答时，到「个性化设置 → 模型配置」填写问答模型的接口地址、模型名称和 Key，再为目标库开启外部模型处理。
4. 在研究对话中选择该库，问「文档版本号是什么？」点击回答中的引用编号，核对原句和来源页。也可从知识库的「Zotero 同步」预览导出 JSON 与 PDF 附件。

模型问答会把问题和选中的证据发送给所配置的服务。独立 CLI 的配置使用各组件的 `.env.example` 模板，网页已保存的模型配置在设置页管理。详见 [工作台操作指南](ResearchDesk/README.md)。

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
