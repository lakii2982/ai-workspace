# 安装与初始化

## 给本地 Agent 的执行指南

用户把项目链接交给你并要求安装时，在本机按以下顺序执行。先确认手头有本机终端权限，并核对仓库地址是 `https://github.com/lakii2982/ai-workspace`。如果当前在云端终端，使用客户端的本地终端能力，或明确告知用户当前只能在云端执行。

1. 检查系统是 macOS/Linux，运行 `git --version` 和 `uv --version`。缺 Git 时走系统安装方式，例如 macOS 的 Command Line Tools 或 Linux 的包管理器。缺 uv 时按 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)安装到用户目录。用独立安装器时，另存官方脚本后运行 `UV_NO_MODIFY_PATH=1 sh <下载的脚本>`，检查 `$HOME/.local/bin/uv --version`。需要系统管理员权限或交互确认时，交给用户操作那一步，再继续。
2. 源码与数据使用两个独立目录。默认把源码放在 `$HOME/.local/share/aiws/source`，把工作空间建在用户指定目录，默认为 `$HOME/AI WorkSpace`。父目录不存在时创建。用 `git clone --branch v0.1.0 --depth 1 https://github.com/lakii2982/ai-workspace.git <源码目录>`。已有检出只在 origin 和版本匹配时复用。目标有其他内容时优先选择新的空目录，明确指定的目录有冲突时请用户选新路径。保留已有资料和规则文件。
3. 确定用户当前的 Agent。Codex 用 `--agent codex`，Claude Code 用 `--agent claude`，其他客户端用 `--agent cli`。从下载的源码目录执行 `./install.sh "<工作空间目录>" --agent <客户端>`。该命令会用 uv 准备 Python 3.12、安装锁定依赖、写入局部启动器及 Agent 规则、初始化本地状态、运行 doctor 并实测 MCP stdio 的 `list_projects`。
4. 核对安装输出中的工作空间绝对路径及 `doctor: healthy`、`mcp: verified`。从任意目录调用 `"<工作空间目录>/aiws" project list` 和 `"<工作空间目录>/aiws" job list`，确认正常。将该目录作为 Agent 会话的工作目录。当前会话未加载新配置时，引导用户在这个目录新开会话并处理客户端信任提示，再试 `aiws` 的 `list_projects`。无 MCP 的客户端走 CLI。
5. 返回工作空间路径、已验证的入口和当前客户端需要的最后一步。给用户一句可直接发给 Agent 的任务，例如「创建『个人知识整理』项目，建一个资料盘点任务，保存下一步，下次从这里接续」。工作区初始为空，用户同时给了真实项目目标时直接创建首个项目与任务。

## 配置与排障

- 项目级配置只在工作空间内生成。Codex 使用 `.codex/config.toml`，Claude Code 使用 `.mcp.json`。两者都用同一个 `aiws --mcp` 启动本工作空间的服务。客户端配置参考 [Codex MCP](https://developers.openai.com/codex/mcp) 和 [Claude Code MCP](https://code.claude.com/docs/en/mcp)。
- 安装命令可以安全重跑，已有规则和客户端文件出现差异时会提示冲突。如果选择把配置合并进已有文件，仅添加当前工作空间的 MCP 条目并保留用户配置。CLI 可在客户端尚未加载 MCP 时使用。
- Python 或依赖下载失败时先修复网络或代理，再用同一命令重试。CLI 报异常时先运行工作空间的 `./aiws doctor`；若 MCP 启动失败，先核对启动器绝对路径及新会话的项目配置。
- 启动器引用源码检出中的虚拟环境。保持源码目录可用。安装新版本前先执行工作空间的 `./aiws backup`；升级可继续使用同一个源码和数据目录，安装器发现模板差异时请核对后合并。搬迁时使用私有备份恢复到新目录，并按新路径重配启动器和客户端。
- 移除工具时可以删除源码检出中的虚拟环境和对应 MCP 条目，资料仍位于独立工作空间。私有备份可能包含业务资料、项目 Git 历史和本地设置，保存在可信位置。
- Windows 原生环境缺少本版使用的文件锁，使用 Linux 或 WSL 中的本地 Agent 和终端。飞书适配器保留供已有兼容环境使用，本地初始化无需启用它。

## 开发与测试

从源码根目录运行 `./install.sh`，传入源码之外的空工作空间，随后运行：

```sh
engine/.venv/bin/python -m pytest engine/tests -q
```
