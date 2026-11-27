
import pytest
from docx import Document

from aiws.core import Error, digest
from aiws.service import Service


def test_office_handoff_publication_and_scenario(tmp_path):
    service = Service(tmp_path)
    call = service.call
    call('init', {})
    project = call('create_project', {'name': '报价'})
    pid = project['project_id']
    scope = dict(project_ids=[pid], include_company=False, include_professional=False, topic_ids=[])
    task = call('create_job', dict(title='初稿', goal='报价', scope=scope))
    jid = task['job_id']
    args = dict(jid=jid, project_id=pid)
    draft = call('job_directories', {'jid': jid})[0]['work'] + '/工作稿/报价.docx'
    main = tmp_path / draft

    def write(path, price):
        doc = Document()
        doc.add_paragraph(price)
        doc.save(path)

    new = call('checkout_draft', dict(**args, name=main.name, create=True))
    write(tmp_path / new['path'], '报价 100')
    call('commit_draft', dict(jid=jid, copy_id=new['copy_id']))
    checkout = call('checkout_draft', dict(**args, name=main.name))
    write(tmp_path / checkout['path'], 'A 报价 100')
    request = dict(**args, name=main.name, target_name='报价_v1.0.docx', reason='核准报价')
    old = call('request_publication', request)
    write(main, '人工改价 88')
    client_b = Service(tmp_path)
    b = client_b.call('checkout_draft', dict(**args, name=main.name))
    assert Document(tmp_path / b['path']).paragraphs[0].text == '人工改价 88'
    with pytest.raises(Error, match='changed'):
        call('commit_draft', dict(jid=jid, copy_id=checkout['copy_id']))
    assert Document(main).paragraphs[0].text == '人工改价 88'
    assert Document(tmp_path / checkout['path']).paragraphs[0].text == 'A 报价 100'
    with pytest.raises(Error, match='changed'):
        call('resolve_publication', dict(**args, decision_id=old['decision_id'], approve=True,
                                         source_hash=old['source_hash']))
    write(tmp_path / b['path'], '核对后 88')
    saved = client_b.call('commit_draft', dict(jid=jid, copy_id=b['copy_id']))
    assert call('commit_draft', dict(jid=jid, copy_id=b['copy_id'])) == saved
    review = client_b.call('request_publication', request)
    assert call('list_decisions', dict(project_id=pid, state='pending'))['items'] == [review]
    approve = dict(**args, source_hash=saved['hash'], decision_id=review['decision_id'], approve=True)
    resolved = call('resolve_publication', approve)
    assert resolved['state'] == 'published' and client_b.call('resolve_publication', approve) == resolved
    resumed = client_b.call('resume_job', {'jid': jid})
    call('save_work_checkpoint', dict(jid=jid, revision=resumed['revision'],
                                      context_patch={'working_on': '核对后 88'}))
    assert client_b.call('resume_job', {'jid': jid})['work_context']
    assert resumed['publication_decisions'][pid]['items']
    published = tmp_path / resumed['directories'][0]['deliverables'] / request['target_name']
    assert digest(main) == digest(published) == saved['hash']
    sent = call('record_sent', dict(**args, name=request['target_name'], source_hash=saved['hash']))
    assert sent['status'] == 'reported_sent'
    assert sent['evidence']['kind'] == 'user_statement'
    listed = call('list_sent', dict(project_id=pid, name=request['target_name'], source_hash=saved['hash']))['items']
    assert listed[0]['sent_id'] == sent['sent_id'] and listed[0]['version']['state'] == 'current'
    with pytest.raises(Error, match='Published deliverable'):
        call('record_sent', dict(**args, name=request['target_name'], source_hash='0' * 64,
                                 channel='email'))

    second = call('write_work_file', dict(**args, name=main.name, content='报价 120',
                                           expected_hash=saved['hash']))
    replacement = call('publish_file', dict(**args, source=draft, source_hash=second['hash'],
                                             name=request['target_name'], expected_hash=saved['hash'], confirm=True))
    receipt = tmp_path / project['path'] / '资料/邮件回执.txt'
    receipt.write_text('message-id: 43')
    latest = call('record_sent', dict(**args, name=request['target_name'], source_hash=replacement['hash'],
                                      channel='email', evidence_path=str(receipt.relative_to(tmp_path)),
                                      evidence_hash=digest(receipt)))
    assert latest['evidence']['kind'] == 'channel_receipt'
    versions = call('list_sent', dict(project_id=pid, name=request['target_name']))['items']
    assert [item['source_hash'] for item in versions] == [replacement['hash'], saved['hash']]
    assert versions[1]['version']['state'] == 'archived'
    assert call('read_sent_version', dict(project_id=pid, sent_id=sent['sent_id']))['content_hex']

    scenario_args = dict(jid=jid, title='按客户询价', instructions='从当前任务取得客户名与产品清单。', reason='保存本次做法')
    scenario = call('save_scenario', scenario_args)
    sid = scenario['scenario_id']
    second = call('create_job', dict(title='新项目报价', goal='报价', scope=scope))['job_id']
    apply = dict(jid=second, scenario_id=sid)
    assert client_b.call('apply_scenario', apply)['version'] == 1
    edit = dict(**scenario_args, expected_version=1, scenario_id=sid)
    with pytest.raises(Error, match='Confirm'):
        call('save_scenario', edit)
    call('save_scenario', {**edit, 'confirmed': True, 'instructions': '按产品和维保分别列项。'})
    assert call('apply_scenario', apply)['version'] == 1
    assert call('read_scenario', {'scenario_id': sid})['version'] == 2
    assert call('resume_job', {'jid': second})['scenarios'][0]['version'] == 1
