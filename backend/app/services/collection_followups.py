"""Targeted collection follow-up and item execution tracking, separate from financial writes."""
import logging
import re

from sqlalchemy import or_, select, tuple_
from sqlalchemy.orm import defer

from app.core.audit import log_op
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.response import bad_request, conflict, forbidden, not_found
from app.core.timeutil import utcnow
from app.integration import llm
from app.models import (
    ContractWorkbook,
    SysNotify,
    SysUser,
    WorkbookCollectionProgress as Progress,
    WorkbookCollectionRequest as Followup,
)
from app.services import business_workbook as source, dict_service
from app.services.collection_service import can_request

NOTICE_REQUEST = 'workbook_collection_request'
NOTICE_REPLY = 'workbook_collection_reply'
NOTICE_PROGRESS = 'workbook_collection_progress'
NOTICE_TYPES = {NOTICE_REQUEST, NOTICE_REPLY, NOTICE_PROGRESS}
STAGES = {
    'contact': '联系沟通',
    'approval': '付款审批',
    'awaiting': '等待付款',
    'blocked': '需协调',
    'reported_paid': '已反馈回款，待原表核对',
}
SELF_TRACK_MARKER = '项目回款执行跟踪'
REF_TYPE = 'workbook_collection'
IDENTITY_ORDER = ('external', 'entity', 'exact')
log = logging.getLogger(__name__)


def _can_view_source(p, wb):
    return source.can_access_view(p, wb.config, "collections")


def _state(row):
    return 'cancelled' if row.cancelled_at else 'replied' if row.replied_at else 'pending'


def _keys(record):
    evidence = dict(record.get('keys', ())) if record else {}
    return [f'{kind}:{evidence[kind]}' for kind in IDENTITY_ORDER if kind in evidence]


def _sheet_item(wb, sheet_id, row_number):
    sheet = next((s for s in source.collection_sheets(wb) if s.id == sheet_id), None)
    item = next((r for r in sheet.items if r['row'] == row_number), None) if sheet else None
    if item is None:
        raise not_found('回款合同')
    return item, _keys(sheet.rows[row_number].get('record'))


def _approved_user(user):
    if not user or user.status != 1 or user.is_deleted:
        return False
    if settings.source_mode('wecom') != 'live':
        return True
    marker = (user.preferences or {}).get('company_directory')
    return bool(settings.wecom_corp_id and isinstance(marker, dict) and marker.get('corp_id') == settings.wecom_corp_id)


def _participants(query, p, wb=None):
    if can_request(p) or (wb is not None and _can_view_source(p, wb)):
        return query
    return query.where(or_(Followup.author_id == p.id, Followup.recipient_id == p.id))


def _brief(row, p, latest_prog=None):
    state = _state(row)
    stage = latest_prog.stage if latest_prog else None
    problems = (latest_prog.problems or '').strip() if latest_prog else ''
    has_problem = bool(stage == 'blocked' or problems)
    expected = latest_prog.expected_on if (latest_prog and latest_prog.expected_on) else row.expected_on
    return {
        'id': row.id,
        'state': state,
        'can_reply': state == 'pending' and row.recipient_id == p.id,
        'path': f'/collections/followups/{row.id}',
        'stage': stage,
        'stage_name': STAGES.get(stage) if stage else None,
        'has_problem': has_problem,
        'problems': problems or None,
        'latest_content': latest_prog.content if latest_prog else row.reply,
        'next_step': latest_prog.next_step if latest_prog else None,
        'expected_on': str(expected) if expected else None,
        'updated_at': (latest_prog.created_at if latest_prog else (row.replied_at or row.created_at)),
    }


