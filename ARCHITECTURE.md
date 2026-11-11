# 研知技术架构

浏览器提交问题、上传资料和订阅任务状态。ResearchDesk API 把长任务写入 SQLite，后台 Worker 调用对应组件。四个 Python 组件分别运行在 Conda 环境中。

## 从文件到可核对的答案

| 阶段 | 组件 | 数据流 |
| --- | --- | --- |
| 解析 | [doc2md](doc2md/) | 文件 → Markdown、结构化块、页码与来源记录 |
| 入库与召回 | [DocBase](DocBase/) | 解析产物 → 文段、BM25Plus 索引、不可变来源快照 |
| 证据问答 | [DocAgent](DocAgent/) | 问题与范围 → 检索和上下文工具 → 结构化回答与引文校验 |
| 交互和定位 | [ResearchDesk](ResearchDesk/) | 任务事件与答案 → 对话分支、引用面板和 PDF 页定位 |

## 运行边界

- **网页**使用 React、TypeScript 和 Vite。浏览器通过 SSE 接收任务进度和草稿，通过受控资源接口访问来源。
- **服务**使用 FastAPI 提供本机接口。Worker 负责进程运行、超时、取消、任务租约和发布结果。
- **存储**使用 SQLite 记录业务状态。文档、来源快照和索引存在磁盘上，构建完成后发布新索引版本。问答固定范围、索引和配置版本。
- **模型**由工作台设置管理。Key 使用 Windows DPAPI 保护，按任务冻结配置。网页当前查询走 BM25Plus，CLI 的向量与重排需单独配置并建索引。
- **宣传页**是 [docs](docs/) 内的静态站点，使用公开演示数据。完整文献工作台在访客安装后的本机运行。

[安装与日常操作](README.md) · [备份恢复](ResearchDesk/README.md#备份与恢复) · [评测流程](evaluations/README.md)
