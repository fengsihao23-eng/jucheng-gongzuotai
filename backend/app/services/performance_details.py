"""Business contract presentation; all source-owned values stay untouched.

Existing contract IDs attach read-only files/plans/project context only through
verified workbook identities and the viewer's original business permissions.
"""
from decimal import Decimal
from urllib.parse import urlsplit

from sqlalchemy import select

from app.core.deps import owner_scope
from app.core.response import BizError, not_found
from app.models import BizContract, ContractWorkbookLink, PROJECT_STATUS
from app.services import business_workbook as source
from app.services.contract_workbook_match import Candidates


def _context(db, p, wb, items, view):
    sheets = source.module_sheets(wb, view)
    records = {(s.id, r['row']): r['record'] for s in sheets for r in s.source['rows'] if r.get('record')}
    if view == 'collections' or not items:
        return records, {}
    links = list(db.scalars(select(ContractWorkbookLink).where(ContractWorkbookLink.workbook_id == wb.id,
        ContractWorkbookLink.kind == 'contract', ContractWorkbookLink.present.is_(True))))
    index = Candidates([], links)
    mapping = {}
    for item in items:
        record = records.get((item['sheet_id'], item['row']))
        if record:
            link, ambiguous = index.previous(item['sheet_id'], record)
            if link and not ambiguous:
                mapping[item['id']] = link.target_id
    ids = set(mapping.values())
    targets = {r.id: {'id': r.id, 'contract_no': r.contract_no, 'project_code': r.project_code}
        for r in db.execute(select(BizContract.id, BizContract.contract_no, BizContract.project_code).where(
            BizContract.id.in_(ids), BizContract.is_deleted.is_(False), owner_scope(BizContract.owner_id, p)))} if ids else {}
    return records, {key: targets[ident] for key, ident in mapping.items() if ident in targets}


def decorate(db, p, wb, items, *, view='contracts'):
    if view == "collections":
        from app.services.collection_amounts import apply
        items = apply(wb, items)
    records, targets = _context(db, p, wb, items, view)
    result = []
    for original in items:
        item = dict(original)
        record = records.get((item['sheet_id'], item['row']), {})
        target = targets.get(item['id'])
        value, received = item.get('amount'), item.get('received')
        ratio = Decimal(received) / Decimal(value) if (not item.get('receipt_cell') and value is not None
                 and received is not None and Decimal(value) > 0) else None
        item.update(contract_no=record.get('external') or (target['contract_no'] if target else None),
                    linked_contract_id=target['id'] if target else None,
                    business_line_name=item.get('business_line'), status_name=item.get('contract_status'),
                    source_name='企微表格同步', received_rate=float(ratio) if ratio is not None else None,
                    detail_path=f"/{view}/items/{item['sheet_id']}/{item['row']}")
        result.append(item)
    return result


def _attachments(sheet, numbers):
    col = 16 if sheet.profile == 'sales' else 15
    items, seen = [], set()
    for number in numbers:
        raw = sheet.raw(number, col)
        pieces = [raw, *(raw.get('rich_string') or [])] if isinstance(raw, dict) else []
        for piece in pieces:
            if not isinstance(piece, dict) or not isinstance(piece.get('link'), str):
                continue
            url = piece['link']
            if url in seen or urlsplit(url).scheme not in {'http', 'https'}:
                continue
            seen.add(url)
            items.append({'filename': piece.get('link_text') or piece.get('value') or '合同附件', 'url': url})
    return items


def detail(db, p, sheet_id, number, revision='', *, view='contracts'):
    wb = source.active_source(db, p, view)
    if wb is None:
        raise not_found('固定来源')
    if revision and revision != wb.revision:
        raise BizError(40901, '项目数据已有更新，请返回列表重新打开详情')
    sheets = source.collection_sheets(wb) if view == 'collections' else source.module_sheets(wb, view)
    rows = source.performance_rows(wb) if view == 'contracts' else (r for s in sheets for r in s.items)
    item = next((r for r in rows if r['sheet_id'] == sheet_id and r['row'] == number), None)
    if not item:
        raise not_found('项目合同')
    sheet = next(s for s in sheets if s.id == sheet_id)
    item = decorate(db, p, wb, [item], view=view)[0]
    item.pop('_search', None)
    numbers = [number, *[n for n, row in sheet.rows.items() if row.get('parent') == number]]
    note_col = 15 if sheet.profile == 'sales' else 14
    notes = list(dict.fromkeys(text for n in numbers if (text := sheet.display(n, note_col))))
    collection = []
    for col in sheet.columns:
        label = sheet.label(col)
        if any(word in label for word in ('预计可回款', '回款进度', '达回款条件')):
            text = sheet.display(number, col)
            if text is not None:
                collection.append({'label': label, 'content': text})
    related = {'plans': [], 'payments': [], 'files': [], 'project': None, 'approval': None, 'registered_received': None,
               'payment_difference': None}
    if item['linked_contract_id'] and p.has_menu('contracts'):
        from app.services import contract_service, project_service
        contract = contract_service.get_contract(db, item['linked_contract_id'], p)
        legacy = contract_service.detail(db, contract)
        for field in ('plans', 'payments', 'files'):
            related[field] = legacy[field]
        related['registered_received'] = str(legacy['received'])
        if item.get('received') is not None:
            related['payment_difference'] = str(Decimal(item['received']) - legacy['received'])
        if legacy.get('approval'):
            related['approval'] = {key: legacy['approval'].get(key) for key in
                ('sp_no', 'sp_name', 'status_name', 'applicant_name', 'apply_time', 'approved_time')}
        if contract.project_code and p.has_menu('projects'):
            try:
                project = project_service.get_project(db, contract.project_code, p)
                related['project'] = {'code': project.project_code, 'name': project.name,
                    'status': PROJECT_STATUS.get(project.status), 'owner': project.owner.name if project.owner else None,
                    'progress': project_service.progress_stats(project), 'path': '/projects/' + project.project_code}
            except BizError as error:
                if error.code not in {40301, 40401}:
                    raise
    return {'contract': item, 'revision': wb.revision, 'source_status': source.source_info(wb, p),
            'can_track_collection': source.can_access_view(p, wb.config, 'collections') and
                any(s.id == sheet_id for s in source.collection_sheets(wb)),
            'notes': notes, 'attachments': _attachments(sheet, numbers), 'collection': collection,
            'project_type': sheet.display(number, 7) if sheet.profile == 'sales' else None,
            'last_received_on': sheet.display(number, 14) if sheet.profile == 'sales' else None,
            'archived_on': sheet.display(number, 17) if sheet.profile == 'sales' else None,
            'related': related}
