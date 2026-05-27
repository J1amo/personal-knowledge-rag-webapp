# ACS Literature Tracker / ACS 文献追踪

这个入口把 `pubs.acs.org` 计划书里最适合稳定落地的部分固化到本项目：配置驱动、公开元数据发现、本地 SQLite 去重、研究相关度评分、阅读状态和 Markdown/CSV 输出。

## 设计边界

- 使用 OpenAlex 公共元数据，不直接抓取 ACS 页面。
- 不绕过登录、验证码、付费墙、403/429 或机构访问提示。
- 默认不下载 PDF，不自动导入全文。
- 使用 Python 标准库和现有 SQLite，不新增 PyYAML、openpyxl、队列系统或前端复杂度。
- CSV 使用 `utf-8-sig`，可直接用 Excel 打开。

## 配置

期刊配置：

```text
config/acs_journals.json
```

研究 profile 配置：

```text
config/acs_profiles.json
```

默认 profile 是 `gaa_vertical_ge_si`，包含 GAA、Vertical Ge/Si、nanosheet、nanowire、SiGe、selective etching、ALD/high-k 等关键词。可以继续添加 profile，但建议先保持每个 profile 的关键词少而明确。

profile 中 `search_query` 和 `search_keywords` 用于 OpenAlex 发现候选，应该短而宽；`include_keywords`、`strong_keywords` 和 `strong_combinations` 用于本地评分，可以更贴近具体研究方向。这样可以避免“搜索过窄导致 0 结果”，同时仍让导出按研究相关度排序。

## 命令

初始化默认配置：

```bash
./scripts/pkb.sh acs init
```

运行一次候选文献发现：

```bash
./scripts/pkb.sh acs run --profile gaa_vertical_ge_si
```

查看本地队列状态：

```bash
./scripts/pkb.sh acs status
```

导出 Markdown digest：

```bash
./scripts/pkb.sh acs export --format markdown
```

导出 Excel-compatible CSV：

```bash
./scripts/pkb.sh acs export --format csv
```

人工标记阅读状态：

```bash
./scripts/pkb.sh acs mark --doi "10.xxxx/yyyy" --status must_read
./scripts/pkb.sh acs mark --doi "10.xxxx/yyyy" --status read
./scripts/pkb.sh acs mark --doi "10.xxxx/yyyy" --status archived --notes "暂不相关"
```

可用状态：

```text
new
maybe_relevant
highly_relevant
must_read
read
archived
```

## 数据表

本功能写入主 SQLite：

```text
acs_literature_runs
acs_literature_papers
acs_literature_run_items
```

去重规则：

```text
有 DOI：doi:<normalized doi>
无 DOI：title_url:<sha256(title + url)>
```

## 评分规则

默认评分保留计划书中的稳定版本：

```text
标题命中 strong keyword: +3
摘要命中 strong keyword: +2
标题命中 include keyword: +1
摘要命中 include keyword: +0.5
命中 strong combination: +4
命中 high value target journal: +1
```

导出分组：

```text
Must Read Candidates: status=must_read 或 score >= 8
Maybe Relevant: status=highly_relevant/maybe_relevant 或 score >= 4
Low Priority: 其他未读候选
Read / Archived: 已人工处理项目
```

## 推荐流程

1. 每周运行 `acs run`。
2. 导出 Markdown digest，快速扫标题、摘要和 DOI。
3. 用 `acs mark` 标记 `must_read` 或 `archived`。
4. 对 `must_read` 的 DOI，再使用 DOI 下载器在合法访问权限内获取全文。
5. 下载后走 `pkb ingest` 进入本地证据库。
