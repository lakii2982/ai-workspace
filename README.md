# AI WorkSpace

给本地 Agent 一个可接续的项目工作空间。项目资料留在本机，Agent 用统一入口记录任务、回读来源、保存检查点和交付成果。

## 把链接交给你的 Agent

在能操作本机终端的 Agent 中，发送这一句话：

> https://github.com/lakii2982/ai-workspace 帮我安装 AI WorkSpace。请读取仓库的初始化指南，直接在本机执行并验证，完成后带我开始第一个项目。

Agent 会下载程序、准备 Python 环境、初始化资料目录，并验证 CLI 和 MCP。默认安装在 `~/AI WorkSpace/`，也可以指定其他安装根目录。支持 macOS 和 Linux，本地模式无需飞书账号。

| 安装根目录内 | 用途 |
|---|---|
| `aiws_core/` | Git 检出、安装器、命令入口、核心代码、测试与 Python 环境。 |
| `Workspace/` | 项目资料、工作稿、成果、知识库和本地运行状态。 |

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

## 员工桌面版

macOS 在下载并解压的 core 中双击 `engine/setup-desktop.command`，选择桌面 Agent 和工作空间。已有安装可追加客户端。安装结果中的 `mcp: verified` 表示服务自检通过。支持 MCP 的桌面端在新会话调用 `verify_desktop_binding` 可以核对实际绑定的本地目录。

- Codex 打开绑定的工作空间即可使用 MCP。工作空间内置同名技能插件目录，日常工具按名称配置授权。
- Claude Desktop 使用安装器打开的 MCPB 扩展。Claude Code 仍使用原有的 `--agent claude` 入口。
- WorkBuddy 通过自定义 MCP 接入。DeepSeek Harness 使用 desktop profile 的本地工具插件。豆包办公使用安装器生成的技能 ZIP 和本机 CLI，在桌面端“技能 → 添加 → 上传技能”导入。
- 对已有 Office 主稿发起续写时，先领取工作副本。正式发布先发起版本确认。在已完成任务中说“把这次做法存下来”可创建个人场景，其他客户端按相同版本复用。
- 启用员工版启动器后，可对 Agent 说“升级 AIWS”，或由 Agent 执行 `aiws upgrade`。默认从 `stable` 通道（最近一次发布）取固定提交；设置 `AIWS_CHANNEL=preview` 时改用 `main` 的最新提交。升级在已校验的备份副本上试运行，随后切换程序入口。新版本须支持当前工作空间格式。升级之后重开桌面会话以刷新工具清单。

五端的真机重启和重复授权验收见 GitHub Issue #8。Windows 原生桌面安装尚待适配。

## 参与开发

仓库的协作约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。克隆后执行一次 `make bootstrap`，提交前运行 `make check`。发布由 `.github/workflows/release.yml` 每周自动完成，紧急情况可手动触发该工作流。
