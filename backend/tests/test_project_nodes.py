"""缺少流程节点的项目补标准节点（合同导入建档的项目默认没有节点）。"""
from app.core.timeutil import today

from app.models import BizProject
from app.services import project_service


def _project(db, code, status):
    p = BizProject(project_code=code, name=f"节点测试项目 {code}", business_line="AF",
                   customer_name="测试客户", status=status)
    db.add(p)
    db.flush()
    return p


def test_ensure_nodes_schedules_forward_without_fake_delay(db):
    p = _project(db, "JC-NODE-9001", 2)
    nodes = project_service.ensure_nodes(db, p)
    assert len(nodes) == 5
    assert [n.node_name for n in nodes][:2] == ["深化设计", "进度管理"]
    assert all(n.status == 1 and n.plan_date and n.plan_date >= today() for n in nodes)
    assert p.plan_end_date == nodes[-1].plan_date
    stats = project_service.progress_stats(p)
    assert stats["current_node"] == "深化设计" and stats["done_nodes"] == 0 and stats["delayed_nodes"] == 0
    assert project_service.ensure_nodes(db, p) == []


def test_ensure_nodes_marks_closed_projects_completed(db):
    p = _project(db, "JC-NODE-9002", 5)
    nodes = project_service.ensure_nodes(db, p)
    assert len(nodes) == 5
    assert all(n.status == 3 and n.finish_date for n in nodes)
    stats = project_service.progress_stats(p)
    assert stats["done_nodes"] == 5 and stats["progress"] == 1 and stats["delayed_nodes"] == 0
    assert stats["current_node"] == "已全部完成"
