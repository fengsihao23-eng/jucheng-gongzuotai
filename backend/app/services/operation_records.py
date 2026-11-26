"""Read-only audit search; pre-feature records keep their original, limited evidence."""
from datetime import date, datetime, time, timedelta, timezone
import csv
from io import StringIO

from sqlalchemy import String, cast, func, literal, or_, select, union_all

from app.core.audit import mask
from app.core.response import bad_request, not_found
from app.core.timeutil import TZ
from app.models import AuditEvent, AuditChange, AuditSetting, LogOperation, LogMcpAudit, SyncJobLog, SysUser
from app.core.operation_audit import AREAS

TABLES = {"sys_user":"账号","sys_user_role":"账号角色","sys_user_permission":"个人权限","sys_role":"角色",
          "sys_dict":"业务配置","ops_task":"运营任务","ops_task_update":"任务反馈","ops_task_discussion":"任务讨论",
          "ops_project":"运营项目","ops_member":"项目成员","biz_contract":"合同","biz_payment":"回款",
          "workbook_collection_request":"回款跟进要求","workbook_collection_progress":"回款跟进记录",
          "contract_workbook":"企微表格","sys_notify":"通知","biz_opportunity":"商机","fin_bank_flow":"银行流水"}
FIELDS = {"name":"名称","title":"标题","content":"内容","status":"状态","amount":"金额","owner_id":"负责人",
          "recipient_id":"接收人","author_id":"操作人","expected_on":"预计回款日","next_step":"下一步",
          "due_date":"截止日期","description":"说明","reply":"回复内容","replied_at":"回复时间","cancelled_at":"撤回时间",
          "stage":"阶段","password_hash":"密码","permissions":"权限","preferences":"个人设置","department_id":"部门",
          "mobile":"手机","email":"邮箱","is_deleted":"删除标记","sheets":"原表内容","revision":"表格版本",
          "config":"配置","dict_value":"配置内容","updated_at":"更新时间","created_at":"创建时间"}


def since(db):
    setting=db.get(AuditSetting,"enabled")
    return datetime.fromisoformat(setting.value["since"]) if setting else datetime.now(timezone.utc)


def legacy_visibility(p):
    from app.services import operations_service as ops
    if p is None or ops.can_manage_all(p):
        return True
    return ~or_(LogOperation.action.startswith("operations."), func.coalesce(LogOperation.target_type, "").startswith("ops_"))


def combined(db, p=None):
    cutoff=since(db)
    def col(value,label):
        return (literal(value) if not hasattr(value,"label") else value).label(label)
    def shape(kind,ident,actor,name,account,module,action,source,outcome,at,target):
        return [col(literal(kind)+literal(":")+cast(ident,String),"key"),col(actor,"actor_id"),col(name,"actor"),
                col(account,"account"),col(module,"module"),col(action,"action"),col(source,"source"),
                col(outcome,"outcome"),col(at,"created_at"),col(target,"target")]
    events=select(*shape("event",AuditEvent.id,AuditEvent.actor_id,AuditEvent.actor_name,AuditEvent.account,
        AuditEvent.module,AuditEvent.action,AuditEvent.source,AuditEvent.outcome,AuditEvent.created_at,AuditEvent.path))
    old=select(*shape("legacy",LogOperation.id,LogOperation.user_id,SysUser.name,SysUser.wecom_userid,"历史操作",
        LogOperation.action,"legacy","historical",LogOperation.created_at,
        func.concat(LogOperation.target_type,":",LogOperation.target_id))).outerjoin(SysUser,SysUser.id==LogOperation.user_id).where(LogOperation.created_at<cutoff)
    tools=select(*shape("mcp",LogMcpAudit.id,LogMcpAudit.user_id,SysUser.name,SysUser.wecom_userid,"智能工具",
        LogMcpAudit.tool_code,"mcp_history",LogMcpAudit.outcome,LogMcpAudit.called_at,LogMcpAudit.tool_code)
        ).outerjoin(SysUser,SysUser.id==LogMcpAudit.user_id).where(LogMcpAudit.called_at<cutoff)
    jobs=select(*shape("job",SyncJobLog.id,None,"系统任务",None,"系统任务",SyncJobLog.job_name,"job_history",
        SyncJobLog.status,SyncJobLog.run_at,SyncJobLog.job_name)).where(SyncJobLog.run_at<cutoff)
    from app.services import operations_service as ops
    if p is not None and not ops.can_manage_all(p):
        # 全局审计可能在一条记录里混有多项目快照，不能按操作人等同项目成员。
        # 成员在项目内读取本项目历史；跨项目审计由全项目管理权限控制。
        operational_change = select(AuditChange.id).where(AuditChange.event_key == AuditEvent.event_key,
            or_(AuditChange.table_name.startswith("ops_"),
                func.coalesce(cast(AuditChange.before, String), "").contains('"ops_'),
                func.coalesce(cast(AuditChange.after, String), "").contains('"ops_'))).exists()
        events = events.where(~operational_change,
            func.coalesce(AuditEvent.module, "") != "运营项目",
            ~func.coalesce(AuditEvent.path, "").contains("/operations"),
            ~func.coalesce(cast(AuditEvent.detail, String), "").contains("operations."),
            ~func.coalesce(cast(AuditEvent.detail, String), "").contains("ops."),
            ~func.coalesce(cast(AuditEvent.detail, String), "").contains("ops_"))
        old = old.where(legacy_visibility(p))
        tools = tools.where(~LogMcpAudit.tool_code.startswith("ops."))
        jobs = jobs.where(~SyncJobLog.job_name.startswith("operations"), ~SyncJobLog.job_name.startswith("ops_"))
    return union_all(events,old,tools,jobs).subquery()


