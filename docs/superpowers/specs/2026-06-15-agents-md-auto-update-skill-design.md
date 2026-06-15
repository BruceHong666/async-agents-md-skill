# agents.md 自动更新 Skill — 设计文档

- 日期：2026-06-15
- 状态：Draft（待评审）
- 仓库：`async-agents-md-skill`
- Skill 名称（拟定）：`agents-md`

## 1. 背景与目标

构建一个 AI skill，从 **git `fix` 提交**和 **GitLab MR 评论**中学习，自动维护项目根目录的 `agents.md`（社区标准、项目级 AI 上下文文档，类似 "robots.txt for agents"）。

具体维护 `agents.md` 中的两类知识：
- **Gotchas（踩坑 / 易错点）**：主要来自 `fix` 提交
- **Conventions（编码约定 / 规则）**：主要来自 MR 评审评论

目标：把团队在 bug 修复和 code review 中积累的教训沉淀进 `agents.md`，让后续的 AI agent 不重蹈覆辙。

## 2. 范围

**范围内：**
- 单仓库、当前分支的增量更新
- 从 git `fix` 提交 + GitLab MR 评论提取踩坑与约定
- 生成"建议变更"供人工确认后写入
- 冷启动时生成完整初始 `agents.md`

**范围外（先不做，记录待定）：**
- 跨分支 / 多仓库
- fix 提交被 amend / rebase 导致的漏抓或重复
- MR 评论中非评审性质的闲聊（靠语义提取过滤，不专门处理）
- CI / hook 自动触发（当前为手动调用 skill）

## 3. 总体架构（两层）

```
┌──────────────────────────────────────────────────────────┐
│  SKILL.md（编排层 / 由 Claude 执行）                       │
│  - 判定冷启动 vs 增量                                      │
│  - 调脚本拉数据；MR 优先 MCP、否则脚本兜底                  │
│  - 派（单个或并行多个）分析子 agent 读缓存做语义提取          │
│  - 去重 → 提议 → 确认 → 写 agents.md                       │
└───────────────▲────────────────────────┬─────────────────┘
                │ 结构化 JSON（缓存文件）  │ 写 agents.md（含 marker）
┌───────────────┴────────────────────────▼─────────────────┐
│  scripts/agents_md.py（确定性数据 + 状态层，纯标准库）       │
│  - marker 读写（嵌在 agents.md 的 HTML 注释里）            │
│  - git fix 提交拉取（可配置 pattern，支持 diff）            │
│  - MR 评论兜底拉取（GitLab REST API + urllib + token）     │
│  - 缓存写入 .agents-md-cache/                              │
└──────────────────────────────────────────────────────────┘
```

**分工原则**：脚本只做"可复现、可测试"的确定性活（状态、范围、抓取、缓存）；语义判断（算不算踩坑、和已有条目是否重复、怎么表述进文档）交给 Claude。

## 4. 状态 / 标记机制（marker 嵌入 agents.md）

**不使用独立状态文件**，marker 以单行 HTML 注释形式嵌在 `agents.md` 末尾：

```markdown
<!-- agents-md-state: {"schema":1,"last_commit":"abc1234","last_mr_updated_at":"2026-06-10T12:00:00Z","updated_at":"2026-06-15T09:00:00Z"} -->
```

**字段：**
- `last_commit`：上次处理到的 git commit SHA
- `last_mr_updated_at`：上次处理到的 MR `updated_at` 时间（ISO8601）
- `updated_at`：marker 自身最后更新时间

**特性与保证：**
- **随文档共享**：marker 跟着 `agents.md` 的正常 commit 进入仓库，全团队自动共享同一个增量起点，无需额外的状态文件，也不引入合并冲突密集的 sidecar。
- **确认后才推进**：写入流程是"确认 → 写内容区块 → 调 `state advance` 更新 marker 注释"，两步**都发生在用户确认之后**、且都只动 `agents.md`。因此拒绝提议时两步都不执行，marker 不推进，相关 commit/MR 下次仍会被处理。
- **单点真相**：marker 的格式与读写完全由脚本 `state` 子命令负责，skill 不直接拼这行注释。
- **缺失兜底**：若 `agents.md` 存在但 marker 注释缺失（被手工删除等），脚本回退为"以上次修改 agents.md 的提交为起点"并给出 warning。

## 5. 数据模式（standard / deep）

