from nonebot import on_command, get_driver      
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment, GroupMessageEvent, MessageEvent      
from nonebot.params import CommandArg      
from nonebot.exception import FinishedException      
from nonebot.rule import to_me      
from datetime import datetime, timedelta      
import asyncio      
import logging      
import nonebot      
from nonebot.plugin import PluginMetadata      
from .concurrent_utils import (
    ConnectionPoolManager, 
    get_user_lock, 
    retry_with_backoff, 
    db_transaction,
    task_manager,
    RateLimiter
)

# 插件元数据      
__plugin_meta__ = PluginMetadata(      
    name="签到插件",      
    description="带有积分系统的每日签到系统，支持@机器人触发，每个群聊积分独立",      
    usage="使用命令：/签到、@机器人 签到、积分、积分排行、补签",      
    type="application",      
    homepage="N/A",      
)      
      
# 数据库配置      
DB_CONFIG = {      
    'host': 'localhost',      
    'user': 'signin',      
    'password': 'signin',      
    'db': 'nonebot_signin',      
    'charset': 'utf8mb4',
    'autocommit': True,
    'maxsize': 20,  # 连接池最大连接数
    'minsize': 5,   # 连接池最小连接数
    'pool_recycle': 3600,  # 连接回收时间（秒）
}      

# 全局变量      
BOT_PREFIX = "小豆泥："      
HELP_ENABLED = True      
BOT_ENABLED = True       

# 获取机器人名称（去掉冒号）
BOT_NAME = BOT_PREFIX.strip('：')
# 导入主机器人插件的awaiting_response_users变量，实现状态共享
from .yuri_bot import awaiting_response_users

# 连接池管理器
pool_manager = ConnectionPoolManager(DB_CONFIG)

# 速率限制器
rate_limiter = RateLimiter(max_calls=30, time_window=60.0)  # 每分钟最多30次操作

# 初始化数据库      
async def init_database():      
    try:      
        async with db_transaction(pool_manager) as (conn, cursor):
            # 创建用户表（按群组分离）      
            await cursor.execute('''      
                CREATE TABLE IF NOT EXISTS users (      
                    user_id VARCHAR(50),      
                    group_id VARCHAR(50),      
                    username VARCHAR(100),      
                    total_points INT DEFAULT 0,      
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,      
                    PRIMARY KEY (user_id, group_id),
                    INDEX idx_group_points (group_id, total_points DESC)
                )      
            ''')      
                      
            # 创建签到记录表（按群组分离）      
            await cursor.execute('''      
                CREATE TABLE IF NOT EXISTS sign_records (      
                    id INT AUTO_INCREMENT PRIMARY KEY,      
                    user_id VARCHAR(50),      
                    group_id VARCHAR(50),      
                    sign_date DATE NOT NULL,      
                    points_earned INT DEFAULT 0,      
                    continuous_days INT DEFAULT 1,      
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,      
                    UNIQUE KEY unique_sign (user_id, group_id, sign_date),      
                    FOREIGN KEY (user_id, group_id) REFERENCES users(user_id, group_id) ON DELETE CASCADE,
                    INDEX idx_user_group_date (user_id, group_id, sign_date)
                )      
            ''')      
                      
            # 创建积分流水表（按群组分离）      
            await cursor.execute('''      
                CREATE TABLE IF NOT EXISTS points_history (      
                    id INT AUTO_INCREMENT PRIMARY KEY,      
                    user_id VARCHAR(50),      
                    group_id VARCHAR(50),      
                    points_change INT NOT NULL,      
                    reason VARCHAR(255),      
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,      
                    FOREIGN KEY (user_id, group_id) REFERENCES users(user_id, group_id) ON DELETE CASCADE,
                    INDEX idx_user_group_time (user_id, group_id, created_at DESC)
                )      
            ''')      
                      
        print("数据库初始化成功")      
                  
    except Exception as e:      
        print(f"数据库初始化失败: {e}")      
      
