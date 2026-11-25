# AI WorkSpace 开发规范

这是本仓库的开发契约，面向所有在本仓库工作的 Agent。开始任何改动前先读本文件；用户不需要重复说明分支、提交、测试和发布流程。

产品对最终用户工作区的规则在 `engine/workspace_rules.md`，安装时会写成用户工作区的 `AGENTS.md`。两者不要混用。

## 角色

用户只提出需求、接收结果。分支、提交、PR、CI、合并和发布都由你完成。不要要求用户执行 `git` / `gh` 命令，也不要让用户去 GitHub 网页点按钮。只有产品意图或内容确认需要用户决定时才提问，且一次只问一个问题。

## 一次任务的标准路径

1. 从最新的 `origin/main` 建分支：`feat/…`、`fix/…`、`refactor/…`、`test/…`、`docs/…`、`chore/…`、`ci/…`、`build/…`。
2. 实现并补测试；行为变化必须有对应测试。
3. 需要时同步更新 `README.md`、`engine/*.md` 或接口说明。
4. `make check` 全绿；改动涉及安装、升级或桌面打包时再跑 `make verify-install`。
5. 用 Conventional Commits 提交，`git push -u origin <分支>`。
6. `gh pr create --fill`；PR 标题必须是 Conventional Commits。
7. `gh pr checks <编号> --watch` 全绿后执行 `gh pr merge <编号> --squash --delete-branch`。不要用 `--admin`，不要绕过检查。
8. 上一个 PR 合并后确认 `main` 仍为绿色，再开始下一个。

## 硬性规则

- 不直接推送 `main`、`stable`；不 force push 任何已推送分支。
- 一个 PR 只做一件事。依赖升级、格式化、schema 变更、大规模重命名分开提交。
- 不提交凭据、`.env`、用户 Workspace 数据、`.aiws-releases/`。
- 不使用 `--no-verify`，不跳过或临时禁用 CI。
- 测试失败先修根因；不要为了通过而放宽断言，除非原断言确实是错的。
- 新增或改变用户可见行为时同步更新文档。
- 破坏兼容的改动必须写清迁移路径。
- `engine/upgrade-schema.txt` 是数据兼容契约，只允许加法；改动它必须单独 PR，并附迁移代码与兼容测试。

## 命令

| 命令 | 作用 |
|---|---|
| `make bootstrap` | 设置 Git hooks、检查本地环境；每个新克隆执行一次 |
| `make check` | lint + 全量测试；提交前必须通过 |
| `make test` | 运行 `engine/tests` 全量测试 |
| `make lint` | ruff 致命规则检查（未定义名、语法错误） |
| `make verify-install` | 在临时目录真实安装一遍并运行 `doctor` |
| `make release-dry` | 查看下一次发布将采用的版本号 |

本地虚拟环境由 `./install.sh` 创建。没有 `engine/.venv` 时先执行它。

## 环境

三套通道，零台常驻服务器：

| 通道 | 代码来源 | 数据 | 用途 |
|---|---|---|---|
| dev | 任意功能分支 | 临时目录（`mktemp -d`）或仓库同级 `Workspace/` | 日常开发，允许坏 |
| preview | `main` 最新提交 | `make verify-install` 创建的临时安装 | 走真实安装/升级链路验收 |
| stable | 已发布 tag，`stable` 分支指向它 | 用户的 `~/AI WorkSpace/` | 用户升级的默认来源 |

- 开发分支不要把未发布的 schema 写进真实工作区；用 `--workspace <临时目录>`。
- preview 验收只用临时目录，不要拿用户数据试。
- 只有发布工作流能移动 `stable`。

## 发布

- 发布自动进行：每周一 09:17（北京时间）检查 `main`，有 `feat` / `fix` 等用户可见变更就发版，没有就跳过。需要提前发布时，让 Agent 执行 `gh workflow run release.yml`。
- 版本号由 `scripts/release.py` 从 Conventional Commits 推导：`feat` / `perf` → minor，`fix` / `refactor` → patch，`!:` → major。发布 commit 固定为 `chore(release): vX.Y.Z`。
- 发布说明由 GitHub 根据 PR 自动生成，以 release 页面为准。
- 不要手动修改 `engine/pyproject.toml` 的版本号，也不要手动移动 `stable`。
- 合并到 `main` 的用户可见改动，最迟在下一个发布窗口到达用户。

## 完成定义

一项任务只有以下全部成立才算结束：

- `make check` 通过；改动触及安装、升级或桌面时 `make verify-install` 通过。
- 新行为有测试，文档已同步。
- PR 标题合规、CI 全绿、已 squash 合并、远程分支已删除。
- 已向用户报告：结果是什么、验证了什么、用户接下来能做什么。不要汇报 git 细节，也不要让用户确认提交、推送或合并。

## 失败处理

- `main` 变红：最高优先级，先修或 revert，再继续其他工作。
- 发布失败：不要反复重试；保留 tag 现场，修好后发下一个版本。
- 升级失败：`aiws upgrade` 会备份并回滚；先用 `aiws doctor` 确认当前状态，再定位。
- 不确定时缩小改动、保持 `main` 可安装，而不是一次性大改。

## 与 AIWS 工作区的关系

仓库开发任务以本文件和仓库代码为准。如果用户把任务登记为 AIWS 项目，按绑定的 MCP 入口记录检查点和交付物；代码改动仍然遵循本文件。

## 仓库结构

- `aiws`、`install.sh`：入口与安装器。
- `engine/`：Python 包、测试、安装/升级/桌面打包代码。
- `engine/workspace_rules.md`：安装到用户工作区的 `AGENTS.md` 内容；改动前评估对存量工作区的影响。
- `Workspace/`：本地开发数据，不入库。
- `.github/workflows/`：CI 与发布。

## 其他客户端入口

`CLAUDE.md` 引用本文件；Copilot 读取 `.github/copilot-instructions.md`，其中同样指向本文件。不要在其他文件里复制规范内容。
