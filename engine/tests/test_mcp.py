import json
import asyncio
import os
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from aiws.core import Workspace

def payload(result):
    assert not result.isError, result
    return result.structuredContent or json.loads(result.content[0].text)

def test_real_stdio(tmp_path):
    w=Workspace(tmp_path);w.init();p=w.project_create('MCP')
    w.path(p['path']+'/资料/source.txt').write_text('MCP采购周期30天')
    async def run():
        params=StdioServerParameters(command=sys.executable,args=['-m','aiws.mcp_server_entry'],env={**os.environ,'AIWS_WORKSPACE':str(tmp_path)})
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                tools=await session.list_tools();assert {'list_jobs','resume_job','save_artifact','receive_source','save_memory_note','list_memory_notes','report_issue'} <= {t.name for t in tools.tools}
                search_tool=next(tool for tool in tools.tools if tool.name=='search_sources')
                assert {'scope','project_id'} <= set(search_tool.inputSchema['properties'])
                assert 'project_ids' not in search_tool.inputSchema['properties']
                result=await session.call_tool('list_projects',{})
                assert not result.isError and 'PRJ-0001' in str(result)
                noted=await session.call_tool('save_memory_note',{'project_id':'PRJ-0001','name':'report-style','description':'用户偏好要点式汇报','type':'user','content':'# 汇报偏好\n\n先给结论。'})
                assert payload(noted)['ok']
                notes=payload(await session.call_tool('list_memory_notes',{'project_id':'PRJ-0001'}))
                assert notes['result']['items'][0]['name']=='report-style'
                scope={'include_company':False,'include_professional':False,'project_ids':['PRJ-0001'],'topic_ids':[]}
                job=await session.call_tool('create_job',{'title':'MCP task','goal':'Verify','scope':scope})
                assert not job.isError and 'JOB-0001' in str(job)
                result=await session.call_tool('resume_job',{'jid':'JOB-0001'})
                assert not result.isError and 'Verify' in str(result)
                # Real host failure: nested scope omitted only topic_ids. The
                # MCP adapter restores it without altering the other bounds.
                searched=await session.call_tool('search_sources',{'query':'采购周期',
                    'scope':{'project_ids':['PRJ-0001'],'include_company':False,
                             'include_professional':False}})
                assert payload(searched)['ok']
                sid=payload(searched)['result']['matches'][0]['source_id']
                scoped_read=await session.call_tool('read_source',{'source_id':sid,
                    'scope':{'project_ids':['PRJ-0001'],'include_company':False,
                             'include_professional':False}})
                assert payload(scoped_read)['ok']
                readback=await session.call_tool('read_source',{'source_id':sid,'project_id':'PRJ-0001'})
                assert payload(readback)['ok']
                # Complete original scope remains valid, as does the selected
                # project shorthand; they cannot be combined.
                legacy=await session.call_tool('search_sources',{'query':'采购周期','scope':scope})
                assert payload(legacy)['ok']
                conflict=payload(await session.call_tool('search_sources',{
                    'query':'采购周期','scope':scope,'project_id':'PRJ-0001'}))
                assert conflict['ok'] is False and conflict['error']['code']=='INVALID_ARGUMENT'
                saved=await session.call_tool('save_artifact',{'jid':'JOB-0001','relative':p['path']+'/工作/JOB-0001/draft.md','content':'MCP草稿'})
                assert payload(saved)['ok']
                received=await session.call_tool('receive_source',{'jid':'JOB-0001','project_id':'PRJ-0001','name':'附件.txt','content':'MCP收件 供应商报价','confirm':True})
                assert payload(received)['ok'] and payload(received)['result']['replayed'] is False
                checkpoint=await session.call_tool('checkpoint_job',{'jid':'JOB-0001','revision':3,'patch':{'status':'in_progress','next_steps':['复核来源']}})
                assert payload(checkpoint)['ok']
                resumed=await session.call_tool('resume_job',{'jid':'JOB-0001'})
                assert payload(resumed)['result']['revision']==4
                enabled=await session.call_tool('initialize_project_git',{'project_id':'PRJ-0001'})
                assert payload(enabled)['result']['enabled']
                prepared=payload(await session.call_tool('prepare_work_context',{'jid':'JOB-0001'}))['result']
                observation=prepared['observation']['commit']
                actual=payload(await session.call_tool('read_project_file',{'project_id':'PRJ-0001','path':'资料/source.txt','commit':observation}))
                assert '采购周期30天' in actual['result']['text']
                ack=payload(await session.call_tool('mark_context_read',{'jid':'JOB-0001','observation_commit':observation,'paths':['资料/source.txt']}))
                assert ack['result']['revision']==5
                saved_context=payload(await session.call_tool('save_work_checkpoint',{'jid':'JOB-0001','revision':5,'context_patch':{'working_on':'采购分析'}}))
                assert saved_context['result']['context']['checkpoint_commit']
                overview=payload(await session.call_tool('get_project_overview',{'project_id':'PRJ-0001'}))
                assert overview['result']['total_tasks']==1
                preview=payload(await session.call_tool('report_issue',{'title':'MCP 预览','details':'只预览不提交'}))
                assert preview['ok'] and preview['result']['requires_confirmation'] is True
                assert str(tmp_path) not in preview['result']['issue']['body']
    asyncio.run(run())


