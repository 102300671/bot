import json
import asyncio
import logging
from pathlib import Path
from typing import Dict, Set, Optional

from nonebot import get_driver, get_bot, on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Message, PrivateMessageEvent
from nonebot.typing import T_State
from nonebot.params import CommandArg
import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

CONFIG_PATH = Path(__file__).parent.parent / "config" / "player_monitor.json"
API_URL = "https://api.mcsrvstat.us/3/{server}"

scheduler = AsyncIOScheduler()
online_players_cache: Set[str] = set()
config: Dict = {}


def load_config() -> Dict:
    global config
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = {
            "server_address": "",
            "check_interval_minutes": 1,
            "monitored_players": [],
            "notify_qq_list": [],
            "online_status": {}
        }
        save_config()
    return config


def save_config():
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)


async def get_server_players(server: str) -> Optional[Set[str]]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(API_URL.format(server=server), timeout=10) as response:
                if response.ok:
                    data = await response.json()
                    if data.get("online", False):
                        players = data.get("players", {})
                        player_list = players.get("list", [])
                        if isinstance(player_list, list):
                            return {p.get("name", "") if isinstance(p, dict) else str(p) for p in player_list}
                        return set()
                    else:
                        # 服务器不在线或API返回错误
                        error_msg = data.get("error", {}).get("ip", "未知错误")
                        logging.error(f"[玩家监控] 服务器 {server} 不在线或API返回错误: {error_msg}")
                        return set()
    except aiohttp.ClientError as e:
        # 网络相关错误，包括DNS解析失败
        logging.error(f"[玩家监控] 网络错误，无法连接到服务器 {server}: {e}")
    except Exception as e:
        logging.error(f"[玩家监控] 获取服务器玩家列表失败: {e}")
    return None


async def check_players():
    global online_players_cache, config
    
    config = load_config()
    server = config.get("server_address", "")
    monitored = set(config.get("monitored_players", []))
    notify_list = config.get("notify_qq_list", [])
    
    if not server or not monitored or not notify_list:
        return
    
    current_players = await get_server_players(server)
    if current_players is None:
        return
    
    for player in monitored:
        is_online = player in current_players
        was_online = player in online_players_cache
        
        if is_online and not was_online:
            message = f"[MC监控] 玩家 {player} 已上线！"
            logging.info(f"[玩家监控] {player} 上线")
            await send_notification(notify_list, message)
        elif not is_online and was_online:
            logging.info(f"[玩家监控] {player} 下线")
    
    online_players_cache = current_players & monitored


async def send_notification(qq_list: list, message: str):
    try:
        bot: Bot = get_bot()
        for qq in qq_list:
            try:
                await bot.send_private_msg(user_id=int(qq), message=message)
            except Exception as e:
                logging.error(f"[玩家监控] 发送消息给 {qq} 失败: {e}")
    except Exception as e:
        logging.error(f"[玩家监控] 获取bot失败: {e}")


add_player_cmd = on_command("添加监控", aliases={"addmonitor"}, priority=10, block=True)
remove_player_cmd = on_command("移除监控", aliases={"removemonitor"}, priority=10, block=True)
list_player_cmd = on_command("监控列表", aliases={"monitorlist"}, priority=10, block=True)
check_now_cmd = on_command("立即检查", aliases={"checknow"}, priority=10, block=True)


@add_player_cmd.handle()
async def handle_add_player(event: PrivateMessageEvent, args: Message = CommandArg()):
    player_name = args.extract_plain_text().strip()
    if not player_name:
        await add_player_cmd.finish("请提供玩家名称，例如：/添加监控 Steve")
    
    config = load_config()
    if player_name not in config["monitored_players"]:
        config["monitored_players"].append(player_name)
        save_config()
        await add_player_cmd.finish(f"已添加玩家 {player_name} 到监控列表")
    else:
        await add_player_cmd.finish(f"玩家 {player_name} 已在监控列表中")


@remove_player_cmd.handle()
async def handle_remove_player(event: PrivateMessageEvent, args: Message = CommandArg()):
    player_name = args.extract_plain_text().strip()
    if not player_name:
        await remove_player_cmd.finish("请提供玩家名称，例如：/移除监控 Steve")
    
    config = load_config()
    if player_name in config["monitored_players"]:
        config["monitored_players"].remove(player_name)
        if player_name in online_players_cache:
            online_players_cache.remove(player_name)
        save_config()
        await remove_player_cmd.finish(f"已从监控列表移除玩家 {player_name}")
    else:
        await remove_player_cmd.finish(f"玩家 {player_name} 不在监控列表中")


@list_player_cmd.handle()
async def handle_list_player(event: PrivateMessageEvent):
    config = load_config()
    players = config.get("monitored_players", [])
    server = config.get("server_address", "未配置")
    interval = config.get("check_interval_minutes", 1)
    
    msg = f"服务器: {server}\n"
    msg += f"检查间隔: {interval} 分钟\n"
    msg += f"监控玩家: {', '.join(players) if players else '无'}\n"
    msg += f"当前在线: {', '.join(online_players_cache) if online_players_cache else '无'}"
    
    await list_player_cmd.finish(msg)


@check_now_cmd.handle()
async def handle_check_now(event: PrivateMessageEvent):
    await check_players()
    config = load_config()
    players = config.get("monitored_players", [])
    current_online = [p for p in players if p in online_players_cache]
    
    msg = f"检查完成！\n当前在线玩家: {', '.join(current_online) if current_online else '无'}"
    await check_now_cmd.finish(msg)


driver = get_driver()


@driver.on_startup
async def start_scheduler():
    config = load_config()
    interval = config.get("check_interval_minutes", 1)
    
    scheduler.add_job(
        check_players,
        IntervalTrigger(minutes=interval),
        id="player_monitor",
        replace_existing=True
    )
    
    if not scheduler.running:
        scheduler.start()
        logging.info(f"[玩家监控] 定时任务已启动，检查间隔: {interval} 分钟")


@driver.on_shutdown
async def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logging.info("[玩家监控] 定时任务已停止")
