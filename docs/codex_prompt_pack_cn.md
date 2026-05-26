# Codex Prompt Pack — Personal Knowledge RAG Webapp

这些提示词用于 Codex Web、Codex CLI、IDE 扩展或 GitHub 任务。使用时把 `{变量}` 替换成你的实际内容。默认让 Codex 先读 `AGENTS.md`，再执行任务。

当前仓库已经提供 `./scripts/pkb.sh` 统一入口，覆盖 `workflow / doctor / open / status / logs / ingest / ask / markdown / discover / codex`。如果未来某个分支缺少该入口，必须把它列为缺口并使用 `scripts/webapp.sh`、`scripts/start.sh`、`python3 scripts/smoke_test.py` 等 fallback，不能把未运行命令写成已验证。

## 0. 通用起手式：接管项目并做体检

```text
请先阅读仓库根目录的 AGENTS.md、README.md、docs/optimized_usage.md、docs/codex_workflows.md，以及 scripts/pkb.sh / scripts/pkb.py。你的目标是把这个 personal-knowledge-rag-webapp 作为“最低时间成本的个人研究 OS”来维护。

先不要改代码。请完成：
1. 运行 git status --short。
2. 运行 ./scripts/pkb.sh doctor；如果不存在，说明原因并选择合理 fallback。
3. 运行 python3 -m pytest -q；如果环境缺依赖，说明缺什么。
4. 涉及前端时运行 node --check static/app.js。
5. 输出当前最短使用路径、主要风险、下一步建议。

请用中文报告，区分“已验证事实”和“推测”。
```

## 1. 应用我给你的优化补丁

```text
我会提供一个补丁文件：{PATCH_PATH}。请你在 personal-knowledge-rag-webapp-main 仓库中应用它，并按 AGENTS.md 规则验证。

执行要求：
1. 先运行 git status --short，确认当前工作区状态。
2. 先 dry-run / check 补丁是否可应用。
3. 应用补丁后，检查新增或修改的文件是否符合项目目标：最低时间成本、CLI 统一入口、首页短路径、Codex handoff。
4. 运行 python3 -m pytest -q。
5. 涉及 static/app.js 时运行 node --check static/app.js。
6. 尝试运行 ./scripts/pkb.sh doctor 和 ./scripts/pkb.sh workflow。

不要删除 raw files，不要覆盖 .env，不要把私密文档发给 API。最后用中文输出：应用结果、冲突情况、修改文件、验证命令、剩余风险。
```

## 2. 把项目改成“一个入口”的最低成本动线

```text
请优化这个项目的日常使用动线。目标：用户只需要记住 ./scripts/pkb.sh workflow / doctor / open / ingest / ask / markdown / codex 这一组命令，就能完成从研究方向到证据输出再到维护修复的流程。

请检查并必要时修改：
- scripts/pkb.sh
- scripts/pkb.py
- README.md
- docs/optimized_usage.md
- static/index.html
- static/app.js
- static/styles.css
- tests/

验收标准：
1. ./scripts/pkb.sh workflow 能解释“已有 PDF”“只有方向”“结果不准”“每周维护”四种场景。
2. ./scripts/pkb.sh doctor 能检查环境、DB、索引、服务和本地 LLM 状态；无法检查的项目要清楚降级。
3. 首页有“最短路径”和“下一步建议”，不要让用户从菜单里找功能。
4. 改动必须有测试或 smoke check。
5. 最终报告必须给出用户下一次怎么用的命令。
```

## 3. 已有 PDF：导入、索引、问答、输出打通

```text
我已有 PDF 文件夹：{PDF_DIR}。研究方向是：{TOPIC}。

请把“导入 → 索引 → 问答 → Markdown 输出”这条路径打通或修复。要求：
1. 不删除原始 PDF。
2. 默认只使用本地索引，不把文档发到 API。
3. 导入后确认文档库记录、canonical data、local/BM25/graph index 覆盖状态。
4. 使用问题：{QUESTION} 做一次检索验证。
5. 生成一份 Markdown 输出，类型优先用 research_summary。
6. 如果因为环境限制无法处理真实 PDF，请用项目已有 sample/smoke fixture 复现，并说明真实文件需要用户本地运行哪些命令。

请根据 AGENTS.md 运行测试，并用中文报告输出路径和任何失败原因。
```

## 4. 只有方向：文献发现和 DOI 下载流程优化

```text
研究方向：{TOPIC}
关键词：{KEYWORDS}

请优化或验证“只有方向、还没有资料”的流程：discover → DOI 候选 → Web 端下载 → 加入文档库 → 检索。

要求：
1. 不绕过登录、验证码、付费墙、403/429 或机构访问提示。
2. DOI 下载只处理用户可合法访问的全文。
3. 文献发现结果要能给出候选 DOI、标题、来源、状态和下一步。
4. Web 页面应提示用户什么时候需要 headed/manual login 模式。
5. 出错时生成可交给 Codex 的 maintenance handoff。

请补必要测试，运行验证命令，最终用中文报告。
```

## 5. 检索不准：指定期望命中的论文/chunk

