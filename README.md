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

## V1 质量版

知识库中打开文件的“质量与修订”，可查看待处理内容、纠错或局部重解析。聊天引用可定位原文，新网页问答增加模型辅助支持性检查；设置页可配置定期备份。

- [质量版操作与实现说明](plans/iterations/V1-IMPLEMENTATION.md)
- [质量版验收记录及未完成的人工质量门槛](plans/iterations/V1-VALIDATION.md)
- [评估集审核与冻结](evaluations/README.md)

运行 `quality.cmd validate` 检查候选题集；`quality.cmd baseline --split dev --allow-draft` 仅做离线候选诊断，不能当正式质量成绩。

全库概括、中文PDF断行/字形和输出截断修复见 [概览修复记录](plans/iterations/OVERVIEW-FIX.md)。

支持性检查已可在网页设为关闭/宽松/标准/严格，默认宽松。[设置与行为说明](plans/iterations/SUPPORT-CHECK-LEVELS.md)。
