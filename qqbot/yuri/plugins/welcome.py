from nonebot import on_notice, get_driver
from nonebot.adapters.onebot.v11 import GroupIncreaseNoticeEvent, Bot, Message
from nonebot.plugin import PluginMetadata
import asyncio
import logging
from .concurrent_utils import RateLimiter, task_manager, retry_with_backoff
import requests
import json
import re

# ================= 配置 =================      
OLLAMA_URL = "http://127.0.0.1:11434"      
MODEL_CHAT = "yuri_chat"      
MODEL_WRITE = "yuri_write"      
      
SENSITIVE_WORDS = ["裸", "性交", "阴部"]      
REPLACEMENTS = ["*", "*", "*"]      
      
BOT_PREFIX = "小豆泥："

# 速率限制器 - 欢迎消息功能
welcome_rate_limiter = RateLimiter(max_calls=10, time_window=60.0)  # 每分钟最多10次欢迎消息

# ================= 插件元数据 =================
__plugin_meta__ = PluginMetadata(
    name="新成员欢迎",
    description="当有新成员加入群时发送个性化的欢迎消息",
    usage="自动触发，无需手动调用",
    type="application",
    homepage="N/A",
    supported_adapters={"~onebot.v11"},
)

# ================= 辅助函数 =================      
def sanitize(text: str) -> str:      
    for w, r in zip(SENSITIVE_WORDS, REPLACEMENTS):      
        text = re.sub(w, r, text, flags=re.IGNORECASE)      
    return text

# ================= Ollama调用函数 =================
@retry_with_backoff(max_retries=2, base_delay=0.5)
async def call_ollama_welcome(bot: Bot, group_id: int, user_id: int) -> str:
    """调用Ollama生成欢迎消息"""
    # 获取群信息
    group_info = await bot.get_group_info(group_id=group_id)
    group_name = group_info.get("group_name", "本群")
    
    # 获取新成员信息
    try:
        member_info = await bot.get_group_member_info(group_id=group_id, user_id=user_id)
        user_name = member_info.get("nickname", "新朋友")
    except:
        user_name = "新朋友"
    
    # 构建提示词
    prompt = f"请以小豆泥的身份，用温柔细腻的语气欢迎新成员{user_name}加入{group_name}。\
              表达对新成员的期待和友好，风格要符合百合群的氛围，专注女性之间的浪漫、亲密关系。\
              可以生成轻微成人向内容，但必须优雅含蓄，使用心理描写、情感共鸣和浪漫隐喻。\
              禁止露骨色情、违法、政治敏感、低俗内容。字数控制在100字以内。"
    
    messages = [
        {"role": "system", "content": "你是一个名为'小豆泥'的AI聊天助手，服务于QQ百合群。风格温柔、细腻、富有情感，专注女性之间的浪漫、亲密关系。"},
        {"role": "user", "content": prompt}
    ]
    
    data = {"model": MODEL_CHAT, "messages": messages, "stream": False}
    
    try:
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=data, timeout=10)
        r.raise_for_status()
        resp = r.json()
        
        if "message" in resp and "content" in resp["message"]:
            content = resp["message"]["content"]
            content = sanitize(content)
            return f"{BOT_PREFIX}{content}"
        else:
            return f"{BOT_PREFIX}欢迎新朋友[CQ:at,qq={user_id}]加入！请先阅读群公告，遵守群规则哦～"
            
    except Exception as e:
        logging.error(f"生成欢迎消息时出错: {e}")
        return f"{BOT_PREFIX}欢迎新朋友[CQ:at,qq={user_id}]加入！请先阅读群公告，遵守群规则哦～"

# ================= 事件处理 =================
welcome = on_notice(priority=10, block=False)

@welcome.handle()
async def handle_group_increase(bot: Bot, event: GroupIncreaseNoticeEvent):
    # 确保是群成员增加事件
    if event.notice_type != "group_increase":
        return
    
    # 获取新成员的信息
    user_id = event.user_id
    group_id = event.group_id
    
    # 速率限制检查
    rate_key = f"welcome_{group_id}"
    if not await welcome_rate_limiter.acquire(rate_key):
        logging.info(f"群{group_id}的欢迎消息触发速率限制，跳过欢迎消息")
        return
    
    async def welcome_operation():
        try:
            # 调用Ollama生成欢迎消息
            welcome_msg = await call_ollama_welcome(bot, group_id, user_id)
            
            # 确保消息中包含@新成员
            if f"[CQ:at,qq={user_id}]" not in welcome_msg:
                welcome_msg = f"[CQ:at,qq={user_id}] {welcome_msg}"
                
            # 发送欢迎消息
            await bot.send_group_msg(group_id=group_id, message=Message(welcome_msg))
            
        except Exception as e:
            logging.error(f"发送欢迎消息时出错: {e}")
            # 出错时发送默认欢迎消息
            default_msg = f"{BOT_PREFIX}欢迎新朋友[CQ:at,qq={user_id}]加入！请先阅读群公告，遵守群规则哦～"
            await bot.send_group_msg(group_id=group_id, message=Message(default_msg))
    
    # 使用任务管理器执行欢迎操作
    await task_manager.execute(welcome_operation())

# ================= 启动检查 =================
driver = get_driver()

@driver.on_bot_connect
async def _on_bot_connect(bot: Bot):
    # 延迟一下确保连接稳定
    await asyncio.sleep(3)
    logging.info("新成员欢迎插件已加载，当有新成员加入群时会自动发送欢迎消息")