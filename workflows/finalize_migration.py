from pathlib import Path
import sys

root=Path(sys.argv[1]).resolve()
if root != Path(r'E:\project_files\agenticdocbase').resolve():
    raise SystemExit('Unexpected target')
path=root/'ResearchDesk/service/src/researchdesk/manage.py'
text=path.read_text(encoding='utf-8')
old="""            if any(psutil.pid_exists(p['pid']) for p in saved):
                raise SystemExit('已有运行记录，请运行 status.cmd 或 stop.cmd')"""
new="""            for item in saved:
                try:
                    process = psutil.Process(item['pid'])
                    if (Path(process.exe()).resolve() == Path(sys.executable).resolve()
                            and 'researchdesk' in ' '.join(process.cmdline())):
                        raise SystemExit('本项目已启动，请运行 status.cmd 查看')
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        # Refuse to start a second application on an occupied port.
        import socket
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', 8765))
            except OSError:
                raise SystemExit('8765端口已被占用；未启动任何新进程')"""
if old in text:text=text.replace(old,new)
old="""        print('研知已启动：http://127.0.0.1:8765')"""
new="""        # Report readiness rather than merely successful process creation.
        import httpx
        import psutil
        ready = False
        for _ in range(40):
            try:
                with httpx.Client(trust_env=False, timeout=1) as client:
                    client.get('http://127.0.0.1:8765/api/bootstrap').raise_for_status()
                    health = client.get('http://127.0.0.1:8765/api/health').json()
                    if health.get('worker_alive'):
                        ready = True
                        break
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(.25)
        if not ready:
            for item in launched:
                try:
                    process = psutil.Process(item['pid'])
                    for child in process.children(recursive=True): child.terminate()
                    process.terminate()
                except psutil.NoSuchProcess: pass
            pidfile.unlink(missing_ok=True)
            raise SystemExit('启动未就绪，已回收本次进程；请检查data/logs')
        print('研知已就绪：http://127.0.0.1:8765')"""
if old in text:text=text.replace(old,new)
path.write_text(text,encoding='utf-8')
verify='@echo off\nsetlocal\nset PYTHONUTF8=1\n'
for project in ['doc2md','DocBase','DocAgent','ResearchDesk\\service']:
    verify+=f'pushd "%~dp0{project}"\n".conda\\python.exe" -m pytest -q\nif errorlevel 1 (popd & exit /b 1)\npopd\n'
verify+='pushd "%~dp0ResearchDesk\\web"\ncall npm.cmd run build\nset result=%errorlevel%\npopd\nexit /b %result%\n'
(root/'verify.cmd').write_text(verify,encoding='ascii')
(root/'ARCHITECTURE.md').write_text('''# 代码与运行架构

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
''',encoding='utf-8')
print('Unified entry points and startup hardening written.')
