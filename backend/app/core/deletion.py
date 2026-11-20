"""删除口令只由服务端验证，不进入 URL、页面配置或审计正文。"""
from secrets import compare_digest

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.core.config import settings
from app.core.response import bad_request


class DeletionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr = Field(min_length=1, max_length=128)


def verify_deletion(body: DeletionIn):
    if not compare_digest(body.password.get_secret_value().encode(), settings.operations_delete_password.encode()):
        raise bad_request("删除密码不正确，请重新输入")