def summaries(db, wb, p, items):
    if not items:
        return {}
    sheets = {s.id: s for s in source.collection_sheets(wb)}
    keys = {
        r['id']: [(r['sheet_id'], k) for k in _keys(sheets[r['sheet_id']].rows[r['row']].get('record'))]
        for r in items if r.get('sheet_id') in sheets and r.get('row') in sheets[r['sheet_id']].rows
    }
    pairs = set(pair for values in keys.values() for pair in values)
    if not pairs:
        return {}
    all_rows_for_pairs = list(db.scalars(
        _participants(
            select(Followup).where(Followup.workbook_id == wb.id, tuple_(Followup.sheet_id, Followup.row_key).in_(pairs)),
            p,
            wb,
        ).order_by(Followup.id.desc())
    ))
    if not all_rows_for_pairs:
        return {}
    req_ids = [r.id for r in all_rows_for_pairs]
    latest_by_req = {}
    for prog in db.scalars(select(Progress).where(Progress.request_id.in_(req_ids)).order_by(Progress.id.desc())):
        if prog.request_id not in latest_by_req:
            latest_by_req[prog.request_id] = prog

    matches = {}
    latest_prog_by_pair = {}
    for row in all_rows_for_pairs:
        pair = (row.sheet_id, row.row_key)
        if pair not in matches or (_state(row) == 'pending' and _state(matches[pair]) != 'pending'):
            matches[pair] = row
        if pair not in latest_prog_by_pair and row.id in latest_by_req:
            latest_prog_by_pair[pair] = latest_by_req[row.id]

    result = {}
    for item_id, identities in keys.items():
        rows = [matches[pair] for pair in identities if pair in matches]
        if rows:
            chosen = max(rows, key=lambda r: (_state(r) == 'pending', r.id))
            progs = [latest_prog_by_pair[pair] for pair in identities if pair in latest_prog_by_pair]
            latest_prog = max(progs, key=lambda pr: pr.id) if progs else latest_by_req.get(chosen.id)
            result[item_id] = _brief(chosen, p, latest_prog=latest_prog)
    return result


def history(db, p, sheet_id, row_number, revision):
    wb = _current(db, p, revision)
    item, keys = _sheet_item(wb, sheet_id, row_number)
    entries = []
    pending = False
    if keys:
        records = list(db.scalars(_participants(_row_query(wb, sheet_id, keys), p, wb).order_by(Followup.id.desc()).limit(50)))
        users = {u.id: u.name for u in db.scalars(select(SysUser).where(
            SysUser.id.in_({uid for r in records for uid in (r.author_id, r.recipient_id)})))} if records else {}
        latest_progs = {}
        if records:
            for prog in db.scalars(select(Progress).where(Progress.request_id.in_([r.id for r in records])).order_by(Progress.id.desc())):
                if prog.request_id not in latest_progs:
                    latest_progs[prog.request_id] = prog
        entries = [{**_brief(r, p, latest_progs.get(r.id)), 'author_name': users.get(r.author_id),
                    'recipient_name': users.get(r.recipient_id), 'content': r.content, 'reply': r.reply,
                    'created_at': r.created_at, 'replied_at': r.replied_at, 'expected_on': r.expected_on} for r in records]
        pending = any(e['state'] == 'pending' for e in entries)
    choices = _recipient_choices(db, p, keys, pending)
    name = source.layout.norm(item.get('owner'))
    matched = [u.id for u in choices if name and source.layout.norm(u.name) == name]
    return {'entries': entries, 'name': item['name'], 'owner': item.get('owner'), 'pending': pending,
            'can_request': bool(can_request(p) and keys and not pending and choices),
            'suggested_recipient_id': matched[0] if len(matched) == 1 else None,
            'choices': [{'id': u.id, 'name': u.name, 'department': u.dept_code} for u in choices]}


def _recipient_choices(db, p, keys, pending):
    if not (can_request(p) and keys and not pending):
        return []
    return [u for u in db.scalars(select(SysUser).where(
        SysUser.status == 1, SysUser.is_deleted.is_(False), SysUser.id != p.id
    ).order_by(SysUser.name, SysUser.id)) if _approved_user(u)]


def _row_query(wb, sheet_id, keys):
    return select(Followup).where(Followup.workbook_id == wb.id, Followup.sheet_id == sheet_id, Followup.row_key.in_(keys))


