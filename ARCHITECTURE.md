# 代码与运行架构

浏览器 → ResearchDesk API → 持久SQLite任务 → Worker → 独立解析/检索/Agent进程。
Agent → DocBase query/get/context → 证据台账 → 本地引用校验 → 最终答案。

## 责任边界

| 目录 | 职责 | 禁止混入 |
|---|---|---|
| doc2md/src/doc2md | 文档解析与来源块 | 网页业务、会话状态 |
| DocBase/src/docbase | 切片、召回、版本化索引 | 用户偏好与聊天生成 |
| DocAgent/src/docagent | 模型工具循环、证据和回答 | HTTP路由、上传管理 |
| ResearchDesk/service/src/researchdesk | 业务、队列、权限、进程、来源访问 | 重复实现解析或检索算法 |
| ResearchDesk/web/src | 页面和用户交互 | Key、磁盘任意路径、模型直连 |

应用模块：store负责事务和登记；api负责请求/资源边界；worker负责执行；
processes负责进程树/超时；watches负责只读扫描；manage负责启停/备份恢复。
前端main为页面编排，ScopeControl与WatchPanel分离资料范围和监控设置，api统一HTTP。

## 一致性与鲁棒性

- 独立环境由Conda克隆后重新安装editable包，避免复制后仍import旧目录。
- 应用数据库和catalog的路径已迁移；不可变来源/索引元数据不直接改字节。
- 删除以成员关系和运行时允许范围立即排除，重建后原子发布空/完整索引。
- Worker租约、取消、幂等、generation和固定索引版本防止迟到结果覆盖。
- 启动先校验真实进程身份和端口，再等待API与Worker就绪；失败回收本次进程。
- 使用 verify.cmd 回归四组件与前端构建；迁移记录放migration。

本次不合并四个环境、不重写已验证算法、不丢弃缓存/旧报告来制造整洁。
course-materials仅保存课程资料，不参与运行；原始数据、来源和历史快照保留。
后续若拆分前端页面或引入数据库迁移框架，应另配行为回归，不与本次路径迁移混改。
