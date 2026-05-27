# Personal Multimodal Knowledge Base Web App

本项目是本地优先、API 可选的 Personal Research OS 基座。当前 MVP 支持论文 PDF：原始文件保留、解析到统一 canonical structured data layer、分别建立 local/API/BM25/graph 索引覆盖记录，并在查询时做覆盖感知检索、citation 打包、retrieval audit 和 Markdown-first 输出。

## 启动

```bash
cd personal-knowledge-rag-webapp
./scripts/start.sh
```

打开：

```text
http://127.0.0.1:8765
```

## 最短使用路径

日常优先使用统一入口：

```bash
./scripts/pkb.sh workflow
./scripts/pkb.sh doctor
./scripts/pkb.sh open
```

已有 PDF：

```bash
./scripts/pkb.sh ingest /path/to/pdfs --topic "研究方向"
./scripts/pkb.sh ask "这个方向的核心问题是什么？"
./scripts/pkb.sh markdown "生成带证据的研究摘要" --type research_summary
```

结果不准或流程异常时生成 Codex handoff：

```bash
./scripts/pkb.sh codex --reason "关键论文没有被检索到"
```

更多场景见 `docs/optimized_usage.md`，可复用 Codex 提示词见 `docs/codex_prompt_pack_cn.md`。

当前实现使用 Python 标准库 HTTP server、SQLite、本机已有的 PyMuPDF，以及 vendored Mozilla PDF.js。如需指定带有项目依赖的解释器，可设置 `PKB_PYTHON`。大型模型、OCR 模型、embedding 模型和权重默认放在项目内已忽略的目录：

```text
./local_models
```

## 自启动

本项目按 macOS 本地 Web App 自启动方式设计：项目内保留可审计脚本，用户级 `LaunchAgent` 负责登录后自动拉起服务。

常用入口：

```bash
./scripts/webapp.sh status
./scripts/webapp.sh start
./scripts/webapp.sh stop
./scripts/webapp.sh restart
./scripts/webapp.sh logs
```

安装登录自启动：

```bash
./scripts/webapp.sh load
```

移除登录自启动：

```bash
./scripts/webapp.sh unload
```

安装后会生成：

```text
~/Library/LaunchAgents/com.maber2k.personal-knowledge-rag-webapp.plist
```

LaunchAgent 使用 `scripts/run_server.sh` 前台运行服务，`RunAtLoad=true`，异常退出时重启。`scripts/webapp.sh load` 会把当前项目 Python 写入 plist 的 `PKB_PYTHON`，避免登录自启动落回 macOS 系统 Python。日志写入 `logs/`，运行时 PID 写入 `run/`，两者都已加入 `.gitignore`。

本轮按全局 Web App 自启动标准保留了三层入口：

- `scripts/start.sh`: 开发期前台启动。
- `scripts/run_server.sh`: LaunchAgent 前台进程入口。
- `scripts/webapp.sh`: `status/start/stop/restart/load/unload/logs` 管理入口。

## 页面

- `Dashboard`: sources/chunks/papers、后端状态、索引覆盖、最近 ingestion、失败记录。
- `Upload / Ingest`: 上传单个 PDF 或导入文件夹；选择 domain、topic、sensitivity。
- `文献发现`: 按主题、关键词、期刊/ISSN 和年份从 OpenAlex 获取可信 DOI 与英文摘要，可选调用本地翻译端点生成中文或中英双语显示。
- `DOI 下载器`: 输入 DOI 或 DOI 列表，通过用户已有合法访问权限下载 PDF，保存 metadata/log，可选加入文档库。
- `Query`: 分开选择 Retrieval Mode 和 Analysis Model，返回 answer 与 evidence。
- `Sources / 文档库`: 浏览 document/source 元数据、raw path、hash、chunks，并打开 PDF。
- `处理状态`: 查看 imported/parsed/chunked/indexed/failed 等流水线状态。
- `检索审计`: 查看 query、backends、merged results、dropped duplicates、citations、warnings，并能展开 audit detail、生成 Codex 修复指导。
- `PDF 阅读器`: 通过 source_id 打开原始 PDF，使用本地 PDF.js 渲染并跳转页面；chunk/citation 可做页级 focus。
- `Markdown 输出工作台`: 生成 research summary、literature review、presentation guidance、Codex prompt 等 `.md` 文件。
- `Research`: 管理研究项目、项目资料范围和可插拔 research pack 输出；首个 pack 为 `research_packs/gaa_vertical/`。
- `Maintenance / 维护中心`: 查看 index coverage、健康报告、missing/stale/failed chunks，触发索引重建、备份 DB、生成 Codex 修复任务。
- `Compare / Evaluation`: 对 Fast Local、API Only、All Available、Strict Exhaustive 做命中和重叠比较。

