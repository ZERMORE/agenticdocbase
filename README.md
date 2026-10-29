# AgenticDocBase

本机文献知识库与 Agent 聊天应用。启动 `start.cmd`，访问 http://127.0.0.1:8765。
停止 `stop.cmd`，检查 `status.cmd`，备份 `backup.cmd`。

## 架构与目录

- ResearchDesk：浏览器界面、API、持久任务 Worker 与业务数据库。
- DocAgent：模型工具循环、多轮上下文与引用校验。
- DocBase：检索、索引版本、来源快照；不能删除 source_store 来清理空间。
- doc2md：文档解析及图片释义。
- 实验方案输出：设计与实施方案；imports：原始导入清单。
- workflows：批量导入和迁移工具；migration：迁移日志与验证结果。
- archive/course-materials：全部原有课程资料和杂项，独立于运行代码保留。

四个组件保留独立 Conda 环境，前端依赖在 ResearchDesk/web。
入口脚本统一在根目录，避免进入错误项目执行命令。
细节参见 [应用使用说明](ResearchDesk/README.md)。

数据：ResearchDesk/data；解析输出：doc2md/output 与 ResearchDesk/data/parsed；
索引和不可变来源：DocBase/storage。历史记录中的原路径是溯源信息，不是运行时依赖。
Key仅保存在各项目.env，不进入前端。原目录完整保留，迁移副本独立运行。
旧.venv、测试缓存和历史报告为完整复制保留，不参与当前运行；不自动删除。
