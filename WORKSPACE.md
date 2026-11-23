# 开始使用 AI WorkSpace

在本目录打开 Agent 聊天，告诉它要做的事。例如「为我创建一个『个人知识整理』项目，把现有资料盘点为第一个任务，给我一份下一步计划」。

原始项目文件放在对应项目的 `资料/`。任务对应的工作稿和成果路径由 Agent 从任务记录取得。下一次打开这个目录时，可以让 Agent «继续『个人知识整理』上一次的任务»。

从默认安装的 `Workspace/` 运行以下命令。自定义安装路径使用安装结果中的 `launcher`，在子命令前传入 `--workspace "$PWD"`。

```sh
../aiws_core/aiws doctor
../aiws_core/aiws project list
../aiws_core/aiws job list
```

继续了解 [工作流程](https://github.com/lakii2982/ai-workspace/blob/main/engine/USER_GUIDE.md) 和 [CLI / MCP 接口](https://github.com/lakii2982/ai-workspace/blob/main/engine/INTERFACE_SPEC.md)。
