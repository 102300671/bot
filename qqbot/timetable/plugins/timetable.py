"""课表查询插件。

查询应急管理大学教务系统教师端课表页面（公开接口，无需登录），
按班级/学期查询后渲染成图片发送；指定周次/星期时返回过滤后的文本。

注意：需要在 .env.prod 中配置 COMMAND_START=[""]，QQ 官方机器人私聊/群消息
不带 / 前缀即可触发命令（nonebot 默认命令前缀为 /）。

用法:
    查课表 [学期] 班级 [周次] [星期]
    例: 查课表 软件B241
    例: 查课表 软件B241 3 星期一
    例: 查课表 2025-2026春季 软件B241 18
"""

import html
import json
from typing import Any

import httpx
from nonebot import get_plugin_config, logger
from nonebot.adapters.qq import Bot, Event, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import on_command
from pydantic import BaseModel

from nonebot_plugin_htmlrender import render_html

API_PATH = "/Teacher/TimeTableHandler.ashx"

USAGE = (
    "用法: 查课表 [学期] 班级 [周次] [星期]\n"
    "学期可省略，默认当前学期；周次/星期可省略，省略则返回完整课表图片。\n"
    "示例: 查课表 软件B241\n"
    "示例: 查课表 软件B241 3 星期一\n"
    "示例: 查课表 2025-2026春季 软件B241 18"
)

DAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
DAY_KEYS = [
    "OnMonday",
    "OnTuesday",
    "OnWednesday",
    "OnThursday",
    "OnFriday",
    "OnSaturday",
    "OnSunday",
]


class Config(BaseModel):
    timetable_base_url: str = "https://jw.cidp.edu.cn"
    """教务系统地址。"""
    timetable_timeout: float = 30.0
    """查询接口超时时间（秒）。"""


config = get_plugin_config(Config)

timetable = on_command("查课表", aliases={"课表", "timetable"}, priority=5, block=True)


async def _request(
    client: httpx.AsyncClient, action: str, **params: str
) -> dict[str, Any] | None:
    """调用 TimeTableHandler.ashx 接口，返回解析后的 JSON（无数据时返回 None）。"""
    url = config.timetable_base_url.rstrip("/") + API_PATH
    resp = await client.post(url, data={"action": action, **params}, timeout=config.timetable_timeout)
    resp.raise_for_status()
    text = resp.text.strip()
    if not text or text == "-1":
        return None
    return json.loads(text)