## 数据层

原始文件默认复制并保留在：

```text
data/raw/papers/
data/raw/chats/
data/raw/images/
data/raw/docs/
data/raw/notes/
data/raw/misc/
```

SQLite 主数据库：

```text
db/knowledge.sqlite
```

主数据库存 canonical 内容和覆盖记录：

- `sources`
- `chunks`
- `multimodal_elements`
- `index_coverage`
- `parser_logs`
- `query_logs`
- `documents`
- `source_files`
- `parsed_artifacts`
- `citations`
- `retrieval_audits`
- `retrieval_results`
- `ingestion_jobs`
- `processing_errors`
- `markdown_outputs`
- `research_projects`
- `project_sources`
- `research_packs`
- `local_llm_runs`
- `doi_download_jobs`
- `doi_download_items`
- `doi_metadata`

原始文件是最高级别证据，系统不会自动删除 raw files。`.gitignore` 已排除 `.env`、`data/raw/`、`db/`、`indexes/`、`cache/`、`backups/`。

Markdown 输出默认写入 `outputs/`，该目录也被 `.gitignore` 排除，因为输出可能包含私有资料片段。

## Research Workspace / Packs

Research workspace 在通用资料库之上增加项目边界：同一篇 source 可以加入多个 research project，检索时通过已有 `source_ids` filter 限定项目资料范围，不复制 raw files，也不把领域字段写入 `sources` 或 `chunks`。

首个领域包位于：

```text
research_packs/gaa_vertical/
```

该 pack 保存 GAA vertical 方向的 ontology、输出模板和低难度第一步 proposal 模板。通用 core 仍只负责 ingestion、chunk、index、retrieval、citation、audit 和隐私边界。

DOI 下载日志默认写入 `outputs/doi_download_logs/`，下载器浏览器 profile 默认写入 `cache/browser_profiles/doi_downloader/`。二者都不应提交。

## DOI Downloader

DOI Downloader 用于用户明确提供 DOI 的文章级 PDF 下载。它只使用用户已经拥有的大学图书馆、机构订阅、出版社账户、VPN/EZproxy/Shibboleth 或 open access 权限，不绕过登录、验证码、付费墙、403/429 或机构警告。

检查运行依赖：

```bash
./scripts/check_doi_downloader.py
```

安装 Playwright：

```bash
python3 -m pip install playwright
python3 -m playwright install chromium
```

下载单篇：

```bash
./scripts/download_by_doi.py --doi "10.xxxx/yyyy" --out data/raw/papers
```

批量下载：

```bash
./scripts/download_by_doi.py --doi-file dois.txt --out data/raw/papers --max-items 10
```

`--max-items` 表示每批最多处理多少个 DOI；同一次任务会按批处理完整的去重 DOI 列表。

需要机构登录时：

```bash
./scripts/download_by_doi.py --doi-file dois.txt --headed --allow-manual-login --manual-login-timeout-seconds 900
```

勾选/传入手动等待后，登录页和机构访问页会保持可见浏览器，等待用户完成合法授权访问；验证码和出版社安全验证页会记录为阻断状态，不再让自动化浏览器反复冲撞。未解决的验证码、429 或可疑流量仍会停止批次。

如果出版社安全验证在自动化浏览器里循环不通过，改用真实浏览器人工接管模式：

```bash
./scripts/download_by_doi_chrome_handoff.py --doi-file dois.txt --auto-ingest --manual-login-timeout-seconds 900
```

该模式启动一个独立的真实 Chrome handoff 会话，默认后台运行并复用同一个标签页，不会每篇 DOI 都弹到前台；用户只负责登录/验证，脚本在页面通过后自动找 PDF 链接、下载、保存 metadata，并按需加入文档库。若页面明确显示学校未提供访问权限，会记录为 `blocked_by_access` 并继续下一篇。可选 `--focus-on-manual` 只在需要人工操作时把 Chrome 带到前台。

Web UI 默认勾选 DeepSeek 页面判断，但只有设置 `DEEPSEEK_API_KEY` 后才会实际调用；CLI 默认同样启用，可用 `--no-deepseek` 关闭。该模式只发送页面标题、可见文本摘要和链接候选，不发送 cookies、账号信息或 PDF 文件，也不会绕过验证码、登录、付费墙或访问控制。

默认不自动 ingestion；如需下载后加入文档库：