def _current(db, p, revision, *, lock=False, allow_empty_revision=False):
    wb = source.active_source(db, p, 'collections')
    if wb is None:
        raise not_found('回款来源')
    if lock:
        # Serialize submissions across alternative keys for one source row.
        wb = db.scalar(select(ContractWorkbook).where(ContractWorkbook.id == wb.id)
                       .with_for_update().execution_options(populate_existing=True))
        if wb is None or not _can_view_source(p, wb):
            raise forbidden()
    if allow_empty_revision and not revision:
        return wb
    if not revision or revision != wb.revision:
        raise conflict('表格已更新，请刷新合同后再操作')
    return wb


def _notify(db, row, recipient, msg_type):
    from app.services import notify_service

    if not recipient or recipient.id == row.recipient_id and msg_type != NOTICE_REQUEST:
        return []
    title = '回款跟进待回复' if msg_type == NOTICE_REQUEST else '回款跟进已有回复'
    title = '回款跟进有新进展' if msg_type == NOTICE_PROGRESS else title
    text = '请打开工作台查看并回复跟进情况。' if msg_type == NOTICE_REQUEST else '负责人已更新进展，请打开工作台查看。'
    notices = notify_service.send(db, [recipient], msg_type, title, text,
        link=f'/collections/followups/{row.id}', ref_type=REF_TYPE, ref_id=str(row.id), defer=True)
    return [n.id for n in notices if n.channel == 'wecom_app']


def request(db, p, sheet_id, row_number, body):
    if not can_request(p):
        raise forbidden('无发起回款跟进权限')
    wb = _current(db, p, body.revision, lock=True)
    item, keys = _sheet_item(wb, sheet_id, row_number)
    if not keys:
        raise conflict('该合同无法唯一识别，暂不能发起跟进')
    if db.scalar(_row_query(wb, sheet_id, keys).with_only_columns(Followup.id).where(
            Followup.replied_at.is_(None), Followup.cancelled_at.is_(None)).limit(1)):
        raise conflict('这份合同已有待回复事项，请查看原跟进')
    recipient = db.get(SysUser, body.recipient_id)
    if body.recipient_id == p.id or not _approved_user(recipient):
        raise bad_request('请选择另一位在职公司账号作为回复人')
    row = Followup(workbook_id=wb.id, sheet_id=sheet_id, row_key=keys[0], contract_name=item['name'],
                   author_id=p.id, recipient_id=recipient.id, content=body.content)
    db.add(row)
    db.flush()
    notices = _notify(db, row, recipient, NOTICE_REQUEST)
    log_op(db, p.id, 'collections.request_followup', REF_TYPE, row.id, after={'recipient_id': recipient.id})
    return row.id, notices


def _get(db, p, ident, *, lock=False):
    row = db.get(Followup, ident, with_for_update=lock)
    if not row:
        raise not_found('回款跟进')
    if p.id not in (row.author_id, row.recipient_id):
        wb = db.get(ContractWorkbook, row.workbook_id, options=[defer(ContractWorkbook.sheets)]) if can_request(p) else None
        if wb is None or not _can_view_source(p, wb):
            raise not_found('回款跟进')
    return row


