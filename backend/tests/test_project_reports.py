"""历史项目财务导入：来源、去重、冲销、事务、统计与下载权限。"""
import hashlib
import io
import uuid
from copy import deepcopy
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest
from sqlalchemy import func, select

from app.core.deps import build_principal
from app.core.response import BizError
from app.models import (BizContract, BizPayment, BizProject, BizProjectCost, FinProjectReport,
    FinProjectReportContract, FinProjectReportEntry, FinReceipt, SysUser)
from app.services import profit_service, project_report_service as svc
from app.services.project_report_parser import parse_report, read_archive
from tests.conftest import data


def workbook(sheets):
    """Minimal OOXML fixture, without relying on an Excel authoring library."""
    out = io.BytesIO()
    with ZipFile(out, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' + ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets) + 1)) + '</Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + ''.join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(sheets, 1)) + '</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1)) + '</Relationships>')
        for i, cells in enumerate(sheets.values(), 1):
            rows = {}
            for ref, value in cells.items():
                rn = int(''.join(c for c in ref if c.isdigit()))
                if isinstance(value, str):
                    cell = f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
                elif isinstance(value, tuple):
                    cell = f'<c r="{ref}"><f>{escape(value[0])}</f><v>{value[1]}</v></c>'
                else:
                    cell = f'<c r="{ref}"><v>{value}</v></c>'
                rows.setdefault(rn, []).append(cell)
            z.writestr(f'xl/worksheets/sheet{i}.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + ''.join(f'<row r="{rn}">{"".join(row)}</row>' for rn, row in sorted(rows.items())) + '</sheetData></worksheet>')
    return out.getvalue()


def source(held=False, duplicate=False, delta=0):
    sheets = {
        '主表': {'B2': '测试项目月度分析', 'C4': '合同金额', 'E4': '已收金额', 'B6': '1月', 'G6': 100 + delta,
                 'B18': '未支付', 'G18': 50, 'H18': 0, 'B19': '合计', 'C19': 1000, 'D19': 200, 'E19': 200,
                 'G19': 150 + delta + (100 if duplicate else 0), 'H19': 0, 'I19': 22 if held else 15,
                 'P19': ('0.03*100.1', '3.003')},
        '成本费用': {'A1': '采购', 'A2': '日期', 'B2': '凭证号', 'C2': '摘要', 'D2': '支付金额', 'E2': '供应商名称',
                     'A3': '2026-01-31', 'B3': '记-1', 'C3': '采购测试设备', 'D3': 100 + delta, 'E3': '测试供应商'},
        '费用报销明细': {'A2': '日期', 'B2': '凭证号', 'C2': '摘要', 'D2': '科目名称', 'E2': '借方金额',
                         'A3': '2026-01-31', 'B3': '记-2', 'C3': '预提餐费', 'D3': '销售费用-项目餐费', 'E3': 20,
                         'A4': '2026-02-28', 'B4': '记-3', 'C4': '冲销预提餐费', 'D4': '销售费用-项目餐费', 'E4': -5},
        '待支付成本': {'A2': '序号', 'B2': '签约公司', 'C2': '供应商名称', 'D2': '项目', 'E2': '合同总金额',
                       'F2': '累计已支付金额', 'G2': '未提单未付金额', 'H2': '已提单应付', 'I2': '提单审批编号',
                       'A3': 1, 'B3': '测试公司', 'C3': '测试供应商', 'D3': '待付设备', 'E3': 100, 'F3': 50,
                       'G3': 0, 'H3': 50, 'I3': '202601010001'},
        '开票回款': {'A2': '日期', 'B2': '凭证号', 'C2': '摘要', 'D2': '回款金额', 'E2': '客户名称',
                     'A3': '2026-01-31', 'B3': '记-9', 'C3': '测试中行-收到测试回款', 'D3': 200, 'E3': '测试客户'},
    }
    if held:
        sheets['费用报销明细'].update({'A5': '2026-02-28', 'B5': '记-4', 'C5': '冲销但正数', 'D5': '销售费用-项目餐费', 'E5': 7})
    if duplicate:
        sheets['成本费用'].update({f'{col}4': sheets['成本费用'][f'{col}3'] for col in 'ABCDE'})
    return sheets


def archive(raw, name=None):
    name = name or f'2026年09月数据动态分析表-测试{uuid.uuid4().hex}.xlsx'
    out = io.BytesIO()
    with ZipFile(out, 'w') as z:
        z.writestr('项目报表/' + name, raw)
    return out.getvalue(), name


def user(db, name='admin'):
    return db.scalar(select(SysUser).where(SysUser.wecom_userid == name))


def setup_import(db, *, held=False, duplicate=False, multi=False):
    contracts = []
    for amount in ([600, 400] if multi else [1000]):
        uid = uuid.uuid4().hex[:12]
        project = BizProject(project_code=f'JC-XY-PR-{uid}', name='测试报表项目', business_line='XY', customer_name='测试客户', status=2)
        db.add(project)
        db.flush()
        c = BizContract(contract_no=f'PR-{uid}', name=f'来源测试合同{uid}', amount=Decimal(amount), sign_date=date(2026, 1, 1),
            business_line='XY', customer_name='测试客户', project_code=project.project_code, status=1)
        db.add(c)
        db.flush()
        contracts.append(c)
    pay = BizPayment(contract_id=contracts[0].id, payment_date=date(2026, 1, 30), amount=Decimal(200), source_type=2)
    db.add(pay)
    db.commit()
    sheets = source(held, duplicate)
    sheets['主表']['B2'] = contracts[0].name
    raw = workbook(sheets)
    blob, filename = archive(raw)
    mapping = {'schema_version': 1, 'archive_sha256': hashlib.sha256(blob).hexdigest(), 'reports': [{
        'filename': filename, 'sha256': hashlib.sha256(raw).hexdigest(), 'contract_ids': [c.id for c in contracts],
        'primary_contract_id': contracts[0].id,
        'contracts': {str(c.id): {'contract_no': c.contract_no, 'name': c.name, 'amount': f'{c.amount:.2f}', 'project_code': c.project_code} for c in contracts}}]}
    return blob, mapping, contracts, pay


def test_parser_preserves_signed_values_tax_and_source_formulas():
    parsed = parse_report(workbook(source()), '2026年09月数据动态分析表-测试.xlsx')
    assert [Decimal(e['amount']) for e in parsed['entries'] if e['kind'] == 'expense'] == [Decimal(20), Decimal(-5)]
    assert parsed['summary']['payable'] == '50'
    assert parsed['summary']['totals']['tax'] == '3.003'
    assert len(parsed['entries']) == 5
    assert not parsed['issues']
    tax = next(c for r in parsed['sheets'][0]['rows'] for c in r['cells'] if c['cell'] == 'P19')
    assert tax['formula'] == '=0.03*100.1'


@pytest.mark.parametrize('mutation', ['missing_amount', 'invalid_date', 'unknown_sheet'])
def test_parser_does_not_drop_unreadable_business_rows(mutation):
    sheets = source()
    if mutation == 'missing_amount':
        del sheets['成本费用']['D3']
    elif mutation == 'invalid_date':
        sheets['成本费用']['A3'] = '日期待补'
    else:
        sheets['额外明细'] = {'A1': '不能漏掉'}
    with pytest.raises(BizError):
        parse_report(workbook(sheets), '2026年09月数据动态分析表-测试.xlsx')


def test_archive_path_and_duplicate_filename_rejected():
    out = io.BytesIO()
    with ZipFile(out, 'w') as z:
        z.writestr('../outside.xlsx', b'data')
    with pytest.raises(BizError, match='路径'):
        read_archive(out.getvalue())
    out = io.BytesIO()
    with ZipFile(out, 'w') as z:
        z.writestr('a/name.xlsx', b'data')
        z.writestr('b/name.xlsx', b'data')
    with pytest.raises(BizError, match='同名'):
        read_archive(out.getvalue())


def test_import_preview_no_writes_and_repeat_is_idempotent(db):
    blob, mapping, contracts, pay = setup_import(db)
    p = build_principal(user(db))
    before = db.scalar(select(func.count()).select_from(FinProjectReport))
    plans = svc.prepare_archive(db, p, blob, mapping)
    assert len(svc.preview(plans)) == 1
    assert db.scalar(select(func.count()).select_from(FinProjectReport)) == before
    first = svc.apply_archive(db, p, blob, mapping)[0]
    db.commit()
    again = svc.apply_archive(db, p, blob, mapping)[0]
    db.commit()
    assert again == {'id': first['id'], 'already_imported': True}
    assert first['cost_rows'] == 3 and Decimal(first['posted_cost']) == Decimal(115)
    assert first['payable_rows'] == 1 and first['linked_payments'] == 1 and not first['errors']
    assert db.scalar(select(func.count()).select_from(BizPayment).where(BizPayment.contract_id == contracts[0].id)) == 1
    receipt = db.scalar(select(FinReceipt).where(FinReceipt.payment_id == pay.id))
    assert receipt.received_on == date(2026, 1, 30) and not receipt.owns_payment


def test_positive_reversal_held_negative_reversal_posted(db):
    blob, mapping, contracts, _ = setup_import(db, held=True)
    result = svc.apply_archive(db, build_principal(user(db)), blob, mapping)[0]
    db.commit()
    assert result['held_rows'] == 1 and Decimal(result['posted_cost']) == Decimal(115)
    report = db.get(FinProjectReport, result['id'])
    assert report.summary['held_amount'] == '7'
    held = db.scalar(select(FinProjectReportEntry).where(FinProjectReportEntry.report_id == report.id,
        FinProjectReportEntry.source_row == 5, FinProjectReportEntry.sheet == '费用报销明细'))
    assert held.cost_id is None


def test_duplicate_voucher_reuses_one_formal_cost(db):
    blob, mapping, _, _ = setup_import(db, duplicate=True)
    result = svc.apply_archive(db, build_principal(user(db)), blob, mapping)[0]
    db.commit()
    assert result['cost_rows'] == 3 and Decimal(result['posted_cost']) == Decimal(115)
    rows = db.scalars(select(FinProjectReportEntry).where(FinProjectReportEntry.report_id == result['id'], FinProjectReportEntry.kind == 'cost')).all()
    assert len(rows) == 2 and rows[0].cost_id == rows[1].cost_id


def test_combined_contract_report_is_linked_once_and_scope_is_complete(client, login, db):
    blob, mapping, contracts, _ = setup_import(db, multi=True)
    p = build_principal(user(db))
    result = svc.apply_archive(db, p, blob, mapping)[0]
    db.commit()
    report = db.get(FinProjectReport, result['id'])
    assert len(svc.contract_rows(db, report)) == 2
    codes = [c.project_code for c in contracts]
    grouped = profit_service.project_profit(db, date(2026, 1, 1), date(2026, 9, 30), codes)
    assert len(grouped['rows']) == 1
    assert grouped['rows'][0]['income'] == Decimal(1000) and grouped['rows'][0]['cost'] == Decimal(115)
    assert set(grouped['rows'][0]['group_codes']) == set(codes)
    assert profit_service.scoped_project_codes(db, codes[0], [codes[0]]) == [codes[0]]
    from app.services import report_service
    ctx = report_service.normalize_period('project_operation', '2026-W39', codes)
    operation = report_service.collect_project_operation(db, ctx)
    assert sum((r['cost'] or Decimal(0) for r in operation['rows']), Decimal(0)) == Decimal(115)
    secondary = next(r for r in operation['rows'] if r['project_code'] == codes[1])
    assert secondary['cost'] is None and '合并' in secondary['finance_note']
    drill = data(client.get(f'/api/v1/reports/profit/{codes[1]}', headers=login('admin'),
        params={'start': '2026-01-01', 'end': '2026-09-30'}))
    assert len(drill['detail']['contracts']) == 2
    assert Decimal(drill['profit']['income']) == Decimal(1000)
    assert sum(Decimal(e['amount']) for e in drill['detail']['costs']) == Decimal(100)
    for c in contracts:
        assert svc.list_reports(db, p, project_code=c.project_code)['items'][0]['id'] == report.id
    from app.core.deps import Principal
    restricted = Principal(user=user(db, 'xuhao'), role_codes=['XM-01'], menus={'profit'}, data_scope='self')
    db.scalar(select(BizProject).where(BizProject.project_code == contracts[0].project_code)).owner_id = restricted.id
    db.flush()
    assert not svc.can_view(db, restricted, report)
    db.rollback()


def test_changed_version_and_mapping_tamper_are_rejected(db):
    blob, mapping, _, _ = setup_import(db)
    p = build_principal(user(db))
    svc.apply_archive(db, p, blob, mapping)
    db.commit()
    changed_raw = workbook(source(delta=1))
    changed, _ = archive(changed_raw, mapping['reports'][0]['filename'])
    revised = deepcopy(mapping)
    revised['archive_sha256'] = hashlib.sha256(changed).hexdigest()
    revised['reports'][0]['sha256'] = hashlib.sha256(changed_raw).hexdigest()
    with pytest.raises(BizError, match='其他版本'):
        svc.apply_archive(db, p, changed, revised)
    db.rollback()
    revised['archive_sha256'] = '0' * 64
    with pytest.raises(BizError, match='校验和'):
        svc.prepare_archive(db, p, blob, revised)
    db.rollback()


def test_contract_amount_or_identity_mismatch_blocks_import(db):
    blob, mapping, contracts, _ = setup_import(db)
    p = build_principal(user(db))
    contracts[0].amount = Decimal(1001)
    db.flush()
    with pytest.raises(BizError, match='合同额'):
        svc.prepare_archive(db, p, blob, mapping)
    db.rollback()
    mapping['reports'][0]['contracts'][str(contracts[0].id)]['name'] = '另一份同金额合同'
    with pytest.raises(BizError, match='发生变化'):
        svc.prepare_archive(db, p, blob, mapping)
    db.rollback()


def test_profit_drilldown_and_source_api_agree_and_cost_immutable(client, login, db):
    blob, mapping, contracts, _ = setup_import(db)
    p = build_principal(user(db))
    result = svc.apply_archive(db, p, blob, mapping)[0]
    db.commit()
    code = contracts[0].project_code
    profit = profit_service.project_profit(db, date(2026, 1, 1), date(2026, 9, 30), [code])['rows'][0]
    assert profit['cost_expense'] == Decimal(15) and profit['cost_project'] == Decimal(100)
    assert profit['cost'] == Decimal(115)
    detail = data(client.get(f'/api/v1/reports/profit/{code}', params={'start': '2026-01-01', 'end': '2026-09-30'}, headers=login('admin')))['detail']
    assert sum(Decimal(e['amount']) for e in detail['expenses']) == Decimal(15)
    assert sum(Decimal(e['amount']) for e in detail['costs']) == Decimal(100)
    view = data(client.get(f'/api/v1/reports/project-sources/{result["id"]}', headers=login('admin')))
    assert view['verification']['posted_cost'] == '115.00'
    cost_id = next(e['cost_id'] for e in view['entries'] if e['cost_id'])
    resp = client.delete(f'/api/v1/finance-data/costs/{cost_id}', headers=login('admin'))
    assert resp.status_code == 409
    resp = client.put(f'/api/v1/finance-data/costs/{cost_id}', headers=login('admin'), json={'project_code': code, 'cost_date': '2026-01-31', 'cost_type': '采购', 'amount': '101'})
    assert resp.status_code == 409


def test_finance_permissions_and_protected_source_download(client, login, db):
    blob, mapping, _, _ = setup_import(db)
    p = build_principal(user(db))
    result = svc.apply_archive(db, p, blob, mapping)[0]
    db.commit()
    ident = result['id']
    view = data(client.get(f'/api/v1/reports/project-sources/{ident}', headers=login('admin')))
    url = view['file']['url']
    assert client.get(url).status_code == 401
    assert client.get(url, headers=login('caofang')).status_code == 404
    assert client.get(url, headers=login('admin')).content == read_archive(blob)[0][1]
    assert client.get(f'/api/v1/files/{view["file"]["id"]}', headers=login('caofang')).status_code == 404
    assert client.get('/api/v1/reports/project-sources', headers=login('caofang')).status_code == 403
    assert client.get(f'/api/v1/reports/project-sources/{ident}', headers=login('caofang')).status_code == 403
    with pytest.raises(BizError, match='管理员'):
        svc.prepare_archive(db, build_principal(user(db, 'chenjing')), blob, mapping)
    db.rollback()
