"""Targeted collection follow-up, without granting access to the workbook."""
import logging

from sqlalchemy import or_, select, tuple_
from sqlalchemy.orm import defer

from app.core.audit import log_op
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.response import bad_request, conflict, forbidden, not_found
from app.core.timeutil import utcnow
from app.models import ContractWorkbook, SysNotify, SysUser, WorkbookCollectionRequest as Followup, WorkbookCollectionProgress as Progress
from app.services import business_workbook as source, contract_workbook_sync, dict_service
from app.services.collection_service import can_request

NOTICE_REQUEST = 'workbook_collection_request'
NOTICE_REPLY = 'workbook_collection_reply'
NOTICE_PROGRESS = 'workbook_collection_progress'
NOTICE_TYPES = {NOTICE_REQUEST, NOTICE_REPLY, NOTICE_PROGRESS}
STAGES = {'contact': '联系沟通', 'approval': '付款审批', 'awaiting': '等待付款', 'blocked': '需协调',
          'reported_paid': '已反馈回款，待原表核对'}
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


def _participants(query, p):
    return query if can_request(p) else query.where(or_(Followup.author_id == p.id, Followup.recipient_id == p.id))


def _brief(row, p):
    state = _state(row)
    return {'id': row.id, 'state': state, 'can_reply': state == 'pending' and row.recipient_id == p.id,
            'path': f'/collections/followups/{row.id}'}


def summaries(db, wb, p, items):
    if not items:
        return {}
    sheets = {s.id: s for s in source.collection_sheets(wb)}
    keys = {r['id']: [(r['sheet_id'], k) for k in _keys(sheets[r['sheet_id']].rows[r['row']].get('record'))] for r in items}
    pairs = set(pair for values in keys.values() for pair in values)
    if not pairs:
        return {}
    matches = {}
    query = _participants(select(Followup).where(Followup.workbook_id == wb.id,
        tuple_(Followup.sheet_id, Followup.row_key).in_(pairs)), p)
    for row in db.scalars(query.order_by(Followup.id.desc())):
        pair = (row.sheet_id, row.row_key)
        if pair not in matches or (_state(row) == 'pending' and _state(matches[pair]) != 'pending'):
            matches[pair] = row
    result = {}
    for item_id, identities in keys.items():
        rows = [matches[pair] for pair in identities if pair in matches]
        if rows:
            result[item_id] = _brief(max(rows, key=lambda r: (_state(r) == 'pending', r.id)), p)
    return result


def history(db, p, sheet_id, row_number, revision):
    wb = _current(db, p, revision)
    item, keys = _sheet_item(wb, sheet_id, row_number)
    entries = []
    pending = False
    if keys:
        records = list(db.scalars(_participants(_row_query(wb, sheet_id, keys), p).order_by(Followup.id.desc()).limit(50)))
        users = {u.id: u.name for u in db.scalars(select(SysUser).where(
            SysUser.id.in_({uid for r in records for uid in (r.author_id, r.recipient_id)})))} if records else {}
        entries = [{**_brief(r, p), 'author_name': users.get(r.author_id), 'recipient_name': users.get(r.recipient_id),
                    'content': r.content, 'reply': r.reply, 'created_at': r.created_at, 'replied_at': r.replied_at,
                    'expected_on': r.expected_on} for r in records]
        pending = any(e['state'] == 'pending' for e in entries)
    choices = []
    if can_request(p) and keys and not pending:
        choices = [u for u in db.scalars(select(SysUser).where(SysUser.status == 1, SysUser.is_deleted.is_(False), SysUser.id != p.id)
                                      .order_by(SysUser.name, SysUser.id)) if _approved_user(u)]
    name = source.layout.norm(item.get('owner'))
    matched = [u.id for u in choices if name and source.layout.norm(u.name) == name]
    return {'entries': entries, 'name': item['name'], 'owner': item.get('owner'), 'pending': pending,
            'can_request': bool(can_request(p) and keys and not pending and choices),
            'suggested_recipient_id': matched[0] if len(matched) == 1 else None,
            'choices': [{'id': u.id, 'name': u.name, 'department': u.dept_code} for u in choices]}


def _row_query(wb, sheet_id, keys):
    return select(Followup).where(Followup.workbook_id == wb.id, Followup.sheet_id == sheet_id, Followup.row_key.in_(keys))


def _current(db, p, revision, *, lock=False):
    wb = source.active_source(db, p, 'collections')
    if wb is None:
        raise not_found('回款来源')
    if lock:
        # Serialize submissions across alternative keys for one source row.
        wb = db.scalar(select(ContractWorkbook).where(ContractWorkbook.id == wb.id)
                       .with_for_update().execution_options(populate_existing=True))
        if wb is None or not _can_view_source(p, wb):
            raise forbidden()
    if not revision or revision != wb.revision:
        raise conflict('表格已更新，请刷新合同后再操作')
    return wb


def _notify(db, row, recipient, msg_type):
    from app.services import notify_service

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
                 'created_at': report.created_at, 'author_name': author_names.get(report.author_id),
                 'expected_on': report.expected_on, 'next_step': report.next_step} for report in reports]
    stage = reports[-1].stage if reports else None
    deliveries = {n.msg_type: {'status': n.send_status, 'sent_at': n.sent_at}
        for n in db.scalars(select(SysNotify).where(SysNotify.ref_type == REF_TYPE, SysNotify.ref_id == str(ident),
                                                  SysNotify.channel == 'wecom_app', SysNotify.msg_type.in_(NOTICE_TYPES)).order_by(SysNotify.id))}
    return {**_brief(row, p), 'contract_name': row.contract_name, 'author_name': names.get(row.author_id),
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
    db.add(Progress(request_id=row.id, author_id=p.id, stage=stage, content=body.content,
                    expected_on=body.expected_on, next_step=body.next_step))
    notices = _notify(db, row, db.get(SysUser, row.author_id), NOTICE_REPLY)
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
    db.add(Progress(request_id=ident, author_id=p.id, stage=body.stage, content=body.content,
                    expected_on=body.expected_on, next_step=body.next_step))
    log_op(db, p.id, 'collections.progress', REF_TYPE, row.id, after={'stage': body.stage})
    return _notify(db, row, db.get(SysUser, row.author_id), NOTICE_PROGRESS)


def cancel(db, p, ident):
    row = _get(db, p, ident, lock=True)
    if row.author_id != p.id:
        raise forbidden('只有发起人可以撤回要求')
    if _state(row) != 'pending':
        raise conflict('此事项已处理')
    row.cancelled_at = utcnow()
    log_op(db, p.id, 'collections.cancel_followup', REF_TYPE, row.id)


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
