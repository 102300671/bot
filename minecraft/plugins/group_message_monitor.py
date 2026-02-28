import json
import logging
from pathlib import Path
from typing import Dict, List

from nonebot import on_message, on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.rule import to_me
from nonebot.params import CommandArg

CONFIG_PATH = Path(__file__).parent.parent / "config" / "group_message_monitor.json"


def load_config() -> Dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"monitored_groups": []}


def save_config(config: Dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)


group_monitor = on_message(priority=5, block=False)


@group_monitor.handle()
async def handle_group_message(bot: Bot, event: GroupMessageEvent):
    config = load_config()
    
    group_id = str(event.group_id)
    user_id = str(event.user_id)
    
    for group_config in config.get("monitored_groups", []):
        if group_config.get("group_id") == group_id:
            monitored_users = group_config.get("monitored_users", [])
            notify_users = group_config.get("notify_users", [])
            
            if user_id in monitored_users:
                message = event.get_message()
                message_text = message.extract_plain_text()
                
                user_info = await bot.get_group_member_info(
                    group_id=int(group_id),
                    user_id=int(user_id)
                )
                user_name = user_info.get("card") or user_info.get("nickname") or user_id
                
                notify_msg = f"[群消息通知]\n"
                notify_msg += f"群号: {group_id}\n"
                notify_msg += f"发送者: {user_name}({user_id})\n"
                notify_msg += f"内容: {message_text}"
                
                for notify_qq in notify_users:
                    try:
                        await bot.send_private_msg(
                            user_id=int(notify_qq),
                            message=notify_msg
                        )
                        logging.info(f"[群消息监控] 已转发 {group_id} 群中 {user_name} 的消息给 {notify_qq}")
                    except Exception as e:
                        logging.error(f"[群消息监控] 发送消息给 {notify_qq} 失败: {e}")
                
                break


add_monitor_cmd = on_command("添加群监控", aliases={"addgroupmonitor"}, priority=10, block=True)
remove_monitor_cmd = on_command("移除群监控", aliases={"removegroupmonitor"}, priority=10, block=True)
list_monitor_cmd = on_command("群监控列表", aliases={"groupmonitorlist"}, priority=10, block=True)


@add_monitor_cmd.handle()
async def handle_add_monitor(args = CommandArg()):
    args_text = args.extract_plain_text().strip()
    parts = args_text.split()
    
    if len(parts) < 3:
        await add_monitor_cmd.finish("格式错误！\n用法: /添加群监控 群号 监控用户QQ 通知用户QQ\n多个用户用逗号分隔")
    
    group_id = parts[0]
    monitored_users = parts[1].split(",")
    notify_users = parts[2].split(",")
    
    config = load_config()
    
    existing_group = None
    for group in config.get("monitored_groups", []):
        if group.get("group_id") == group_id:
            existing_group = group
            break
    
    if existing_group:
        for user in monitored_users:
            if user not in existing_group.get("monitored_users", []):
                existing_group.setdefault("monitored_users", []).append(user)
        for user in notify_users:
            if user not in existing_group.get("notify_users", []):
                existing_group.setdefault("notify_users", []).append(user)
    else:
        config.setdefault("monitored_groups", []).append({
            "group_id": group_id,
            "monitored_users": monitored_users,
            "notify_users": notify_users
        })
    
    save_config(config)
    await add_monitor_cmd.finish(f"已添加群监控配置\n群号: {group_id}\n监控用户: {', '.join(monitored_users)}\n通知用户: {', '.join(notify_users)}")


@remove_monitor_cmd.handle()
async def handle_remove_monitor(args = CommandArg()):
    args_text = args.extract_plain_text().strip()
    parts = args_text.split()
    
    if len(parts) < 2:
        await remove_monitor_cmd.finish("格式错误！\n用法: /移除群监控 群号 监控用户QQ\n多个用户用逗号分隔")
    
    group_id = parts[0]
    users_to_remove = parts[1].split(",")
    
    config = load_config()
    
    for group in config.get("monitored_groups", []):
        if group.get("group_id") == group_id:
            monitored_users = group.get("monitored_users", [])
            for user in users_to_remove:
                if user in monitored_users:
                    monitored_users.remove(user)
            
            if not monitored_users:
                config["monitored_groups"].remove(group)
                await remove_monitor_cmd.finish(f"已移除群 {group_id} 的所有监控配置")
            else:
                save_config(config)
                await remove_monitor_cmd.finish(f"已从群 {group_id} 移除监控: {', '.join(users_to_remove)}")
            break
    else:
        await remove_monitor_cmd.finish(f"未找到群 {group_id} 的监控配置")


@list_monitor_cmd.handle()
async def handle_list_monitor():
    config = load_config()
    groups = config.get("monitored_groups", [])
    
    if not groups:
        await list_monitor_cmd.finish("当前没有群监控配置")
    
    msg = "群监控列表:\n\n"
    for idx, group in enumerate(groups, 1):
        msg += f"{idx}. 群号: {group.get('group_id')}\n"
        msg += f"   监控用户: {', '.join(group.get('monitored_users', []))}\n"
        msg += f"   通知用户: {', '.join(group.get('notify_users', []))}\n\n"
    
    await list_monitor_cmd.finish(msg)
