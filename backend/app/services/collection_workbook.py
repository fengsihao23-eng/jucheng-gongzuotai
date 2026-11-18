"""Read-only collection overview from the two explicitly selected worksheets."""
from datetime import date
from decimal import Decimal
import re

from app.core.response import bad_request, not_found
from app.core.timeutil import month_range, today, week_range
from app.services import business_workbook as source, collection_followups as followups, performance_details, workbook_metrics as metrics, collection_amounts

STATES = {'open': '待回款', 'cleared': '未收为零', 'unspecified': '未收未填写', 'review': '待核对'}


def state(row):
    value = row.get('outstanding')
    return 'unspecified' if value is None else 'open' if Decimal(value) > 0 else 'cleared' if Decimal(value) == 0 else 'review'


def _filter(rows, *, keyword, region, customer, owner, collection_state):
    if collection_state and collection_state not in STATES:
        raise bad_request('未收情况筛选无效')
    key, client, area, lead = map(source.layout.norm, (keyword, customer, region, owner))
    return [r for r in rows if (not key or key in r['_search'])
            and (not client or client in source.layout.norm(r.get('customer')))
            and (not area or area == source.layout.norm(r.get('region')))
            and (not lead or lead == source.layout.norm(r.get('owner')))
            and (not collection_state or state(r) == collection_state)]


def quick_stats(rows, filters):
    end = today()
    result = {}
    for key, start in (('month', month_range(ref=end)[0]), ('week', week_range(ref=end)[0])):
        current = metrics.between(rows, start, end)
        result[key] = {'amount': source.decimal_text(metrics.amount(current)), 'count': len(current),
                       'missing_amount': sum(r.get('amount') is None for r in current), 'start': str(start), 'end': str(end),
                       'drill': {'path': '/collections', 'query': {**filters, 'year': str(end.year) if start.year == end.year else 'all',
                                  'start': str(start), 'end': str(end), 'period_kind': key}}}
    return result


def _iso_date(value):
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError()
        return date.fromisoformat(value)
    except ValueError:
        raise bad_request('起止日期格式无效') from None


