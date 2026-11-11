# Windows x64 安装包

双击 ResearchDesk-Setup-x64.exe 安装，然后从开始菜单打开 Research Desk。程序会启动本机 API 和 Worker，再打开 http://127.0.0.1:8766。适用于 Windows 10/11 x64，内置 Python 运行时和网页资源。用户无需安装 Conda 或 Node.js。

- 知识库、对话、配置和日志位于 %LOCALAPPDATA%\ResearchDesk\Data。卸载后保留该目录。
- 从开始菜单打开 Stop Research Desk 可以停止后台。命令行也可在安装目录执行 .\ResearchDesk.exe --stop。
- 安装目录下的 doc2md\examples\sample.pdf 可用于验证离线 PDF 解析和检索。模型问答需在工作台设置页填入自己的 Key。旧 Office 文件转换需另装 LibreOffice。

## 从源码构建

在仓库根目录执行 setup.cmd，准备四套 Conda 环境并构建网页。安装 Inno Setup 7 后，在 PowerShell 中执行以下命令。将 ISCC.exe 的路径替换为本机路径。

~~~powershell
.\ResearchDesk\service\.conda\python.exe -X utf8 packaging\windows\build.py --inno "C:\Program Files\Inno Setup 7\ISCC.exe"
~~~

安装包输出到 dist\windows。构建收集运行时文件、已构建的网页、四个组件的源码、Agent 规则和样例 PDF。个人 .env、知识库、运行记录、索引和测试工具均排除。使用 --stage-only 可以单独生成目录来检查运行时。
