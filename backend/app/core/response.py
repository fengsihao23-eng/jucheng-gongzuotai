"""统一响应包 {code, message, data, trace_id} 与业务错误码（PRD 第 10 章）。

金额：Decimal 统一序列化为字符串（单位元），前端 Number() 后计算/展示。
"""
import contextvars
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")

def _dec(d: Decimal) -> str:
    """金额（≤2 位小数）统一 2 位；比率等更高精度（如毛利率 0.6943）保留原精度，最多 6 位。"""
    exp = d.as_tuple().exponent
    if not isinstance(exp, int) or exp >= -2:
        return f"{d:.2f}"
    return format(d.quantize(Decimal("0.000001")).normalize() if exp < -6 else d, "f")


ENCODERS = {Decimal: _dec,
            datetime: lambda d: d.isoformat(), date: lambda d: d.isoformat()}


def encode(data: Any) -> Any:
    return jsonable_encoder(data, custom_encoder=ENCODERS)


def ok(data: Any = None, message: str = "ok", headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"code": 0, "message": message, "data": encode(data), "trace_id": trace_id_var.get()},
                        headers=headers)


def page_data(items: list, total: int, page: int, page_size: int, **extra) -> dict:
    return {"items": items, "total": total, "page": page, "page_size": page_size, **extra}


class BizError(Exception):
    """业务错误。code 采用 4xx00 段：40001 参数错误、40101 未登录、40102 令牌过期、40301 无权限、
    40401 不存在、40901 重复登记/冲突、42201 文件解析失败、42901 频率超限；50001 服务错误、50201 外部系统错误。"""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    @property
    def http_status(self) -> int:
        return int(str(self.code)[:3])


def not_found(what: str = "记录") -> BizError:
    return BizError(40401, f"{what}不存在")


def forbidden(msg: str = "无权限访问") -> BizError:
    return BizError(40301, msg)


def bad_request(msg: str) -> BizError:
    return BizError(40001, msg)


def conflict(msg: str, data: Any = None) -> BizError:
    return BizError(40901, msg, data)