def test_stdio_checkpoint_schema_and_write_revision(tmp_path):
    w=Workspace(tmp_path);w.init();project=w.project_create('检查点')
    async def run():
        params=StdioServerParameters(command=sys.executable,args=['-m','aiws.mcp_server_entry'],
                                     env={**os.environ,'AIWS_WORKSPACE':str(tmp_path)})
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                tools=await session.list_tools()
                checkpoint_tool=next(tool for tool in tools.tools if tool.name=='save_work_checkpoint')
                definitions=checkpoint_tool.inputSchema['$defs']
                context_schema=definitions['ContextPatch']
                assert {'working_on','working_files','resume_note'} <= set(context_schema['properties'])
                assert context_schema['additionalProperties'] is True
                file_schema=definitions['WorkingFile']
                assert {'path','role'} <= set(file_schema['properties'])
                scope={'include_company':False,'include_professional':False,
                       'project_ids':[project['project_id']],'topic_ids':[]}
                job=payload(await session.call_tool('create_job',{'title':'续写','goal':'保存主稿','scope':scope}))['result']
                saved=payload(await session.call_tool('write_work_file',{
                    'jid':job['job_id'],'project_id':project['project_id'],'name':'报价.md','content':'第一版'}))['result']
                assert saved['job_revision']==job['revision']+1
                checkpoint=payload(await session.call_tool('save_work_checkpoint',{
                    'jid':job['job_id'],'revision':saved['job_revision'],
                    'context_patch':{'base_commit':None,
                                      'working_files':[{'path':saved['path'],'role':'主稿','legacy_hint':'保留'}]}}))
                assert checkpoint['ok'] and checkpoint['result']['context']['working_files'][0]['role']=='主稿'
                assert checkpoint['result']['context']['working_files'][0]['legacy_hint']=='保留'
                resumed=payload(await session.call_tool('resume_job',{'jid':job['job_id']}))['result']
                assert resumed['revision']==checkpoint['result']['revision']
                appended=payload(await session.call_tool('checkpoint_job',{
                    'jid':job['job_id'],'revision':resumed['revision'],
                    'patch':{'next_steps':['继续核对']}}))
                assert appended['ok']
                stale=payload(await session.call_tool('save_work_checkpoint',{
                    'jid':job['job_id'],'revision':saved['job_revision']}))
                assert stale['ok'] is False and stale['error']['code']=='CONFLICT'
                unknown=payload(await session.call_tool('save_work_checkpoint',{
                    'jid':job['job_id'],'revision':appended['result']['revision'],
                    'context_patch':{'primary_draft':'报价.md'}}))
                assert unknown['ok'] is False and unknown['error']['code']=='INVALID_ARGUMENT'
                assert 'working_files' in unknown['error']['message']
    asyncio.run(run())


def test_open_work_stdio(tmp_path):
    w=Workspace(tmp_path);w.init();p=w.project_create('入口')
    (w.path(p['path']+'/资料/报价.md')).write_text('报价资料')
    (w.path(p['path']+'/资料/附件')).mkdir()
    (w.path(p['path']+'/资料/.DS_Store')).write_text('macOS metadata')
    (w.path(p['path']+'/资料/~$报价.xlsx')).write_text('Office lock')
    async def run():
        params=StdioServerParameters(command=sys.executable,args=['-m','aiws.mcp_server_entry'],env={**os.environ,'AIWS_WORKSPACE':str(tmp_path)})
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                tools=await session.list_tools();assert 'open_work' in {t.name for t in tools.tools}
                # Unknown request: bounded candidates, original intent, no writes.
                choices=payload(await session.call_tool('open_work',{'intent':'写年度总结'}))
                assert choices['result']['status']=='candidates'
                assert choices['result']['reason']=='no_project'
                assert choices['result']['intent']=='写年度总结'
                assert choices['result']['projects']==[{'project_id':'PRJ-0001','name':'入口','status':'active','path':p['path']}]
                assert 'project_sources' not in choices['result']
                # Explicit intentional creation through the same entry.
                opened=payload(await session.call_tool('open_work',{'intent':'写年度总结','project':'入口','new_task':True,'request_id':'mcp-entry-1'}))
                result=opened['result']
                assert result['status']=='opened' and result['created']=={'project':False,'task':True}
                assert result['work']['goal']=='写年度总结'
                assert result['work']['scope']['project_ids']==['PRJ-0001']
                sources=result['project_sources']
                assert sources['project_id']=='PRJ-0001' and sources['folder']=='资料/' and sources['count']==2
                quote=next(item for item in sources['items'] if item['name']=='报价.md')
                assert quote['read']==dict(operation='read_project_file',project_id='PRJ-0001',path='资料/报价.md')
                folder=next(item for item in sources['items'] if item['name']=='附件')
                assert folder['kind']=='directory' and folder['recursive'] is False
                # Retries through separate CLI processes replay the same task.
                again=payload(await session.call_tool('open_work',{'intent':'写年度总结','project':'入口','new_task':True,'request_id':'mcp-entry-1'}))
                assert again['result']['work']['job_id']==result['work']['job_id']
                assert again['result']['replayed'] is True
                jobs=payload(await session.call_tool('list_jobs',{'project_id':'PRJ-0001'}))
                assert [x['job_id'] for x in jobs['result']['items']]==[result['work']['job_id']]
    asyncio.run(run())
