# 安装与初始化

## 给本地 Agent 的执行指南

用户把项目链接交给你并要求安装时，在本机按以下顺序执行。先确认手头有本机终端权限，并核对仓库地址是 `https://github.com/lakii2982/ai-workspace`。如果当前在云端终端，使用客户端的本地终端能力，或明确告知用户当前只能在云端执行。

1. 检查系统是 macOS/Linux，运行 `git --version` 和 `uv --version`。缺 Git 时走系统安装方式，例如 macOS 的 Command Line Tools 或 Linux 的包管理器。缺 uv 时按 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)安装到用户目录。用独立安装器时，另存官方脚本后运行 `UV_NO_MODIFY_PATH=1 sh <下载的脚本>`，检查 `$HOME/.local/bin/uv --version`。需要系统管理员权限或交互确认时，交给用户操作那一步，再继续。
2. 选定安装根目录，默认是 `$HOME/AI WorkSpace`。在根目录执行 `git clone https://github.com/lakii2982/ai-workspace.git aiws_core`。已有检出时核对 `origin` 和要安装的版本。开发和日常使用选各自的安装根目录，每套安装各有一个 `aiws_core/` 和 `Workspace/`。
3. 在安装根目录执行 `./aiws_core/install.sh --agent codex`，Claude Code 使用 `--agent claude`，其他客户端使用 `--agent cli`。安装器用 uv 准备 Python 3.12 和锁定依赖，创建同级 `Workspace/` 并初始化本地资料目录与状态。已有数据目录可以显式传入，例如 `./aiws_core/install.sh "<数据目录>" --agent codex`。
4. 核对安装输出的 `core`、`workspace`、`doctor` 和 `mcp`。安装器验证核心服务能启动。Codex 还需信任 `session_directories` 中实际打开的目录，再新建会话调用 `verify_desktop_binding`，核对返回的工作空间路径。
5. 返回工作空间路径、已验证的入口和当前客户端需要的最后一步。给用户一句可直接发给 Agent 的任务，例如「创建『个人知识整理』项目，建一个资料盘点任务，保存下一步，下次从这里接续」。工作区初始为空，用户同时给了真实项目目标时直接创建首个项目与任务。

## 配置与排障

- Codex 在数据目录下写入 `.codex/config.toml`。默认的同级 `aiws_core/` 与 `Workspace/` 布局也在安装根目录注册同一个服务。Claude Code 使用数据目录内的 `.mcp.json`。服务通过核心目录的 `aiws --mcp` 启动，并通过 `AIWS_WORKSPACE` 绑定数据目录。客户端配置参考 [Codex MCP](https://developers.openai.com/codex/mcp) 和 [Claude Code MCP](https://code.claude.com/docs/en/mcp)。
- 在 Codex 的绑定入口，`required = true` 要求核心在会话启动时连通。所有 AIWS 项目任务从其中一个绑定目录开会话，在会话内通过核心选择项目。已有会话、直接打开的项目子目录和其他 Codex 项目应先验证绑定。核心不可用时暂停任务。
- 安装可以重复执行。安装器更新旧版默认 Agent 规则并保留用户自定义规则、已有文档和工具级审批设置。
- Python 或依赖安装失败时修复网络或代理后重试。CLI 报异常时用核心入口执行 `doctor`。MCP 启动失败时核对客户端配置中的核心入口与数据目录。
- 移除工具时可以删除源码检出中的虚拟环境和对应 MCP 条目，资料仍位于独立工作空间。私有备份可能包含业务资料、项目 Git 历史和本地设置，保存在可信位置。
- Windows 原生环境缺少本版使用的文件锁，使用 Linux 或 WSL 中的本地 Agent 和终端。飞书适配器保留供已有兼容环境使用，本地初始化无需启用它。

## 开发与测试

进入开发安装的 `aiws_core/`，执行 `./install.sh` 初始化同级测试数据目录，然后：

```sh
make bootstrap   # 每个克隆执行一次
make check       # lint + 全量测试
```

改动涉及安装、升级或桌面打包时，额外运行 `make verify-install`。分支、提交和发布规则见仓库根目录 `AGENTS.md`。

## 旧版升级

升级前使用旧工作空间内的 `./aiws backup` 保存私有备份。更新代码后用 `./install.sh "<原数据目录>" --agent <客户端>` 指定原工作空间。核心和数据路径相同的旧安装可以继续使用已有的启动器和客户端配置。搬迁到新目录时，重建核心目录内的虚拟环境并按新路径更新客户端的 `aiws` 条目。之后用核心目录的 `aiws` 验证 `doctor` 和项目清单。