def thread(db, p, ident):
    row = _get(db, p, ident)
    names = dict(db.execute(select(SysUser.id, SysUser.name).where(SysUser.id.in_((row.author_id, row.recipient_id)))).all())
    wb = db.get(ContractWorkbook, row.workbook_id, options=[defer(ContractWorkbook.sheets)])
    active = dict_service.get(db, source.CONFIG, 'primary') or {}
    contract_link = None
    contract = None
    if wb and active.get('enabled') and active.get('source_key') == wb.source_key:
        for sheet in source.collection_sheets(wb):
            if sheet.id != row.sheet_id:
                continue
            matched = [r for r in sheet.items if row.row_key in _keys(sheet.rows[r['row']].get('record'))]
            if len(matched) == 1:
                item = matched[0]
                from app.services.collection_amounts import apply
                item = apply(wb, [item])[0]
                # An assignment authorizes this contract context for the recipient, not the workbook.
                contract = {k: item.get(k) for k in ('name', 'customer', 'owner', 'party', 'amount', 'amount_note', 'outstanding', 'date', 'note')}
                from app.services.performance_details import _attachments
                contract['attachments'] = _attachments(sheet, [item['row']])
                if _can_view_source(p, wb):
                    contract_link = {'path': f"/collections/items/{sheet.id}/{item['row']}/report", 'revision': wb.revision}
    reports = list(db.scalars(select(Progress).where(Progress.request_id == ident).order_by(Progress.id)))
    author_names = dict(db.execute(select(SysUser.id, SysUser.name).where(SysUser.id.in_([r.author_id for r in reports]))).all()) if reports else {}
    timeline = [{'id': report.id, 'stage': report.stage, 'stage_name': STAGES[report.stage], 'content': report.content,
                 'problems': report.problems, 'created_at': report.created_at, 'author_name': author_names.get(report.author_id),
                 'expected_on': report.expected_on, 'next_step': report.next_step} for report in reports]
    stage = reports[-1].stage if reports else None
    deliveries = {n.msg_type: {'status': n.send_status, 'sent_at': n.sent_at}
        for n in db.scalars(select(SysNotify).where(SysNotify.ref_type == REF_TYPE, SysNotify.ref_id == str(ident),
                                                  SysNotify.channel == 'wecom_app', SysNotify.msg_type.in_(NOTICE_TYPES)).order_by(SysNotify.id))}
    latest_prog = reports[-1] if reports else None
    return {**_brief(row, p, latest_prog), 'contract_name': row.contract_name, 'author_name': names.get(row.author_id),
            'recipient_name': names.get(row.recipient_id), 'content': row.content, 'created_at': row.created_at,
            'reply': row.reply, 'replied_at': row.replied_at, 'expected_on': row.expected_on,
            'can_cancel': row.author_id == p.id and _state(row) == 'pending', 'request_delivery': deliveries.get(NOTICE_REQUEST),
            'reply_delivery': deliveries.get(NOTICE_REPLY), 'contract_link': contract_link, 'contract': contract,
            'reports': timeline, 'stage': stage,
            'stages': [{'value': k, 'label': v} for k, v in STAGES.items()],
            'can_update': row.recipient_id == p.id and not row.cancelled_at and stage != 'reported_paid',
            'progress_delivery': deliveries.get(NOTICE_PROGRESS)}


def reply(db, p, ident, body):
    row = _get(db, p, ident, lock=True)
    if row.recipient_id != p.id:
        raise forbidden('只有指定负责人可回复')
    if _state(row) != 'pending':
        raise conflict('此事项已回复或撤回，请勿重复提交')
    row.reply, row.expected_on, row.replied_at = body.content, body.expected_on, utcnow()
    stage = getattr(body, 'stage', 'contact')
    if stage not in STAGES:
        raise bad_request('请选择有效的回款跟进阶段')
    problems = getattr(body, 'problems', None)
    db.add(Progress(request_id=row.id, author_id=p.id, stage=stage, content=body.content,
                    problems=problems, expected_on=body.expected_on, next_step=body.next_step))
    notices = _notify(db, row, db.get(SysUser, row.author_id), NOTICE_REPLY) if row.author_id != p.id else []
    log_op(db, p.id, 'collections.reply_followup', REF_TYPE, row.id)
    return notices


