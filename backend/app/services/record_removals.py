"""Logical record removal shared by normal list, detail, and export queries."""
from sqlalchemy import Column, String, Table, cast, event, select
from sqlalchemy.orm import Session, with_loader_criteria
from sqlalchemy.sql.visitors import iterate

from app import models as m
from app.core.audit import log_op
from app.core.response import conflict

MODELS = (
    m.BizExpenseReport, m.BizPayment, m.BizProjectMilestone, m.BizProjectReview, m.CleanJob, m.CsTicket,
    m.FinBankImport, m.FinBankFlow, m.FinReceivablesSourceRow, m.FinSalesInvoice,
    m.HrCandidate, m.HrContractAlert, m.HrDocument, m.HrEmployee, m.KbDocument,
    m.ImportBatch, m.OaSubmission, m.OpsAssetMovement, m.OpsCompetitor, m.OpsDigestSnapshot,
    m.OpsEquipment, m.OpsImportBatch, m.OpsIncome, m.OpsMaintenanceRecord, m.OpsOutreachGoal,
    m.OpsProduct, m.OpsProjectIncome, m.OpsProjectProgress, m.OpsProjectUser, m.OpsSchoolVisit,
    m.ProdBidCheck, m.ProdGeneration, m.RptReport, m.SporadicCollectionProject,
    m.SyncLemonBatch, m.SyncTaxFiling, m.SyncWecomApproval, m.SysNotify,
    m.WorkbookCollectionReport, m.WorkbookCollectionRequest,
)

REMOVED = m.BusinessRecordDeletion.__table__


def _visible(model):
    marked = select(REMOVED.c.record_key).where(
        REMOVED.c.resource == model.__tablename__, REMOVED.c.record_key == cast(model.id, String),
    ).correlate_except(REMOVED)
    return with_loader_criteria(model, ~marked.exists(), include_aliases=True, propagate_to_loaders=False)


VISIBLE_OPTIONS = {model.__table__.name: _visible(model) for model in MODELS}


@event.listens_for(Session, "do_orm_execute")
def _visible_records(state):
    if not state.is_select or state.is_column_load or state.execution_options.get("include_removed_records"):
        return
    names = set()
    for node in iterate(state.statement):
        table = node.table if isinstance(node, Column) else node
        if isinstance(table, Table) and table.name in VISIBLE_OPTIONS:
            names.add(table.name)
    if names:
        state.statement = state.statement.options(*(VISIBLE_OPTIONS[name] for name in sorted(names)))


def mark(db, p, resource, key, *, name, ip=None):
    identity = (resource, str(key))
    if db.get(m.BusinessRecordDeletion, identity):
        raise conflict("该记录已删除，请刷新列表")
    db.add(m.BusinessRecordDeletion(resource=resource, record_key=identity[1], removed_by=p.id))
    audit_key = identity[1].rsplit(":", 1)[-1] if len(identity[1]) > 64 else identity[1]
    log_op(db, p.id, "record.delete", resource, audit_key, before={"name": name}, after={"removed": True}, ip=ip)


def is_removed(db, row):
    return db.get(m.BusinessRecordDeletion, (row.__tablename__, str(row.id))) is not None
