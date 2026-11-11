# doc2md：本地多模态文档转 Markdown

支持 PDF、DOCX、PPTX、DOC、PPT 和 PNG/JPG/JPEG/WebP；输出 Markdown、结构化图文块、原图、原文快照和来源元数据。模型固定初始配置为 Gitee 的 `Qwen3.6-Plus`，密钥留空。运行时无需 Claude Code，也不读取/修改其 DeepSeek 配置。

## 1. 安装和配置

**CMD / Anaconda Prompt 使用 `.cmd` 入口**。提示符通常为 `(环境名) C:\路径>`，没有 `PS`：

```bat
cd doc2md
setup.cmd
run.cmd doctor
```

若环境已创建，只需运行 `run.cmd doctor`。该入口也可在 PowerShell 中用 `.\run.cmd doctor` 调用。

在 PowerShell 中：

```powershell
cd doc2md
.\setup.ps1
```

`setup.ps1` 按 `environment.yml` 创建项目专用 Conda 环境 `.conda`，或更新已有环境；在该环境内安装锁定的 Python 依赖，不覆盖已有 `.env`。不修改 Conda base 环境。可在 Anaconda PowerShell Prompt 中执行。

也可手动创建并激活：

```powershell
conda env create --prefix .\.conda --file environment.yml
conda activate .\.conda
python -m doc2md doctor
```

编辑本目录 `.env`，仅在等号后填写自己的 Key：

```dotenv
DOC2MD_BASE_URL=https://ai.gitee.com/v1
DOC2MD_MODEL=Qwen3.6-Plus
DOC2MD_API_KEY=
```

程序优先读取进程环境变量，其次读取项目 `.env`；也可通过 `--env-file` 指定配置文件。密钥不写入输出结果、日志、缓存或示例配置，不需要填写到 Claude Code 配置中。

**Key 不必加引号**，例如 `DOC2MD_API_KEY=你的Key`；一对英文单引号或双引号也支持。不要使用中文引号。后续运行安装脚本会保留你已经填写的配置。

`run.ps1` 默认通过 `conda run --prefix .conda` 调用环境，无须提前激活。若使用其他独立 Conda 环境，可先执行 `.\setup.ps1 -Prefix 'D:\你的Conda环境路径'`，并设置 `$env:DOC2MD_CONDA_PREFIX='D:\你的Conda环境路径'` 后使用启动脚本。安装和运行需使用同一环境路径。

脚本优先使用 Conda base 的 `python -m conda`，兼容 Windows 上 `conda.exe` 启动器的 DLL 重定位问题。此前建立的 `.venv` 已不被启动脚本使用，保留原目录，不自动删除。

## 2. 使用

```powershell
# 环境诊断，不联网，不显示 Key 内容
.\run.ps1 doctor

# 填 Key 后，用自动生成的简单图验证视觉接口；该操作会调用模型
.\run.ps1 probe

# 单文件（替换为你自己的输入路径）
.\run.ps1 parse 'D:\资料\论文.pdf' --output '.\output'

# 目录递归，跳过自身输出、缓存及虚拟环境
.\run.ps1 parse 'D:\资料' --recursive --category '技术资料' --output '.\output'

# 不联网的运行示例
.\run.ps1 parse '.\examples\sample.pdf' --offline --output '.\output\smoke'

# 正式解析 Office 时要求完整渲染，缺转换器则明确失败
.\run.ps1 parse 'D:\资料\流程说明.pptx' --require-renderer

# 有 Tesseract 和 chi_sim/eng 数据时可尝试本地 OCR
.\run.ps1 parse 'D:\资料\扫描件.pdf' --ocr
```

激活目标 Conda 环境后，也可把 `.\run.ps1` 替换为 `python -m doc2md`。默认输出目录固定在项目 `output`；显式传入的相对输入/输出路径以当前 PowerShell 工作目录为准。

上述示例在 CMD 中把 `.\run.ps1` 换成 `run.cmd`，并将路径的单引号换成双引号，例如：

```bat
run.cmd parse "D:\资料\技术文档.pdf" --output ".\output"
run.cmd parse ".\examples\sample.pdf" --offline --output ".\output\smoke"
```

