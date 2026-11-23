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
