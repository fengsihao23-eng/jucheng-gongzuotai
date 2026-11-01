"""Pi import plans must stay drafts until yes, and write through real business services exactly once."""
import io
import uuid
import zipfile

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from app.core.response import BizError
from app.models import BizContract, BizOpportunity, BizPayment, BizPaymentPlan, CleanJob
from app.services import intake_agent, pi_bridge
from tests.conftest import data


def _book(sheets):
    wb=Workbook();wb.remove(wb.active)
    for name,rows in sheets.items():
        ws=wb.create_sheet(name)
        for row in rows:ws.append(row)
    buf=io.BytesIO();wb.save(buf);return buf.getvalue()


@pytest.fixture
def agent(monkeypatch):
    calls=[]
    def run(**kwargs):
        execute=kwargs['execute']
        for name,args in calls:
            result=execute(name,args)
            assert result['ok'],result
        return {'engine':'pi-coding-agent','model':'fixture','calls':len(calls)}
    monkeypatch.setattr(pi_bridge,'run',run)
    return calls


def _upload(client,login,content,name,user='admin'):
    created=data(client.post('/api/v1/imports',headers=login(user),data={'intelligent':'true','background':'true'},files={'file':(name,content)}))
    return data(client.get(f"/api/v1/imports/{created['id']}",headers=login(user)))


def _decide(client,login,b,yes,user='admin'):
    return client.post(f"/api/v1/imports/{b['id']}/decision",headers=login(user),
                       json={'version':b['agent_plan']['version'],'approve':yes})


def _table_calls(agent,sheet='合同'):
    agent.extend([
        ('read_source',{'sheet':sheet,'limit':20}),
        ('prepare_table',{'target':'contract','sheet':sheet,'header_row':1,'mapping':{'0':'name','1':'customer_name','2':'amount','3':'sign_date','4':'business_line'},'reason':'合同已签约，按原始字段整理'}),
        ('finalize',{'summary':'准备导入合同，跳过重复和异常行','reason':'核对全表后生成清单'})])


def test_no_declines_without_writing_and_checks_owner(client,login,db,agent):
    tag=uuid.uuid4().hex[:8]
    _table_calls(agent)
    b=_upload(client,login,_book({'合同':[['合同名称','客户','金额','签约日期','业务线'],[f'否决{tag}',f'客户{tag}',12001,'2026-10-20','XY']]}),'待决定.xlsx')
    assert b['status']=='pending' and b['stage']=='confirmation'
    assert b['agent_plan']['totals']['new']==1
    stale=client.post(f"/api/v1/imports/{b['id']}/decision",headers=login('admin'),json={'version':uuid.uuid4().hex,'approve':True})
    assert stale.status_code==409
    assert db.scalar(select(func.count(BizContract.id)).where(BizContract.name==f'否决{tag}'))==0
    assert client.get(f"/api/v1/imports/{b['id']}",headers=login('zengli')).status_code==404
    rejected=data(_decide(client,login,b,False))
    assert rejected['status']=='dismissed'
    assert db.scalar(select(func.count(BizContract.id)).where(BizContract.name==f'否决{tag}'))==0


