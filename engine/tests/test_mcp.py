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
                tools=await session.list_tools();assert {'list_jobs','resume_job','save_artifact','save_memory_note','list_memory_notes','report_issue'} <= {t.name for t in tools.tools}
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
                searched=await session.call_tool('search_sources',{'query':'采购周期','scope':scope})
                assert payload(searched)['ok']
                sid=payload(searched)['result']['matches'][0]['source_id']
                readback=await session.call_tool('read_source',{'source_id':sid,'scope':scope})
                assert payload(readback)['ok']
                saved=await session.call_tool('save_artifact',{'jid':'JOB-0001','relative':p['path']+'/工作/JOB-0001/draft.md','content':'MCP草稿'})
                assert payload(saved)['ok']
                checkpoint=await session.call_tool('checkpoint_job',{'jid':'JOB-0001','revision':2,'patch':{'status':'in_progress','next_steps':['复核来源']}})
                assert payload(checkpoint)['ok']
                resumed=await session.call_tool('resume_job',{'jid':'JOB-0001'})
                assert payload(resumed)['result']['revision']==3
                enabled=await session.call_tool('initialize_project_git',{'project_id':'PRJ-0001'})
                assert payload(enabled)['result']['enabled']
                prepared=payload(await session.call_tool('prepare_work_context',{'jid':'JOB-0001'}))['result']
                observation=prepared['observation']['commit']
                actual=payload(await session.call_tool('read_project_file',{'project_id':'PRJ-0001','path':'资料/source.txt','commit':observation}))
                assert '采购周期30天' in actual['result']['text']
                ack=payload(await session.call_tool('mark_context_read',{'jid':'JOB-0001','observation_commit':observation,'paths':['资料/source.txt']}))
                assert ack['result']['revision']==4
                saved_context=payload(await session.call_tool('save_work_checkpoint',{'jid':'JOB-0001','revision':4,'context_patch':{'working_on':'采购分析'}}))
                assert saved_context['result']['context']['checkpoint_commit']
                overview=payload(await session.call_tool('get_project_overview',{'project_id':'PRJ-0001'}))
                assert overview['result']['total_tasks']==1
                preview=payload(await session.call_tool('report_issue',{'title':'MCP 预览','details':'只预览不提交'}))
                assert preview['ok'] and preview['result']['requires_confirmation'] is True
                assert str(tmp_path) not in preview['result']['issue']['body']
    asyncio.run(run())