```bash
./scripts/download_by_doi.py --doi "10.xxxx/yyyy" --auto-ingest
```

默认等待策略：页面操作 jitter `0.3-1.2s`，文章间隔 `15-25s`，并发 `1`，单次默认最多 `10` 篇、绝对最多 `20` 篇。Fast mode 默认关闭，仅小批量使用，文章间隔 `5-10s`、最多 `5` 篇。

详细说明见 `docs/doi_downloader.md`。

## Local LLM / Gemma4

Markdown 输出工作台支持 OpenAI-compatible 本地 Gemma4 端点：

```text
GEMMA4_OPENAI_BASE_URL=http://127.0.0.1:1234/v1
GEMMA4_MODEL=gemma4
```

未配置本地端点时，系统不会编造 polished claims，而是输出 deterministic scaffold、evidence、citation map、missing information notes 和人工检查清单。

诊断入口：

```bash
./scripts/check_local_llm.py
```

Web API:

```text
GET /api/local-llm/status
```

文献发现页的 DOI、题名、期刊、年份和英文摘要来自 OpenAlex Works API；本地模型只用于摘要翻译，不作为 DOI 或文章元数据来源。可选本地翻译端点：

```text
TRANSLATION_LLM_BASE_URL=http://127.0.0.1:18181/v1
TRANSLATION_LLM_MODEL=hy-mt2
# 或 HYMT2_OPENAI_BASE_URL / HYMT2_MODEL
```

Web API:

```text
POST /api/literature/discover
```

## 索引层

索引是 canonical chunks 的派生物，不是新的解析管线。

- Local vector: `indexes/local_vector/local_hash_embedding_v1.sqlite`
- API vector: `indexes/api_vector/api_text_embedding_3_large_v1.sqlite`
- BM25: `indexes/bm25/bm25_v1.sqlite`
- Graph/entity: `indexes/graph/graph_entities_v1.sqlite`

不同 embedding model 使用不同 index file 和 `index_name`，不会混入同一向量索引。每个 chunk 在 `index_coverage` 中记录是否进入 local vector、API vector、BM25、graph，以及错误、版本、维度和 content hash。

## 检索模式

- `Fast Local`: local vector + BM25 + graph，不调用 API。
- `API Only`: API vector，只用于 policy 允许的公开范围。
- `All Available Indexes`: local vector + API vector + BM25 + graph，公开论文默认模式。
- `Private Local Only`: local vector + BM25 + graph，禁止 API 检索和 API 分析。
- `Strict Exhaustive`: 所有可用索引 + full scan + adjacent expansion。

检索结果按 canonical `chunk_id` 合并去重。若同一 chunk 被多个索引命中，只保留一份 evidence，并记录：

```json
{
  "chunk_id": "...",
  "found_by": ["local_vector", "bm25", "graph"],
  "ranks": {"local_vector": 1, "bm25": 3}
}
```

## 隐私

私密资料默认 `Private Local Only`。当 sensitivity 是 `private` 或 `confidential` 时，API retrieval 和 API LLM 默认被拦截，只有用户在 UI 勾选 `Allow private API` 后才会尝试 API。API key 只从 `.env` 读取。

## 当前 MVP 边界

已实现：

- PDF paper ingestion
- raw file retention
- canonical sources/chunks/schema
- multimodal placeholder rows for images/captions
- duplicate detection by `file_hash`
- local vector/BM25/graph indexes
- API vector/LLM adapters with privacy guard
- coverage-aware retrieval and merged evidence
- Web App six pages
- local extractive grounded answer
- PDF.js 本地阅读器和 citation/page focus
- retrieval audit detail 与 one-click Codex repair guidance
- real PDF evaluation 脚本；无 3-5 篇公开研究 PDF 时会明确输出 blocked report
- DOI Downloader MVP：CLI + Web UI + Playwright persistent profile + metadata sidecar + download logs + optional ingestion

预留但未完整实现：

- RAG-Anything 深度多模态解析
- WeChat 复杂导入
- OCR/image embedding
- 真正本地 LLM 后端
- API cost 精确统计

## 评估

核心测试：

```bash
PYTHONPYCACHEPREFIX=/tmp/pkb-next-pycache python3 -m unittest discover -s tests -v
```

真实公开论文评估：

```bash
./scripts/evaluate_real_pdfs.py --pdf-dir /path/to/public-research-pdfs
```

默认只扫描项目内 `data/raw/papers/`，避免误碰桌面或下载目录里的私密 PDF。若不足 3 篇公开研究 PDF，脚本会在 `outputs/evaluation/` 生成 blocked report。