如果运行 `.ps1` 后显示源码，说明它未被 PowerShell 执行；请改用 `run.cmd`。也可明确指定解释器：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 doctor`。`.cmd` 封装只对该子进程指定执行策略，不改变系统设置或 `.ps1` 文件关联，也会自动选择项目 Conda 环境。

命令最后输出 JSON 摘要，进度写入 stderr。退出码：`0` 全部成功，`2` 有部分成功/待处理/混合失败，`1` 全部失败或输入错误；`130` 表示中断。**退出码 2 不等于程序崩溃**：未填 Key 时保留图像、正文和 pending 标记是预期行为。

## 3. 各格式能力与外部依赖

| 输入 | 默认处理 | 完整性说明 |
|---|---|---|
| PDF | 原生文字/表格提取；位图区域和矢量图整体渲染；扫描页交给模型或本地 OCR | 不会把原生全文交给模型改写；复杂多栏顺序需抽查 |
| PNG/JPG/WebP | 保存图像，模型转录及解释内容 | 不填 Key 时 pending，不编造描述 |
| DOCX/PPTX + LibreOffice | 转换 PDF 后统一处理，保留转换副本 | 完整处理图形的首选路线，仍需人工核验 |
| DOCX/PPTX 无 LibreOffice | 原生正文、表格、位图及段落/幻灯片锚点 | 标为 partial；不承诺 SmartArt、浮动和组合矢量图完整 |
| DOC/PPT | 必须有 LibreOffice | 未找到转换器时返回明确错误 |

若需要 Office 完整渲染，请安装 LibreOffice，或在 `.env` 中指定现有程序：

```dotenv
DOC2MD_SOFFICE=C:\Program Files\LibreOffice\program\soffice.com
```

若需要离线文字识别，可安装 Tesseract 和简体中文、英文训练数据，配置：

```dotenv
DOC2MD_TESSERACT=C:\Program Files\Tesseract-OCR\tesseract.exe
DOC2MD_TESSDATA=C:\Program Files\Tesseract-OCR\tessdata
```

不安装 Tesseract 仍可通过 Qwen 处理扫描页；`--offline` 会禁止模型调用。如果扫描页已有本地 OCR 转录但图像释义未完成，仍会标为 partial。

## 4. 输出结构与来源

```text
output/
  batch_report.json
  <doc_id>/<revision>/<config_fingerprint>/
    document.md
    blocks.jsonl
    manifest.json
    parse.log.jsonl
    images/
    source/
      原文快照
      rendered.pdf         # Office 成功渲染时生成
```

- `document.md`：按页/幻灯片组织的正文、原图、释义和来源链接。
- `blocks.jsonl`：每行一个结构化块，包含稳定 block ID、类型、文本、来源、状态；供后续 DocBase 使用。
- `manifest.json`：文档哈希、模式、页数、模型、时间、缓存命中、实际接口返回 usage、未完成块和警告。
- `parse.log.jsonl`：未完成块及局部警告；全部失败的文档记录在批处理报告中。
- 图像、图像转录及对应释义共享 figure ID。图序 ID 由工具生成，不冒充原文印刷图号。
- 对外页序从 1 开始，坐标为保存 PDF 页面坐标；原生 DOCX 后备模式不填写虚假页码，PPTX 后备模式保留幻灯片号。
- `snapshot_path`、图像路径等以单篇结果目录为根；`original_path` 记录输入来源，长期引用应使用快照。

同一输入内容和配置重复运行更新同一个结果目录；图像成功释义复用 `.cache/vision`，上下文、模型或提示词版本变化会使缓存失效。无 Key/离线/失败不缓存为成功；填写 Key 后重新执行可补齐结果。完整产物会在校验来源快照与内容后复用。

每文档使用锁避免同时覆盖结果。正常退出自动释放；若进程被强制终止，确认该进程已退出后，仅移除报错中对应的 `<doc_id>.lock` 再重试。成功发布新产物前不会删除同文档旧结果；文档内容变化保留旧版本快照。

## 5. Python 调用

```python
from doc2md import parse_document, parse_path
from doc2md.config import Settings

settings = Settings.load()
result = parse_document(r'D:\资料\说明.pdf', r'D:\解析结果', settings)
report = parse_path(r'D:\资料', r'D:\解析结果', settings, recursive=True)
```

单文件接口在文件级失败时抛出异常；批量接口逐文件捕获并返回 `failed`。模型局部失败保留原生结果并标为 partial。输入文件不能位于输出目录内部，避免覆盖或递归解析自己的产物。

## 6. 检验与已知限制

```powershell
conda activate .\.conda
python -m pytest -q
python -m doc2md doctor
```

自动测试覆盖 PDF/图像/Office 后备解析、协议、缓存、错误响应、来源、版本和批处理隔离；不使用真实 Key。`probe` 返回成功后仍应人工确认识别到测试图的 `INPUT A → OUTPUT B`，再选少量真实页面核对节点、箭头和分支条件。

通用 PDF 阅读顺序、标题层级和图像区域检测为启发式方法；跨页合并表格、复杂多栏、手写和低清晰度图片需要人工核对。正文不删除页眉页脚以避免误删证据；图像释义为模型输出，不能替代原图。当前工具串行处理文档和模型请求，便于限制负载与追溯错误。

本工具只上传待解释的图像/页面及附近文本上下文到所配置的模型服务；`--offline` 不产生网络请求。整个文件不会作为附件直接上传，但扫描页渲染本身可能包含该页完整内容。

开发方案见上一级 `实验方案输出/doc2md工具开发方案.md`。真实模型及 Office/OCR 外部程序的验证状态见 `验证记录.md`。
