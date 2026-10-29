# doc2md 工具独立开发方案

## 1. 目标、位置和本次实现范围

最终工具工程位于：`C:\Users\Lenovo\Desktop\研一\大模型软件开发\doc2md`。

实现一个可从 PowerShell 调用、也可被后续 DocBase/Service 导入的 Python 工具：将 PDF、Word、PPT 及图片转为带来源信息的 Markdown；解释图像中的文字、节点、箭头、条件和步骤；同时输出结构化内容块供后续检索。

本次实现不依赖 CodeAgent 在运行时参与。Python 控制输入、转换、提取、模型调用、缓存及输出；用户已经配置的 Claude Code + DeepSeek 可在后续开发中辅助修改代码和分析日志，不需要修改其模型配置，也不应假设其 DeepSeek 接口能代替视觉接口。

## 2. 已明确的模型配置

```dotenv
DOC2MD_BASE_URL=https://ai.gitee.com/v1
DOC2MD_MODEL=Qwen3.6-Plus
DOC2MD_API_KEY=
```

创建工程 `.env`，API Key 留空，等待用户填写；提供 `.env.example` 及 `.gitignore`。仅读取 `DOC2MD_*` 变量，避免与 Claude Code 的密钥、模型和 API 地址互相覆盖。

按 Gitee 官方集成说明采用兼容 Chat Completions 的 HTTP 请求：`POST /chat/completions`，与 base_url 拼接后为 `https://ai.gitee.com/v1/chat/completions`。本地图片编码为 data URL，使用 `text` 和 `image_url` 消息块。模型名称严格保留用户给定大小写，不擅自更换服务地址或模型。[Gitee 集成说明](https://ai.gitee.com/docs/integrations/intro)

模型提供方公开说明中 Qwen3.6-Plus 包含图像输入能力，但当前 Gitee 账号权限、data URL 支持、限流和输出长度需要真实连通性测试确认，不能以文档代替实测。[Qwen 模型说明](https://qwen.ai/apiplatform)

初版以 HTTP 客户端直接请求，不增加 Agent 框架和 SDK 依赖。默认不发送未经验证的 `response_format`、thinking 等扩展参数；通过提示词要求 JSON，接收后本地校验。只读取最终 `message.content`，不把推理内容写入正文。

## 3. 资源、依赖与实际环境

| 资源 | 角色 | 实现安排 |
|---|---|---|
| Python 3.11 / Conda | 程序运行 | 根据 environment.yml 创建项目专用 Conda 环境 |
| PyMuPDF | PDF 文字、页面和图像区域 | 项目环境安装 |
| Pillow | 图片归一化和模型输入缩放 | 项目环境安装 |
| httpx、python-dotenv | 请求模型、读取本地配置 | 项目环境安装 |
| python-docx、python-pptx | 原生 Office 后备解析 | 处理正文、表格、图片及原生锚点 |
| LibreOffice | Office 渲染为 PDF，保留组合图和矢量图整体 | 当前 PATH/常用位置未找到；可配置程序路径；缺失时显式降级 |
| Tesseract + 中文/英文语言数据 | 本地 OCR | 可选；当前 PATH/常用位置未找到；缺失时可由 Qwen 识别扫描页 |
| pytest | 本地回归测试 | 验证解析、元数据、模型协议、失败处理和缓存 |

环境统一由 Conda 管理，默认前缀为项目 `.conda`；Conda 管理 Python/pip，应用依赖由该环境内 pip 按锁定约束安装，不修改 base 环境。模型推理通过远端 API，因此本地不要求部署 Qwen 权重或配置 GPU。

## 4. 输入支持与降级边界

| 输入 | 首选流程 | 外部依赖缺失时 |
|---|---|---|
| PDF | 逐页原生提取 + 表格检测 + 页面/图像区域渲染 | 原生正文可离线处理；扫描页依赖本地 OCR 或模型 |
| DOCX | 有 LibreOffice 时先转 PDF，再统一解析 | 原生提取段落、表格、内嵌图片；页码为空，保存段落锚点；记录浮动/矢量图可能遗漏 |
| PPTX | 有 LibreOffice 时先转 PDF，再统一解析 | 按幻灯片提取文本、表格、位图，保存 slide/shape 锚点；组合流程图不假装已完整解析 |
| DOC/PPT | LibreOffice 转 PDF | 无转换程序时报告失败，并说明需安装/配置转换器 |
| PNG/JPG/JPEG/WebP | 图片归一化 → OCR 或模型分析 | 无密钥时保存原图和待解析状态，不伪造图像释义 |

`--require-renderer` 可要求 Office 必须先渲染，未找到转换器则直接失败，适用于正式评估。默认提供原生后备解析以便当前环境使用，但报告为部分成功而不是完整多模态成功。

## 5. 模块与工程目录

```text
doc2md/
  .env                     # 创建时 Key 为空；不覆盖用户后续填写值
  .env.example
  .gitignore
  pyproject.toml
  requirements.txt
  requirements.lock.txt
  environment.yml          # Conda 环境声明
  README.md
  setup.ps1                # 创建/更新项目 Conda 环境
  run.ps1                  # 通过 conda run 启动
  setup.cmd                # CMD / Anaconda Prompt 安装入口
  run.cmd                  # CMD / Anaconda Prompt 运行入口
  src/doc2md/
    __init__.py
    __main__.py
    cli.py                 # parse / doctor / probe
    config.py              # 配置和外部程序定位
    vision.py              # Qwen 请求、JSON 校验、缓存、重试
    extract.py             # PDF/图片/Office 解析与来源标记
    pipeline.py            # 文档身份、版本、批处理、输出
  tests/
  examples/                # 小型合成样本和离线示例输出
```

对外提供 `parse_document()` 和 `parse_path()`，与命令行共用处理流程。阶段二使用结构化块，不重新猜测 Markdown 中的来源。

## 6. 具体开发流程

### D1：配置与诊断

建立独立配置，提供 `doctor`，输出 Python、依赖、转换器、OCR、模型名称、base_url 及密钥“已设置/未设置”。任何日志不打印密钥或 Authorization 头。`probe` 生成一张简单测试图发给模型验证视觉能力，不发送用户文档；该命令仅在用户填写 Key 后显式执行。

### D2：输入管理与文档版本

支持单文件、目录和递归，按文件名稳定排序；排除 `.conda`、`.conda-pkgs`、旧 `.venv`、输出目录、缓存及 Office 临时文件。以来源规范路径生成 doc_id，以文件 SHA256 生成 revision，另外保存解析配置指纹。相同源文档及配置写到同一结果目录，不产生重复块。

输出原文快照和元数据，防止原文改变后历史引用失效。输出采用临时目录完成后发布；替换只限本工具对应结果目录，失败保留可解释的报告。

### D3：PDF 与扫描页

读取文字块、字号、坐标与表格位置，先按页面分组，再处理标题、段落与表格；过滤明确的重复页眉页脚需谨慎，初版优先保留而非误删正文。原生正文不全量交给模型重写。

提取位图并按显示区域裁剪；对矢量/组合图使用有向图形区域或页面渲染，保证箭头关系不因只提取位图而丢失。复杂多栏页面的阅读顺序作为已知检查项，不承诺通用版面识别完全正确。

扫描页无可用文字时：可选本地 Tesseract OCR；默认让 Qwen 对整页同时提取文字和解释结构。模型成功后写页面转录与图像释义，离线且无 OCR 时标为 pending，不产生“解析成功但没有文字”的假结果。

### D4：图片与模型释义

图片统一 EXIF 方向，限制长边及像素数；默认一图一请求，并发 1。优先处理图表/流程图及扫描页，重复图像复用缓存；保留原始区域文件，不把缩小后的输入当作原图。

提示词要求返回：`transcription、summary、nodes、edges、steps、uncertainties`。其中 edges 只包含可见且方向明确的关系；标签不清楚时留空/说明不确定；不依据标题补写未出现在图上的事实。

JSON 解析后校验字段类型和边引用；不合法响应有限重试，不用同一模型的自述置信度当作人工准确率。缓存键包含图片内容、上下文、模型、base_url、提示词版本和参数；失败不缓存为成功。

重试覆盖超时、429 和服务端错误，采用退避并限制次数；401/403 等配置错误立即报告。缺 Key 时无需请求，图像块为 pending；提供 `--offline` 强制不联网。超时或返回被截断时记录失败，不能输出截断释义充当完整结果。

### D5：Office 转换与后备解析

有 LibreOffice 时逐文档转换，使用独立临时输出和 profile；子进程使用参数数组及超时，检查真实 PDF 产物。有转换器但转换失败时记录错误，不能静默伪装为完整结果。

原生 DOCX 按正文 XML 顺序处理段落、表格和图片关系，锚点包括段落序号；PPTX 按页、形状位置递归提取组内文本/表格/位图。复杂 SmartArt、图表、浮动对象和矢量组合图需要渲染模式核验；原生后备解析在 manifest 中标注 partial。

### D6：输出与后续检索交接

```text
output/<doc_id>/<revision>/<config_fingerprint>/
  document.md
  blocks.jsonl
  manifest.json
  images/
  source/                  # 原始文件快照；Office 可附 rendered.pdf
```

批量输出另外生成 `batch_report.json`。状态至少包括 `ok、partial、failed`；块级区分 `ok、pending、failed`。有待解释的图片、模型错误或后备解析限制时文档为 partial。

内容块包括 schema_version、doc_id、revision、block_id、type、text、heading_path、source、status；来源含原文件快照、page_index（对外从 1 开始）、slide_number、anchor、bbox、figure_id、image_path。不能确定的字段为 null。

Markdown 按页/幻灯片/段落组装，原图与释义相邻，标明 block ID 和来源链接；纯文本、模型转录和模型解释分开标记，便于 DocBase 做来源过滤。块 ID 在相同输入和配置下稳定。

### D7：命令行与使用体验

以下命令将随工程实现：

```powershell
cd 'C:\Users\Lenovo\Desktop\研一\大模型软件开发\doc2md'
.\setup.ps1
.\run.ps1 doctor
.\run.ps1 parse 'D:\资料\技术文档.pdf' --output '.\output'
.\run.ps1 parse 'D:\资料' --recursive --output '.\output'
.\run.ps1 parse '.\examples\sample.pdf' --offline --output '.\examples\output'
.\run.ps1 probe
```

命令退出码：0 全部成功；2 存在部分成功/待补充；1 输入、配置或全部处理失败。进度输出到 stderr，最终摘要可使用 JSON；便于后台捕获和自动化调用。

## 7. 检验安排

| 检验范围 | 方法 | 通过标准 |
|---|---|---|
| PDF 正文/表格/矢量图 | 使用人工可判定的合成样本及页面渲染 | 文字可读；图像保存；表格与图块来源正确 |
| 图像与扫描页 | 离线运行 + Mock 正常模型响应 | 离线明确 pending；正常响应写入转录、节点/边和描述 |
| 模型协议 | Mock HTTP，检查 URL、model、Bearer、data URL | 严格使用给定配置，无密钥输出 |
| 异常处理 | 模拟 401、429、超时、错误 JSON、空响应、截断 | 重试策略正确；错误状态真实；失败不进入成功缓存 |
| 缓存与恢复 | 同图重复处理、修改上下文/模型参数、填 Key 后重跑 | 成功缓存复用；配置变化失效；pending 可补齐 |
| Office 后备 | 自动生成 DOCX/PPTX 样本 | 正文/表格/图片可提取；锚点正确；缺渲染明确 partial |
| Office 正式渲染 | 配置 LibreOffice 后转换三类 Office 样本 | PDF 存在；页/幻灯片及图像来源一致 |
| 目录批处理 | 含中文、空格、同名文件、输出子目录 | 不漏有效输入、不递归处理自身输出、不覆盖不同来源 |
| 来源核对 | 抽查 20 块及 10 幅图（样本不足扩充） | 文档、页/锚点、图像匹配；演示图无关键关系错误 |
| 真实 Qwen | 用户填 Key 后执行 probe，再选小样本解析 | 真正识别测试图及文档图像，记录实际模型耗时与可得 usage |

本轮先执行不需要密钥的自动测试与离线解析，不消耗用户尚未填写的模型额度。真实 API、LibreOffice 与 Tesseract 缺失部分单独标明验证状态。通用 PDF 渲染/提取方法参照 [PyMuPDF 文档](https://pymupdf.readthedocs.io/en/latest/recipes-images.html)。

## 8. 完成定义

工程可安装、可直接运行；API Key 配置为空；离线 PDF/图片与 Office 后备路径可复现；模型协议、输出结构、批处理和异常处理有测试；README 说明真实模型连通性和外部依赖的验证入口。完整多模态质量验收须在用户填 Key 并提供或安装 Office 渲染依赖后执行，结果如实补充到验证记录中。