def progress(db, p, ident, body):
    row = _get(db, p, ident, lock=True)
    if row.recipient_id != p.id:
        raise forbidden('只有指定跟进人可以填写进度')
    if row.cancelled_at or not row.replied_at:
        raise conflict('请先回复跟进要求，或重新打开有效事项')
    last = db.scalar(select(Progress).where(Progress.request_id == ident).order_by(Progress.id.desc()).limit(1))
    if body.expected_report_id != (last.id if last else None):
        raise conflict('进度已有更新，请刷新后继续填写')
    if last and last.stage == 'reported_paid':
        raise conflict('本轮回款已反馈，后续请由发起人核对')
    if body.stage not in STAGES:
        raise bad_request('请选择有效的回款跟进阶段')
    problems = getattr(body, 'problems', None)
    db.add(Progress(request_id=ident, author_id=p.id, stage=body.stage, content=body.content,
                    problems=problems, expected_on=body.expected_on, next_step=body.next_step))
    log_op(db, p.id, 'collections.progress', REF_TYPE, row.id, after={'stage': body.stage})
    return _notify(db, row, db.get(SysUser, row.author_id), NOTICE_PROGRESS) if row.author_id != p.id else []


def cancel(db, p, ident):
    row = _get(db, p, ident, lock=True)
    if row.author_id != p.id:
        raise forbidden('只有发起人可以撤回要求')
    if _state(row) != 'pending':
        raise conflict('此事项已处理')
    row.cancelled_at = utcnow()
    log_op(db, p.id, 'collections.cancel_followup', REF_TYPE, row.id)


