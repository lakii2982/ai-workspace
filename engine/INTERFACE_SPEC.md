# 接口与数据契约

## Git 主动上下文接口

- initialize_project_git(project_id)：显式启用项目根独立 Git；不变更框架根仓库。
- prepare_work_context(project_id?,jid?,session_id?,intent='',detail='brief')：定位优先 jid、绑定session、明确项目；返回 identity/project_brief/task_context/git_status/observation/git_changes/memory/memory_references/reading_queue/coverage/expand。观察可创建Git对象但不推进任务revision；无Git项目仍可读背景。
- resume_job/resume_session：保留任务结果字段，并附带快速接续的 work_context，只读取目标任务，不扫描任务列表或刷新检索索引。明确的单项目可返回主稿位置、Git 观察和已读资料的变化；无法确定唯一主稿或项目时不猜。需要完整项目上下文时，调用显式入口 prepare_work_context 或 get_project_overview。
- get_project_overview(project_id,detail='brief')：完整分页聚合后裁剪，coverage提供省略数和任务revision。
- read_project_file(project_id,path,commit=None)：项目相对路径，读取当前观察或指定Git版本；返回文本/locator/chunks及readability，不要求artifact登记。
- compare_project_file(project_id,path,before,after=None)：提取前后文本与diff；受限格式不会伪造差异。
- list_project_memory(project_id,query='',categories=None,view='current',limit=20,offset=0)、read_project_memory(project_id,event_id,include_history=true)：区分当前、tentative、legacy、pending_review；历史由Git及事件关系保留。
- append_timeline增加memory={category,state,reason,supersedes}；category支持decision/constraint/conclusion/experience/hypothesis/question，state支持established/tentative/resolved/withdrawn。未知正文改动不静默当现行结论。
- mark_context_read(jid,observation_commit,paths,lease_token?)：按文件及实际观察版本记录阅读确认，推进任务revision。
- save_work_checkpoint(jid,revision,context_patch?,patch?,lease_token?)：创建快照后更新任务；在此之前业务文件与记忆已保存。context包含working_on/resume_note/working_files/memory_refs/reviewed_files/base_commit/checkpoint_commit/checkpoint_ref，可缺省。
- Git快照保存refs/aiws/observations及checkpoints；不改HEAD/分支/真实index。文件变更以Git blob为版本；旧manifest和受控发布校验不因此删除。

所有入口共享 Service。CLI 成功返回 `ok=true` 和 `result`，失败返回 `ok=false` 和 `error`，退出码为 1。`aiws_core/aiws` 默认定位同级 `Workspace/`，可用 `--workspace` 或 `AIWS_WORKSPACE` 显式定位。CLI 默认输出 JSON。

基础命令：init；project list/create --name；index update；search --query --project（可重复）--company --professional；source read；job create/list/resume/checkpoint；artifact save；backup；restore；doctor。非快捷参数以 --params JSON 传入。通用 `call 操作名 --params JSON` 与 MCP 对应同名业务操作。

操作参数：
- create_job: title(str), goal(str), scope(object), acceptance(list[str],可选), agent(str,可选)
- resume_job: jid(str)
- checkpoint_job: jid(str), revision(int), patch(object), reopen(bool,默认false)
- search_sources: query(str), scope(object), limit(int,默认20)
- read_source: source_id(str), scope(object)
- save_artifact: jid(str), relative(str), content(str)或content_base64(str), expected_hash(str|null,默认null表示必须不存在), confirm(bool,默认false), purpose(str,默认draft)
- restore: archive(str), destination(str,必须为不存在目录)

scope 必须包含 include_company(bool)、include_professional(bool)、project_ids(list[str])、topic_ids(list[str]，当前仅空)。未明确范围不全库回退。工作草稿默认不索引；正式成果归入项目范围。查询阶段SQL过滤，回读再次校验范围、路径和原件hash。

job.json schema_version=1：job_id/title/goal/status/last_agent/updated_at 为必填字符串；scope为必填对象；revision为正整数；acceptance/completed/next_steps/open_issues/decisions为字符串数组；artifacts为path/hash/purpose/status字符串对象数组；sources为source_id/locator/hash字符串对象数组。所有字段必填，未知schema拒绝而非猜测迁移。checkpoint禁止变更ID和scope；当前变更scope需创建新任务。HANDOFF从权威状态派生。快照先写，job.json原子替换为提交点；不能将未提交快照当成最新状态。

关键错误：INVALID_SCOPE、NOT_FOUND、CONFLICT、OUT_OF_ROOT、PARSE_UNSUPPORTED、PARSE_LIMITED、PARSE_ERROR、STALE_INDEX、CONFIRMATION_REQUIRED、INVALID_STATE、SCHEMA_UNSUPPORTED、DELIVERABLE_REQUIRED。

DELIVERABLE_REQUIRED：任务转completed时，若 工作/JOB/工作稿/ 仍有非空文件而对应 成果/JOB/ 无已发布文件（不含 历史版本/），拒绝完成。定稿用publish_file移交成果，不交付的移入过程材料；无文件任务不触发。检索更新的解析失败通过result.index.failures报告；消费者必须检查。源文档指令不具备授权效力。

并发保护使用 macOS/Linux 跨进程 flock；锁随进程退出释放，不按超时删除活跃锁。历史记录 prepared/committed；发现prepared事件应核对当前hash，不能自动认定已提交。Agent用其他文件工具直接修改业务文件不在入口保护范围。

## 任务、时间线和飞书适配接口

