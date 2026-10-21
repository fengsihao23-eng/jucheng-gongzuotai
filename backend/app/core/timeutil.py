"""东八区时间工具与周期口径（自然周 周一~周日，ISO 周号）。

测试/演示可通过 sys_dict 之外的环境变量 FAKE_TODAY=2026-10-20 固定“今天”，保证验收数据可复现。
"""
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")


def now() -> datetime:
    fake = os.environ.get("FAKE_TODAY")
    real = datetime.now(TZ)
    if fake:
        d = date.fromisoformat(fake)
        return real.replace(year=d.year, month=d.month, day=d.day)
    return real


def today() -> date:
    return now().date()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def week_no(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_range(week: str | None = None, ref: date | None = None) -> tuple[date, date]:
    """返回自然周 [周一, 周日]。week 形如 2026-W42；不传则取 ref 所在周。"""
    if week:
        y, w = week.split("-W")
        start = date.fromisocalendar(int(y), int(w), 1)
    else:
        ref = ref or today()
        start = ref - timedelta(days=ref.weekday())
    return start, start + timedelta(days=6)


def last_week(ref: date | None = None) -> str:
    return week_no((ref or today()) - timedelta(days=7))


def month_range(period: str | None = None, ref: date | None = None) -> tuple[date, date]:
    if period:
        y, m = map(int, period.split("-"))
    else:
        ref = ref or today()
        y, m = ref.year, ref.month
    start = date(y, m, 1)
    end = (date(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)
    return start, end


def month_of(d: date) -> str:
    return f"{d:%Y-%m}"


def prev_month(period: str) -> str:
    y, m = map(int, period.split("-"))
    return f"{y - (m == 1)}-{(m - 2) % 12 + 1:02d}"


def day_bounds(d: date) -> tuple[datetime, datetime]:
    start = datetime(d.year, d.month, d.day, tzinfo=TZ)
    return start, start + timedelta(days=1)