def execution_detail(db, p, sheet_id, row_number, revision=''):
    """Unified item execution view aligned with Operations TaskDialog."""
    from app.services.performance_details import detail as project_detail
    from app.services.collection_plans import for_project
    from app.services.collection_workbook import STATES, state as calc_state

    wb = _current(db, p, revision, allow_empty_revision=True)
    base = project_detail(db, p, sheet_id, row_number, wb.revision, view='collections')
    item, keys = _sheet_item(wb, sheet_id, row_number)
    plans = for_project(wb, sheet_id, row_number)
    c_state = calc_state(base['contract'])

    records = list(db.scalars(_row_query(wb, sheet_id, keys).order_by(Followup.id.asc()))) if keys else []
    req_ids = [r.id for r in records]
    progs = list(db.scalars(select(Progress).where(Progress.request_id.in_(req_ids)).order_by(Progress.id.asc()))) if req_ids else []

    user_ids = {uid for r in records for uid in (r.author_id, r.recipient_id)} | {pr.author_id for pr in progs}
    users = dict(db.execute(select(SysUser.id, SysUser.name).where(SysUser.id.in_(user_ids))).all()) if user_ids else {}

    updates = []
    for r in records:
        if r.author_id != r.recipient_id or r.content != SELF_TRACK_MARKER:
            updates.append({
                'id': f'req-{r.id}',
                'kind': 'cancelled' if r.cancelled_at else 'request',
                'title': '已撤回跟进要求' if r.cancelled_at else f"跟进要求（指定 {users.get(r.recipient_id) or '负责人'}）",
                'content': r.content,
                'problems': None,
                'next_step': None,
                'expected_on': str(r.expected_on) if r.expected_on and not r.replied_at else None,
                'author_name': users.get(r.author_id) or '管理员',
                'created_at': r.created_at,
            })
    for pr in progs:
        updates.append({
            'id': f'prog-{pr.id}',
            'progress_id': pr.id,
            'kind': 'feedback',
            'stage': pr.stage,
            'stage_name': STAGES.get(pr.stage, pr.stage),
            'title': STAGES.get(pr.stage, '跟进反馈'),
            'content': pr.content,
            'problems': pr.problems,
            'next_step': pr.next_step,
            'expected_on': str(pr.expected_on) if pr.expected_on else None,
            'author_name': users.get(pr.author_id) or '跟进人',
            'created_at': pr.created_at,
        })
    updates.sort(key=lambda u: u['created_at'], reverse=True)

    deliveries = []
    if req_ids:
        notifies = list(db.scalars(select(SysNotify).where(
            SysNotify.ref_type == REF_TYPE,
            SysNotify.ref_id.in_([str(i) for i in req_ids]),
            SysNotify.channel == 'wecom_app',
            SysNotify.msg_type.in_(NOTICE_TYPES),
        ).order_by(SysNotify.id.desc())))
        notify_users = dict(db.execute(select(SysUser.id, SysUser.name).where(
            SysUser.id.in_({n.user_id for n in notifies})
        )).all()) if notifies else {}
        status_map = {'sent': '企微已受理', 'pending': '待发送', 'failed': '发送失败', 'skipped': '未开启推送'}
        deliveries = [{
            'id': n.id,
            'recipient': notify_users.get(n.user_id) or '成员',
            'status': status_map.get(n.send_status, n.send_status or '已记录'),
            'title': n.title,
            'sent_at': n.sent_at,
            'error': n.error_msg,
        } for n in notifies]

    pending_req = next((r for r in reversed(records) if _state(r) == 'pending'), None)
    latest_prog = progs[-1] if progs else None
    latest_stage = latest_prog.stage if latest_prog else None
    latest_problems = (latest_prog.problems or '').strip() if latest_prog else ''
    has_problem = bool(latest_stage == 'blocked' or latest_problems)

    if has_problem:
        status_label = '存在问题 · 需协调'
        status_tone = 'down'
    elif pending_req:
        status_label = '待回复跟进'
        status_tone = 'warn'
    elif latest_stage == 'reported_paid':
        status_label = STAGES['reported_paid']
        status_tone = 'up'
    elif latest_stage:
        status_label = STAGES.get(latest_stage, '跟进中')
        status_tone = 'info'
    else:
        status_label = '待登记跟进'
        status_tone = 'neutral'

    choices = _recipient_choices(db, p, keys, bool(pending_req))
    owner_norm = source.layout.norm(item.get('owner'))
    matched = [u.id for u in choices if owner_norm and source.layout.norm(u.name) == owner_norm]

    return {
        'sheet_id': sheet_id,
        'row': row_number,
        'revision': wb.revision,
        'profile': item.get('profile'),
        'profile_label': '2026年销售合同' if item.get('profile') == 'sales' else '2026年前已签合同',
        'collection_state': c_state,
        'collection_state_name': STATES.get(c_state, '待回款'),
        'status_label': status_label,
        'status_tone': status_tone,
        'has_problem': has_problem,
        'contract': base['contract'],
        'notes': base.get('notes') or [],
        'attachments': base.get('attachments') or [],
        'plans': plans,
        'report_path': f'/collections/items/{sheet_id}/{row_number}/report',
        'pending_request': {
            'id': pending_req.id,
            'author_id': pending_req.author_id,
            'author_name': users.get(pending_req.author_id) or '管理员',
            'recipient_id': pending_req.recipient_id,
            'recipient_name': users.get(pending_req.recipient_id) or '负责人',
            'content': pending_req.content,
            'created_at': pending_req.created_at,
            'can_cancel': pending_req.author_id == p.id,
            'is_recipient': pending_req.recipient_id == p.id,
        } if pending_req else None,
        'latest': {
            'progress_id': latest_prog.id,
            'stage': latest_prog.stage,
            'stage_name': STAGES.get(latest_prog.stage, latest_prog.stage),
            'content': latest_prog.content,
            'problems': latest_prog.problems,
            'next_step': latest_prog.next_step,
            'expected_on': str(latest_prog.expected_on) if latest_prog.expected_on else None,
            'author_name': users.get(latest_prog.author_id) or '跟进人',
            'created_at': latest_prog.created_at,
        } if latest_prog else None,
        'latest_progress_id': latest_prog.id if latest_prog else None,
        'updates': updates,
        'notification_deliveries': deliveries,
        'stages': [{'value': k, 'label': v} for k, v in STAGES.items()],
        'can_feedback': bool(keys),
        'can_request': bool(can_request(p) and keys and not pending_req and choices),
        'suggested_recipient_id': matched[0] if len(matched) == 1 else None,
        'choices': [{'id': u.id, 'name': u.name, 'department': u.dept_code} for u in choices],
    }