async def _get_semesters(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """获取学期列表，第一项为当前学期。"""
    data = await _request(client, "initTablePage", isPublic="")
    return (data or {}).get("Sems", [])


async def _get_timetable(
    client: httpx.AsyncClient, classes: str, sem_id: int
) -> dict[str, Any] | None:
    """按行政班查询指定学期课表。"""
    return await _request(
        client,
        "getTeacherTimeTable",
        isShowStudent="0",
        classes=classes,
        lsCodes="",
        lsIds="",
        srNumbers="",
        semId=str(sem_id),
        pbn="0",
        testTeacherTimeTablePublishStatus="0",
        ttId="",
        isPublic="",
    )


def _slot_maps(patterns: list[dict[str, Any]]) -> tuple[dict[int, int], dict[int, int], int]:
    """把节次槽位编码映射为节次序号（从 1 开始）。"""
    starts = {p["StartTimeSlot"]: i + 1 for i, p in enumerate(patterns)}
    ends = {p["EndTimeSlot"]: i + 1 for i, p in enumerate(patterns)}
    return starts, ends, len(patterns)


def _course_block(c: dict[str, Any]) -> str:
    weeks = f"{c.get('WeekStart', '?')}-{c.get('WeekEnd', '?')}周"
    if c.get("WeekInterval"):
        weeks += "、隔周"
    room = "".join(filter(None, [c.get("Building"), c.get("Classroom")]))
    return (
        '<div class="course">'
        f'<div class="c-name">{html.escape(str(c.get("LUName", "")))}</div>'
        f'<div class="c-meta">{html.escape(str(c.get("FullName", "")))}</div>'
        + (f'<div class="c-meta">{html.escape(room)}</div>' if room else "")
        + f'<div class="c-meta">{weeks}</div>'
        "</div>"
    )


def _merge_week_runs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """合并同课时段、周次连续的重复条目（如 1-6 周 + 7-14 周 -> 1-14 周）。"""
    key = lambda c: (
        c.get("LUName"),
        c.get("FullName"),
        c.get("Building"),
        c.get("Classroom"),
        c.get("WeekInterval"),
    )
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for c in items:
        groups.setdefault(key(c), []).append(c)
    merged: list[dict[str, Any]] = []
    for g in groups.values():
        g.sort(key=lambda c: (c.get("WeekStart") or 0, c.get("TimeSlotStart") or 0))
        cur: dict[str, Any] | None = None
        for c in g:
            if (
                cur is not None
                and c.get("TimeSlotStart") == cur.get("TimeSlotStart")
                and c.get("TimeSlotEnd") == cur.get("TimeSlotEnd")
                and c.get("WeekStart") == (cur.get("WeekEnd") or 0) + 1
                and not c.get("WeekInterval")
            ):
                cur["WeekEnd"] = c.get("WeekEnd")
            else:
                if cur is not None:
                    merged.append(cur)
                cur = dict(c)
        if cur is not None:
            merged.append(cur)
    return merged


def build_html(class_name: str, sem_name: str, data: dict[str, Any]) -> str:
    """把课表数据渲染成 HTML 页面。

    布局策略：同一天内按“轨道”排布课程，时段重叠的课程分到不同轨道
    （横向并排），时段不重叠的课程共用同一轨道（纵向衔接），避免
    rowspan 相互冲突导致浏览器把单元格推入新列、整表错位。
    """
    patterns = data.get("ClassTimePatterns", [])
    starts, ends, n = _slot_maps(patterns)

    # 按天收集课程，并合并周次连续的重复条目
    by_day: list[list[dict[str, Any]]] = [[] for _ in range(7)]
    for c in data.get("Data", []):
        p0 = starts.get(c.get("TimeSlotStart"))
        p1 = ends.get(c.get("TimeSlotEnd"))
        if not p0 or not p1:
            continue
        for d, key in enumerate(DAY_KEYS):
            if c.get(key):
                by_day[d].append({**c, "_p0": p0, "_p1": p1})

    day_courses: list[list[dict[str, Any]]] = []
    for d in range(7):
        merged = _merge_week_runs(by_day[d])
        merged.sort(key=lambda c: (c["_p0"], c["_p1"] - c["_p0"]))
        day_courses.append(merged)

    # 轨道分配：同轨道内课程时段两两不重叠
    tracks: list[list[list[dict[str, Any]]]] = []
    for d in range(7):
        day_tracks: list[list[dict[str, Any]]] = []
        for c in day_courses[d]:
            p0, p1 = c["_p0"], c["_p1"]
            placed = False
            for track in day_tracks:
                if all(p1 < t["_p0"] or p0 > t["_p1"] for t in track):
                    track.append(c)
                    placed = True
                    break
            if not placed:
                day_tracks.append([c])
        for track in day_tracks:
            track.sort(key=lambda c: c["_p0"])
        tracks.append(day_tracks)

    max_tracks = max((len(t) for t in tracks), default=1)

    head = "".join(f'<th colspan="{max_tracks}">{day}</th>' for day in DAYS)
    body_rows = []
    next_free = [[0] * max_tracks for _ in range(7)]
    for i in range(n):
        tds = [f'<td class="period">{i + 1}</td>']
        for d in range(7):
            day_tracks = tracks[d]
            for t in range(max_tracks):
                if t >= len(day_tracks):
                    tds.append('<td class="daycell"></td>')
                    continue
                if i < next_free[d][t]:
                    continue
                cur = next((c for c in day_tracks[t] if c["_p0"] == i + 1), None)
                if cur is None:
                    tds.append('<td class="daycell"></td>')
                    next_free[d][t] = i + 1
                else:
                    span = cur["_p1"] - cur["_p0"] + 1
                    tds.append(
                        f'<td class="daycell" rowspan="{span}">{_course_block(cur)}</td>'
                    )
                    next_free[d][t] = i + span
        body_rows.append(f'<tr>{"".join(tds)}</tr>')

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body {{ margin: 0; padding: 16px; background: #f5f7fa;
       font-family: 'Noto Sans CJK SC', 'Noto Serif CJK SC', 'AR PL UMing CN', sans-serif; }}
h2 {{ margin: 0; font-size: 20px; text-align: center; color: #1f2d3d; }}
.sub {{ text-align: center; color: #7f8c9b; font-size: 13px; margin: 6px 0 12px; }}
table {{ border-collapse: collapse; margin: 0 auto; background: #fff; }}
th, td {{ border: 1px solid #c9d4e0; padding: 4px 6px; text-align: center; vertical-align: middle; }}
th {{ background: #34495e; color: #fff; font-size: 14px; font-weight: 600; }}
td.period {{ background: #eef2f7; font-weight: bold; width: 34px; color: #34495e; }}
td.daycell {{ min-width: 64px; max-width: 76px; height: 52px; }}
.course {{ padding: 2px 0; }}
.c-name {{ font-size: 13px; font-weight: 700; color: #16537e; word-break: break-all; }}
.c-meta {{ font-size: 11px; color: #5d6d7e; line-height: 1.45; word-break: break-all; }}
</style></head><body>
<h2>{html.escape(class_name)} 课表</h2>
<div class="sub">{html.escape(sem_name)}</div>
<table>
<tr><th>节次</th>{head}</tr>
{''.join(body_rows)}
</table>
</body></html>"""


def build_text(class_name: str, sem_name: str, data: dict[str, Any]) -> str:
    """渲染失败时的纯文本兜底。"""
    patterns = data.get("ClassTimePatterns", [])
    starts, ends, _ = _slot_maps(patterns)
    lines = [f"【{class_name} 课表】{sem_name}"]
    for d, day in enumerate(DAYS):
        courses = [c for c in data.get("Data", []) if c.get(DAY_KEYS[d])]
        if not courses:
            continue
        items = []
        for c in courses:
            p0 = starts.get(c.get("TimeSlotStart"), "?")
            p1 = ends.get(c.get("TimeSlotEnd"), "?")
            room = "".join(filter(None, [c.get("Building"), c.get("Classroom")]))
            items.append(
                f"{p0}-{p1}节 {c.get('LUName')}（{c.get('FullName')} {room} "
                f"{c.get('WeekStart')}-{c.get('WeekEnd')}周）"
            )
        lines.append(f"{day}: " + " | ".join(items))
    return "\n".join(lines)


def _match_semester(sems: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """按关键词模糊匹配学期，如 '2025-2026春季'。"""
    q = query.replace(" ", "")
    result = []
    for s in sems:
        name = s["Name"].replace("学年度", "").replace(" ", "")
        if q in name or s["Name"] in query:
            result.append(s)
    return result


_DAY_ALIASES = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "日": 7,
    "天": 7,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
}


def _parse_week(token: str) -> int | None:
    """解析周次，支持 '3'、'3周'、'第3周'。"""
    t = token.strip().removeprefix("第").removesuffix("周")
    if t.isdigit() and int(t) >= 1:
        return int(t)
    return None


def _parse_day(token: str) -> int | None:
    """解析星期，支持 '星期一'、'周一'、'礼拜一'、'1'-'7'（1=周一）。"""
    t = token.strip()
    for prefix in ("星期", "礼拜", "周"):
        if t.startswith(prefix):
            t = t[len(prefix):]
            break
    return _DAY_ALIASES.get(t)


def _course_in_week(c: dict[str, Any], week: int) -> bool:
    """课程在指定周是否上课（WeekInterval 非 0 视为隔周，按起始周奇偶推算）。"""
    ws, we = c.get("WeekStart"), c.get("WeekEnd")
    if not isinstance(ws, int) or not isinstance(we, int) or not (ws <= week <= we):
        return False
    if c.get("WeekInterval") and (week - ws) % 2 != 0:
        return False
    return True


def _course_line(c: dict[str, Any], starts: dict[int, int], ends: dict[int, int], with_weeks: bool) -> str:
    p0 = starts.get(c.get("TimeSlotStart"), "?")
    p1 = ends.get(c.get("TimeSlotEnd"), "?")
    room = "".join(filter(None, [c.get("Building"), c.get("Classroom")]))
    meta = " ".join(filter(None, [str(c.get("FullName") or ""), room]))
    week_txt = f" {c.get('WeekStart')}-{c.get('WeekEnd')}周" if with_weeks else ""
    return f"{p0}-{p1}节 {c.get('LUName')}{week_txt}（{meta}）"


def build_filtered_text(
    class_name: str, sem_name: str, data: dict[str, Any], week: int | None, day: int | None
) -> str:
    """按周次/星期过滤后的文本课表。"""
    patterns = data.get("ClassTimePatterns", [])
    starts, ends, _ = _slot_maps(patterns)
    courses = data.get("Data", [])

    title = f"【{class_name}】{sem_name}"
    extra = []
    if week is not None:
        extra.append(f"第{week}周")
    if day is not None:
        extra.append(DAYS[day - 1])
    if extra:
        title += " " + " ".join(extra)

    selected_days = [day - 1] if day is not None else list(range(7))
    lines = [title]
    any_found = False
    for d in selected_days:
        items = [
            (starts.get(c.get("TimeSlotStart"), 99), c)
            for c in courses
            if c.get(DAY_KEYS[d]) and (week is None or _course_in_week(c, week))
        ]
        if not items:
            continue
        any_found = True
        items.sort(key=lambda ic: ic[0])
        if len(selected_days) > 1:
            lines.append(f"{DAYS[d]}:")
            indent = "  "
        else:
            indent = ""
        for _, c in items:
            lines.append(f"{indent}{_course_line(c, starts, ends, with_weeks=week is None)}")
    if not any_found:
        lines.append("没有课")
    return "\n".join(lines)


async def _handle_query(bot: Bot, event: Event, args_text: str) -> None:
    parts = args_text.split()
    if not parts:
        await bot.send(event, MessageSegment.text(USAGE))
        return

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            sems = await _get_semesters(client)
            if not sems:
                await bot.send(event, MessageSegment.text("获取学期列表失败，请稍后再试。"))
                return

            # 解析参数: [学期] 班级 [周次] [星期]
            # 首个 token 若是学期关键词则消费掉，否则视为班级名（学期取当前）
            sem: dict[str, Any] | None = None
            idx = 0
            matches = _match_semester(sems, parts[0])
            if matches:
                if len(matches) > 1:
                    options = "\n".join(s["Name"] for s in matches)
                    await bot.send(
                        event,
                        MessageSegment.text(f"学期「{parts[0]}」匹配到多个，请写完整一些:\n{options}"),
                    )
                    return
                sem = matches[0]
                idx = 1
            if idx >= len(parts):
                await bot.send(event, MessageSegment.text(USAGE))
                return
            class_name = parts[idx]
            idx += 1

            week: int | None = None
            day: int | None = None
            if idx < len(parts):
                week = _parse_week(parts[idx])
                if week is not None:
                    idx += 1
                    if idx < len(parts):
                        day = _parse_day(parts[idx])
                        if day is None:
                            await bot.send(
                                event,
                                MessageSegment.text(
                                    f"星期「{parts[idx]}」无法识别，请填 星期一/周一/1 这样的格式。\n{USAGE}"
                                ),
                            )
                            return
                        idx += 1
                else:
                    # 允许省略周次直接填星期，如「查课表 软件B241 周一」
                    day = _parse_day(parts[idx])
                    if day is None:
                        await bot.send(
                            event,
                            MessageSegment.text(
                                f"「{parts[idx]}」无法识别。\n"
                                f"周次请填数字（如 3、第3周），星期请填 星期一/周一/1 这样的格式。\n{USAGE}"
                            ),
                        )
                        return
                    idx += 1

            if idx < len(parts):
                await bot.send(event, MessageSegment.text(USAGE))
                return

            if sem is None:
                sem = sems[0]

            data = await _get_timetable(client, class_name, int(sem["Id"]))
            courses = (data or {}).get("Data", [])
            if not courses:
                await bot.send(
                    event,
                    MessageSegment.text(
                        f"未查到「{class_name}」在 {sem['Name']} 的课表。\n"
                        "请检查班级名是否正确，或该学期课表尚未发布。"
                    ),
                )
                return

            if week is None and day is None:
                html_str = build_html(class_name, sem["Name"], data)
                try:
                    img = await render_html(html_str, width=1140, device_pixel_ratio=2.0)
                    await bot.send(
                        event,
                        MessageSegment.file_image(img.data, f"{class_name}课表.png"),
                    )
                except Exception:
                    logger.exception("课表图片渲染失败，改用文本回复")
                    await bot.send(event, MessageSegment.text(build_text(class_name, sem["Name"], data)))
            else:
                await bot.send(
                    event, MessageSegment.text(build_filtered_text(class_name, sem["Name"], data, week, day))
                )
    except httpx.HTTPError:
        logger.exception("查询课表接口请求失败")
        await bot.send(event, MessageSegment.text("查询课表失败（网络错误），请稍后再试。"))
    except Exception:
        logger.exception("查询课表发生未知错误")
        await bot.send(event, MessageSegment.text("查询课表失败，请稍后再试。"))


@timetable.handle()
async def _handler(bot: Bot, event: Event, args: Message = CommandArg()) -> None:
    await _handle_query(bot, event, str(args))