# 获取或创建用户（按群组）      
@retry_with_backoff(max_retries=3, base_delay=0.1)
async def get_or_create_user(user_id: str, group_id: str, username: str = "未知用户"):      
    async with db_transaction(pool_manager) as (conn, cursor):
        # 检查用户是否存在      
        await cursor.execute("SELECT * FROM users WHERE user_id = %s AND group_id = %s", (user_id, group_id))      
        user = await cursor.fetchone()      
                  
        if not user:      
            # 创建新用户      
            await cursor.execute(      
                "INSERT INTO users (user_id, group_id, username) VALUES (%s, %s, %s)",      
                (user_id, group_id, username)      
            )      
                      
            # 重新获取用户信息      
            await cursor.execute("SELECT * FROM users WHERE user_id = %s AND group_id = %s", (user_id, group_id))      
            user = await cursor.fetchone()      
                  
        return user      
      
# 获取用户积分（按群组）      
@retry_with_backoff(max_retries=3, base_delay=0.1)
async def get_user_points(user_id: str, group_id: str) -> int:      
    async with db_transaction(pool_manager) as (conn, cursor):
        await cursor.execute("SELECT total_points FROM users WHERE user_id = %s AND group_id = %s", (user_id, group_id))      
        result = await cursor.fetchone()      
        return result[0] if result else 0      
      
# 更新用户积分（按群组）      
@retry_with_backoff(max_retries=3, base_delay=0.1)
async def update_user_points(user_id: str, group_id: str, points_change: int, reason: str):      
    """异步更新用户积分与流水"""
    async with db_transaction(pool_manager) as (conn, cursor):
        # 锁定用户记录，避免并发导致的锁等待      
        await cursor.execute("SELECT total_points FROM users WHERE user_id = %s AND group_id = %s FOR UPDATE", (user_id, group_id))      
        
        # 更新用户总积分      
        await cursor.execute(      
            "UPDATE users SET total_points = total_points + %s WHERE user_id = %s AND group_id = %s",      
            (points_change, user_id, group_id)      
        )      
        
        # 记录积分流水      
        await cursor.execute(      
            "INSERT INTO points_history (user_id, group_id, points_change, reason) VALUES (%s, %s, %s, %s)",      
            (user_id, group_id, points_change, reason)      
        )      
      
# 获取用户昵称（用于群消息）      
async def get_user_nickname(event: GroupMessageEvent) -> str:      
    try:      
        # 尝试获取群成员信息      
        bot = nonebot.get_bot()      
        member_info = await bot.get_group_member_info(      
            group_id=event.group_id,      
            user_id=event.user_id      
        )      
        return member_info.get('card') or member_info.get('nickname') or "未知用户"      
    except:      
        return "未知用户"      
      
# ================= 消息转发功能 =================      
async def send_as_forward(bot: Bot, event: MessageEvent, content: list | str):      
    if isinstance(content, str):      
        content = [content]      
      
    forward_msg = []      
    for i, line in enumerate(content):      
        if line.strip():      
            forward_msg.append({      
                "type": "node",      
                "data": {      
                    "name": BOT_PREFIX.strip('：'),      
                    "uin": bot.self_id,      
                    "content": f"{BOT_PREFIX}{line}" if i == 0 else line      
                }      
            })      
      
    if hasattr(event, "group_id") and event.group_id:      
        await bot.call_api(      
            "send_group_forward_msg",      
            group_id=event.group_id,      
            messages=forward_msg      
        )      
    else:      
        await bot.call_api(      
            "send_private_forward_msg",      
            user_id=event.user_id,      
            messages=forward_msg      
        )      
      
# ================= 开启/关闭提示命令 =================
enable_notice_cmd = on_command("提示开", priority=10, block=True, rule=None)

@enable_notice_cmd.handle()
async def enable_notice(bot: Bot, event: MessageEvent):
    # 检查用户是否已呼叫机器人名字
    user_id = event.get_user_id()
    if user_id not in awaiting_response_users:
        # 检查消息是否包含机器人名字
        msg = event.get_plaintext().strip()
        if BOT_NAME not in msg:
            return
        else:
            # 如果消息中包含机器人名字且是命令，记录用户并继续处理
            awaiting_response_users[user_id] = datetime.now().timestamp()
    else:
        # 用户已呼叫机器人名字，处理命令
        del awaiting_response_users[user_id]
        
    global HELP_ENABLED      
    HELP_ENABLED = True      
    await bot.send(event, "（提示消息已开启，启动时会发送通知~）")      

disable_notice_cmd = on_command("提示关", priority=10, block=True, rule=None)