```text
当前问题：{QUESTION}
期望行为：{EXPECTED_BEHAVIOR}
审计 ID：{AUDIT_ID}

请修复检索质量问题。先读取 outputs/maintenance/ 下与该 audit id 相关的 handoff 或审计文件；如果没有，请运行 ./scripts/pkb.sh codex --audit-id {AUDIT_ID} --expected "{EXPECTED_BEHAVIOR}" 生成。

执行步骤：
1. 复现当前 query 的 candidate generation、ranking、evidence packaging 和 audit。
2. 找出期望论文/chunk 没进结果的原因：未导入、解析失败、索引缺失、query 改写问题、rank 权重问题、过滤问题、citation packaging 问题等。
3. 做最小代码修改。
4. 增加回归测试，证明修复有效。
5. 保持无证据内容不能被伪装成引用。

最终报告必须说明：问题根因、改了什么、expected source/chunk 是否进入结果、还有什么不确定。
```

## 6. Markdown 输出质量：让结果可直接用于研究

```text
请优化 Markdown 输出工作室，使它更适合快速做研究判断。目标输出类型：{OUTPUT_TYPE}。用户问题/任务：{QUESTION}

要求：
1. 每个关键结论尽量附 evidence/citation。
2. 没有证据的内容明确标记为“无直接来源，需人工确认”。
3. 输出包含：核心结论、证据表、争议/缺口、下一步阅读或实验建议。
4. CLI 和 Web 端路径一致。
5. 输出文件路径稳定，并在界面/报告中显示。

请修改必要代码和文档，补测试，运行验证。
```

## 7. 首页/UI 动线：从“功能菜单”改成“下一步建议”

```text
请审查并优化 Web 首页。目标：用户打开页面后不需要理解模块，只看“最短路径”和“下一步建议”即可继续。

请处理这些状态：
1. 文档库为空 → 引导导入 PDF 或文献发现。
2. 有 failed jobs → 引导维护/失败原因。
3. 有 missing/stale indexes → 引导重建索引。
4. 文档和索引正常 → 引导检索问答或 Markdown 输出。
5. 检索质量异常 → 引导生成 Codex handoff。

要求：
- 不增加多余顶层导航。
- 文案用中文，短、可执行。
- 修改 static/app.js 后运行 node --check static/app.js。
- 更新 tests/test_static_ui_contract.py 或相应测试。
```

## 8. 维护中心：把问题自动转成 Codex 可执行任务

```text
请增强维护中心和 Codex handoff。目标：当导入失败、索引缺失、检索不准、输出缺证据时，系统能生成一个 outputs/maintenance/*.md 文件，Codex 可直接接手修复。

handoff 必须包含：
1. 当前 health summary。
2. failed jobs / missing indexes / duplicate files / missing originals。
3. audit id 和 expected behavior（如有）。
4. 复现命令。
5. 安全边界：不删 raw files、不外发私密文档、不覆盖配置、不绕过付费墙。
6. 建议修改范围和验收标准。

请补测试，运行 ./scripts/pkb.sh codex --reason "smoke handoff" 验证生成文件。
```

## 9. 隐私与安全审计

```text
请做一次隐私和安全审计，重点看 ingest、retrieval、API embedding/API LLM、DOI downloader、logs、maintenance handoff。

要求：
1. 默认 private 文档不得外发 API。
2. API index 和 local index 必须分离并可见。
3. 日志和 handoff 不得包含密钥、cookie、大段私密原文。
4. DOI 下载不得绕过登录、验证码、付费墙、403/429 或机构警告。
5. 发现问题时做最小修复并补测试。

最终输出中文审计报告：发现项、严重级别、修复项、未修复风险、验证命令。
```

## 10. 测试补齐：为关键路径加回归测试

```text
请为 personal-knowledge-rag-webapp 的关键路径补齐测试，优先级如下：
1. scripts/pkb.sh / scripts/pkb.py 的 workflow、doctor、codex 命令。
2. 首页“最短路径/下一步建议”的静态 UI 契约。
3. ingest 后索引覆盖状态。
4. retrieval audit 能解释 missing expected chunk。
5. Markdown 输出中无证据内容必须标记。

要求：
- 测试应使用 fixture/sample，不依赖真实私密 PDF。
- 不引入重型依赖。
- 运行 python3 -m pytest -q。
- 涉及前端运行 node --check static/app.js。

最终报告说明新增测试覆盖了哪些失败模式。
```

## 11. 周维护任务：自动体检并生成修复计划

```text
请执行一次周维护。目标是发现并修复会影响“最低时间成本动线”的问题。

请执行：
1. git status --short。
2. ./scripts/pkb.sh doctor。
3. python3 -m pytest -q。
4. node --check static/app.js（如果前端相关）。
5. ./scripts/pkb.sh codex --reason "例行健康检查，修复失败导入、缺失索引和检索弱点"。

如果发现小问题，请直接修；如果发现大问题，请生成 maintenance handoff 并给出优先级。不要删除 raw files，不要外发私密文档。
```

## 12. Codex 最终报告模板

把这个附在任务最后，可以强制 Codex 输出可读交付物：

```text
请最终用中文按以下格式报告：

## 结论
一句话说明任务是否完成。

## 修改内容
按文件列出修改点和原因。

## 验证
列出每条命令、结果、失败原因。不要声称运行过未运行的命令。

## 用户现在怎么用
给出最短命令或 Web 路径。

## 输出路径
列出生成的 Markdown、handoff、日志或测试文件路径。

## 风险与后续
只列真实风险和下一步，不要泛泛总结。
```