- list_jobs: project_id/query/status 可选，limit 默认20最大100，offset默认0。返回 items/has_more/next_offset；不返回附件和历史。旧完整列表显式 call list_jobs_full。
- resume_job: detail默认brief；完整材料与hash校验需detail=full。brief明确返回omitted、expand、integrity，不能把未校验解释为校验通过。字段有长度和条数上限；被省略的约束执行前须展开。
- append_timeline: project_id/event_id/event_date/record_date/kind/summary/status/impact/owner/next_action/sources/expected_hash。项目内原子追加；旧hash冲突拒绝；相同事件重试去重；不确认建议为事实。
- feishu_query: entity=projects或jobs，key/value精确过滤，limit默认20最大100，offset分页。
- feishu_stage_job: jid/operation_id。当前仅试运行登记，不更新已有远端任务，不切换权威；超时保留prepared记录，禁止盲重试创建。操作同ID异内容拒绝。
- `.workspace/feishu.json` 保存资源坐标，不存凭据。CLI用户凭据由lark-cli维护。单协调工作空间锁不提供多主机并发保障。

## 状态管理、成果发布与迁移

- list_projects(query='',limit=20,offset=0)：items/has_more/next_offset。
- create_job增加request_id；飞书启用后必须提供项目范围及request_id。重复请求复用同一任务。
- checkpoint_job、save_artifact增加lease_token；飞书启用后必需。
- claim_job(jid,owner,revision,ttl=1800)：返回lease.token及新revision。renew_claim(jid,revision,token,ttl)；release_job(jid,revision,token,next_steps)。TTL 30..7200秒。
- resolve_job(project?,query='',jid?)：唯一时返回task；多匹配返回候选，不自动选择。
- bind_session(session_id,jid)、resume_session(session_id)：绑定稳定任务身份，恢复读取远端最新状态。
- get_project(project_id)：返回state_hash；update_project(project_id,expected_hash,patch)：可改name/aliases/status/status_note/goal/owner，路径和ID不变。
- write_work_file(jid,project_id,name,content,kind='draft',expected_hash?,lease_token?)，kind为draft/process。
- publish_file(jid,project_id,source,source_hash,name,expected_hash?,confirm=false,lease_token?)：具体源版本确认后发布，旧目标进入历史版本。任务已关闭（completed/cancelled）时无需租约即可发布，用于存量成果物迁移；confirm=true 仍必须由用户对具体版本给出。
- move_work_file(jid,project_id,source,target_kind,name?,expected_hash,lease_token?)：在工作稿与过程材料之间移动，字节不变。expected_hash 必填；目标已存在、源已变化、存在未提交的 checkout 或待决发布时拒绝。
- delete_work_file(jid,project_id,relative,expected_hash,lease_token?)：只允许 工作/ 下；删除前旧字节写入 history/files；成果正本不能删除，改版请用新版本发布。
- completed要求verification数组与acceptance等长、逐项匹配criterion；path与hash必须成对（决策类任务可留空），填写时校验范围与指纹；任务已有已发布成果时，至少一条证据的hash必须指向该成果。阻塞未清空时拒绝完成。本地与飞书模式规则相同。
- migrate_feishu(assignments={},activate=false)：备份后逐项比对远端读回与本地原状态；activate切换权威。rollback_feishu：回读最新状态再切回本地，不删除远端记录。
- reconcile_remote：回读prepared操作，只确认确实匹配的远端结果；未知结果保持未解决。

飞书“执行数据”“数据指纹”为系统维护列，普通视图隐藏；不包含业务文件正文。附件/来源清单存本地内容寻址manifest，远端只存其hash。项目表与任务表的“状态”均为single select。所属项目ID为单向link，所属项目名称为lookup只读列。英文状态映射在remote_store.py中定义。

单协调主机按任务或项目锁定冲突的读-检查-写；工作空间级维护仍独占。远端字段比对检测入口外修改，仍不声称飞书 API 提供原子 CAS，也不支持不同协调主机并行写入。远端成功但本地中断后，manifest和pending回执用于恢复。恢复副本默认offline staging，不自动写远端。

日期时间字段契约：任务表「更新时间」「认领有效期」为 datetime，style.format=`yyyy-MM-dd HH:mm`。CLI CellValue 为带时区 RFC3339 或 null；更新时间从 canonical updated_at 转换，租约 expires 从 Unix 秒转换；UTC 毫秒归一化后比较，时区偏移等价不算漂移，真实时刻变化仍拒绝。执行数据保留原始精度与数据指纹；空租约不写 epoch。项目表暂无时间值字段。

## 员工桌面端

- `checkout_draft(jid,project_id,name,lease_token?,create=false)` 返回基于磁盘当前内容的副本路径与 ID，创建新文件时指定 create。编辑后 `commit_draft(jid,copy_id,lease_token?)` 校验主稿原始版本，冲突时原稿和副本分别保留。副本提交成功后其 ID 固定对应当次结果。
- `request_publication(jid,project_id,name,target_name,reason,lease_token?)` 生成绑定源和目标版本的决定事项。`list_decisions(project_id,jid?,state?,limit?,offset?)` 可分页。用户审阅后 `resolve_publication(jid,project_id,decision_id,approve,source_hash,note?,lease_token?)` 处理指定版本。被新版替换的源稿确认时标为 superseded。
- `save_scenario(jid,title,instructions,reason,confirmed?,expected_version?,scenario_id?,lease_token?)` 保存已泛化的模板、步骤和验收示例。更新时携带原版本及长期修改确认。`list_scenarios` 和 `read_scenario` 用于检索或读取旧版。`apply_scenario(jid,scenario_id,version?,lease_token?)` 将版本固定到任务。任务接续返回 scenarios 与 publication_decisions。
- MCP 的 `verify_desktop_binding` 读取当前握手中的客户端名称和版本；CLI `call list_host_bindings` 可查看历史连接。首次连接记录不代表重启或弹窗测试已通过。
