# Codex 执行指南：Personal Knowledge RAG Webapp

## 当前仓库说明

本仓库已经保留本执行包的长期文件：

```text
AGENTS.codex.template.md
docs/codex_prompt_pack_cn.md
docs/codex_execution_guide_cn.md
docs/optimized_usage.md
scripts/pkb.sh
scripts/pkb.py
```

根目录 `AGENTS.md` 已合并核心规则；`AGENTS.codex.template.md` 仅作为完整模板参考，不应盲目覆盖根目录规则。

## 放置方式

建议把本套文件放入仓库：

```text
personal-knowledge-rag-webapp-main/
  AGENTS.md
  docs/
    codex_prompt_pack_cn.md
    codex_execution_guide_cn.md
```

`AGENTS.md` 是给 Codex 自动读取的项目规则；`docs/codex_prompt_pack_cn.md` 是你给 Codex 派任务时复制的提示词。

如果仓库已有 `AGENTS.md`，不要盲目覆盖。优先把本套 `AGENTS.md` 中的规则合并进去，尤其保留以下部分：

- Product Mission
- Startup Protocol
- Operating Path
- Modification Rules
- Testing Matrix
- Final Report Format

## 推荐使用方式

### 第一次接管

把下面这段给 Codex：

```text
请先阅读 AGENTS.md 和 docs/codex_prompt_pack_cn.md。先不要改代码。请按“通用起手式：接管项目并做体检”执行，并用中文报告当前项目状态、最短使用路径、主要风险和下一步建议。
```

### 日常修复

优先在项目里生成 handoff：

```bash
./scripts/pkb.sh codex --reason "具体问题"
```

然后把生成的 `outputs/maintenance/*.md` 发给 Codex，并补一句：

```text
请严格按这个 handoff 复现、修复、补测试并报告。不要删除 raw files，不要外发私密文档，不要覆盖 .env。
```

### 检索不准

如果某篇论文或某个 chunk 应该命中但没命中：

```bash
./scripts/pkb.sh codex --audit-id aud_xxx --expected "某篇论文或某个 chunk 应该进入前 5"
```

然后使用提示词文件里的“检索不准：指定期望命中的论文/chunk”。

## 派任务原则

每个 Codex 任务尽量包含 6 个要素：

```text
目标：我要达到什么结果。
上下文：相关路径、错误、audit id、topic、PDF_DIR。
边界：不能删 raw files，不能外发私密文档，不能绕过付费墙。
修改范围：优先改哪些文件。
验收标准：哪些命令必须通过，什么输出算完成。
报告格式：中文，列修改文件、验证命令、风险和使用方式。
```

## 推荐任务粒度

适合一次交给 Codex 的任务：

- 修一个具体 CLI 命令。
- 修一个检索 audit 中的具体 miss。
- 优化首页下一步建议。
- 给一个路径补测试。
- 生成或修复 maintenance handoff。

不建议一次交给 Codex 的任务：

- “把整个系统做完”。
- “全面重构”。
- “自动下载所有论文”。
- “无条件接入外部 API 处理我的资料”。

## 最小可用流程

已有 PDF：

```bash
./scripts/pkb.sh ingest /path/to/pdfs --topic "你的方向"
./scripts/pkb.sh ask "这个方向的核心问题是什么？"
./scripts/pkb.sh markdown "生成带证据的研究摘要" --type research_summary
```

只有方向：

```bash
./scripts/pkb.sh discover "你的方向" --keywords "关键词1,关键词2" --max-results 8
./scripts/pkb.sh open
```

维护修复：

```bash
./scripts/pkb.sh doctor
./scripts/pkb.sh codex --reason "关键论文没有被检索到"
```

## 交付检查清单

Codex 完成任务后，至少检查：

- 是否说明具体改了哪些文件。
- 是否运行测试，而不是只说“应该可以”。
- 是否给出你下一步可复制的命令。
- 是否保留 raw files 和私密边界。
- 是否把无法验证的内容标明为未验证。
- 是否没有把无证据结论当作事实。