def submit_execution(db, p, sheet_id, row_number, body):
    """Record execution progress and existing problems directly on a collection item."""
    wb = _current(db, p, body.revision, lock=True)
    item, keys = _sheet_item(wb, sheet_id, row_number)
    if not keys:
        raise conflict('该合同无法唯一识别，暂不能登记执行情况')
    stage = body.stage
    if stage not in STAGES:
        raise bad_request('请选择有效的回款跟进阶段')
    problems = (body.problems or '').strip() or None
    if getattr(body, 'has_problem', False) and stage != 'reported_paid':
        stage = 'blocked'
    if stage == 'blocked' and not problems:
        raise bad_request('遇到阻碍或需协调时，请填写具体存在的问题')

    records = list(db.scalars(_row_query(wb, sheet_id, keys).order_by(Followup.id.desc())))
    req_ids = [r.id for r in records]
    if getattr(body, 'expected_progress_id', None) is not None:
        last_prog = db.scalar(select(Progress).where(Progress.request_id.in_(req_ids)).order_by(Progress.id.desc()).limit(1)) if req_ids else None
        if body.expected_progress_id != (last_prog.id if last_prog else None):
            raise conflict('该条目跟进记录已有新变化，请刷新后继续提交')

    pending_req = next((r for r in records if _state(r) == 'pending'), None)
    notices = []
    now_ts = utcnow()

    if pending_req and pending_req.recipient_id == p.id:
        target = pending_req
        target.reply = body.content
        target.expected_on = body.expected_on
        target.replied_at = now_ts
        if target.author_id != p.id:
            notices = _notify(db, target, db.get(SysUser, target.author_id), NOTICE_REPLY)
    else:
        replied_req = next((r for r in records if _state(r) == 'replied'), None)
        if replied_req:
            target = replied_req
            if body.expected_on:
                target.expected_on = body.expected_on
            if target.author_id != p.id and target.content != SELF_TRACK_MARKER:
                notices = _notify(db, target, db.get(SysUser, target.author_id), NOTICE_PROGRESS)
        else:
            target = Followup(
                workbook_id=wb.id,
                sheet_id=sheet_id,
                row_key=keys[0],
                contract_name=item['name'],
                author_id=p.id,
                recipient_id=p.id,
                content=SELF_TRACK_MARKER,
                reply=body.content,
                expected_on=body.expected_on,
                replied_at=now_ts,
            )
            db.add(target)
            db.flush()

    prog = Progress(
        request_id=target.id,
        author_id=p.id,
        stage=stage,
        content=body.content,
        problems=problems,
        next_step=(body.next_step or '').strip() or None,
        expected_on=body.expected_on,
    )
    db.add(prog)
    db.flush()
    log_op(db, p.id, 'collections.execution_feedback', REF_TYPE, target.id,
           after={'stage': stage, 'has_problem': bool(problems), 'progress_id': prog.id})
    return notices


def draft_execution_text(db, p, sheet_id, row_number, text: str):
    wb = _current(db, p, '', allow_empty_revision=True)
    item, _ = _sheet_item(wb, sheet_id, row_number)
    cleaned = re.sub(r'\s+', ' ', (text or '').strip())
    if not cleaned:
        raise bad_request('请先输入跟进内容草稿')
    if llm.enabled():
        polished = llm.complete(
            system=(
                '你是企业回款管理助手。请将负责人输入的回款跟进草稿整理为简明、客观、规范的工作汇报表述（1-3句话）。'
                '严格保留原文中的时间、金额、节点和事实，绝对禁止编造任何原文未提及的数字、日期或承诺。直接输出整理后的正文，不要加标题或引号。'
            ),
            user=f"合同：{item.get('name')}\n客户：{item.get('customer') or '未知'}\n原草稿：{cleaned}",
            temperature=0.2,
            max_tokens=300,
            timeout_seconds=12,
        )
        if polished:
            return {'assisted': True, 'text': polished}
    normalized = cleaned.rstrip('。；;，,') + '。'
    if normalized != text.strip():
        return {'assisted': True, 'text': normalized}
    return {'assisted': False, 'text': cleaned}


