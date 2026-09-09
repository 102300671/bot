"""QQ 官方机器人「指令面板」OpenAPI 封装。

nonebot-adapter-qq 1.7.2 未内置指令面板接口，本模块按适配器内置 API 的同样约定
（Request + Bot._request，鉴权头与错误码处理自动复用）把 6 个面板接口挂载到
nonebot.adapters.qq.Bot 上。

本文件位于项目根目录（运行目录，在 sys.path 上），由 plugins/timetable.py 以
`import qq_panels` 导入；不要放进 plugins/ 目录——nonebot 会把 plugins/ 下的文件
按「plugins.xxx」命名空间包加载，裸 import 无法解析同目录模块。

接口文档:
- 创建:   POST   /v2/panels                 https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_panels.post.html
- 列表:   GET    /v2/panels                 https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_panels.get.html
- 详情:   GET    /v2/panels/{panel_id}      https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_panels_panel_id.get.html
- 修改:   PUT    /v2/panels/{panel_id}      https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_panels_panel_id.put.html
- 删除:   DELETE /v2/panels/{panel_id}      https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_panels_panel_id.delete.html
- 关联:   PUT    /v2/panels/{panel_id}/target  修改面板关联的用户/群（add/del）

用法:
    import qq_panels
    from nonebot.adapters.qq import Bot

    panel = qq_panels.Panel(items=[
        qq_panels.PanelItem(type="command", name="/timetable", desc="查询课表"),
        qq_panels.PanelItem(type="link", name="更多服务", link="https://example.com"),
    ], remark="课表面板")

    ret = await bot.post_panels(scope="c2c", target_type="all", panel=panel)
    print(ret.panel_id)
"""

from datetime import datetime
from typing import Literal

from nonebot.compat import type_validate_python
from nonebot.drivers import Request
from pydantic import BaseModel

from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.utils import exclude_none

#: 生效场景：c2c 单聊 / group 群聊 / channel 文字子频道 / dm 频道私信
PanelScope = Literal["c2c", "group", "channel", "dm"]
#: 作用范围：all 全局 / specific 指定用户或群（仅 c2c、group 支持 specific）
PanelTargetType = Literal["all", "specific"]
#: 面板元素类型：command 指令（点击填入输入框）/ link 链接跳转
PanelItemType = Literal["command", "link"]
#: 关联对象操作：add 添加 / del 移除
PanelTargetOp = Literal["add", "del"]


class PanelItem(BaseModel):
    """面板元素。一个面板最多 20 个。"""

    type: PanelItemType
    """元素类型：command 指令 / link 链接。"""
    name: str | None = None
    """元素名称，最多 14 个字符（约 7 个汉字）；command 时点击填入输入框。"""
    desc: str | None = None
    """元素描述，最多 30 个字符（约 15 个汉字）。"""
    only_admin: bool | None = None
    """是否仅管理员可点击。"""
    link: str | None = None
    """跳转链接（仅 type=link 有效），必须以 https:// 开头。"""


class Panel(BaseModel):
    """面板配置内容。"""

    items: list[PanelItem] | None = None
    """面板元素列表，最多 20 个。"""
    remark: str | None = None
    """面板备注（仅开发者可见，最多 255 字符）。"""
    version: int | None = None
    """版本号。"""


class PostPanelsReturn(BaseModel):
    """创建面板响应。"""

    panel_id: str
    """新创建的面板 ID。"""


class PanelRecord(BaseModel):
    """面板记录（列表/详情接口返回；openids 仅详情且 specific 时返回）。"""

    panel_id: str
    scope: PanelScope | None = None
    target_type: PanelTargetType | None = None
    panel: Panel | None = None
    version: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    user_openids: list[str] | None = None
    group_openids: list[str] | None = None


class GetPanelsReturn(BaseModel):
    """面板列表响应。"""

    records: list[PanelRecord] = []
    next_cursor: str | None = None
    """下一页游标，空串表示无更多数据。"""
    is_end: bool | None = None


class PutPanelReturn(BaseModel):
    """修改面板响应。"""

    version: int | None = None
    """修改后的面板版本号。"""


