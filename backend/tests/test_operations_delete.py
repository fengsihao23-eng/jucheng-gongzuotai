"""运营项目删除：点名授权（梁道灿/冯思皓/聚成数智管理员）+ 软删除。"""
import uuid

import pytest
from sqlalchemy import select

from app.core.deps import build_principal
from app.core.response import BizError
from app.models import OpsProject, SysUser
from app.services import operations_service as ops
from tests.conftest import data

BASE = "/api/v1/operations"
WHITELIST = [("ken", "梁道灿"), ("FengSiHao", "冯思皓"), ("SZGLY", "聚成数智管理员")]


@pytest.fixture(autouse=True)
def fixtures(db):
    """测试库只种了演示账号，这里补建三个授权账号，用完删掉。"""
    def wipe():
        for project in db.scalars(select(OpsProject).where(OpsProject.name.like("删除验收-%"))).all():
            db.delete(project)
        db.commit()

    wipe()
    created = []
    for wecom_userid, name in WHITELIST:
        user = db.scalar(select(SysUser).where(SysUser.wecom_userid == wecom_userid))
        if not user:
            user = SysUser(wecom_userid=wecom_userid, name=name, status=1)
            db.add(user)
            created.append(user)
    db.commit()
    yield
    wipe()
    # 审计日志外键引用了这些账号，物理删除会破坏约束；置为停用即可。
    for user in created:
        user.status, user.is_deleted = 2, True
    db.commit()


def person(db, wecom_userid):
    return build_principal(db.scalar(select(SysUser).where(SysUser.wecom_userid == wecom_userid)))


def test_only_three_people_can_delete(db):
    for wecom_userid, _ in WHITELIST:
        assert ops.can_delete_project(person(db, wecom_userid))
    assert not ops.can_delete_project(person(db, "songjia"))


def test_delete_is_soft_and_hides_project(client, login, db):
    project = data(client.post(BASE, headers=login("admin"), json={
        "name": f"删除验收-{uuid.uuid4().hex[:6]}", "owner_id": person(db, "songjia").id,
        "objective": "验证删除", "category": "智慧校园"}))
    result = ops.delete_project(db, person(db, "ken"), db.get(OpsProject, project["id"]))
    assert result["deleted"] is True

    db.expire_all()
    assert db.get(OpsProject, project["id"]).status == "deleted"
    listing = data(client.get(BASE, headers=login("admin")))
    assert project["id"] not in [item["id"] for item in listing["items"]]


def test_other_roles_get_forbidden(client, login, db):
    project = data(client.post(BASE, headers=login("admin"), json={
        "name": f"删除验收-{uuid.uuid4().hex[:6]}", "owner_id": person(db, "songjia").id,
        "category": "智慧校园"}))
    with pytest.raises(BizError):
        ops.delete_project(db, person(db, "songjia"), db.get(OpsProject, project["id"]))

    resp = client.delete(f"{BASE}/{project['id']}", headers=login("admin"))
    assert resp.json()["code"] != 0