def _filters(table, *, actor="", keyword="", module="", source="", outcome="", start="", end=""):
    conditions=[]
    if actor:
        conditions.append(or_(table.c.actor.icontains(actor,autoescape=True),table.c.account.icontains(actor,autoescape=True)))
    if keyword:
        conditions.append(or_(*(table.c[field].icontains(keyword,autoescape=True) for field in ("action","target","module"))))
    for field,val in (("module",module),("source",source),("outcome",outcome)):
        if val:
            conditions.append(table.c[field]==val)
    parsed={}
    for key,raw in (("start",start),("end",end)):
        if raw:
            try:
                parsed[key]=date.fromisoformat(raw)
            except ValueError:
                raise bad_request("请选择有效的日期范围") from None
    if parsed.get("start") and parsed.get("end") and parsed["start"]>parsed["end"]:
        raise bad_request("结束日期不能早于开始日期")
    if "start" in parsed:
        conditions.append(table.c.created_at>=datetime.combine(parsed["start"],time.min,tzinfo=TZ))
    if "end" in parsed:
        conditions.append(table.c.created_at<datetime.combine(parsed["end"]+timedelta(days=1),time.min,tzinfo=TZ))
    return conditions


def listing(db, *, page=1,size=30,p=None,**filters):
    table=combined(db,p)
    where=_filters(table,**filters)
    total=db.scalar(select(func.count()).select_from(table).where(*where))
    page=min(page,max(1,(total+size-1)//size))
    rows=[dict(row) for row in db.execute(select(table).where(*where).order_by(table.c.created_at.desc(),table.c.key.desc())
                                        .offset((page-1)*size).limit(size)).mappings()]
    events=[int(row["key"].split(":")[1]) for row in rows if row["key"].startswith("event:")]
    counts=dict(db.execute(select(AuditEvent.id,func.count(AuditChange.id)).join(AuditChange,AuditChange.event_key==AuditEvent.event_key)
        .where(AuditEvent.id.in_(events)).group_by(AuditEvent.id)).all()) if events else {}
    for row in rows:
        row["changed_count"]=counts.get(int(row["key"].split(":")[1]),0) if row["key"].startswith("event:") else None
        row["actor"]=row["actor"] or ("系统任务" if row["source"] in ("job","database","job_history") else "未登录/外部请求")
    return {"items":rows,"total":total,"page":page,"size":size,"since":since(db)}


def options(db,p=None):
    table=combined(db,p)
    modules=list(db.scalars(select(table.c.module).distinct().order_by(table.c.module)))
    return {"modules":modules,"since":since(db),"coverage_tables":db.scalar(
        __import__("sqlalchemy").text("SELECT count(*) FROM pg_trigger WHERE tgname='jc_operation_audit' AND NOT tgisinternal")),
        "sources":{"web":"接口操作","page":"页面操作","job":"系统任务","mcp":"智能工具","database":"数据库连接",
                   "maintenance":"已确认的数据维护","legacy":"历史操作","mcp_history":"历史智能工具","job_history":"历史任务"}}


def detail(db,key,p=None):
    kind,_,raw=key.partition(":")
    if kind not in ("event","legacy","mcp","job") or not raw.isascii() or not raw.isdigit():
        raise not_found("操作记录")
    cls={"event":AuditEvent,"legacy":LogOperation,"mcp":LogMcpAudit,"job":SyncJobLog}[kind]
    row=db.get(cls,int(raw))
    if not row:
        raise not_found("操作记录")
    if p is not None:
        visible = combined(db,p)
        if not db.scalar(select(visible.c.key).where(visible.c.key == key)):
            raise not_found("操作记录")
    cutoff=since(db)
    if kind!="event" and (row.created_at if kind=="legacy" else row.called_at if kind=="mcp" else row.run_at)>=cutoff:
        raise not_found("历史记录")
    if kind=="event":
        changes=list(db.scalars(select(AuditChange).where(AuditChange.event_key==row.event_key).order_by(AuditChange.id)))
        return {"key":key,"actor":row.actor_name or "系统/未登录","account":row.account,"source":row.source,
                "action":row.action,"module":row.module,"outcome":row.outcome,"created_at":row.created_at,
                "method":row.method,"path":row.path,"ip":row.client_ip,"duration_ms":row.duration_ms,
                "status_code":row.status_code,"detail":mask(row.detail),"event_key":row.event_key,
                "changes":[{"id":c.id,"table":c.table_name,"object":TABLES.get(c.table_name,c.table_name),
                            "record_key":c.record_key,"operation":c.operation,"before":mask(c.before),"after":mask(c.after),
                            "fields":[{"key":f,"label":FIELDS.get(f,f)} for f in c.fields]} for c in changes]}
    user=db.get(SysUser,getattr(row,"user_id",None)) if getattr(row,"user_id",None) else None
    common={"key":key,"actor":user.name if user else "系统/历史账号","account":user.wecom_userid if user else None,
            "source":"历史记录","outcome":"historical","changes":[],"note":"历史记录保留当时已记录的内容，未记录的字段不补造。"}
    if kind=="legacy":
        return {**common,"action":row.action,"created_at":row.created_at,"ip":row.client_ip,
                "changes":[{"object":TABLES.get(row.target_type,row.target_type),"record_key":row.target_id,"operation":"historical",
                            "before":mask(row.before),"after":mask(row.after),
                            "fields":[{"key":f,"label":FIELDS.get(f,f)} for f in (row.before or {}).keys()|(row.after or {}).keys()]}]}
    if kind=="mcp":
        return {**common,"action":row.tool_code,"outcome":row.outcome,"created_at":row.called_at,"ip":row.client_ip,
                "detail":{"参数":mask(row.params),"结果":mask(row.result_summary)}}
    return {**common,"action":row.job_name,"outcome":row.status,"created_at":row.run_at,
            "detail":{"说明":mask(row.message),"失败信息":mask(row.error_msg)}}


def export_csv(db,**filters):
    rows=listing(db,page=1,size=50001,**filters)
    if rows["total"]>50000:
        raise bad_request("记录超过50000条，请缩小日期范围后导出")
    buffer=StringIO()
    writer=csv.writer(buffer)
    writer.writerow(["时间","操作账号","姓名","模块","操作","来源","结果","对象","数据变更条数"])
    def safe(value):
        s=str(value or "")
        return "'"+s if s[:1] in ("=","+","-","@") else s
    for r in rows["items"]:
        writer.writerow([safe(r[k].astimezone(TZ).isoformat() if k=="created_at" else r[k]) for k in
            ("created_at","account","actor","module","action","source","outcome","target","changed_count")])
    return ("\ufeff"+buffer.getvalue()).encode("utf-8")
