"""Workbook collections and follow-up requests share scope, not financial writes."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select

from app.models import BizContract, BizPayment, BizPaymentPlan, SysNotify, SysUser, WorkbookCollectionRequest, WorkbookCollectionProgress
from app.services import collection_followups as tasks, notify_service, workbook_board
from tests.conftest import data
from tests.test_business_workbook import sales_row, source_only  # noqa: F401
from tests.test_contract_workbook import older, sales

BASE = '/api/v1/business-workbook/collections'
TASK = '/api/v1/business-workbook/collection-followups'


@pytest.fixture
def collection_source(db, source_only, monkeypatch):
    wb, _ = source_only
    monkeypatch.setenv('FAKE_TODAY', '2026-11-17')
    person = db.scalar(select(SysUser).where(SysUser.wecom_userid == 'xiekai'))
    one = sales_row(3, '本周项目', '客户甲', 100, received=30)
    one['cells'][3], one['cells'][7], one['cells'][9], one['cells'][11] = person.name, '2026-11-16', 55, '11月16日已回款30元'
    two = sales_row(4, '月内前一周项目', '客户乙', 200)
    two['cells'][3], two['cells'][7], two['cells'][9], two['cells'][11] = '另一负责人', '2026-11-12', 200, '预计本月回款500元'
    three = sales_row(5, '未来项目', '客户丙', 700, received=0)
    three['cells'][7], three['cells'][9], three['cells'][11] = '2026-11-26', 700, '11月26日已回款10元'
    current = sales(one, two, three)
    current['rows'][0]['cells'][11] = '11月回款进度'
    current['rows'].append({'row': 8, 'cells': [None] * 11 + [40]})
    past = older((3, '2025年待收项目', 300, '2025-12-31', 70, 230),
                 (4, '2024年项目', 500, '2024-03-01', 200, 0),
                 (5, '日期未填写的合同', 800, '待定', None, None))
    past['rows'][0]['cells'][11] = '11月回款进度'
    past['rows'][2]['cells'][11] = '2026年11月12日已回款70元'
    past['rows'].append({'row': 8, 'cells': [None] * 11 + [70]})
    wb.sheets = [current, past, older((3, '其他子表不纳入', 99999, '2026-11-16', 20, 99979), profile='longterm')]
    db.commit()
    yield wb, person.id
    db.execute(delete(WorkbookCollectionProgress).where(WorkbookCollectionProgress.request_id.in_(
        select(WorkbookCollectionRequest.id).where(WorkbookCollectionRequest.workbook_id == wb.id))))
    db.execute(delete(SysNotify).where(SysNotify.ref_type == tasks.REF_TYPE))
    db.execute(delete(WorkbookCollectionRequest).where(WorkbookCollectionRequest.workbook_id == wb.id))
    db.commit()


def test_two_sheets_default_and_year_month_range(client, login, collection_source):
    headers = login('admin')
    get = lambda **params: data(client.get(BASE, params=params, headers=headers))
    view = get()
    assert {s['id'] for s in view['sheets']} == {'sales-sheet', 'older'}
    assert view['period']['year'] == 'all' and view['total'] == 6
    assert [Decimal(view['summary'][f]['value']) for f in ('amount', 'received', 'outstanding')] == [2600, 300, 1185]
    assert get(year='2025')['total'] == 1 and get(sign_month='2024-03')['summary']['amount']['value'] == '500.00'
    assert get(year='undated')['filter_options']['undated_count'] == 1
    assert get(start='2025-12-31', end='2026-11-16', period_kind='custom')['total'] == 3
    assert get(collection_state='unspecified')['total'] == 1
    assert get(collection_state='cleared')['total'] == 1
    assert client.get(BASE, params={'sheet': 'longterm'}, headers=headers).status_code == 404
    assert client.get(BASE + '/items/longterm/3', headers=headers).status_code == 404
    assert client.get(BASE, params={'start': '20261117'}, headers=headers).status_code == 400


def test_month_week_cards_match_their_contract_lists(client, login, collection_source):
    headers = login('admin')
    for filters, expected in (({}, [300, 100]), ({'keyword': '本周项目'}, [100, 100]), ({'sheet': 'older'}, [0, 0])):
        report = data(client.get(BASE, params=filters, headers=headers))
        for card, amount in zip(('month', 'week'), expected):
            metric = report['quick_stats'][card]
            listed = data(client.get(BASE, params=metric['drill']['query'], headers=headers))
            assert Decimal(metric['amount']) == Decimal(listed['summary']['amount']['value']) == amount
            assert metric['count'] == listed['total']
            assert listed['period_kind'] == card and listed['collection_mode'] == 'contracts'


def test_dashboard_actual_receipts_remain_separate_and_cutoff_matches(db, client, login, collection_source):
    wb, _ = collection_source
    headers = login('admin')
    pay = workbook_board.payment(db, wb, date(2026, 11, 17))
    rows = data(client.get(BASE, params=pay['drill']['query'], headers=headers))
    assert rows['collection_mode'] == 'receipts' and rows['total'] == 2
    assert Decimal(rows['summary']['received']['value']) == pay['amount'] == 100
    assert not rows['summary']['amount']['available']
    assert {r['received'] for r in rows['items']} == {'70.00', '30.00'}
    assert all(r['received_rate'] is None for r in rows['items'])
    narrowed = data(client.get(BASE, params={**pay['drill']['query'], 'customer': '客户甲'}, headers=headers))
    assert narrowed['total'] == 1 and narrowed['summary']['received']['value'] == '30.00'


def test_missing_second_sheet_does_not_publish_partial_total(db, client, login, collection_source):
    wb, _ = collection_source
    wb.sheets = [t for t in wb.sheets if t['id'] != 'older']
    db.commit()
    assert client.get(BASE, headers=login('admin')).status_code == 400
    assert workbook_board.payment(db, wb, date(2026, 11, 17))['amount'] is None


def test_targeted_followup_reply_and_notifications_leave_money_alone(db, client, login, collection_source, monkeypatch):
    wb, recipient_id = collection_source
    manager, recipient, other = login('admin'), login('xiekai'), login('xuhao')
    endpoint = BASE + '/items/sales-sheet/3/followups'
    monkeypatch.setattr(tasks, 'deliver', lambda ids: None)  # check the committed queue without contacting anyone
    original = data(client.get(BASE, headers=manager))['summary']
    ledger_counts = [db.scalar(select(func.count()).select_from(m)) for m in (BizContract, BizPayment, BizPaymentPlan)]
    who = data(client.get(endpoint, params={'revision': '1'}, headers=manager))
    assert who['suggested_recipient_id'] == recipient_id
    assert client.get(BASE, headers=recipient).status_code == 403
    command = {'revision': '1', 'recipient_id': recipient_id, 'content': '请回复预计回款日'}
    assert client.post(endpoint, json={**command, 'revision': '0'}, headers=manager).status_code == 409
    item = data(client.post(endpoint, json=command, headers=manager))
    ident = item['id']
    assert item['state'] == 'pending'
    assert client.post(endpoint, json=command, headers=manager).status_code == 409
    assert client.get(f'{TASK}/{ident}', headers=other).status_code == 404
    personal = data(client.get(f'{TASK}/{ident}', headers=recipient))
    assert personal['can_reply'] and personal['contract_link'] is None
    assert client.post(f'{TASK}/{ident}/reply', json={'content': '冒名回复'}, headers=manager).status_code == 403

    notifications = data(client.get('/api/v1/notifications', headers=recipient))['items']
    notice = next(n for n in notifications if n['link'] == item['path'])
    client.post('/api/v1/notifications/read', json={'ids': [notice['id']]}, headers=recipient)
    assert data(client.get(f'{TASK}/{ident}', headers=recipient))['state'] == 'pending'
    reply = data(client.post(f'{TASK}/{ident}/reply', json={'content': '客户已确认，等待付款', 'expected_on': '2026-11-25'}, headers=recipient))
    assert reply['state'] == 'replied' and reply['expected_on'] == '2026-11-25'
    assert client.post(f'{TASK}/{ident}/reply', json={'content': '重复回复'}, headers=recipient).status_code == 409
    assert data(client.get(f'{TASK}/{ident}', headers=manager))['reply'] == '客户已确认，等待付款'
    result = data(client.get(BASE, headers=manager))
    assert result['items'][0]['followup']['state'] == 'replied' and result['summary'] == original
    assert [db.scalar(select(func.count()).select_from(m)) for m in (BizContract, BizPayment, BizPaymentPlan)] == ledger_counts

    notices = list(db.scalars(select(SysNotify).where(SysNotify.ref_type == tasks.REF_TYPE, SysNotify.ref_id == str(ident))))
    assert {(n.msg_type, n.channel) for n in notices} == {(kind, channel) for kind in (tasks.NOTICE_REQUEST, tasks.NOTICE_REPLY) for channel in ('inapp', 'wecom_app')}
    queued = [n for n in notices if n.channel == 'wecom_app']
    sent = []

    class FakeProvider:
        def send_app_message(self, users, message):
            from types import SimpleNamespace
            sent.append((users, message))
            return SimpleNamespace(ok=True, invalid_users=[])

    monkeypatch.setattr(notify_service, 'get_wecom', FakeProvider)
    notify_service.deliver_pending(db, [n.id for n in queued])
    assert len(sent) == 1 and '跟进' in sent[0][1].title
    assert item['path'] in sent[0][1].url and '客户已确认' not in sent[0][1].content
    db.commit()


def test_followup_follows_contract_identity_not_row_number(db, client, login, collection_source, monkeypatch):
    from copy import deepcopy

    wb, recipient_id = collection_source
    headers = login('admin')
    monkeypatch.setattr(tasks, 'deliver', lambda ids: None)
    item = data(client.post(BASE + '/items/sales-sheet/3/followups',
        json={'revision': '1', 'recipient_id': recipient_id, 'content': '请反馈最新进展'}, headers=headers))
    updated = deepcopy(wb.sheets)
    updated[0]['rows'][2]['row'], updated[0]['rows'][3]['row'] = 4, 3
    wb.sheets, wb.revision = updated, '2'
    db.commit()
    items = data(client.get(BASE, headers=headers))['items']
    assert items[0]['row'] == 4 and items[0]['followup']['id'] == item['id']
    assert items[1]['row'] == 3 and items[1]['followup'] is None
    assert data(client.get(f"{TASK}/{item['id']}", headers=headers))['contract_link']['path'].endswith('/4/report')
    assert data(client.post(f"{TASK}/{item['id']}/cancel", headers=headers))['state'] == 'cancelled'
    assert client.post(f"{TASK}/{item['id']}/reply", json={'content': '已撤回，不能回复'}, headers=login('xiekai')).status_code == 409


def test_monthly_plans_use_source_totals_and_merged_anchor_once(db, client, login, collection_source, monkeypatch):
    from copy import deepcopy
    from app.services.collection_plans import bucket

    wb, _ = collection_source
    sheets = deepcopy(wb.sheets)
    for index, source in enumerate(sheets[:2]):
        source['rows'][0]['cells'][10] = '11月预计可回款金额'
        source['rows'][0]['cells'][12] = '12月达回款条件，应回款金额'
        rows = {r['row']: r for r in source['rows']}
        for n in (3, 4, 5):
            rows[n]['cells'][10] = rows[n]['cells'][12] = None
        if index == 0:
            rows[3]['cells'][10], rows[5]['cells'][10] = 80, 40
            source['merges'].append('K3:K4')
            for n, value in ((3, 50), (4, 70), (5, 80)):
                rows[n]['cells'][12] = value
            total, following = 120, 200
        else:
            rows[3]['cells'][10], rows[3]['cells'][12] = 200, 300
            total, following = 200, 300
        rows[8]['cells'].extend([None] * (13 - len(rows[8]['cells'])))
        rows[8]['cells'][10], rows[8]['cells'][12] = total, following
    wb.sheets = sheets
    db.commit()
    headers = login('admin')
    report = data(client.get(BASE, params={'unpaid_only': True}, headers=headers))
    assert [c['amount'] for c in report['collection_cards']] == ['320.00', '500.00']
    assert len(report['items']) == 5  # The contract with an explicitly zero balance is hidden.
    assert Decimal(report['overall_summary']['amount']['value']) == 2600
    assert data(client.get(BASE, params={'unpaid_only': True, 'year': '2024'}, headers=headers))['total'] == 5
    period = data(client.get(BASE + '/periods/2026-11', headers=headers))
    assert period['available'] and len(period['controls']) == 2 and period['amount'] == '320.00'
    shared = next(r for r in period['items'] if r['sheet_id'] == 'sales-sheet' and r['row'] == 4)
    assert shared['plan_shared'] and shared['plan_amount'] is None and shared['plan_group_amount'] == '80.00'
    assert shared['amount'] == '200.00'  # The contract amount is never replaced by a monthly plan.
    details = data(client.get(BASE + '/items/sales-sheet/4/report', params={'revision': '1'}, headers=headers))
    assert details['plans'][0]['plan_shared'] and details['contract']['amount'] == '200.00'
    assert client.get(BASE + '/periods/2026-11', headers=login('xiekai')).status_code == 403
    assert client.get(BASE + '/periods/2026-13', headers=headers).status_code == 400
    assert not bucket(wb, '2027-11')['available']  # An old column cannot be relabeled for a new year.
    changed = deepcopy(wb.sheets)
    changed[0]['rows'][-1]['cells'][10] = 121
    wb.sheets = changed
    db.commit()
    assert bucket(wb, '2026-11')['amount'] is None


def test_progress_history_contract_context_and_notifications(db, client, login, collection_source, monkeypatch):
    import json

    from app.services import collection_plans

    wb, recipient_id = collection_source
    manager, recipient, third = login('admin'), login('xiekai'), login('xuhao')
    monkeypatch.setattr(tasks, 'deliver', lambda ids: None)
    initial = data(client.get(BASE, headers=manager))['summary']
    request = data(client.post(BASE + '/items/sales-sheet/3/followups',
        json={'revision': '1', 'recipient_id': recipient_id, 'content': '请联系客户并记录付款流程'}, headers=manager))
    endpoint = f"{TASK}/{request['id']}"
    context = data(client.get(endpoint, headers=recipient))
    assert context['contract']['name'] == '本周项目' and context['contract_link'] is None
    first = data(client.post(endpoint + '/reply', json={'content': '已联系客户', 'stage': 'contact'}, headers=recipient))
    assert [r['stage'] for r in first['reports']] == ['contact']
    revision = first['reports'][-1]['id']
    body = {'stage': 'approval', 'content': '客户已经递交付款审批', 'expected_on': '2026-12-05', 'expected_report_id': revision}
    assert client.post(endpoint + '/progress', json=body, headers=manager).status_code == 403
    assert client.post(endpoint + '/progress', json=body, headers=third).status_code == 404
    contents = b'\x00\xffpayment review notes'
    updated = data(client.post(endpoint + '/progress', data={'payload': json.dumps(body)},
        files={'files': ('付款审批留档.原件', contents, 'application/octet-stream')}, headers=recipient))
    assert [r['stage'] for r in updated['reports']] == ['contact', 'approval']
    assert updated['reports'][1]['expected_on'] == '2026-12-05'
    [attachment] = updated['reports'][1]['attachments']
    assert client.get(attachment['url'], headers=manager).content == contents
    assert client.get(attachment['url'], headers=third).status_code == 404
    assert client.post(endpoint + '/progress', json=body, headers=recipient).status_code == 409
    final = data(client.post(endpoint + '/progress', json={**body, 'stage': 'reported_paid', 'content': '客户反馈已付款，等待核对',
        'expected_report_id': updated['reports'][-1]['id']}, headers=recipient))
    assert not final['can_update'] and len(final['reports']) == 3
    assert data(client.get(BASE, headers=manager))['summary'] == initial
    notices = list(db.scalars(select(SysNotify).where(SysNotify.msg_type == tasks.NOTICE_PROGRESS)))
    assert notices and all(n.user_id != recipient_id for n in notices)