调用 skill 时可选模式，默认 `standard`：

| 模式 | 抓取内容 | 适用 |
|---|---|---|
| `standard`（默认） | commit 标题 + body + MR 评论原文 | 日常、轻量、省 token |
| `deep` | 额外抓取每条 fix 提交的 diff | 需要从"改了什么"提炼具体踩坑时 |

两种模式都先**缓存成文件**再分析（见第 8 节）。

## 6. 数据流

### A. 冷启动（bootstrap）—— `agents.md` 不存在
1. 读 `README.md` + 目录结构 + 最近 N 条提交（N 可配置，默认全部）
2. 生成完整 `agents.md`（结构见第 9 节），marker 的 `last_commit` 设为当前 HEAD
3. 不进入增量分析

### B. 增量—— `agents.md` 已存在
1. 脚本读 marker，按 `last_commit` 拉取范围内的 `fix` 提交（`last_commit..HEAD`），按 pattern 过滤
2. MR：skill 先试 GitLab MCP（按 `last_mr_updated_at` 过滤更新过的 MR）；MCP 不可用 → 调脚本 `mr gather` 用 token 兜底
3. 抓取结果写入 `.agents-md-cache/`
4. 派分析子 agent（单个或并行多个）读缓存，提取 gotchas + conventions（第 8 节）
5. 去重对比已有 `agents.md` → 提议 → 确认 → 写入 → 推进 marker（第 10 节）

## 7. MR 来源策略

**优先 GitLab MCP，脚本兜底：**
1. skill 探测 GitLab MCP 是否可用（尝试调用一次列表 MR 的能力，成功即视为可用）
2. 可用 → 通过 MCP 拉取自 `last_mr_updated_at` 起更新过的 MR 及其评论；记录抓到的最新 `updated_at`
3. 不可用 → 调 `python scripts/agents_md.py mr gather --via api`，脚本用环境变量里的 token（默认 `GITLAB_TOKEN`）+ urllib 调 GitLab REST API
4. 两者都不可用 → 跳过 MR，仅基于 git 提交更新（给出提示）

GitLab 实例地址默认从 git remote 自动推断，可配置覆盖。

## 8. 分析流水线（缓存 → 单 agent → 合并）

**为什么缓存成文件再分析**：把 gather（大量原始 git/MR 输出）与 analyze 的上下文分离——分析子 agent 拿到的是干净、紧凑的缓存文件，不被原始命令输出污染，提取质量更稳。

**缓存文件**（位于 `.agents-md-cache/`，gitignored，每次运行覆盖、保留到下次便于调试）：
- `commits.json`：`[{sha, message, body, files?, diff?}]`（`diff` 仅 deep 模式）
- `mrs.json`：`[{iid, title, updated_at, comments: [...]}]`

**执行方式：并行多子 agent + 合并（本期实现）**
- 当缓存条目数 ≤ `batch_size`（默认 10）时，skill 派**单个**分析子 agent 读完即分析
- 当条目数 > `batch_size` 时，按每 `batch_size` 条切批，**并行**派多个分析子 agent（`Agent` / `Task` 工具，general-purpose，遵循 `dispatching-parallel-agents` 模式）；每个 agent 只读自己那批、独立提取 gotchas / conventions，返回结构化 JSON
- **合并去重**：全部批次返回后，主流程合并所有候选，先做**跨批次去重**（不同批次可能提炼出相似条目），再与已有 `agents.md` 做语义去重（第 10 节）
- 并发上限遵循 `dispatching-parallel-agents` 指引，避免一次派太多
- `analysis_mode` 可配置：`parallel`（默认）/ `sequential`（环境受限或想省 token 时退回顺序单 agent）

## 9. agents.md 结构模板

```markdown
# Agents

> 本文件由 agents-md skill 维护，记录项目上下文供 AI agent 使用。
> 标记为「skill 维护」的章节由 skill 自动更新；其余章节请手工维护。

## Overview
<冷启动生成；之后基本不动>

## Build & Test
<构建 / 测试 / lint 命令>

## Code Layout
<关键目录与模块职责>

## Conventions        <!-- skill 维护：编码约定 / 规则 -->
- ...

## Gotchas            <!-- skill 维护：踩坑 / 易错点 -->
- ...

<!-- agents-md-state: {...} -->
```

