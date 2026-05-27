# Optimized Usage / 最低时间成本用法

本项目的日常目标是把个人研究流程压缩成一条清楚路径：

```text
收集资料 -> 确认文档库 -> 本地索引 -> 带证据检索 -> Markdown 输出 -> 维护/Codex 修复
```

## 一个入口

优先使用统一 CLI：

```bash
./scripts/pkb.sh workflow
./scripts/pkb.sh doctor
./scripts/pkb.sh open
```

`workflow` 说明下一步，`doctor` 做本地健康检查，`open` 启动并打开 Web App。

如果 `doctor` 显示 `PyMuPDF/fitz available: False`，PDF 导入需要先切换到带 PyMuPDF 的 Python，例如：

```bash
PKB_PYTHON=/path/to/python-with-pymupdf ./scripts/pkb.sh doctor
```

## 已有 PDF

```bash
./scripts/pkb.sh ingest /path/to/pdfs --topic "研究方向"
./scripts/pkb.sh ask "这个方向的核心问题是什么？"
./scripts/pkb.sh markdown "生成带证据的研究摘要" --type research_summary
```

默认保留原始 PDF，默认使用本地解析、本地索引和本地检索。私密资料不要外发 API。

## 只有研究方向

```bash
./scripts/pkb.sh discover "研究方向" --keywords "关键词1,关键词2" --max-results 8
./scripts/pkb.sh open
```

文献发现来自 OpenAlex 候选元数据；全文下载仍必须遵守用户已有合法访问权限，不绕过登录、验证码、付费墙、403/429 或机构访问提示。

## ACS 期刊追踪

从 ACS 计划书中固化为本项目的稳定入口：

```bash
./scripts/pkb.sh acs init
./scripts/pkb.sh acs run --profile gaa_vertical_ge_si
./scripts/pkb.sh acs status
./scripts/pkb.sh acs export --format markdown
./scripts/pkb.sh acs export --format csv
```

`acs run` 使用 `config/acs_journals.json` 和 `config/acs_profiles.json`，通过 OpenAlex 公共元数据发现候选文章，写入本地 SQLite，并按 DOI 去重；没有 DOI 时按 title + url 生成稳定 key。`csv` 是 Excel-compatible 导出，不依赖 openpyxl。

人工筛选后可标记状态：

```bash
./scripts/pkb.sh acs mark --doi "10.xxxx/yyyy" --status must_read
./scripts/pkb.sh acs mark --doi "10.xxxx/yyyy" --status archived --notes "与当前方向无关"
```

状态只改变本地研究队列，不会自动下载全文。下载或导入全文仍走 DOI 下载器和合法访问边界。

## 结果不准或流程异常

```bash
./scripts/pkb.sh codex --reason "关键论文没有被检索到"
./scripts/pkb.sh codex --audit-id aud_xxx --expected "某篇论文或 chunk 应进入前 5"
```

生成的 handoff 默认写入：

```text
outputs/maintenance/
```

把该文件交给 Codex 时，应要求先复现、再做最小修复、补测试，并保留 raw files 和隐私边界。

## 当前基线检查

推荐每次维护先运行：

```bash
git status --short
./scripts/pkb.sh doctor
python3 -m pytest -q
node --check static/app.js
```

如果当前环境没有 `pytest`，可用项目的 unittest fallback：

```bash
PYTHONPYCACHEPREFIX=/tmp/pkb-pycache python3 -m unittest discover -s tests -v
```

最终报告必须区分“已运行且通过”“已运行但失败”“未运行及原因”。
