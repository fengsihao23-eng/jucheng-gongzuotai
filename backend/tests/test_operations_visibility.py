"""运营项目按成员隔离：不参与项目的人看不到「运营项目」菜单。"""
from sqlalchemy import select

from app.api.auth import visible_menus
from app.core.deps import build_principal
from app.models import OpsMember, OpsProject, SysUser


def _user(db, userid="menu_probe"):
    u = SysUser(name="菜单测试", wecom_userid=userid, status=1)
    db.add(u)
    db.flush()
    return u


def test_operations_menu_hidden_for_non_member(db):
    u = _user(db)
    p = build_principal(u)
    assert "operations" in p.menus  # 角色本身有该菜单
    assert "operations" not in visible_menus(db, p)  # 但没有参与任何项目

    project = db.scalar(select(OpsProject))
    if project is None:
        owner = db.scalar(select(SysUser).where(SysUser.id != u.id))
        project = OpsProject(name="可见性测试项目", owner_id=owner.id)
        db.add(project)
        db.flush()
    db.add(OpsMember(project_id=project.id, user_id=u.id, role="member"))
    db.flush()
    assert "operations" in visible_menus(db, p)


def test_operations_menu_kept_for_project_owner(db):
    u = _user(db, "menu_owner")
    db.add(OpsProject(name="负责人可见性项目", owner_id=u.id))
    db.flush()
    p = build_principal(u)
    assert "operations" in visible_menus(db, p)
