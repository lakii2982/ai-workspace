# AI WorkSpace

在 Codex 对话里做项目。告诉它要做什么，收到能打开的稿件；下次开新对话，说出项目和工作就能接着改。项目资料和版本保存在你的工作空间。

## 把链接交给你的 Agent

在能操作本机终端的 Agent 中，发送这一句话：

> https://github.com/lakii2982/ai-workspace 帮我安装 AI WorkSpace。请读取仓库的初始化指南，直接在本机执行并验证，完成后带我开始第一个项目。

Agent 会下载程序、准备 Python 环境、初始化资料目录，并验证 CLI 和 MCP。默认安装在 `~/AI WorkSpace/`，也可以指定其他安装根目录。支持 macOS 和 Linux，本地模式无需飞书账号。

| 安装根目录内 | 用途 |
|---|---|
| `aiws_core/` | Git 检出、安装器、命令入口、核心代码、测试与 Python 环境。 |
| `Workspace/` | 项目资料、工作稿、成果、知识库和本地运行状态。 |

## 第一次交一件工作

安装完成后，在已信任并接入 AIWS 的工作空间打开 Codex。你可以直接说：

> 帮我把附件里的交期和报价整理成一页，明天给采购看。

如果你还没说这项工作属于哪个项目，Codex 会在对话里问一次。回一句项目名即可。比如“给秋季采购”，它接着原来的要求整理首稿，并给出可以打开的文件。也可以直接说“给秋季采购项目整理一页报价”，省去这轮追问。

| 你接下来可能说 | 对话应给你的结果 |
|---|---|
| “这个交期从哪里来？” | 原件和对应的页码、段落或单元格。 |
| “接着改上次秋季采购那份报价。” | 当前主稿、上次做到哪里、还差什么。 |
| “刚看的这版可以定稿。” | 这份具体版本的成果文件。 |
| “上次发给客户的是哪一版？” | 已记录的那份文件及发送依据；只有定稿时会说明尚无发送记录。 |

一个 Codex 就能完成这些工作。只有你主动要求另一位助手复核时，才会出现交接选项；本机缺少指定工具时，可以带走主稿和交接说明，也可以让当前助手继续。要求独立意见会保留为待接手。

## 自己从终端安装

确认系统已有 Git 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。

```sh
mkdir -p "$HOME/AI WorkSpace"
cd "$HOME/AI WorkSpace"
git clone https://github.com/lakii2982/ai-workspace.git aiws_core
./aiws_core/install.sh --agent codex
./aiws_core/aiws doctor
```

Claude Code 用 `--agent claude`。其他有终端能力的 Agent 用 `--agent cli`。在 `Workspace/` 里打开 Agent 会话，按客户端提示信任目录或启用项目 MCP，参阅 `Workspace/START.md` 开始使用。

- [Agent 初始化指南、手工安装与排障](engine/INSTALL.md)
- [日常工作流程](engine/USER_GUIDE.md)
- [架构设计](engine/ARCHITECTURE.md)
- [CLI / MCP 接口和数据契约](engine/INTERFACE_SPEC.md)
- [MIT 许可证](LICENSE)

## 项目记忆

每个项目有自己的 `记忆/` 目录：`MEMORY.md` 是索引，`条目/` 下一条记忆一个 Markdown 文件，分为 user、feedback、project、reference 四类，条目之间可用 `[[名称]]` 互链。Agent 开始任务时从 `prepare_work_context` 取得索引，只读取相关条目；用 MCP 工具 `list_memory_notes`、`read_memory_note`、`save_memory_note`、`delete_memory_note` 维护，更新和删除都校验 hash。索引由核心根据文件生成。

## 员工桌面版

macOS 在下载并解压的 core 中双击 `engine/setup-desktop.command`，选择桌面 Agent 和工作空间。已有安装可追加客户端。安装结果中的 `mcp: verified` 表示服务自检通过。支持 MCP 的桌面端在新会话调用 `verify_desktop_binding` 可以核对实际绑定的本地目录。

- Codex 在绑定的安装根目录或数据目录打开新对话，并按提示信任目录、启用项目 MCP。安装器会配置入口，实际对话连接的目录可通过绑定回执核对。
- Claude Desktop 使用安装器打开的 MCPB 扩展。Claude Code 仍使用原有的 `--agent claude` 入口。
- WorkBuddy 通过自定义 MCP 接入。DeepSeek Harness 使用 desktop profile 的本地工具插件。豆包办公使用安装器生成的技能 ZIP 和本机 CLI，在桌面端“技能 → 添加 → 上传技能”导入。
- 对已有 Office 主稿发起续写时，先领取工作副本。正式发布先发起版本确认。在已完成任务中说“把这次做法存下来”可创建个人场景，其他客户端按相同版本复用。
- 启用员工版启动器后，可对 Agent 说“升级 AIWS”，或由 Agent 执行 `aiws upgrade`。默认从 `stable` 通道（最近一次发布）取固定提交；设置 `AIWS_CHANNEL=preview` 时改用 `main` 的最新提交，设置 `AIWS_UPGRADE_SHA=<40 位提交>` 可钉住已评审的提交。升级在已校验的备份副本上试运行，随后切换程序入口，升级日志同时记录发布包的 SHA-256。新版本须支持当前工作空间格式。升级之后重开桌面会话以刷新工具清单。

五端的真机重启和重复授权验收见 GitHub Issue #8。Windows 原生桌面安装尚待适配。

## 问题反馈

使用中遇到工具报错、入口不可用、文档与实际不符，或某个场景因框架限制做不下去时，Agent 会先处理手头的工作，再询问是否要把问题提交给本仓库。同意后 Agent 先展示标题和正文，再调用 `report_issue` 提交；本机安装并登录 GitHub CLI 时直接创建 Issue，否则返回预填好的新建 Issue 链接。报告只包含问题现象、复现步骤和框架版本、系统版本、Python 版本、客户端名称，不含项目名称、文件路径、文件正文或客户资料，同时保存在工作空间的 `.workspace/feedback/reports/`。详见 [日常工作流程](engine/USER_GUIDE.md#问题反馈)。

## 参与开发

仓库的开发契约是 [AGENTS.md](AGENTS.md)，摘要见 [CONTRIBUTING.md](CONTRIBUTING.md)。克隆后执行一次 `make bootstrap`，提交前运行 `make check`。发布由 `.github/workflows/release.yml` 每周自动完成，紧急情况可手动触发该工作流。