async def post_panels(
    self: Bot,
    *,
    scope: PanelScope,
    panel: Panel,
    target_type: PanelTargetType | None = None,
    user_openids: list[str] | None = None,
    group_openids: list[str] | None = None,
) -> PostPanelsReturn:
    """创建指令面板。一个机器人最多 20 个，接口限频 10 QPM。

    scope 为 channel/dm 时 target_type 只能为 all；
    user_openids 仅 c2c + specific 有效，group_openids 仅 group + specific 有效，
    单次最多 20 个。
    """
    json_data = exclude_none(
        {
            "scope": scope,
            "target_type": target_type,
            "user_openids": user_openids,
            "group_openids": group_openids,
            "panel": panel.model_dump(exclude_none=True),
        }
    )
    request = Request(
        "POST",
        self.adapter.get_api_base().joinpath("v2", "panels"),
        json=json_data,
    )
    return type_validate_python(PostPanelsReturn, await self._request(request))


async def get_panels(
    self: Bot,
    *,
    scope: PanelScope,
    cursor: str | None = None,
    limit: int | None = None,
) -> GetPanelsReturn:
    """分页查询指定场景的面板列表（按设置时间倒序），限频 30 QPM。

    limit 默认 20、最大 50；next_cursor 为空串且 is_end=True 表示无更多页。
    """
    request = Request(
        "GET",
        self.adapter.get_api_base().joinpath("v2", "panels"),
        params=exclude_none({"scope": scope, "cursor": cursor, "limit": limit}),
    )
    return type_validate_python(GetPanelsReturn, await self._request(request))


async def get_panel(self: Bot, *, panel_id: str) -> PanelRecord:
    """查询面板详情（含关联的 user_openids/group_openids），限频 30 QPM。"""
    request = Request(
        "GET",
        self.adapter.get_api_base().joinpath("v2", "panels", panel_id),
    )
    return type_validate_python(PanelRecord, await self._request(request))


async def put_panel(
    self: Bot,
    *,
    panel_id: str,
    panel: Panel,
) -> PutPanelReturn:
    """修改面板内容（覆盖 items/remark，不影响已关联的用户/群），限频 10 QPM。"""
    request = Request(
        "PUT",
        self.adapter.get_api_base().joinpath("v2", "panels", panel_id),
        json={"panel": panel.model_dump(exclude_none=True)},
    )
    return type_validate_python(PutPanelReturn, await self._request(request))


async def delete_panel(self: Bot, *, panel_id: str) -> None:
    """删除面板，删除后不再对任何用户/群生效，限频 10 QPM。"""
    request = Request(
        "DELETE",
        self.adapter.get_api_base().joinpath("v2", "panels", panel_id),
    )
    await self._request(request)


async def put_panel_target(
    self: Bot,
    *,
    panel_id: str,
    op: PanelTargetOp,
    user_openids: list[str] | None = None,
    group_openids: list[str] | None = None,
) -> None:
    """增删面板关联对象（c2c 操作用户 openid，group 操作群 openid），限频 60 QPM。

    仅 target_type=specific 的面板支持；单次最多 20 个。
    """
    request = Request(
        "PUT",
        self.adapter.get_api_base().joinpath("v2", "panels", panel_id, "target"),
        json=exclude_none(
            {
                "op": op,
                "user_openids": user_openids,
                "group_openids": group_openids,
            }
        ),
    )
    await self._request(request)


# 挂载到 Bot 类：既可 await bot.post_panels(...) 直接调用，
# 也可走 bot.call_api("post_panels", ...) 分发（适配器 _call_api 按类属性查找）。
for _fn in (
    post_panels,
    get_panels,
    get_panel,
    put_panel,
    delete_panel,
    put_panel_target,
):
    setattr(Bot, _fn.__name__, _fn)


__all__ = [
    "PanelScope",
    "PanelTargetType",
    "PanelItemType",
    "PanelTargetOp",
    "PanelItem",
    "Panel",
    "PostPanelsReturn",
    "PanelRecord",
    "GetPanelsReturn",
    "PutPanelReturn",
    "post_panels",
    "get_panels",
    "get_panel",
    "put_panel",
    "delete_panel",
    "put_panel_target",
]
