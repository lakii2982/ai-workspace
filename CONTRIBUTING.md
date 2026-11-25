# 贡献指南

本仓库面向 Agent 协作。开发契约以 [AGENTS.md](AGENTS.md) 为准，本文件是给人类读者的摘要。

## 本地准备

```sh
./install.sh "$HOME/AI WorkSpace Dev/Workspace" --agent cli
make bootstrap
make check
```

`install.sh` 会准备 `engine/.venv` 和同级数据目录；`make bootstrap` 设置 Git hooks。之后每次提交前跑 `make check`。

## 分支与提交

- 从 `origin/main` 切短生命周期分支：`feat/…`、`fix/…`、`refactor/…`、`test/…`、`docs/…`、`chore/…`、`ci/…`、`build/…`。
- 提交使用 Conventional Commits：`<type>(<scope>): <说明>`，scope 可用 `upgrade`、`mcp`、`install`、`desktop`、`schema`。
- 一个 PR 只做一件事。PR 标题就是 squash 后的提交标题。
- 不直接推送 `main` / `stable`，不 force push 已推送分支。

## PR

1. 推送分支，`gh pr create --fill`。
2. 等 CI 全绿：`gh pr checks <编号> --watch`。
3. `gh pr merge <编号> --squash --delete-branch`。

改动涉及安装、升级或桌面打包时，合并前额外跑 `make verify-install`。

## 环境与发布

| 通道 | 来源 | 说明 |
|---|---|---|
| dev | 功能分支 | 临时工作区，允许坏 |
| preview | `main` | 用 `make verify-install` 在临时目录验收 |
| stable | 已发布 tag | 用户 `aiws upgrade` 的默认来源 |

发布由 `.github/workflows/release.yml` 每周一自动执行：从 Conventional Commits 推导版本号（`!:` 表示破坏性变更），在 `main` 上创建 `chore(release): vX.Y.Z` 提交和 tag，生成 release notes，并把 `stable` 快进到该提交。紧急发布可手动触发该工作流。

## 数据兼容

`engine/upgrade-schema.txt` 是升级器认可的工作区数据契约，只允许加法变更。需要破坏性变更时，另开 PR：更新 token、在 `engine/aiws/migration.py` 写迁移、补兼容测试，并说明旧版本如何处理。

## 不要提交

`.env`、凭据、用户 Workspace 数据、`.aiws-releases/`、虚拟环境。