def test_document_contract_and_payment_plan_commit_once(client,login,db,agent):
    tag=uuid.uuid4().hex[:8].upper()
    quote=f'合同编号 AI-{tag}；客户 测试学校{tag}；智慧校园合同；金额128600元；签约日期2026-10-20。'
    stage='2026-11-01支付首期50000元，2026-12-01支付尾款78600元。'
    agent.extend([
        ('read_source',{}),('ledger_fields',{'target':'contract'}),
        ('prepare_records',{'target':'contract','records':[{'contract_no':f'AI-{tag}','name':f'智慧校园{tag}',
            'customer_name':f'测试学校{tag}','amount':'128600','sign_date':'2026-10-20','business_line':'XY','source_quote':quote,
            'payment_plans':[{'name':'首期','amount':'50000','due_date':'2026-11-01','source_quote':stage},
                             {'name':'尾款','amount':'78600','due_date':'2026-12-01','source_quote':stage}]}], 'reason':'从合同正文提取，不将付款条件当作到账'}),
        ('prepare_records',{'target':'project','records':[{'name':f'智慧校园{tag}','customer_name':f'测试学校{tag}',
            'business_line':'XY','source_quote':quote}],'reason':'同份合同对应的项目草案'}),
        ('prepare_knowledge',{'title':f'合同原件{tag}','category':'业务资料','subcategory':'合同原件','summary':'保留合同原件和付款条件'}),
        ('finalize',{'summary':'新增1条合同及2期回款计划，归档原件','reason':'原文金额日期明确'})])
    before=data(client.get('/api/v1/board/overview',headers=login('admin')))
    b=_upload(client,login,(quote+'\n'+stage).encode(),f'合同-{tag}.txt')
    assert b['status']=='pending',b
    assert b['agent_plan']['totals']['new']==2
    assert next(s for s in b['agent_plan']['steps'] if s.get('target')=='contract')['linked_projects']
    assert db.scalar(select(func.count(BizContract.id)).where(BizContract.contract_no==f'AI-{tag}'))==0
    data(_decide(client,login,b,True))
    done=data(client.get(f"/api/v1/imports/{b['id']}",headers=login('admin')))
    assert done['agent_plan']['state']=='completed',done
    c=db.scalar(select(BizContract).where(BizContract.contract_no==f'AI-{tag}'))
    assert c and c.archive_file_id==b['file']['id']
    assert c.project_code
    assert db.scalar(select(func.count(BizPaymentPlan.id)).where(BizPaymentPlan.contract_id==c.id))==2
    assert db.scalar(select(func.count(BizPayment.id)).where(BizPayment.contract_id==c.id))==0
    assert done['kb_doc']['status']=='published'
    after=data(client.get('/api/v1/board/overview',headers=login('admin')))
    assert after['sign']!=before['sign']
    data(_decide(client,login,b,True))
    assert db.scalar(select(func.count(BizContract.id)).where(BizContract.contract_no==f'AI-{tag}'))==1


def test_all_sheets_and_cross_sheet_duplicates_are_handled(client,login,db,agent):
    tag=uuid.uuid4().hex[:8]
    row=[f'两表{tag}',f'客户{tag}',13001,'2026-10-20','AF']
    headers=['合同名称','客户','金额','签约日期','业务线']
    for sheet in ('第一表','重复表'):
        agent.extend([('read_source',{'sheet':sheet}),('prepare_table',{'target':'contract','sheet':sheet,'header_row':1,
            'mapping':{'0':'name','1':'customer_name','2':'amount','3':'sign_date','4':'business_line'},'reason':'合同数据'})])
    agent.append(('finalize',{'summary':'两个工作表，重复内容跳过','reason':'每表均已分析'}))
    b=_upload(client,login,_book({'第一表':[headers,row],'重复表':[headers,row]}),'两张表.xlsx')
    assert len(b['agent_plan']['steps'])==2
    assert b['agent_plan']['totals']['duplicate']==1
    data(_decide(client,login,b,True))
    done=data(client.get(f"/api/v1/imports/{b['id']}",headers=login('admin')))
    assert done['agent_plan']['state']=='completed',done
    assert db.scalar(select(func.count(BizContract.id)).where(BizContract.name==f'两表{tag}'))==1
    assert done['agent_plan']['totals']['duplicate']==1


def test_file_duplicate_returns_popup_metadata_and_no_second_batch(client,login,agent):
    agent.extend([('read_source',{}),('prepare_knowledge',{'title':'巡检','category':'业务资料','subcategory':'其他资料','summary':'巡检资料'}),
                  ('finalize',{'summary':'原件归档','reason':'参考资料'})])
    content=('巡检资料，记录机房温度及设备异常'+uuid.uuid4().hex).encode()
    b=_upload(client,login,content,'资料.txt')
    r=client.post('/api/v1/imports',headers=login('admin'),data={'intelligent':'true'},files={'file':('换文件名.txt',content)})
    assert r.status_code==409 and r.json()['data']['duplicate'] is True
    assert r.json()['data']['batch_id']==b['id']


def test_low_permission_yes_routes_to_handler_then_admin_confirms(client,login,db,agent):
    tag=uuid.uuid4().hex[:8];_table_calls(agent)
    agent.insert(-1,('prepare_knowledge',{'title':'合同业务原件','category':'业务资料','subcategory':'合同原件','summary':'保留合同原件'}))
    b=_upload(client,login,_book({'合同':[['合同名称','客户','金额','签约日期','业务线'],[f'授权{tag}',f'授权客户{tag}',14001,'2026-10-20','XY']]}),'授权合同.xlsx',user='zengli')
    assert not next(s for s in b['agent_plan']['steps'] if s['kind']=='knowledge')['will_publish']
    data(_decide(client,login,b,True,'zengli'))
    assert db.scalar(select(func.count(BizContract.id)).where(BizContract.name==f'授权{tag}'))==0
    waiting=data(client.get(f"/api/v1/imports/{b['id']}",headers=login('admin')))
    assert next(s for s in waiting['agent_plan']['steps'] if s['kind']=='knowledge')['will_publish']
    assert waiting['status']=='pool'
    data(_decide(client,login,waiting,True))
    assert db.scalar(select(func.count(BizContract.id)).where(BizContract.name==f'授权{tag}'))==1


