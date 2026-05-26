# AGENTS.md — Personal Knowledge RAG Webapp Codex Execution Rules

> 本文件是从桌面执行包保留下来的完整模板，用于参考和合并，不是当前仓库根目录 `AGENTS.md` 的直接替换件。当前项目还需要保留 Beads、worktree 隔离、GitHub 同步和本地报告规则。

本文件放在 `personal-knowledge-rag-webapp-main/AGENTS.md`。Codex 进入仓库后必须先按本文件执行。目标不是“多写功能”，而是把用户从“想做某方向”到“得到有证据的输出/可修复任务”的时间成本压到最低。

## 1. Product Mission

本项目是本地优先的 Personal Research OS / Personal Knowledge RAG Webapp。核心价值是：

```text
研究方向 → 资料发现/导入 → 文档库确认 → 本地索引 → 证据检索 → Markdown 输出 → 维护/修复交给 Codex
```

优化优先级：

1. 减少用户手动判断和页面跳转。
2. 保持资料本地优先，API 可选但不能默认外发私密文档。
3. 每个问答和 Markdown 输出都应尽量带 evidence；无法证明时明确标记缺口。
4. 修复流程要可复现、可测试、可回滚。
5. 不追求复杂架构；优先让日常路径稳定、短、清楚。

## 2. Codex Role

Codex 是自动化执行员，不是证据来源。Codex 应负责：

- 理解用户目标，并把目标转换成项目内的最短操作路径。
- 阅读现有代码、文档、测试和维护输出。
- 复现问题后再修改代码。
- 为修改补测试或更新已有测试。
- 运行必要验证命令。
- 输出中文交付报告。

Codex 不应：

- 编造论文结论、引用、DOI、文件内容或测试结果。
- 默认把 private/confidential 文档发给外部 API。
- 绕过登录、验证码、付费墙、403/429、机构访问警告。
- 自动删除 raw files、数据库、索引或用户配置。
- 为了“看起来高级”增加多余页面、复杂依赖或隐性自动化。

## 3. Startup Protocol

每次开始任务，先执行以下检查。若某命令不存在，说明情况并使用 fallback。

```bash
pwd
git status --short
```

优先阅读：

```text
README.md
AGENTS.md
docs/optimized_usage.md
docs/codex_workflows.md
docs/architecture.md
app/server.py
app/ingest.py
app/retrieval.py
app/maintenance.py
app/output_studio.py
scripts/pkb.sh
scripts/pkb.py
tests/
```

如果 `scripts/pkb.sh` 存在，先运行：

```bash
./scripts/pkb.sh doctor
```

然后运行基础测试：

```bash
python3 -m pytest -q
```

如果仓库使用 unittest 而没有 pytest，则运行：

```bash
PYTHONPYCACHEPREFIX=/tmp/pkb-pycache python3 -m unittest discover -s tests -v
```

涉及前端时至少运行：

```bash
node --check static/app.js
```

如果环境缺少 Node、pytest、浏览器、模型、API key 或本地 LLM，不要假装已验证；在最终报告中列出未运行项和原因。

## 4. Operating Path

所有功能改动都必须服务以下主路径：

```text
collect → verify library → retrieve with evidence → generate Markdown → maintain / Codex repair
```

推荐命令入口：

```bash
./scripts/pkb.sh workflow
./scripts/pkb.sh doctor
./scripts/pkb.sh open
./scripts/pkb.sh status
./scripts/pkb.sh logs
```

已有 PDF 的路径：

```bash
./scripts/pkb.sh ingest /path/to/pdfs --topic "研究方向"
./scripts/pkb.sh ask "这个方向的核心问题、代表方法和关键争议是什么？"
./scripts/pkb.sh markdown "基于现有 evidence 生成研究摘要" --type research_summary
```

只有研究方向、没有资料时：

```bash
./scripts/pkb.sh discover "研究方向" --keywords "关键词1,关键词2" --max-results 8
./scripts/pkb.sh open
```

结果不准或流程异常时：

```bash
./scripts/pkb.sh codex --reason "具体问题"
./scripts/pkb.sh codex --audit-id aud_xxx --expected "期望命中的文献/chunk/行为"
```

Codex 应优先读取 `outputs/maintenance/*.md` 中的 handoff 文件，按其中的 health JSON、failed jobs、missing indexes、audit id、expected behavior 复现问题。

## 5. Modification Rules

代码修改规则：

- 小步、可回滚、最小必要改动。
- 不做大规模重构，除非用户明确要求，且有测试覆盖。
- 新增行为必须有测试或 smoke check。
- 修改用户可见流程时，同步更新 README 或 docs。
- 修改 CLI 时，确保命令有 `--help` 或清晰错误提示。
- 修改 Web UI 时，不增加无必要的顶层导航；优先优化首页下一步、短路径和错误提示。
- 修改检索逻辑时，保留 audit trail，解释命中/跳过/缺失原因。
- 修改 ingest/DOI 下载时，保留原始文件，不覆盖同名文件，不绕过访问限制。
- 修改索引逻辑时，保持 local vector、API vector、BM25、graph 的覆盖状态可见。
- 修改配置时，不覆盖 `.env`、用户规则文件或密钥。

