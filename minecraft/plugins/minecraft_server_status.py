from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Message, MessageSegment
from nonebot.typing import T_State
from nonebot.params import CommandArg
import aiohttp
import json
import logging
import asyncio

# ================= 配置 =================
API_URL = "https://api.mcsrvstat.us/3/{server}"
BOT_PREFIX = "服务器状态："

# ================= 命令定义 =================
mc_status_cmd = on_command("mcstatus", aliases={"mc状态", "服务器状态", "mc服务器"}, priority=10, block=True)

# ================= 服务器状态检查 =================
async def check_server_status(server: str) -> dict:
    """检查Minecraft服务器状态"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(API_URL.format(server=server), timeout=10) as response:
                if response.ok:
                    data = await response.json()
                    return data
                else:
                    logging.error(f"API请求失败: {response.status}")
                    return {"error": f"API请求失败: {response.status}"}
    except aiohttp.ClientError as e:
        logging.error(f"网络错误: {str(e)}")
        return {"error": f"网络错误: {str(e)}"}
    except asyncio.TimeoutError:
        logging.error("请求超时")
        return {"error": "请求超时"}
    except Exception as e:
        logging.error(f"未知错误: {str(e)}")
        return {"error": f"未知错误: {str(e)}"}

# ================= 格式化状态信息 =================
def format_status(data: dict) -> str:
    """格式化服务器状态信息"""
    if "error" in data:
        return f"{BOT_PREFIX}错误: {data['error']}"
    
    if not data.get("online", False):
        return f"{BOT_PREFIX}{data.get('hostname', '服务器')} 离线"
    
    server_name = data.get('hostname', '服务器')
    ip = data.get('ip', '未知IP')
    port = data.get('port', '未知端口')
    version = data.get('version', '未知版本')
    players = data.get('players', {})
    online_players = players.get('online', 0)
    max_players = players.get('max', 0)
    motd = data.get('motd', {})
    clean_motd = ' '.join(motd.get('clean', ['无描述']))
    
    status = f"{BOT_PREFIX}{server_name}\n"
    status += f"状态: 在线\n"
    status += f"IP: {ip}:{port}\n"
    status += f"版本: {version}\n"
    status += f"玩家: {online_players}/{max_players}\n"
    status += f"描述: {clean_motd}"
    
    return status

# ================= 命令处理 =================
@mc_status_cmd.handle()
async def handle_mc_status(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """处理服务器状态查询命令"""
    server = args.extract_plain_text().strip()
    if not server:
        await bot.send(event, f"{BOT_PREFIX}请提供服务器地址，例如：/mcstatus mc.example.com:25565")
        return
    
    try:
        status_data = await check_server_status(server)
        status_message = format_status(status_data)
        await bot.send(event, status_message)
    except Exception as e:
        logging.error(f"处理命令时出错: {str(e)}")
        await bot.send(event, f"{BOT_PREFIX}处理请求时出错: {str(e)}")

# ================= 模块初始化 =================
def __init__():
    """模块初始化"""
    logging.info("Minecraft服务器状态插件已加载")

__init__()
