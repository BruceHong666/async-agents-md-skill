# async-agents-md

[English](README.md) · [简体中文](README.zh-CN.md)

> 一个 skill：从 git 的 `fix` 提交和 GitLab MR 评论中学习，增量维护项目 `agents.md`（社区标准的 AI 上下文文档）中的 `## Gotchas` 与 `## Conventions` 两节。

---

## 为什么有用

每一次 bug 修复、每一轮 code review，背后都藏着一条来之不易的经验。如果这些经验只停留在提交历史和 MR 评论里，下一个 AI agent 还会踩同样的坑。`async-agents-md` 把这些经验从 `fix` 提交和 review 评论里提炼出来，与 `agents.md` 已有内容去重，再把具体的修改建议交给人工确认。

进度由一个内嵌的 marker 追踪，所以每个提交/MR 在多次运行中只会被处理一次——不会重复处理，也不会被悄悄丢弃。

## 架构

两层清晰分工：确定性的活交给可复现的脚本，语义判断留给编排 agent。

```
                 +-------------------------------+
   自然语言       |         SKILL.md              |   编排层
   触发      --> |  （由 Claude 执行）            |   - 判定冷启动 / 增量
                 |                               |   - 派分析子 agent
                 |  - 判定冷启动/增量            |   - 去重 + 提议 + 确认
                 |  - 调脚本                     |   - 写入后再推进 marker
                 +---------------+---------------+
                                 |
                                 v
                 +-------------------------------+
                 |    scripts/agents_md.py       |   确定性数据 + 状态层
                 |    （纯标准库，零依赖）        |   - marker 解析 / 渲染
                 |                               |   - git 提交抓取
                 |                               |   - GitLab MR 抓取
                 |                               |   - 缓存 + 冷启动 bootstrap
                 +-------------------------------+
```

- **`SKILL.md` —— 编排层（由 Claude 执行）。** 判定冷启动还是增量，调用脚本，派发分析子 agent，合并并去重它们的输出，给出一个清单供用户确认，最后写入——并在用户确认后——推进 marker。
- **`scripts/agents_md.py` —— 确定性数据 + 状态层。** 单文件 Python 3 标准库（argparse、json、re、subprocess、urllib、pathlib、datetime、os）。负责 marker 读写、git 提交抓取、GitLab MR 抓取、缓存与冷启动 bootstrap。零安装。

## 核心特性

- **增量追踪。** 一个 HTML 注释 marker 嵌在 `agents.md` 末尾，**仅在用户接受修改后才推进**。被否决的提议不会改动 marker，下次运行会重新考虑这些提交/MR。
- **MR 来源兜底。** 优先使用 GitLab MCP（数据更丰富、已鉴权、无需本地 token）；脚本自带的 `urllib` 客户端作为兜底，从 git remote 推断 GitLab 实例，并从环境变量读取 token。
- **并行分析子 agent。** 数据量大时按 `batch_size` 切批，每批一个子 agent（遵循 `dispatching-parallel-agents`）；并发受限时可退化为单个串行 agent。
- **standard / deep 两种模式。** `standard` 读提交标题 + 正文 + MR 评论；`deep` 额外读取每个 fix 提交的 diff，gotchas 更精准（token 开销更高）。
- **冷启动。** 当 `agents.md` 不存在时，skill 会读 README、顶层目录树和近期提交，组合出完整文档（`Overview`、`Build & Test`、`Code Layout`、`Conventions`、`Gotchas`），并以当前 HEAD 作为起点。
- **设计即安全。** 流程为 提议 → 确认 → 写入，带 propose-only 闸门。marker 在内容真正写入前绝不推进。

## 安装

把 `SKILL.md` 和 `scripts/` 目录复制到你的 Claude skills 目录：

```bash
mkdir -p ~/.claude/skills/async-agents-md \
  && cp SKILL.md scripts/agents_md.py ~/.claude/skills/async-agents-md/
```

> 注意：保持相同的目录结构，以便 `SKILL.md` 能从仓库根目录找到 `scripts/agents_md.py`。

## 用法

在项目里用自然语言触发 skill，例如「update agents.md from recent fixes」「refresh the AI context」「distill our review conventions」。配置项以 `key=value` 形式传入：

| 配置项          | 默认值        | 说明                                                                            |
| --------------- | ------------- | ------------------------------------------------------------------------------- |
| `mode`          | `standard`    | `standard` = 提交标题 + 正文 + MR 评论；`deep` = 额外读取每个 fix 提交的 diff。 |
| `pattern`       | `^fix`        | fix 提交的正则（传给 `git log --grep -E`）。                                     |
| `language`      | `en`          | 首次运行语言。后续运行会匹配已有文档的主体语言。                                 |
| `target`        | `agents.md`   | 目标文件名。                                                                     |
| `batch_size`    | `10`          | 每个分析批次的条目数。                                                           |
| `analysis_mode` | `parallel`    | `parallel` = 每批一个子 agent；`sequential` = 单 agent（省 token / 避开并发）。  |

skill 只维护两节：`## Conventions` 与 `## Gotchas`。冷启动后其余所有章节都归用户所有。

## CLI

`scripts/agents_md.py` 提供四个子命令，请在仓库根目录执行。

```bash
python scripts/agents_md.py {git|mr|state|bootstrap} ...
```

| 子命令                  | 关键 flag                                                                                       | 用途                                              |
| ----------------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| `git gather`            | `--pattern`、`--mode {standard\|deep}`、`--file`、`--repo`、`--out`                            | 读取 marker、解析 `since`、输出匹配的提交。       |
| `mr gather`             | `--via api`、`--since`、`--repo`、`--out`、`--gitlab-token-env`（默认 `GITLAB_TOKEN`）         | 从 remote 推断 GitLab 并抓取已合并 MR 的评论。    |
| `state show`            | `--file`、`--repo`                                                                              | 打印当前 marker（或回退的 `since`）。             |
| `state advance`         | `--file`、`--commit`、`--mr`                                                                    | 推进 marker；仅在用户接受修改后运行。             |
| `bootstrap gather`      | `--repo`、`--limit`、`--out`                                                                    | 冷启动抓取：README + 顶层目录 + 近期提交。        |

默认值：缓存写在 `.agents-md-cache/` 下（已被 gitignore）。

## 测试

```bash
pip install pytest && pytest
```

30 个测试通过（见 `tests/test_agents_md.py`）。

## 许可证

[MIT](LICENSE) © 2026 BruceHong