@disable_notice_cmd.handle()
async def disable_notice(bot: Bot, event: MessageEvent):
    # 检查用户是否已呼叫机器人名字
    user_id = event.get_user_id()
    if user_id not in awaiting_response_users:
        # 检查消息是否包含机器人名字
        msg = event.get_plaintext().strip()
        if BOT_NAME not in msg:
            return
        else:
            # 如果消息中包含机器人名字且是命令，记录用户并继续处理
            awaiting_response_users[user_id] = datetime.now().timestamp()
    else:
        # 用户已呼叫机器人名字，处理命令
        del awaiting_response_users[user_id]
        
    global HELP_ENABLED      
    HELP_ENABLED = False      
    await bot.send(event, "（提示消息已关闭，启动时将不会发送通知~）")      
      
# ================= 开启/关闭机器人命令 =================
enable_bot_cmd = on_command("开启机器人", priority=10, block=True, rule=None)

@enable_bot_cmd.handle()
async def enable_bot(bot: Bot, event: MessageEvent):
    # 检查用户是否已呼叫机器人名字
    user_id = event.get_user_id()
    if user_id not in awaiting_response_users:
        # 检查消息是否包含机器人名字
        msg = event.get_plaintext().strip()
        if BOT_NAME not in msg:
            return
        else:
            # 如果消息中包含机器人名字且是命令，记录用户并继续处理
            awaiting_response_users[user_id] = datetime.now().timestamp()
    else:
        # 用户已呼叫机器人名字，处理命令
        del awaiting_response_users[user_id]
        
    global BOT_ENABLED      
    BOT_ENABLED = True      
    await bot.send(event, "（机器人已开启，可以正常使用~）")      

disable_bot_cmd = on_command("关闭机器人", priority=10, block=True, rule=None)

@disable_bot_cmd.handle()
async def disable_bot(bot: Bot, event: MessageEvent):
    # 检查用户是否已呼叫机器人名字
    user_id = event.get_user_id()
    if user_id not in awaiting_response_users:
        # 检查消息是否包含机器人名字
        msg = event.get_plaintext().strip()
        if BOT_NAME not in msg:
            return
        else:
            # 如果消息中包含机器人名字且是命令，记录用户并继续处理
            awaiting_response_users[user_id] = datetime.now().timestamp()
    else:
        # 用户已呼叫机器人名字，处理命令
        del awaiting_response_users[user_id]
        
    global BOT_ENABLED      
    BOT_ENABLED = False      
    await bot.send(event, "（机器人已关闭，将不再响应任何命令~）")      
      
# 签到命令
sign_cmd = on_command("签到", aliases={"打卡", "sign"}, priority=10, block=True, rule=None)      
      
