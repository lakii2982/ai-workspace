# AI WorkSpace

给本地 Agent 一个可接续的项目工作空间。项目资料留在本机，Agent 用统一入口记录任务、回读来源、保存检查点和交付成果。

## 把链接交给你的 Agent

在能操作本机终端的 Agent 中，发送这一句话：

> https://github.com/lakii2982/ai-workspace 帮我安装 AI WorkSpace。请读取仓库的初始化指南，直接在本机执行并验证，完成后带我开始第一个项目。

Agent 会处理 Git 仓库、Python 环境、工作空间目录和客户端配置，运行健康检查并调用一次 MCP 工具。默认工作空间是 `~/AI WorkSpace`，也可以在消息里指定新的空目录。使用 macOS 或 Linux；本地模式无需飞书账号。

## 自己从终端安装

确认系统已有 Git 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。

```sh
git clone --branch v0.1.0 --depth 1 https://github.com/lakii2982/ai-workspace.git
cd ai-workspace
./install.sh "$HOME/AI WorkSpace" --agent codex
```

Claude Code 用 `--agent claude`。其他有终端能力的 Agent 用 `--agent cli`，通过 `./aiws` 运行完整工作流。在工作空间里开启新会话，按客户端提示信任目录或启用项目 MCP。先读工作空间的 `START.md`，也可以直接告诉 Agent 想做的事。

- [Agent 初始化指南、手工安装与排障](engine/INSTALL.md)
- [日常工作流程](engine/USER_GUIDE.md)
- [架构设计](engine/ARCHITECTURE.md)
- [CLI / MCP 接口和数据契约](engine/INTERFACE_SPEC.md)
- [MIT 许可证](LICENSE)