def test_stale_version_and_unread_document_cannot_be_approved(client,login,agent):
    agent.append(('prepare_knowledge',{'title':'未读资料','category':'业务资料','summary':'正文尚未读取'}))
    agent.append(('finalize',{'summary':'不应完成','reason':'尚未读取'}))
    b=_upload(client,login,('未读正文必须完整读取'+uuid.uuid4().hex).encode(),'未读.txt')
    assert b['status']=='pool' and not b['agent_plan'].get('summary')


def test_zip_can_exceed_individual_file_limit_and_duplicate_archive_is_explicit(client,login,agent,monkeypatch):
    from app.services import import_service
    monkeypatch.setattr(import_service,'MAX_BYTES',1024)
    payload=io.BytesIO()
    with zipfile.ZipFile(payload,'w',compression=zipfile.ZIP_STORED) as z:
        for i in range(4):z.writestr(f'资料{i}.txt',('测试资料'+str(i))*70)
    assert len(payload.getvalue())>1024
    r=data(client.post('/api/v1/imports',headers=login('admin'),files={'file':('大压缩包.zip',payload.getvalue())}))
    assert len(r['batches'])==4
    duplicate=client.post('/api/v1/imports',headers=login('admin'),files={'file':('另一个包.zip',payload.getvalue())})
    assert duplicate.status_code==409 and duplicate.json()['data']['duplicate']
    limits=data(client.get('/api/v1/imports/limits',headers=login('admin')))
    assert limits['zip_bytes']==512*1024*1024 and limits['zip_files']==500


def test_unproven_document_amount_never_creates_a_draft_or_business_record(client,login,db,agent):
    tag=uuid.uuid4().hex[:8]
    quote=f'客户是原文学校{tag}，智慧校园合同金额128600元，签约日期2026-10-20。'
    before=db.scalar(select(func.count(CleanJob.id)))
    agent.extend([('read_source',{}),('prepare_records',{'target':'contract','records':[{
        'name':f'伪造金额{tag}','customer_name':f'原文学校{tag}','amount':'999999','business_line':'XY',
        'sign_date':'2026-10-20','source_quote':quote}],'reason':'不应通过'})])
    b=_upload(client,login,quote.encode(),'金额不符.txt')
    assert b['status']=='pool'
    assert db.scalar(select(func.count(CleanJob.id)))==before
    assert not db.scalar(select(BizContract.id).where(BizContract.name==f'伪造金额{tag}'))


def test_restart_keeps_approval_pending_until_person_confirms(client,login,db,agent):
    from app.core.deps import build_principal
    from app.models import ImportBatch,SysUser
    tag=uuid.uuid4().hex[:8];_table_calls(agent)
    b=_upload(client,login,_book({'合同':[['合同名称','客户','金额','签约日期','业务线'],[f'恢复{tag}',f'恢复客户{tag}',15001,'2026-10-20','XY']]}),'重启恢复.xlsx')
    p=build_principal(db.get(SysUser,b['uploader_id']))
    intake_agent.decide(db,p,b['id'],b['agent_plan']['version'],True);db.commit()
    intake_agent.recover_interrupted()
    db.expire_all()
    interrupted=data(client.get(f"/api/v1/imports/{b['id']}",headers=login('admin')))
    assert interrupted['agent_plan']['state']=='attention'
    assert not db.scalar(select(BizContract.id).where(BizContract.name==f'恢复{tag}'))
    data(_decide(client,login,interrupted,True))
    assert db.scalar(select(func.count(BizContract.id)).where(BizContract.name==f'恢复{tag}'))==1