- skill **只写** `Conventions` 和 `Gotchas` 两节；用 HTML 注释界定区块边界，便于精确替换、不触碰手写内容
- 其余章节冷启动生成后由用户维护
- **语言**：首次生成默认**英文**；之后跟随已有 `agents.md` 的主导语言
- 文件名默认 `agents.md`（小写，社区标准），可配置

## 10. 提议 → 确认 → 写入流程

1. 分析子 agent 返回候选条目（每条带：来源 commit SHA / MR iid、归类 gotcha/convention、一句话描述）
2. skill 将候选与已有 `Conventions`/`Gotchas` 做**语义去重**：
   - 全新条目 → 标"新增"
   - 与已有条目高度相似 → 标"疑似重复"，交用户裁决，不自动合并
   - 对已有条目的补充/修正 → 标"修改"
3. skill 产出**建议清单**（每条注明来源、归类、动作、与现有条目的相似度）
4. 用户逐条勾选或全选确认
5. 确认后，skill 写入 `agents.md` 的 `Conventions`/`Gotchas` 区块
6. 随即调 `state advance` 更新末尾 marker（新 `last_commit` = 当前 HEAD，新 `last_mr_updated_at` = 本次抓到的最新 MR 时间）。两步都在确认之后执行，故拒绝即不推进

## 11. Python 脚本接口（`scripts/agents_md.py`）

纯标准库实现（`urllib`、`json`、`subprocess` 调 `git`、`argparse`），零安装。

```
state show   [--file agents.md]
    打印当前 marker；缺失则打印 MISSING 并以"上次改 agents.md 的提交"回退

git gather   [--pattern fix] [--mode standard|deep] [--file agents.md] [--out .agents-md-cache/commits.json]
    读 marker，输出 last_commit..HEAD 内匹配 pattern 的提交 JSON；deep 模式含 diff

mr gather    --via api [--since <ts>] [--out .agents-md-cache/mrs.json]
    token 兜底：拉取 since 起更新过的 MR 评论 JSON

state advance --commit <sha> [--mr <ts>] [--file agents.md]
    更新/插入 agents.md 末尾的 marker 注释（仅在写入内容后调用）
```

所有输出为结构化 JSON，便于 Claude 解析。退出码：0 成功；非 0 附带可读错误信息。

## 12. 配置项

| 项 | 默认 | 说明 |
|---|---|---|
| `mode` | `standard` | `standard` / `deep` |
| `pattern` | `fix` | fix 提交匹配模式（git log `--grep` 的正则） |
| `lookback` | `--all` | 冷启动回看多少提交（可限 N 条） |
| `language` | `en` | 首次生成语言；之后跟随已有文档 |
| `target_file` | `agents.md` | 目标文件名 |
| `gitlab_token_env` | `GITLAB_TOKEN` | 兜底拉 MR 的 token 环境变量名 |
| `gitlab_url` | 自动推断 | GitLab 实例地址 |
| `batch_size` | `10` | 大缓存分批分析的批次大小 |
| `analysis_mode` | `parallel` | `parallel`（多批并行）/ `sequential`（顺序单 agent） |
| `cache_dir` | `.agents-md-cache/` | 缓存目录（gitignored） |

## 13. 仓库文件布局

```
async-agents-md-skill/
├── README.md
├── LICENSE
├── SKILL.md                      # skill 指令（编排层）
├── scripts/
│   └── agents_md.py              # 确定性数据 + 状态层
├── .gitignore                    # 忽略 .agents-md-cache/
└── docs/superpowers/specs/
    └── 2026-06-15-agents-md-auto-update-skill-design.md   # 本文档
```

安装时 `SKILL.md` + `scripts/` 复制到 `~/.claude/skills/agents-md/`。

## 14. 边界与限制

- fix 提交被 amend / rebase 掉 → 可能漏抓或重复（YAGNI，先不处理）
- 仅支持单仓库当前分支
- MR 非评审闲聊靠语义过滤，不专门清洗
- 手工删掉 marker 注释 → 走第 4 节的缺失兜底
- marker 嵌入文档意味着它必须随 `agents.md` 一起提交才能团队共享；若本地未提交，则仅为本地进度

## 15. 后续可选增强（不在本期）

- `.agents-md.state.json` sidecar 模式（替代嵌入注释）
- CI / pre-push hook 自动触发
- 跨分支聚合
