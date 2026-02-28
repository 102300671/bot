from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
import sys
sys.path.insert(0, '/home/jianying/code/bot/qqbot/yuri')
from config.bot_scope_config import bot_scope_config

bot_scope_cmd = on_command("bot范围", priority=5, block=True)
add_group_cmd = on_command("添加群", priority=5, block=True)
remove_group_cmd = on_command("移除群", priority=5, block=True)
add_user_cmd = on_command("添加用户", priority=5, block=True)
remove_user_cmd = on_command("移除用户", priority=5, block=True)
set_mode_cmd = on_command("设置模式", priority=5, block=True)
add_admin_cmd = on_command("添加管理员", priority=5, block=True)
remove_admin_cmd = on_command("移除管理员", priority=5, block=True)

@bot_scope_cmd.handle()
async def handle_bot_scope(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = int(event.user_id)
    
    if not bot_scope_config.is_admin(user_id):
        await bot_scope_cmd.finish("（只有管理员才能查看bot范围配置~）")
    
    status = bot_scope_config.get_status()
    
    msg = f"""Bot启用范围配置：
模式：{status['mode']}
启用群聊：{status['enabled_groups']}
启用用户：{status['enabled_users']}
禁用群聊：{status['disabled_groups']}
禁用用户：{status['disabled_users']}
管理员：{status['admin_users']}

使用方法：
- 添加群 <群号>：添加群聊到启用列表
- 移除群 <群号>：从启用列表移除群聊
- 添加用户 <用户ID>：添加用户到启用列表
- 移除用户 <用户ID>：从启用列表移除用户
- 设置模式 <whitelist/blacklist>：设置启用模式
- 添加管理员 <用户ID>：添加管理员
- 移除管理员 <用户ID>：移除管理员
"""
    
    await bot_scope_cmd.finish(msg)

@add_group_cmd.handle()
async def handle_add_group(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = int(event.user_id)
    
    if not bot_scope_config.is_admin(user_id):
        await add_group_cmd.finish("（只有管理员才能添加群聊~）")
    
    group_id_str = args.extract_plain_text().strip()
    if not group_id_str:
        await add_group_cmd.finish("（请提供群号，例如：添加群 123456789）")
    
    try:
        group_id = int(group_id_str)
        bot_scope_config.add_enabled_group(group_id)
        await add_group_cmd.finish(f"（已将群 {group_id} 添加到启用列表~）")
    except ValueError:
        await add_group_cmd.finish("（群号格式不正确，请提供数字群号~）")

@remove_group_cmd.handle()
async def handle_remove_group(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = int(event.user_id)
    
    if not bot_scope_config.is_admin(user_id):
        await remove_group_cmd.finish("（只有管理员才能移除群聊~）")
    
    group_id_str = args.extract_plain_text().strip()
    if not group_id_str:
        await remove_group_cmd.finish("（请提供群号，例如：移除群 123456789）")
    
    try:
        group_id = int(group_id_str)
        bot_scope_config.remove_enabled_group(group_id)
        await remove_group_cmd.finish(f"（已将群 {group_id} 从启用列表移除~）")
    except ValueError:
        await remove_group_cmd.finish("（群号格式不正确，请提供数字群号~）")

@add_user_cmd.handle()
async def handle_add_user(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = int(event.user_id)
    
    if not bot_scope_config.is_admin(user_id):
        await add_user_cmd.finish("（只有管理员才能添加用户~）")
    
    user_id_str = args.extract_plain_text().strip()
    if not user_id_str:
        await add_user_cmd.finish("（请提供用户ID，例如：添加用户 123456789）")
    
    try:
        target_user_id = int(user_id_str)
        bot_scope_config.add_enabled_user(target_user_id)
        await add_user_cmd.finish(f"（已将用户 {target_user_id} 添加到启用列表~）")
    except ValueError:
        await add_user_cmd.finish("（用户ID格式不正确，请提供数字ID~）")

@remove_user_cmd.handle()
async def handle_remove_user(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = int(event.user_id)
    
    if not bot_scope_config.is_admin(user_id):
        await remove_user_cmd.finish("（只有管理员才能移除用户~）")
    
    user_id_str = args.extract_plain_text().strip()
    if not user_id_str:
        await remove_user_cmd.finish("（请提供用户ID，例如：移除用户 123456789）")
    
    try:
        target_user_id = int(user_id_str)
        bot_scope_config.remove_enabled_user(target_user_id)
        await remove_user_cmd.finish(f"（已将用户 {target_user_id} 从启用列表移除~）")
    except ValueError:
        await remove_user_cmd.finish("（用户ID格式不正确，请提供数字ID~）")

@set_mode_cmd.handle()
async def handle_set_mode(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = int(event.user_id)
    
    if not bot_scope_config.is_admin(user_id):
        await set_mode_cmd.finish("（只有管理员才能设置模式~）")
    
    mode = args.extract_plain_text().strip().lower()
    if mode not in ['whitelist', 'blacklist']:
        await set_mode_cmd.finish("（模式只能是 whitelist（白名单）或 blacklist（黑名单）~）")
    
    bot_scope_config.set_mode(mode)
    mode_name = "白名单" if mode == "whitelist" else "黑名单"
    await set_mode_cmd.finish(f"（已将模式设置为 {mode_name}~）")

@add_admin_cmd.handle()
async def handle_add_admin(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = int(event.user_id)
    
    if not bot_scope_config.is_admin(user_id):
        await add_admin_cmd.finish("（只有管理员才能添加其他管理员~）")
    
    user_id_str = args.extract_plain_text().strip()
    if not user_id_str:
        await add_admin_cmd.finish("（请提供用户ID，例如：添加管理员 123456789）")
    
    try:
        target_user_id = int(user_id_str)
        bot_scope_config.add_admin(target_user_id)
        await add_admin_cmd.finish(f"（已将用户 {target_user_id} 设置为管理员~）")
    except ValueError:
        await add_admin_cmd.finish("（用户ID格式不正确，请提供数字ID~）")

@remove_admin_cmd.handle()
async def handle_remove_admin(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = int(event.user_id)
    
    if not bot_scope_config.is_admin(user_id):
        await remove_admin_cmd.finish("（只有管理员才能移除其他管理员~）")
    
    user_id_str = args.extract_plain_text().strip()
    if not user_id_str:
        await remove_admin_cmd.finish("（请提供用户ID，例如：移除管理员 123456789）")
    
    try:
        target_user_id = int(user_id_str)
        bot_scope_config.remove_admin(target_user_id)
        await remove_admin_cmd.finish(f"（已将用户 {target_user_id} 从管理员列表移除~）")
    except ValueError:
        await remove_admin_cmd.finish("（用户ID格式不正确，请提供数字ID~）")
