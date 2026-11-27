# 在 Codex 里开始工作

在这个工作空间打开 Codex，像交代工作一样说出需求。

> 帮我把这份报价整理成一页给采购看。

如果你没有说项目，Codex 会问一次。回项目名即可继续。第一次使用还没有项目时，可以说「新建秋季采购项目，用它来放这份报价」。收到首稿后，点开文件查看，直接在聊天里说要改哪里。

| 目的 | 可以怎样说 |
|---|---|
| 查看依据 | “这里的交期是从哪份文件哪一行来的？” |
| 隔天继续 | “继续秋季采购的报价，把上次待补的交期加上。” |
| 定稿 | “我刚看的这一版可以定稿。” |
| 找回发出的版本 | “发给客户的是哪一版？” |

一个 Codex 就能完成这些工作。项目中的 `资料/` 保存原件，`工作/` 保存可以继续编辑的稿件，`成果/` 保存你已确认的版本。

## 查看安装状态

从默认安装的 `Workspace/` 运行以下命令。自定义安装路径使用安装结果中的 `launcher`，在子命令前传入 `--workspace "$PWD"`。

```sh
../aiws_core/aiws doctor
../aiws_core/aiws project list
../aiws_core/aiws job list
```

继续了解 [工作流程](https://github.com/lakii2982/ai-workspace/blob/main/engine/USER_GUIDE.md) 和 [CLI / MCP 接口](https://github.com/lakii2982/ai-workspace/blob/main/engine/INTERFACE_SPEC.md)。
