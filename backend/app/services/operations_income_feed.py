"""Reserved authenticated income ingress: preview, hash-confirm and idempotent batches."""
import csv
import hashlib
import io
import json
from sqlalchemy import select
from app.core.audit import log_op
from app.core.response import conflict, encode
from app.core.timeutil import now
from app.models import OpsIncomeIngressBatch, OpsSchool
from app.services import operations_service as ops, campus_income_file as importer


def prepare(db, p, project_id, body):
    project = ops.get_project(db, p, project_id)
    ops.manage(db, p, project)
    payload = body.model_dump(mode="json", exclude={"expected_sha256"})
    sha = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    old = db.scalar(select(OpsIncomeIngressBatch).where(OpsIncomeIngressBatch.project_id == project_id,
        OpsIncomeIngressBatch.source_key == body.source_key, OpsIncomeIngressBatch.batch_key == body.batch_key))
    if old:
        if old.payload_sha256 != sha:
            raise conflict("同一来源批次内容已变化，不能覆盖原收入；请核对原记录")
        return {"sha256": sha, "already_imported": True, "result": old.result}, None, old
    schools = dict(db.execute(select(OpsSchool.id, OpsSchool.name).where(OpsSchool.project_id == project_id)).all())
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(importer.HEADER)
    for row in body.rows:
        if row.school_id is not None and row.school_id not in schools:
            raise conflict("来源学校不属于本项目或尚未建档；请使用已核对的学校 ID")
        writer.writerow([schools.get(row.school_id, ""), row.income_date.isoformat(), str(row.amount), row.note])
    raw = output.getvalue().encode("utf-8")
    report, _, _ = importer.prepare(db, p, project_id, raw, f"{body.source_key}.csv")
    report["file_sha256"], report["sha256"] = report["sha256"], sha
    return report, raw, None


def confirm(db, p, project_id, body):
    project = ops.get_project(db, p, project_id, lock=True)
    ops.manage(db, p, project)
    ops.active(project)
    report, raw, old = prepare(db, p, project_id, body)
    if report["sha256"] != body.expected_sha256:
        raise conflict("请先预览并使用返回的 sha256 确认，不能提交已变化的批次")
    if old:
        return {"already_imported": True, "result": old.result}
    if report["error_count"]:
        raise conflict("批次含差异或无效数据，未写入任何收入", data=encode(report))
    result = importer.confirm(db, p, project_id, raw, f"{body.source_key}.csv", report["file_sha256"],
                              report["new_rows"], report["new_amount"])
    batch = OpsIncomeIngressBatch(project_id=project_id, source_key=body.source_key, batch_key=body.batch_key,
        payload_sha256=report["sha256"], result=result, operator_id=p.id, created_at=now())
    db.add(batch)
    db.flush()
    log_op(db, p.id, "operations.income.ingress", "ops_income_ingress_batch", batch.id,
           after={"source_key": body.source_key, "batch_key": body.batch_key, "sha256": report["sha256"]})
    return {"already_imported": False, "result": result}
