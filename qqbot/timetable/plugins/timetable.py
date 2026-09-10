"""课表查询插件。

查询应急管理大学教务系统教师端课表页面（公开接口，无需登录），
按班级/学期查询后渲染成图片发送；指定周次/星期时返回过滤后的课表图片。

命令前缀为 /（.env.prod 中 COMMAND_START=["/"]），使用标准 GNU 风格选项，
严格选项制，不接受位置参数。

用法:
    /timetable -c 班级 [-s 学期] [-w 周次] [-d 星期]
    例: /timetable -c 软件B241
    例: /timetable -c 软件B241 -w 3 -d 一
    例: /timetable -c 软件B241 -s 2025-2026春季 -w 18
    /today -c 班级  （自动计算本周周次与今日星期）

"""

import datetime
import html
import json
from typing import Any

import httpx
from nonebot import get_driver, get_plugin_config, logger
from nonebot.adapters.qq import Bot, Event, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import on_command
from pydantic import BaseModel

from nonebot_plugin_htmlrender import render_html

import qq_panels

API_PATH = "/Teacher/TimeTableHandler.ashx"

USAGE = (
    "用法: /timetable -c 班级 [-s 学期] [-w 周次] [-d 星期]\n"
    "选项:\n"
    "  -c, --class <班级>     班级名（必填），如 软件B241\n"
    "  -s, --semester <学期>  学期，省略则查当前学期，如 2025-2026春季\n"
    "  -w, --week <周次>      周次，如 3（也支持 第3周、3周）\n"
    "  -d, --day <星期>       星期，如 星期一/周一/一/1（1=周一）\n"
    "  -h, --help             显示本帮助\n"
    "省略 -w/-d 返回完整课表图片，指定则返回过滤后的课表图片（渲染失败自动回退文本）。\n"
    "示例: /timetable -c 软件B241\n"
    "示例: /timetable -c 软件B241 -w 3 -d 一\n"
    "示例: /timetable -c 软件B241 -s 2025-2026春季 -w 18\n"
    "快捷今日: /today -c 软件B241  （自动计算周次与星期）"
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
    semester_start: str = "2026-09-07"
    """学期开始日期，用于自动计算周次（格式 YYYY-MM-DD）。"""


config = get_plugin_config(Config)


def _current_week_and_day() -> tuple[int, int]:
    """根据配置的学期开始日期自动计算当前周次与星期（1=周一）。"""
    now = datetime.datetime.now()
    day = now.weekday() + 1  # 1=周一 ... 7=周日
    start = datetime.datetime.strptime(config.semester_start, "%Y-%m-%d").date()
    today = now.date()
    week = max(1, (today - start).days // 7 + 1)
    return week, day


timetable = on_command("timetable", aliases={"课表"}, priority=5, block=True)
today = on_command("today", aliases={"今日课表"}, priority=5, block=True)


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


def _course_block(c: dict[str, Any], show_weeks: bool = True) -> str:
    room = "".join(filter(None, [c.get("Building"), c.get("Classroom")]))
    parts = [
        '<div class="course">',
        f'<div class="c-name">{html.escape(str(c.get("LUName", "")))}</div>',
        f'<div class="c-meta">{html.escape(str(c.get("FullName", "")))}</div>',
    ]
    if room:
        parts.append(f'<div class="c-meta">{html.escape(room)}</div>')
    if show_weeks:
        weeks = f"{c.get('WeekStart', '?')}-{c.get('WeekEnd', '?')}周"
        if c.get("WeekInterval"):
            weeks += "、隔周"
        parts.append(f'<div class="c-meta">{weeks}</div>')
    parts.append("</div>")
    return "".join(parts)


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


def build_html(
    class_name: str,
    sem_name: str,
    data: dict[str, Any],
    week: int | None = None,
    day: int | None = None,
) -> str:
    """把课表数据渲染成 HTML 页面。

    通用布局策略（适配不同班级课表）：
    - 同一节课次范围内（同段）的课程合并为一个“组”，在同一格内纵向堆叠，
      避免同段课程（如 1-4 周 + 5-13 周）各自占列产生空列；
    - 组与组时段重叠（如横跨多节的课程设计）才分到不同轨道横向并排；
    - 每天轨道数按当天实际需要动态计算，不为无重叠的天生成多余列。

    week / day 用于过滤：week 只保留该周上课的课程，day 只渲染该天
    （如「/timetable -c 软件B241 -w 3 -d 一」渲染第 3 周星期一的课表图片）。
    """
    patterns = data.get("ClassTimePatterns", [])
    starts, ends, n = _slot_maps(patterns)
    days = [day - 1] if day is not None else list(range(7))

    # 按天收集课程（按周次过滤），并合并周次连续的重复条目
    by_day: list[list[dict[str, Any]]] = [[] for _ in range(7)]
    for c in data.get("Data", []):
        if week is not None and not _course_in_week(c, week):
            continue
        p0 = starts.get(c.get("TimeSlotStart"))
        p1 = ends.get(c.get("TimeSlotEnd"))
        if not p0 or not p1:
            continue
        for d in days:
            if c.get(DAY_KEYS[d]):
                by_day[d].append({**c, "_p0": p0, "_p1": p1})

    day_courses = [_merge_week_runs(by_day[d]) for d in range(7)]

    # 按 (起始节, 结束节) 分组：同段课程合并为同一格内的堆叠块
    groups: list[list[dict[str, Any]]] = []
    for d in range(7):
        gmap: dict[tuple[int, int], dict[str, Any]] = {}
        order: list[tuple[int, int]] = []
        for c in day_courses[d]:
            k = (c["_p0"], c["_p1"])
            if k not in gmap:
                gmap[k] = {"p0": k[0], "p1": k[1], "courses": []}
                order.append(k)
            gmap[k]["courses"].append(c)
        gs = [gmap[k] for k in order]
        gs.sort(key=lambda g: (g["p0"], g["p1"] - g["p0"]))
        groups.append(gs)

    # 轨道分配（组层面）：同轨道内各组时段两两不重叠
    tracks: list[list[list[dict[str, Any]]]] = []
    for d in range(7):
        day_tracks: list[list[dict[str, Any]]] = []
        for g in groups[d]:
            placed = False
            for track in day_tracks:
                if all(g["p1"] < t["p0"] or g["p0"] > t["p1"] for t in track):
                    track.append(g)
                    placed = True
                    break
            if not placed:
                day_tracks.append([g])
        for track in day_tracks:
            track.sort(key=lambda g: g["p0"])
        tracks.append(day_tracks)

    head = "".join(
        f'<th colspan="{max(len(tracks[d]), 1)}">{DAYS[d]}</th>' for d in days
    )
    body_rows = []
    next_free = [[0] * len(tracks[d]) for d in range(7)]
    for i in range(n):
        tds = [f'<td class="period">{i + 1}</td>']
        for d in days:
            if not tracks[d]:
                # 当天无任何轨道（如查询无课的某天），补空白列对齐表头
                tds.append('<td class="daycell"></td>')
            for t in range(len(tracks[d])):
                if i < next_free[d][t]:
                    continue
                cur = next((g for g in tracks[d][t] if g["p0"] == i + 1), None)
                if cur is None:
                    tds.append('<td class="daycell"></td>')
                    next_free[d][t] = i + 1
                else:
                    span = cur["p1"] - cur["p0"] + 1
                    # 指定周次查询时，整表已按该周过滤，课程卡片不再显示周次行
                    inner = "".join(
                        _course_block(c, show_weeks=week is None) for c in cur["courses"]
                    )
                    tds.append(
                        f'<td class="daycell" rowspan="{span}">'
                        f'<div class="group">{inner}</div></td>'
                    )
                    next_free[d][t] = i + span
        body_rows.append(f'<tr>{"".join(tds)}</tr>')

    sub = sem_name
    extra = []
    if week is not None:
        extra.append(f"第{week}周")
    if day is not None:
        extra.append(DAYS[day - 1])
    if extra:
        sub += " · " + " ".join(extra)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body {{ margin: 0; padding: 16px; background: #f5f7fa;
       font-family: 'Noto Sans CJK SC', 'Noto Serif CJK SC', 'AR PL UMing CN', sans-serif; }}
h2 {{ margin: 0; font-size: 20px; text-align: center; color: #1f2d3d; }}
.sub {{ text-align: center; color: #7f8c9b; font-size: 13px; margin: 6px 0 12px; }}
table {{ border-collapse: collapse; margin: 0 auto; background: #fff; }}
th, td {{ border: 1px solid #c9d4e0; padding: 4px 6px; text-align: center; vertical-align: middle; }}
th {{ background: #34495e; color: #fff; font-size: 14px; font-weight: 600; }}
td.period {{ background: #eef2f7; font-weight: bold; width: 34px; color: #34495e; height: 46px; }}
td.daycell {{ min-width: 64px; }}
.course {{ padding: 2px 0; }}
.group {{ display: flex; flex-direction: column; justify-content: center; }}
.c-name {{ font-size: 13px; font-weight: 700; color: #16537e; word-break: break-all; }}
.c-meta {{ font-size: 11px; color: #5d6d7e; line-height: 1.45; word-break: break-all; }}
</style></head><body>
<h2>{html.escape(class_name)} 课表</h2>
<div class="sub">{html.escape(sub)}</div>
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


# GNU 风格选项表：-缩写 / --全名 -> 内部键
_OPTIONS = {
    "-c": "class", "--class": "class",
    "-s": "semester", "--semester": "semester",
    "-w": "week", "--week": "week",
    "-d": "day", "--day": "day",
    "-h": "help", "--help": "help",
}


def _parse_options(args_text: str) -> tuple[dict[str, str] | None, str | None]:
    """解析 GNU 风格选项（-x 值 / --xxx 值 / --xxx=值）。

    严格选项制：不接受位置参数；未知选项、选项缺值均返回错误提示。
    返回 (opts, None) 或 (None, 错误消息)。
    """
    tokens = args_text.split()
    opts: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        key, sep, inline = tok.partition("=")
        name = _OPTIONS.get(key) if sep else _OPTIONS.get(tok)
        if name is None:
            if tok.startswith("-"):
                return None, f"未知选项「{tok}」。\n{USAGE}"
            return None, f"不支持位置参数「{tok}」，请使用选项形式（如 -c {tok}）。\n{USAGE}"
        if name == "help":
            opts["help"] = "1"
            i += 1
            continue
        if sep:
            opts[name] = inline
            i += 1
        else:
            if i + 1 >= len(tokens):
                return None, f"选项 {tok} 缺少值。\n{USAGE}"
            opts[name] = tokens[i + 1]
            i += 2
    return opts, None


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
    opts, err = _parse_options(args_text)
    if err is not None:
        await bot.send(event, MessageSegment.text(err))
        return
    assert opts is not None
    if not opts or opts.get("help"):
        await bot.send(event, MessageSegment.text(USAGE))
        return

    class_name = opts.get("class", "").strip()
    if not class_name:
        await bot.send(
            event, MessageSegment.text(f"缺少必填选项 -c/--class <班级>。\n{USAGE}")
        )
        return

    week: int | None = None
    if "week" in opts:
        week = _parse_week(opts["week"])
        if week is None:
            await bot.send(
                event,
                MessageSegment.text(
                    f"周次「{opts['week']}」无法识别，请填数字（如 3、第3周）。\n{USAGE}"
                ),
            )
            return

    day: int | None = None
    if "day" in opts:
        day = _parse_day(opts["day"])
        if day is None:
            await bot.send(
                event,
                MessageSegment.text(
                    f"星期「{opts['day']}」无法识别，请填 星期一/周一/一/1 这样的格式。\n{USAGE}"
                ),
            )
            return

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            sems = await _get_semesters(client)
            if not sems:
                await bot.send(event, MessageSegment.text("获取学期列表失败，请稍后再试。"))
                return

            # -s 指定学期则模糊匹配，省略则取当前学期（列表第一项）
            sem: dict[str, Any] | None = None
            sem_query = opts.get("semester")
            if sem_query:
                matches = _match_semester(sems, sem_query)
                if not matches:
                    await bot.send(
                        event,
                        MessageSegment.text(
                            f"学期「{sem_query}」没有匹配项，请检查学期名称（如 2025-2026春季）。\n{USAGE}"
                        ),
                    )
                    return
                if len(matches) > 1:
                    options = "\n".join(s["Name"] for s in matches)
                    await bot.send(
                        event,
                        MessageSegment.text(f"学期「{sem_query}」匹配到多个，请写完整一些:\n{options}"),
                    )
                    return
                sem = matches[0]
            else:
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

            try:
                html_str = build_html(class_name, sem["Name"], data, week=week, day=day)
                img = await render_html(html_str, width=1140, device_pixel_ratio=2.0)
                await bot.send(
                    event,
                    MessageSegment.file_image(img.data, f"{class_name}课表.png"),
                )
            except Exception:
                logger.exception("课表图片渲染失败，改用文本回复")
                if week is None and day is None:
                    text = build_text(class_name, sem["Name"], data)
                else:
                    text = build_filtered_text(class_name, sem["Name"], data, week, day)
                await bot.send(event, MessageSegment.text(text))
    except httpx.HTTPError:
        logger.exception("查询课表接口请求失败")
        await bot.send(event, MessageSegment.text("查询课表失败（网络错误），请稍后再试。"))
    except Exception:
        logger.exception("查询课表发生未知错误")
        await bot.send(event, MessageSegment.text("查询课表失败，请稍后再试。"))


@timetable.handle()
async def _handler(bot: Bot, event: Event, args: Message = CommandArg()) -> None:
    await _handle_query(bot, event, str(args))


@today.handle()
async def _today_handler(bot: Bot, event: Event, args: Message = CommandArg()) -> None:
    args_text = str(args)
    opts, err = _parse_options(args_text)
    if err is not None:
        await bot.send(event, MessageSegment.text(err))
        return
    assert opts is not None
    if not opts or opts.get("help"):
        await bot.send(event, MessageSegment.text(USAGE))
        return

    class_name = opts.get("class", "").strip()
    if not class_name:
        await bot.send(
            event, MessageSegment.text(f"缺少必填选项 -c/--class <班级>。\n{USAGE}")
        )
        return

    auto_week, auto_day = _current_week_and_day()

    if "week" in opts:
        week = _parse_week(opts["week"])
        if week is None:
            await bot.send(
                event,
                MessageSegment.text(
                    f"周次「{opts['week']}」无法识别，请填数字（如 3、第3周）。\n{USAGE}"
                ),
            )
            return
    else:
        week = auto_week

    if "day" in opts:
        day = _parse_day(opts["day"])
        if day is None:
            await bot.send(
                event,
                MessageSegment.text(
                    f"星期「{opts['day']}」无法识别，请填 星期一/周一/一/1 这样的格式。\n{USAGE}"
                ),
            )
            return
    else:
        day = auto_day

    await _handle_query(bot, event, f"-c {class_name} -w {week} -d {day}")


# --- 指令面板自动注册 ---
# 机器人上线时自动在 c2c（单聊）/ group（群聊）场景注册指令面板，
# 用户点击后面板指令自动填入聊天输入框（仅 /timetable，不带参数）。
# 通过 remark 标识做幂等：已存在且内容一致则跳过，内容变化则更新，
# 避免每次重启都新建面板（一个机器人最多 20 个面板）。

_PANEL_MARKER = "timetable-plugin-auto"
_PANEL_SCOPES = ("c2c", "group")


def _build_panel() -> qq_panels.Panel:
    return qq_panels.Panel(
        items=[
            qq_panels.PanelItem(type="command", name="/timetable", desc="查询课表"),
        ],
        remark=_PANEL_MARKER,
    )


def _panel_items_same(
    a: list[qq_panels.PanelItem] | None, b: list[qq_panels.PanelItem] | None
) -> bool:
    """比较两组面板元素内容是否一致（忽略 None 字段，按顺序逐项比较）。"""
    norm = lambda items: [i.model_dump(exclude_none=True) for i in (items or [])]
    return norm(a) == norm(b)


async def _ensure_panel(bot: Bot, scope: str) -> None:
    """确保指定场景存在内容最新的课表指令面板（幂等）。"""
    panel = _build_panel()
    try:
        result = await bot.get_panels(scope=scope, limit=50)
    except Exception:
        logger.exception(f"查询 {scope} 场景指令面板列表失败")
        return

    existing = next(
        (r for r in result.records if r.panel and r.panel.remark == _PANEL_MARKER),
        None,
    )
    if existing is None:
        try:
            ret = await bot.post_panels(scope=scope, target_type="all", panel=panel)
            logger.info(f"{scope} 指令面板已创建: {ret.panel_id}")
        except Exception:
            logger.exception(f"创建 {scope} 指令面板失败")
        return

    if existing.panel and _panel_items_same(existing.panel.items, panel.items):
        logger.debug(f"{scope} 指令面板已存在且内容一致，跳过: {existing.panel_id}")
        return

    try:
        await bot.put_panel(panel_id=existing.panel_id, panel=panel)
        logger.info(f"{scope} 场景指令面板已更新: {existing.panel_id}")
    except Exception:
        logger.exception(f"更新 {scope} 场景指令面板失败")


@get_driver().on_bot_connect
async def _register_timetable_panels(bot: Bot) -> None:
    """机器人连接成功后自动注册 c2c / 群聊指令面板。"""
    if not isinstance(bot, Bot):
        return
    for scope in _PANEL_SCOPES:
        await _ensure_panel(bot, scope)