def all_active_followups(db, wb):
    """Aggregate latest execution records across all collection items in the workbook for stage reporting."""
    sheets = {s.id: s for s in source.collection_sheets(wb)}
    all_reqs = list(db.scalars(select(Followup).where(
        Followup.workbook_id == wb.id, Followup.cancelled_at.is_(None)
    ).order_by(Followup.id.asc())))
    if not all_reqs:
        return {}
    req_ids = [r.id for r in all_reqs]
    progs = list(db.scalars(select(Progress).where(Progress.request_id.in_(req_ids)).order_by(Progress.id.asc())))
    user_ids = {uid for r in all_reqs for uid in (r.author_id, r.recipient_id)} | {pr.author_id for pr in progs}
    users = dict(db.execute(select(SysUser.id, SysUser.name).where(SysUser.id.in_(user_ids))).all()) if user_ids else {}

    progs_by_req = {}
    for pr in progs:
        progs_by_req.setdefault(pr.request_id, []).append(pr)

    by_pair = {}
    for r in all_reqs:
        pair = (r.sheet_id, r.row_key)
        entry = by_pair.setdefault(pair, {'requests': [], 'progs': []})
        entry['requests'].append(r)
        entry['progs'].extend(progs_by_req.get(r.id, []))

    result = {}
    for s_id, sheet in sheets.items():
        for item in sheet.items:
            keys = _keys(sheet.rows[item['row']].get('record'))
            reqs, item_progs = [], []
            seen_req = set()
            for k in keys:
                data = by_pair.get((s_id, k))
                if not data:
                    continue
                for r in data['requests']:
                    if r.id not in seen_req:
                        seen_req.add(r.id)
                        reqs.append(r)
                        item_progs.extend(progs_by_req.get(r.id, []))
            if not reqs and not item_progs:
                continue
            item_progs.sort(key=lambda x: x.id)
            latest_p = item_progs[-1] if item_progs else None
            pending_r = next((r for r in reversed(reqs) if _state(r) == 'pending'), None)
            stage = latest_p.stage if latest_p else None
            problems = (latest_p.problems or '').strip() if latest_p else ''
            result[item['id']] = {
                'stage': stage,
                'stage_name': STAGES.get(stage) if stage else ('待回复跟进' if pending_r else '已发起'),
                'has_problem': bool(stage == 'blocked' or problems),
                'content': latest_p.content if latest_p else (reqs[-1].reply or reqs[-1].content),
                'problems': problems or None,
                'next_step': latest_p.next_step if latest_p else None,
                'expected_on': str(latest_p.expected_on) if (latest_p and latest_p.expected_on) else (str(reqs[-1].expected_on) if reqs[-1].expected_on else None),
                'author_name': users.get(latest_p.author_id) if latest_p else users.get(reqs[-1].recipient_id),
                'updated_at': latest_p.created_at if latest_p else (reqs[-1].replied_at or reqs[-1].created_at),
                'pending': bool(pending_r),
            }
    return result


def eligible_notice(db, notice):
    try:
        row = db.get(Followup, int(notice.ref_id)) if notice.ref_type == REF_TYPE else None
    except (ValueError, TypeError):
        return False
    return bool(row and ((notice.msg_type == NOTICE_REQUEST and row.recipient_id == notice.user_id and _state(row) == 'pending')
                         or (notice.msg_type in {NOTICE_REPLY, NOTICE_PROGRESS} and row.author_id == notice.user_id and _state(row) == 'replied')))


def deliver(ids):
    """Send after the request and its notice have committed; retry worker backs up failed delivery."""
    from app.services import notify_service

    try:
        with SessionLocal() as db:
            notify_service.deliver_pending(db, ids)
            db.commit()
    except Exception:
        log.exception('Could not deliver queued collection follow-up notices')