依赖规则：

- 不随意新增生产依赖。
- 如果必须新增依赖，说明原因、替代方案、安装方式和失败降级策略。
- 优先使用 Python 标准库和项目已有依赖。

隐私规则：

- 默认所有用户导入文档均视为 private。
- 默认只使用本地解析、本地索引、本地检索。
- 只有用户明确授权并配置 API key 时，才允许 API embedding / API LLM。
- 不在日志、测试输出或报告中泄露完整私密文本、密钥或 cookie。

## 6. Task Classes

### 6.1 Workflow / 使用动线优化

目标：用户不需要理解内部模块也能完成任务。

应检查：

- `./scripts/pkb.sh workflow` 是否能说明下一步。
- 首页是否直接提示“最短路径”和“下一步”。
- 空库、索引缺失、失败导入、服务未启动时，是否有明确行动建议。
- CLI 和 Web 路径是否一致。

验收：

- 用户可以按一条命令或一个首页指引继续。
- README/docs 有对应说明。
- 相关测试通过。

### 6.2 Ingestion / DOI / 文档库

目标：资料进入系统后可追踪、可重试、可检索。

应检查：

- raw 文件是否保留。
- canonical structured data 是否生成。
- failed jobs 是否能看到错误原因。
- 重复文件、缺失原始文件、解析失败是否进入维护中心。
- DOI 下载是否尊重访问边界。

验收：

- 成功导入后可在文档库中看到。
- 索引覆盖状态正确。
- 失败有可操作的修复建议。

### 6.3 Retrieval / 检索质量

目标：回答引用真正相关的 chunks，并解释缺失。

应检查：

- query → candidate generation → rank → evidence packaging → audit 是否连续。
- BM25/local/API/graph 覆盖状态是否影响检索结果并可解释。
- 期望命中的文献未出现时，audit 是否能定位原因。

验收：

- 对指定 query 有可复现测试或 fixture。
- 修复后 expected source/chunk 进入合理排名，或明确说明为什么不能进入。
- 输出不会把无证据内容伪装成证据。

### 6.4 Markdown Output / 输出工作室

目标：把检索证据转成可用的研究材料。

应检查：

- research_summary、project_next_step_guidance 等类型是否清楚。
- 输出是否包含 evidence/citation 或缺口标记。
- 文件路径是否稳定、可找到。

验收：

- CLI 和 Web 均可生成输出。
- 输出路径记录在报告中。
- 无证据段落清楚标记 `[无直接来源，需人工确认]` 或等价提示。

### 6.5 Maintenance / Codex Repair Loop

目标：系统能把问题变成 Codex 可执行任务。

应检查：

- `./scripts/pkb.sh doctor` 是否覆盖环境、DB、索引、失败任务。
- `./scripts/pkb.sh codex --reason ...` 是否生成 handoff。
- audit-specific handoff 是否包含 audit id、expected behavior、复现步骤。

验收：

- `outputs/maintenance/*.md` 可直接交给 Codex。
- handoff 内有 safety rules、health summary、recommended commands。
- 维护输出不泄露密钥或大段私密原文。

## 7. Testing Matrix

基础验证：

```bash
python3 -m pytest -q
```

前端语法验证：

```bash
node --check static/app.js
```

服务 smoke test：

```bash
./scripts/pkb.sh open
./scripts/pkb.sh status
./scripts/pkb.sh logs
```

如果项目有 smoke script：

```bash
python3 scripts/smoke_test.py
```

针对 CLI：

```bash
./scripts/pkb.sh workflow
./scripts/pkb.sh doctor
./scripts/pkb.sh codex --reason "smoke check"
```

最终报告必须区分：

- 已运行且通过。
- 已运行但失败。
- 未运行及原因。

## 8. Final Report Format

Codex 完成后用中文报告，结构固定如下：

```text
## 结论
一句话说明是否完成目标。

## 修改内容
- 文件 A：改了什么，为什么。
- 文件 B：改了什么，为什么。

## 验证
- 命令：结果。
- 命令：结果。
- 未运行：原因。

## 使用方式
给用户 3-6 条可直接复制的命令或 Web 路径。

## 输出路径
- 生成文件路径。
- 维护/handoff 路径。

## 风险与后续
只列真实风险，不要泛泛而谈。
```

## 9. Beads / Task Tracking

如果仓库启用了 Beads，并且 `bd` 命令可用，使用 Beads 跟踪持久任务：

```bash
bd ready
bd show <id>
bd update <id> --claim
bd close <id>
bd prime
```

规则：

- 有 Beads 时优先用 Beads，不额外创建散乱 TODO 文件。
- Beads 不可用时，在最终报告中说明，不因缺少 Beads 阻塞代码修复。
- 不把用户私密文档内容写入 Beads。