@sign_cmd.handle()      
async def handle_sign(bot: Bot, event: GroupMessageEvent):
    # 检查用户是否已呼叫机器人名字
    user_id = event.get_user_id()
    if user_id not in awaiting_response_users:
        # 检查消息是否包含机器人名字
        msg = event.get_plaintext().strip()
        if BOT_NAME not in msg:
            return
        else:
            # 如果消息中包含机器人名字且是命令，记录用户并继续处理
            awaiting_response_users[user_id] = datetime.now().timestamp()
    else:
        # 用户已呼叫机器人名字，处理命令
        del awaiting_response_users[user_id]      
    # 检查机器人是否启用      
    if not BOT_ENABLED:      
        return      
      
    user_id = event.get_user_id()      
    group_id = str(event.group_id)      
    today = datetime.now().date()      
    
    # 速率限制检查
    rate_key = f"sign_{user_id}_{group_id}"
    if not await rate_limiter.acquire(rate_key):
        await sign_cmd.finish(MessageSegment.at(user_id) + " 操作过于频繁，请稍后再试~")
        return
    
    # 使用任务管理器执行签到操作
    async def sign_operation():
        # 获取用户锁，确保并发安全
        user_lock = get_user_lock(user_id, group_id)
        
        async with user_lock:
            try:
                # 获取用户昵称      
                username = await get_user_nickname(event)      
                
                # 获取用户信息      
                user_info = await get_or_create_user(user_id, group_id, username)      
                
                async with db_transaction(pool_manager) as (conn, cursor):
                    # 检查今天是否已签到      
                    await cursor.execute(      
                        "SELECT * FROM sign_records WHERE user_id = %s AND group_id = %s AND sign_date = %s",      
                        (user_id, group_id, today)      
                    )      
                    if await cursor.fetchone():      
                        await sign_cmd.finish(MessageSegment.at(user_id) + " 你今天已经签到过了哦~")      
                    
                    # 获取昨天日期      
                    yesterday = today - timedelta(days=1)      
                    
                    # 检查昨天是否签到      
                    await cursor.execute(      
                        "SELECT continuous_days FROM sign_records WHERE user_id = %s AND group_id = %s AND sign_date = %s",      
                        (user_id, group_id, yesterday)      
                    )      
                    result = await cursor.fetchone()      
                    
                    if result:      
                        continuous_days = result[0] + 1      
                    else:      
                        continuous_days = 1      
                    
                    # 计算本次签到获得的积分（基础10柔汁 + 连续签到奖励）      
                    base_points = 10      
                    bonus_points = min(continuous_days - 1, 5)  # 最多奖励5柔汁      
                    total_points_earned = base_points + bonus_points      
                    
                    # 插入签到记录      
                    await cursor.execute(      
                        "INSERT INTO sign_records (user_id, group_id, sign_date, points_earned, continuous_days) VALUES (%s, %s, %s, %s, %s)",      
                        (user_id, group_id, today, total_points_earned, continuous_days)      
                    )      
                    
                    # 更新用户积分      
                    await update_user_points(user_id, group_id, total_points_earned, f"每日签到（连续{continuous_days}天）")      
                    
                    # 获取总签到天数      
                    await cursor.execute(      
                        "SELECT COUNT(*) as total FROM sign_records WHERE user_id = %s AND group_id = %s",      
                        (user_id, group_id)      
                    )      
                    total_days = (await cursor.fetchone())[0]      
                    
                    # 获取当前总积分      
                    current_points = await get_user_points(user_id, group_id)      
                    
                    # 构建回复消息，包含@用户      
                    reply_msg = MessageSegment.at(user_id) + MessageSegment.text(      
                        f"🎉 签到成功！\n"      
                        f"• 获得积分: {total_points_earned}柔汁\n"      
                        f"• 连续签到: {continuous_days}天\n"      
                        f"• 总签到: {total_days}天\n"      
                        f"• 当前积分: {current_points}柔汁\n"      
                        f"继续加油哦~"      
                    )      
                    
                    await sign_cmd.finish(reply_msg)      
                    
            except FinishedException:      
                return      
            except Exception as e:      
                await sign_cmd.finish(MessageSegment.at(user_id) + f" 签到失败: {e}")
    
    # 通过任务管理器执行
    await task_manager.execute(sign_operation())      
      
# 查询积分命令
points_cmd = on_command("积分", aliases={"我的积分", "points"}, priority=10, block=True, rule=None)      
      
@points_cmd.handle()      
async def handle_points(bot: Bot, event: GroupMessageEvent):
    # 检查用户是否已呼叫机器人名字
    user_id = event.get_user_id()
    if user_id not in awaiting_response_users:
        # 检查消息是否包含机器人名字
        msg = event.get_plaintext().strip()
        if BOT_NAME not in msg:
            return
        else:
            # 如果消息中包含机器人名字且是命令，记录用户并继续处理
            awaiting_response_users[user_id] = datetime.now().timestamp()
    else:
        # 用户已呼叫机器人名字，处理命令
        del awaiting_response_users[user_id]      
    # 检查机器人是否启用      
    if not BOT_ENABLED:      
        return      
      
    user_id = event.get_user_id()      
    group_id = str(event.group_id)      
    
    # 速率限制检查
    rate_key = f"points_{user_id}_{group_id}"
    if not await rate_limiter.acquire(rate_key):
        await points_cmd.finish(MessageSegment.at(user_id) + " 操作过于频繁，请稍后再试~")
        return
    
    # 使用任务管理器执行查询
    async def points_operation():
        points = await get_user_points(user_id, group_id)      
        reply_msg = MessageSegment.at(user_id) + MessageSegment.text(f" 你当前拥有 {points} 柔汁")      
        await points_cmd.finish(reply_msg)
    
    await task_manager.execute(points_operation())      
      
# 积分排行榜命令
leaderboard_cmd = on_command("积分排行", aliases={"积分榜", "排行榜"}, priority=10, block=True, rule=None)      
      