def page(db, wb, p, *, sheet_id='', keyword='', year='', page=1, size=30, raw=False, start='', end='',
         region='', customer='', owner='', sign_month='', source_type='', collection_state='', period_kind='', basis='', period='', unpaid_only=False):
    sheets = source.collection_sheets(wb)
    if not sheets:
        raise bad_request('回款情况的两张固定来源子表尚未完整同步，请检查同步状态')
    if sheet_id not in ('', 'all') and not any(s.id == sheet_id for s in sheets):
        raise not_found('回款情况来源子表')
    if raw:
        raise bad_request('回款情况展示业务清单，请从项目详情查看内容')
    if source_type not in ('', 'wecom', '4'):
        raise bad_request('回款情况仅使用指定企微工作簿')
    if basis not in ('', 'monthly'):
        raise bad_request('回款清单类型无效')
    if period_kind not in ('', 'month', 'week', 'year', 'custom'):
        raise bad_request('期间筛选无效')
    year = year or 'all'
    if year not in ('all', 'undated') and not re.fullmatch(r'20\d{2}', year):
        raise bad_request('年份筛选无效')
    if sign_month:
        if not re.fullmatch(r'20\d{2}-(?:0[1-9]|1[0-2])', sign_month):
            raise bad_request('签订月份格式无效')
        if year not in ('all', sign_month[:4]):
            raise bad_request('签订月份与所选年份不一致')
        year = sign_month[:4]
    if start:
        _iso_date(start)
    if end:
        _iso_date(end)
    if start and end and start > end:
        raise bad_request('起始日期不能晚于结束日期')

    all_rows = source.module_rows(wb, "collections")
    years = sorted({r['date_value'][:4] for r in all_rows if r['date_value']}, reverse=True)
    options = {'years': years, 'regions': sorted({r['region'] for r in all_rows if r.get('region')}),
               'owners': sorted({r['owner'] for r in all_rows if r.get('owner')}),
               'sources': [{'value': 'wecom', 'label': '企业微信表格'}],
               'collection_states': [{'value': k, 'label': v} for k, v in STATES.items()],
               'undated_count': sum(not r['date_value'] for r in all_rows)}
    business_filters = dict(keyword=keyword, region=region, customer=customer, owner=owner, collection_state=collection_state)
    selected = all_rows if sheet_id in ('', 'all') else [r for r in all_rows if r['sheet_id'] == sheet_id]
    base = _filter(selected, **business_filters)
    quick = quick_stats(base, {k: v for k, v in {**business_filters, 'sheet': sheet_id}.items() if v})
    briefs = [{**s.brief(), "summary": collection_amounts.summary(s, [r for r in all_rows if r["sheet_id"] == s.id]),
               "controls": [c for c in s.controls() if c["field"] != "amount"]} for s in sheets]
    common = {'active': True, 'source': source.source_info(wb, p), 'sheets': briefs,
              'years': years, 'filter_options': options, 'quick_stats': quick, 'current_year': today().year,
              'can_request_followup': followups.can_request(p)}
    if unpaid_only and basis != 'monthly':
        from app.services.collection_plans import cards
        common['overall_summary'] = collection_amounts.summary(sheets[0], base)
        common['collection_cards'] = cards(wb)
        # A signing year does not scope an outstanding balance.
        year, sign_month, start, end, period_kind = 'all', '', '', '', ''
        base = [r for r in base if r.get('outstanding') is None or Decimal(r['outstanding']) != 0]

    if basis == 'monthly':
        allowed = {(r['sheet_id'], r['row']) for r in base}
        result = metrics.collection_page(wb, period, page, size, allowed_rows=allowed, end=end)
        # Monthly receipt amounts are a separate measure from contract totals.
        result['items'] = performance_details.decorate(db, p, wb, result['items'], view='collections')
        _add_followups(db, wb, p, result['items'])
        return {**result, **common, 'collection_mode': 'receipts',
                'period': {'year': 'all', 'month': '', 'label': period + ' 实际回款清单'}, 'period_kind': 'receipts'}

    rows = base
    if year == 'undated':
        rows = [r for r in rows if not r['date_value']]
    elif year != 'all':
        rows = [r for r in rows if r['date_value'] and r['date_value'].startswith(year + '-')]
    if sign_month:
        rows = [r for r in rows if (r['date_value'] or '').startswith(sign_month + '-')]
    if start or end:
        rows = [r for r in rows if r['date_value'] and (not start or r['date_value'] >= start) and (not end or r['date_value'] <= end)]
    summary = collection_amounts.summary(sheets[0], rows)
    summary['groups'] = len({(r['sheet_id'], r['group_row']) for r in rows})
    count = len(rows)
    if not count:
        summary['amount']['value'] = '0.00'
    page = min(page, max(1, (count + size - 1) // size))
    items = performance_details.decorate(db, p, wb, rows[(page - 1) * size:page * size], view='collections')
    for item in items:
        item.pop('_search', None)
        item['collection_state'] = state(item)
        item['collection_state_name'] = STATES[item['collection_state']]
    _add_followups(db, wb, p, items)
    label = '两张表合并' if year == 'all' else '未注明签订日期' if year == 'undated' else year + ' 年全年'
    if sign_month:
        label = sign_month.replace('-', ' 年 ') + ' 月签订合同'
    if start or end:
        prefix = {'month': '本月合同清单', 'week': '本周合同清单', 'custom': '时间段业绩'}.get(period_kind, '合同清单')
        label = prefix + ' · ' + (start or '…') + ' 至 ' + (end or '…')
    return {**common, 'sheet': {'id': 'all', 'name': '回款情况', 'profile': 'collections', 'summary': summary,
                               'controls': [], 'warning': None}, 'summary': summary, 'items': items, 'total': count,
            'page': page, 'size': size, 'columns': [], 'collection_mode': 'contracts', 'period_kind': period_kind,
            'period': {'year': year, 'month': sign_month, 'label': label}}


def _add_followups(db, wb, p, items):
    latest = followups.summaries(db, wb, p, items)
    for row in items:
        row['followup'] = latest.get(row['id'])
