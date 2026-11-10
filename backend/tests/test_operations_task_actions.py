"""运营任务删除与成员转交移除。"""
from datetime import date

from sqlalchemy import select

from app.core.deps import build_principal
from app.models import OpsMember, OpsProject, OpsTask, OpsTaskNode, SysUser
from app.services import operations_service as ops


def _setup(db, code="ACT"):
    owner = SysUser(name=f"负责人{code}", wecom_userid=f"owner_{code}", status=1)
    member = SysUser(name=f"成员{code}", wecom_userid=f"member_{code}", status=1)
    db.add_all([owner, member])
    db.flush()
    project = OpsProject(name=f"动作测试{code}", owner_id=owner.id)
    db.add(project)
    db.flush()
    db.add(OpsMember(project_id=project.id, user_id=member.id, role="member"))
    task = OpsTask(project_id=project.id, title="待删除任务", owner_id=member.id, creator_id=owner.id,
                   due_date=date(2026, 11, 20), plan_month="2026-11", status="doing", progress=30)
    db.add(task)
    db.flush()
    db.add(OpsTaskNode(task_id=task.id, seq=1, name="节点一", due_date=date(2026, 11, 15)))
    db.flush()
    return owner, member, project, task


def test_delete_task_removes_nodes_and_task(db):
    owner, member, project, task = _setup(db, "DEL")
    ops.delete_task(db, build_principal(owner), task.id)
    db.flush()
    assert db.get(OpsTask, task.id) is None
    assert db.scalar(select(OpsTaskNode).where(OpsTaskNode.task_id == task.id)) is None


def test_remove_member_requires_transfer_then_moves_tasks(db):
    owner, member, project, task = _setup(db, "MOVE")
    p = build_principal(owner)
    try:
        ops.remove_member(db, p, project.id, member.id)
        raise AssertionError("应当提示存在未完成任务")
    except Exception as exc:  # noqa: BLE001
        assert "未完成任务" in str(exc)
    ops.remove_member(db, p, project.id, member.id, transfer=True)
    db.flush()
    db.refresh(task)
    assert task.owner_id == owner.id
    assert db.scalar(select(OpsMember).where(OpsMember.project_id == project.id,
                                            OpsMember.user_id == member.id)) is None


def test_co_owner_gets_manager_rights(db):
    from app.models import OpsMember

    owner, member, project, task = _setup(db, "CO")
    row = db.scalar(select(OpsMember).where(OpsMember.project_id == project.id, OpsMember.user_id == member.id))
    row.role = "co_owner"
    db.flush()
    p = build_principal(member)
    assert ops.role(db, p, project) == "manager"          # 权限等同项目管理员
    assert any(m["user_id"] == member.id and m["role"] == "co_owner" for m in ops.members(db, project))