@leaderboard_cmd.handle()      
async def handle_leaderboard(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    # 检查用户是否已呼叫机器人名字
    user_id = event.get_user_id()
    if user_id not in awaiting_response_users:
        # 检查消息是否包含机器人名字
        msg = event.get_plaintext().strip()
        if BOT_NAME not in msg:
            return
        else:
            # 如果消息中包含机器人名字且是命令，记录用户并继续处理
            awaiting_response_users[user_id] = datetime.now().timestamp()
    else:
        # 用户已呼叫机器人名字，处理命令
        del awaiting_response_users[user_id]      
    # 检查机器人是否启用      
    if not BOT_ENABLED:      
        return      
    
    group_id = str(event.group_id)      
    limit = 10      
    if args.extract_plain_text().isdigit():      
        limit = min(int(args.extract_plain_text()), 20)  # 最多显示20名      
    
    # 速率限制检查
    rate_key = f"leaderboard_{group_id}"
    if not await rate_limiter.acquire(rate_key):
        await leaderboard_cmd.finish("操作过于频繁，请稍后再试~")
        return
    
    # 使用任务管理器执行查询
    async def leaderboard_operation():
        async with db_transaction(pool_manager) as (conn, cursor):
            await cursor.execute(      
                "SELECT username, total_points FROM users WHERE group_id = %s ORDER BY total_points DESC LIMIT %s",      
                (group_id, limit)      
            )      
            results = await cursor.fetchall()      
            
            if not results:      
                await leaderboard_cmd.finish("暂无积分数据")      
            
            leaderboard_text = "🏆 积分排行榜 🏆\n"      
            for i, (username, points) in enumerate(results, 1):      
                leaderboard_text += f"{i}. {username}: {points}柔汁\n"      
            
            await leaderboard_cmd.finish(leaderboard_text)
    
    await task_manager.execute(leaderboard_operation())      
      
# 补签命令
resign_cmd = on_command("补签", aliases={"补打卡"}, priority=10, block=True, rule=None)      
      
@resign_cmd.handle()      
async def handle_resign(bot: Bot, event: GroupMessageEvent):
    # 检查用户是否已呼叫机器人名字
    user_id = event.get_user_id()
    if user_id not in awaiting_response_users:
        # 检查消息是否包含机器人名字
        msg = event.get_plaintext().strip()
        if BOT_NAME not in msg:
            return
        else:
            # 如果消息中包含机器人名字且是命令，记录用户并继续处理
            awaiting_response_users[user_id] = datetime.now().timestamp()
    else:
        # 用户已呼叫机器人名字，处理命令
        del awaiting_response_users[user_id]      
    # 检查机器人是否启用      
    if not BOT_ENABLED:      
        return      
      
    user_id = event.get_user_id()      
    group_id = str(event.group_id)      
    today = datetime.now().date()      
    cost_per_day = 15  # 补签一天需要15柔汁      
    
    # 速率限制检查
    rate_key = f"resign_{user_id}_{group_id}"
    if not await rate_limiter.acquire(rate_key):
        await resign_cmd.finish(MessageSegment.at(user_id) + " 操作过于频繁，请稍后再试~")
        return
    
    # 使用任务管理器执行补签操作
    async def resign_operation():
        # 获取用户锁，确保并发安全
        user_lock = get_user_lock(user_id, group_id)
        
        async with user_lock:
            try:
                # 检查用户积分是否足够      
                current_points = await get_user_points(user_id, group_id)      
                if current_points < cost_per_day:      
                    reply_msg = MessageSegment.at(user_id) + MessageSegment.text(      
                        f" 积分不足！补签需要{cost_per_day}柔汁，你当前只有{current_points}柔汁"      
                    )      
                    await resign_cmd.finish(reply_msg)      
                
                async with db_transaction(pool_manager) as (conn, cursor):
                    # 查找最近未签到的日期      
                    await cursor.execute(      
                        "SELECT sign_date FROM sign_records WHERE user_id = %s AND group_id = %s ORDER BY sign_date DESC LIMIT 1",      
                        (user_id, group_id)      
                    )      
                    result = await cursor.fetchone()      
                    
                    if result:      
                        last_sign_date = result[0]      
                        miss_date = last_sign_date + timedelta(days=1)      
                    else:      
                        # 如果从未签到过，从昨天开始补      
                        miss_date = today - timedelta(days=1)      
                    
                    # 检查是否可以补签（不能补未来的日期）      
                    if miss_date >= today:      
                        await resign_cmd.finish(MessageSegment.at(user_id) + " 没有需要补签的日期")      
                    
                    # 扣除积分      
                    await update_user_points(user_id, group_id, -cost_per_day, f"补签{miss_date}")      
                    
                    # 插入补签记录      
                    await cursor.execute(      
                        "INSERT INTO sign_records (user_id, group_id, sign_date, points_earned, continuous_days) VALUES (%s, %s, %s, %s, %s)",      
                        (user_id, group_id, miss_date, 0, 1)  # 补签不获得积分，连续天数重置为1      
                    )      
                    
                    reply_msg = MessageSegment.at(user_id) + MessageSegment.text(      
                        f" 补签成功！已补签{miss_date}的签到\n"      
                        f"扣除{cost_per_day}柔汁，当前剩余{current_points - cost_per_day}柔汁"      
                    )      
                    
                    await resign_cmd.finish(reply_msg)      
                    
            except FinishedException:      
                return      
            except Exception as e:      
                await resign_cmd.finish(MessageSegment.at(user_id) + f" 补签失败: {e}")
    
    await task_manager.execute(resign_operation())      
      
# 积分流水查询
points_history_cmd = on_command("积分流水", aliases={"积分记录"}, priority=10, block=True, rule=None)      
      
@points_history_cmd.handle()      
async def handle_points_history(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    # 检查用户是否已呼叫机器人名字
    user_id = event.get_user_id()
    if user_id not in awaiting_response_users:
        # 检查消息是否包含机器人名字
        msg = event.get_plaintext().strip()
        if BOT_NAME not in msg:
            return
        else:
            # 如果消息中包含机器人名字且是命令，记录用户并继续处理
            awaiting_response_users[user_id] = datetime.now().timestamp()
    else:
        # 用户已呼叫机器人名字，处理命令
        del awaiting_response_users[user_id]      
    # 检查机器人是否启用      
    if not BOT_ENABLED:      
        return      
      
    user_id = event.get_user_id()      
    group_id = str(event.group_id)      
    limit = 10      
    
    if args.extract_plain_text().isdigit():      
        limit = min(int(args.extract_plain_text()), 20)      
    
    # 速率限制检查
    rate_key = f"history_{user_id}_{group_id}"
    if not await rate_limiter.acquire(rate_key):
        await points_history_cmd.finish(MessageSegment.at(user_id) + " 操作过于频繁，请稍后再试~")
        return
    
    # 使用任务管理器执行查询
    async def history_operation():
        async with db_transaction(pool_manager) as (conn, cursor):
            await cursor.execute(      
                "SELECT points_change, reason, created_at FROM points_history WHERE user_id = %s AND group_id = %s ORDER BY created_at DESC LIMIT %s",      
                (user_id, group_id, limit)      
            )      
            results = await cursor.fetchall()      
            
            if not results:      
                await points_history_cmd.finish(MessageSegment.at(user_id) + " 暂无积分记录")      
            
            history_text = "📊 最近积分流水\n"      
            for points_change, reason, created_at in results:      
                sign = "+" if points_change > 0 else ""      
                history_text += f"{created_at.strftime('%m-%d %H:%M')} {sign}{points_change}柔汁 ({reason})\n"      
            
            reply_msg = MessageSegment.at(user_id) + MessageSegment.text("\n" + history_text)      
            await points_history_cmd.finish(reply_msg)
    
    await task_manager.execute(history_operation())      
      
# 帮助命令
help_cmd = on_command("help", aliases={"帮助"}, priority=10, block=True, rule=None)      
      
@help_cmd.handle()      
async def handle_help(bot: Bot, event: MessageEvent):
    # 检查用户是否已呼叫机器人名字
    user_id = event.get_user_id()
    if user_id not in awaiting_response_users:
        # 检查消息是否包含机器人名字
        msg = event.get_plaintext().strip()
        if BOT_NAME not in msg:
            return
        else:
            # 如果消息中包含机器人名字且是命令，记录用户并继续处理
            awaiting_response_users[user_id] = datetime.now().timestamp()
    else:
        # 用户已呼叫机器人名字，处理命令
        del awaiting_response_users[user_id]      
    # 检查机器人是否启用      
    if not BOT_ENABLED:      
        return      
    
    # 基础信息（签到插件的帮助信息）      
    base_info = [      
        f"{BOT_PREFIX}签到插件功能说明：",      
        "• 签到/打卡 - 每日签到获得柔汁积分",      
        "• 积分 - 查看当前积分",      
        "• 积分排行 - 查看积分排行榜",      
        "• 补签 - 消耗柔汁补签",      
        "• 积分流水 - 查看积分记录",      
        "• 开启/关闭机器人 - 控制机器人响应",      
        "• 提示开/提示关 - 控制启动提示",
        "• 每个群聊的积分独立计算，不会同步"      
    ]      
    
    await send_as_forward(bot, event, base_info)      
      
# ================== 启动/关闭提示 ==================      
driver = get_driver()      

# 添加消息处理函数，用于检测呼叫机器人名字
from nonebot import on_message
from nonebot.adapters.onebot.v11 import MessageEvent

# 处理普通消息，用于检测是否呼叫了机器人名字
message_matcher = on_message(priority=15, block=False)

@message_matcher.handle()
async def handle_message(bot: Bot, event: MessageEvent):
    # 如果机器人未启用，不处理
    if not BOT_ENABLED:
        return
        
    msg = event.get_plaintext().strip()
    user_id = event.get_user_id()
    current_time = datetime.now().timestamp()
    
    # 清理过期的等待响应用户（10分钟内未发送消息则过期）
    for uid in list(awaiting_response_users.keys()):
        if current_time - awaiting_response_users[uid] > 600:
            del awaiting_response_users[uid]
    
    # 检查是否为命令消息，如果是则不处理
    if msg.startswith("/"):
        return
    
    # 检查是否呼叫了机器人名字
    if BOT_NAME in msg and user_id not in awaiting_response_users:
        # 用户呼叫了机器人名字，记录用户并提示
        awaiting_response_users[user_id] = current_time
        # 回复时@用户
        reply_msg = f"{BOT_PREFIX}我在听~\n请直接发送命令，如签到、积分等~"
        if hasattr(event, 'group_id') and event.group_id:
            reply_msg = MessageSegment.at(user_id) + reply_msg
        await bot.send(event, reply_msg)
      
async def _broadcast_simple(bot: Bot, message: str):      
    """简化版广播函数"""      
    if not HELP_ENABLED:      
        return      
          
    # 这里可以根据需要设置要通知的群组和用户      
    notice_groups = [284205050]  # 示例群号      
    notice_users = [2193807541]  # 示例用户ID      
      
    for gid in notice_groups:      
        try:      
            await bot.send_group_msg(group_id=gid, message=message)      
        except Exception as e:      
            logging.error(f"发送群 {gid} 提示失败: {e}")      
      
    for uid in notice_users:      
        try:      
            await bot.send_private_msg(user_id=uid, message=message)      
        except Exception as e:      
            logging.error(f"发送私聊 {uid} 提示失败: {e}")      
      
@driver.on_bot_connect      
async def _on_bot_connect(bot: Bot):      
    # 初始化数据库      
    await init_database()      
    await asyncio.sleep(2)  # 等待连接稳定      
    await _broadcast_simple(bot, f"{BOT_PREFIX}签到插件启动完成！发送 /help 查看可用功能~")      
      
@driver.on_bot_disconnect      
async def _on_bot_disconnect(bot: Bot):      
    logging.info(f"{BOT_PREFIX}签到插件即将下线（将在 shutdown 钩子里发送通知）")      

@driver.on_shutdown
async def _on_shutdown():
    # 在应用关闭阶段发送下线通知，此时连接通常仍然可用
    if not HELP_ENABLED:
        return
    try:
        for bot in nonebot.get_bots().values():
            try:
                await _broadcast_simple(bot, f"{BOT_PREFIX}签到插件即将下线，感谢使用~")
            except Exception as e:
                logging.error(f"关闭前发送提示失败: {e}")
    except Exception as e:
        logging.error(f"下线通知流程异常: {e}")
    
    # 关闭连接池管理器
    await pool_manager.close()
    
    # 输出任务统计信息
    stats = task_manager.get_stats()
    logging.info(f"任务统计: {stats}")