def test_real_directory_department_is_valid_for_knowledge_visibility(client,login,db,agent):
    from app.models import SysDepartment,SysUser
    code='W'+uuid.uuid4().hex[:7]
    dept=SysDepartment(dept_code=code,name='真实通讯录部门',sort=500)
    db.add(dept);db.flush()
    admin_id=data(client.get('/api/v1/auth/me',headers=login('admin')))['id']
    user=db.get(SysUser,admin_id);original=user.department_id
    try:
        user.department_id=dept.id;db.commit()
        agent.extend([('read_source',{}),('prepare_knowledge',{'title':'真实部门资料','category':'业务资料','subcategory':'其他资料','summary':'部门资料归档'}),
                      ('finalize',{'summary':'归档资料到实际部门','reason':'使用实际通讯录部门'})])
        b=_upload(client,login,('真实部门原件资料'+code).encode(),'部门资料.txt')
        assert b['status']=='pending',b
        data(_decide(client,login,b,True))
        done=data(client.get(f"/api/v1/imports/{b['id']}",headers=login('admin')))
        assert done['agent_plan']['state']=='completed',done
        assert done['kb_doc']['visible_depts']==[code]
        options=data(client.get('/api/v1/kb/meta',headers=login('admin')))
        assert any(d['code']==code for d in options['departments'])
    finally:
        user.department_id=original;db.commit()


def test_chinese_payment_percentages_become_plans_without_inventing_dates(client,login,db,agent):
    tag=uuid.uuid4().hex[:8].upper()
    quote=f'合同编号CN-{tag}，客户中文比例学校{tag}，合同金额100000元，签约日期2026-10-20。'
    terms='首付款为百分之五十，剩余款在项目验收后支付百分之五十。'
    agent.extend([('read_source',{}),('prepare_records',{'target':'contract','records':[{
        'contract_no':f'CN-{tag}','name':'中文付款条款合同','customer_name':f'中文比例学校{tag}','amount':'100000',
        'sign_date':'2026-10-20','business_line':'XY','source_quote':quote,
        'payment_plans':[{'name':'首期','ratio':.5,'source_quote':terms},{'name':'尾款','ratio':.5,'source_quote':terms}]}],
        'reason':'原文比例计算，不编造付款日期'}),('finalize',{'summary':'1条合同及2期应收计划','reason':'按原文百分之五十计算'})])
    b=_upload(client,login,(quote+terms).encode(),'合同付款条款.txt')
    assert b['status']=='pending',b
    data(_decide(client,login,b,True))
    c=db.scalar(select(BizContract).where(BizContract.contract_no==f'CN-{tag}'))
    assert c
    plans=db.scalars(select(BizPaymentPlan).where(BizPaymentPlan.contract_id==c.id)).all()
    assert len(plans)==2 and all(p.amount==50000 and p.due_date is None for p in plans)
    assert not db.scalar(select(BizPayment.id).where(BizPayment.contract_id==c.id))


def test_supplier_purchase_payments_are_archived_without_becoming_income(client,login,db,monkeypatch):
    before=db.scalar(select(func.count(BizPayment.id)))
    def run(**kw):
        execute=kw['execute'];execute('read_source',{})
        with pytest.raises(BizError,match='供应商付款'):
            execute('prepare_table',{'target':'payment','header_row':1,'mapping':{'0':'customer_name','1':'amount'},'business_line':'AF','reason':'供应商付款'})
        execute('prepare_knowledge',{'title':'项目采购清单','category':'业务资料','subcategory':'采购清单','summary':'保留供应商采购明细和付款情况'})
        execute('finalize',{'summary':'采购清单归档，不登记客户回款','reason':'采购支出与客户回款区分处理'})
        return {'engine':'pi-coding-agent','calls':4}
    monkeypatch.setattr(pi_bridge,'run',run)
    b=_upload(client,login,_book({'Sheet':[['供应商','采购金额'],['设备供应商',12000]]}),'采购清单.xlsx')
    assert b['status']=='pending' and all(s['kind']=='knowledge' for s in b['agent_plan']['steps'])
    data(_decide(client,login,b,True))
    assert db.scalar(select(func.count(BizPayment.id)))==before


def test_payment_mapping_ignores_irrelevant_model_business_line(client,login,db,monkeypatch):
    from app.services import smart_intake
    contract=db.scalar(select(BizContract).where(BizContract.is_deleted.is_(False)))
    content=_book({'收款':[['合同编号','到账金额','到账日期'],[contract.contract_no,123.45,'2026-10-20']]})
    job=data(client.post('/api/v1/clean/preview',headers=login('admin'),files={'file':('回款明细.xlsx',content)},data={'target':'payment'}))
    monkeypatch.setattr(smart_intake,'plan_table',lambda *a,**k:{'header_row':1,'mapping':{0:'contract_no',1:'amount',2:'payment_date'},'business_line':'AF','reason':'到账记录'})
    remapped=data(client.post(f"/api/v1/clean/jobs/{job['id']}/remap",headers=login('admin'),json={'ai':True}))
    assert remapped['stats']['importable']==1 and 'business_line' not in remapped['rows'][0]
