from nonebot import on_message, on_command, get_driver      
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent, Message      
from nonebot.typing import T_State      
from nonebot.params import CommandArg      
import requests      
import re      
import logging      
import time      
import asyncio      
import json      
from collections import defaultdict, deque      
from .concurrent_utils import (
    RateLimiter,
    task_manager,
    retry_with_backoff
)
      
# ================= 配置 =================      
OLLAMA_URL = "http://127.0.0.1:11434"      
MODEL_CHAT = "yuri_chat"      
MODEL_WRITE = "yuri_write"      
      
SENSITIVE_WORDS = ["裸", "性交", "阴部"]      
REPLACEMENTS = ["*", "*", "*"]      
      
MAX_CONTEXT = 5      
CONTEXT_TTL = 600      
BOT_PREFIX = "小豆泥："      
      
HELP_ENABLED = True      
STARTUP_NOTICE_GROUPS = [284205050]      
STARTUP_NOTICE_USERS = [2193807541]      

# 速率限制器 - AI聊天功能
ai_rate_limiter = RateLimiter(max_calls=20, time_window=60.0)  # 每分钟最多20次AI调用      
      
# ================= 可用性检测与开关 =================      
def _is_ollama_available() -> bool:
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=2)
        return resp.ok
    except Exception:
        return False

# 全局状态变量
PLUGIN_ENABLED = _is_ollama_available()
OLLAMA_AVAILABLE = PLUGIN_ENABLED
last_connection_check = time.time()
CONNECTION_CHECK_INTERVAL = 30  # 每30秒检查一次连接

async def check_ollama_connection():
    """定期检查Ollama连接状态"""
    global PLUGIN_ENABLED, OLLAMA_AVAILABLE, last_connection_check
    
    while True:
        try:
            current_time = time.time()
            if current_time - last_connection_check >= CONNECTION_CHECK_INTERVAL:
                was_available = OLLAMA_AVAILABLE
                OLLAMA_AVAILABLE = _is_ollama_available()
                last_connection_check = current_time
                
                # 状态变化时记录日志
                if was_available != OLLAMA_AVAILABLE:
                    if OLLAMA_AVAILABLE:
                        logging.info(f"{BOT_PREFIX}Ollama连接已恢复，AI聊天功能已启用")
                        PLUGIN_ENABLED = True
                    else:
                        logging.warning(f"{BOT_PREFIX}Ollama连接断开，AI聊天功能已进入待机状态")
                        PLUGIN_ENABLED = False
                
        except Exception as e:
            logging.error(f"检查Ollama连接时出错: {e}")
        
        await asyncio.sleep(CONNECTION_CHECK_INTERVAL)

# ================= 上下文管理 =================      
user_contexts = defaultdict(lambda: {"messages": deque(maxlen=MAX_CONTEXT), "last_time": 0})      
memory_enabled = defaultdict(lambda: False)      
      
def sanitize(text: str) -> str:      
    for w, r in zip(SENSITIVE_WORDS, REPLACEMENTS):      
        text = re.sub(w, r, text, flags=re.IGNORECASE)      
    return text      
      
def add_to_context(user_id: str, role: str, content: str):      
    now = time.time()      
    ctx = user_contexts[user_id]      
    if now - ctx["last_time"] > CONTEXT_TTL:      
        ctx["messages"].clear()      
    ctx["messages"].append({"role": role, "content": content})      
    ctx["last_time"] = now      
      
def get_context(user_id: str):      
    now = time.time()      
    ctx = user_contexts[user_id]      
    if now - ctx["last_time"] > CONTEXT_TTL:      
        ctx["messages"].clear()      
    return list(ctx["messages"])      
      
def clear_context(user_id: str):      
    user_contexts[user_id]["messages"].clear()      
    user_contexts[user_id]["last_time"] = 0      
      
def format_status(user_id: str) -> str:      
    enabled = memory_enabled[user_id]      
    ctx = user_contexts[user_id]      
    msg_count = len(ctx["messages"])      
    ttl_left = max(0, CONTEXT_TTL - (time.time() - ctx["last_time"])) if msg_count > 0 else 0      
    if enabled:      
        return f"（记忆模式：开启，已存储 {msg_count} 条对话，剩余 {int(ttl_left)} 秒过期）"      
    else:      
        return "（记忆模式：关闭，无上下文记忆）"      
      
# ================= 安全调用 Ollama =================      
@retry_with_backoff(max_retries=2, base_delay=0.5)
async def call_ollama(bot: Bot, event: MessageEvent, user_id: str, user_message: str, model: str):      
    if not memory_enabled[user_id]:      
        messages = [      
            {"role": "system", "content": "你是一个名为 '小豆泥' 的AI聊天助手，服务于QQ百合群。风格温柔、细腻、富有情感，专注女性之间的浪漫、亲密关系。可以生成轻微成人向内容，但必须优雅含蓄，使用心理描写、情感共鸣和浪漫隐喻。禁止露骨色情、违法、政治敏感、低俗内容。在描述女性角色时请使用'她'而不是'他'。"} if model == MODEL_CHAT else      
            {"role": "system", "content": "你是一个名为 '小豆泥' 的写作助手，专注百合向浪漫文学创作。风格温柔浪漫、情感细腻，可以生成成人向内容，但必须优雅含蓄。擅长使用隐喻、心理描写、浪漫描写和情感共鸣，避免露骨色情。禁止违法、政治敏感和低俗内容。在描写女性角色时请使用'她'、'她们'等女性代词，不要使用'他'。"},      
            {"role": "user", "content": user_message}      
        ]      
    else:      
        add_to_context(user_id, "user", user_message)      
        messages = [{"role": "system", "content": "你是一个名为'小豆泥'的AI聊天助手，服务于QQ百合群。风格温柔、细腻、富有情感，专注女性之间的浪漫、亲密关系。可以生成轻微成人向内容，但必须优雅含蓄，使用心理描写、情感共鸣和浪漫隐喻。禁止露骨色情、违法、政治敏感、低俗内容。在描述女性角色时请使用'她'而不是'他'。"} if model == MODEL_CHAT else      
                    {"role": "system", "content": "你是一个名为'小豆泥'的写作助手，专注百合向浪漫文学创作。风格温柔浪漫、情感细腻，可以生成成人向内容，但必须优雅含蓄。擅长使用隐喻、心理描写、浪漫描写和情感共鸣，避免露骨色情。禁止违法、政治敏感和低俗内容。在描写女性角色时请使用'她'、'她们'等女性代词，不要使用'他'。"}]      
        messages.extend(get_context(user_id))      
      
    data = {"model": model, "messages": messages, "stream": True}      
      
    try:      
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=data, stream=True, timeout=60)      
        r.raise_for_status()      
      
        content = ""      
        for line in r.iter_lines():      
            if not line:      
                continue      
            try:      
                resp = json.loads(line.decode("utf-8"))      
                if "message" in resp and "content" in resp["message"]:      
                    content += resp["message"]["content"]      
                elif "done" in resp and resp["done"] and content:      
                    break      
            except json.JSONDecodeError:      
                continue      
      
        if not content:      
            try:      
                data_no_stream = {"model": model, "messages": messages, "stream": False}      
                r2 = requests.post(f"{OLLAMA_URL}/api/chat", json=data_no_stream, timeout=60)      
                r2.raise_for_status()      
                resp = r2.json()      
                if "message" in resp and "content" in resp["message"]:      
                    content = resp["message"]["content"]      
            except Exception as fallback_error:      
                logging.error(f"非流式调用也失败: {fallback_error}")      
                content = "（AI 没有生成任何内容，请检查模型是否正常运行）"      
      
        content = sanitize(content)      
      
        if memory_enabled[user_id] and content:      
            add_to_context(user_id, "assistant", content)      
      
        return content      
      
    except Exception as e:      
        logging.error(f"Ollama 调用错误: {e}")      
        raise e      
      
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
      
# ================= 写作生成（逐条生成并合并转发） =================      
async def call_generate_stream(bot: Bot, event: MessageEvent, prompt: str):      
    user_id = str(event.user_id)      
    
    # 速率限制检查
    rate_key = f"write_{user_id}"
    if not await ai_rate_limiter.acquire(rate_key):
        error_msg = f"{BOT_PREFIX}写作功能使用过于频繁，请稍后再试~"
        if hasattr(event, "group_id") and event.group_id:      
            await bot.send_group_msg(group_id=event.group_id, message=error_msg)      
        else:      
            await bot.send_private_msg(user_id=event.user_id, message=error_msg)
        return
    
    async def write_operation():
        try:      
            content = await call_ollama(bot, event, user_id, prompt, model=MODEL_WRITE)      
                  
            # 将生成的内容按段落分割成多条消息      
            paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]      
            if not paragraphs:      
                paragraphs = [content]      
                  
            # 创建合并转发消息      
            forward_msg = []      
            for i, paragraph in enumerate(paragraphs):      
                forward_msg.append({      
                    "type": "node",      
                    "data": {      
                        "name": BOT_PREFIX.strip('：'),      
                        "uin": bot.self_id,      
                        "content": f"{BOT_PREFIX}{paragraph}" if i == 0 else paragraph      
                    }      
                })      
                  
            # 发送合并转发消息      
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
                        
        except Exception as e:      
            error_msg = f"{BOT_PREFIX}调用出错: {str(e)}"      
            if hasattr(event, "group_id") and event.group_id:      
                await bot.send_group_msg(group_id=event.group_id, message=error_msg)      
            else:      
                await bot.send_private_msg(user_id=event.user_id, message=error_msg)
    
    await task_manager.execute(write_operation())      
      
# ================= 聊天消息处理 =================      
async def call_chat(bot: Bot, event: MessageEvent, user_id: str, msg: str):      
    # 速率限制检查
    rate_key = f"chat_{user_id}"
    if not await ai_rate_limiter.acquire(rate_key):
        error_msg = f"{BOT_PREFIX}聊天功能使用过于频繁，请稍后再试~"
        if hasattr(event, "group_id") and event.group_id:      
            await bot.send_group_msg(group_id=event.group_id, message=error_msg)      
        else:      
            await bot.send_private_msg(user_id=event.user_id, message=error_msg)
        return
    
    async def chat_operation():
        try:      
            content = await call_ollama(bot, event, user_id, msg, model=MODEL_CHAT)      
            if hasattr(event, "group_id") and event.group_id:      
                await bot.send_group_msg(group_id=event.group_id, message=f"{BOT_PREFIX}{content}")      
            else:      
                await bot.send_private_msg(user_id=event.user_id, message=f"{BOT_PREFIX}{content}")      
        except Exception as e:      
            error_msg = f"{BOT_PREFIX}调用出错: {str(e)}"      
            if hasattr(event, "group_id") and event.group_id:      
                await bot.send_group_msg(group_id=event.group_id, message=error_msg)      
            else:      
                await bot.send_private_msg(user_id=event.user_id, message=error_msg)      
    
    await task_manager.execute(chat_operation())
      
async def call_chat_stream(bot: Bot, event: MessageEvent, user_id: str, msg: str):      
    await call_chat(bot, event, user_id, msg)      
      
# ================= 写作引用 =================      
async def handle_generate_with_reference(bot: Bot, event: MessageEvent, prompt: str):      
    refs = [seg for seg in event.get_message() if seg.type == "reply"]      
    context_text = ""      
    if refs:      
        for ref in refs:      
            try:      
                ref_msg = await bot.get_msg(message_id=ref.data["id"])      
                context_text += ref_msg["message"] + "\n"      
            except Exception as e:      
                logging.error(f"获取引用消息失败: {e}")      
    final_prompt = (context_text or "") + prompt      
    await call_generate_stream(bot, event, final_prompt)      
      
# ================= AI聊天插件命令处理 =================
# 记忆相关命令
memory_on_cmd = on_command("记忆", aliases={"记忆开"}, priority=10, block=True)
memory_off_cmd = on_command("记忆关", priority=10, block=True)
clear_memory_cmd = on_command("清除记忆", priority=10, block=True)

@memory_on_cmd.handle()
async def handle_memory_on(bot: Bot, event: MessageEvent):
    user_id = str(event.user_id)
    memory_enabled[user_id] = True
    await bot.send(event, "（记忆已开启，我会记住我们最近的对话啦~）")

@memory_off_cmd.handle()
async def handle_memory_off(bot: Bot, event: MessageEvent):
    user_id = str(event.user_id)
    memory_enabled[user_id] = False
    clear_context(user_id)
    await bot.send(event, "（记忆已关闭，并且清除了之前的对话~）")

@clear_memory_cmd.handle()
async def handle_clear_memory(bot: Bot, event: MessageEvent):
    user_id = str(event.user_id)
    clear_context(user_id)
    await bot.send(event, "（记忆已清除，我们可以重新开始聊天啦~）")

# 状态查询命令
status_cmd = on_command("状态", priority=10, block=True)

@status_cmd.handle()
async def handle_status(bot: Bot, event: MessageEvent):
    user_id = str(event.user_id)
    status = format_status(user_id)
    if not OLLAMA_AVAILABLE:
        status += "（系统待机中，等待Ollama连接）"
    await bot.send(event, status)

# 写作命令
write_cmd = on_command("写作", priority=10, block=True)

@write_cmd.handle()
async def handle_write(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_prompt = args.extract_plain_text().strip()
    if not user_prompt:
        await bot.send(event, "（请提供写作内容，例如：/写作 写一个百合故事）")
        return
    await handle_generate_with_reference(bot, event, user_prompt)

# 帮助命令
help_cmd = on_command("help", aliases={"帮助"}, priority=10, block=True)

@help_cmd.handle()
async def handle_help(bot: Bot, event: MessageEvent):
    messages = [      
        f"{BOT_PREFIX}AI聊天功能说明：",      
        "1. 直接对话 - 与我进行日常聊天",      
        "2. /写作 内容 - AI 百合写作，支持引用原文续写（合并转发）",      
        "3. /记忆 开/关 - 开启/关闭多轮上下文记忆",       
        "4. /清除记忆 - 清除记忆上下文",      
        "5. /状态 - 查看记忆状态",      
        "6. /提示开/提示关 - 开启/关闭启动提示",      
        "7. 群聊需 @ 才会响应；私聊可直接对话",      
        "8. 敏感词会自动替换",
        "9. 系统待机时无法使用AI功能，请等待连接恢复"      
    ]      
    await send_as_forward(bot, event, messages)

# ================= 开启/关闭提示命令 =================      
enable_notice_cmd = on_command("提示开", priority=10, block=True)
disable_notice_cmd = on_command("提示关", priority=10, block=True)

@enable_notice_cmd.handle()
async def enable_notice(bot: Bot, event: MessageEvent):
    global HELP_ENABLED
    HELP_ENABLED = True
    await bot.send(event, "（提示消息已开启，启动时会发送通知~）")

@disable_notice_cmd.handle()
async def disable_notice(bot: Bot, event: MessageEvent):
    global HELP_ENABLED
    HELP_ENABLED = False
    await bot.send(event, "（提示消息已关闭，启动时将不会发送通知~）")

# ================= 消息处理 =================
matcher = on_message(priority=10, block=True)

@matcher.handle()
async def handle_message(bot: Bot, event: MessageEvent, state: T_State):
    msg = event.get_plaintext().strip()      
    is_private = not hasattr(event, "group_id") or event.group_id is None      
    
    if not is_private:      
        if not getattr(event, "to_me", False) and not getattr(event, "is_tome", lambda: False)():      
            return      
        msg = re.sub(rf"^\s*@?{bot.self_id}\s*", "", msg).strip()      
    
    if not msg:      
        await matcher.finish("（你想和我聊点什么呢？）")      
    
    user_id = str(event.user_id)      
    
    # 检查是否为命令类消息（以/开头），如果是则不处理
    if msg.startswith("/"):
        # 命令类消息不处理，让其他插件处理
        return
    
    # 检查Ollama连接状态
    if not OLLAMA_AVAILABLE:
        await matcher.finish(f"{BOT_PREFIX}系统正在待机中，等待Ollama连接恢复...")
        return
    
    # 普通聊天消息才走Ollama
    await call_chat_stream(bot, event, user_id, msg)
      
# ================== 启动/关闭提示 ==================      
driver = get_driver()

async def _broadcast_simple(bot: Bot, message: str):
    """简化版广播函数"""
    if not HELP_ENABLED:
        return
        
    for gid in STARTUP_NOTICE_GROUPS:
        try:
            await bot.send_group_msg(group_id=gid, message=message)
        except Exception as e:
            logging.error(f"发送群 {gid} 提示失败: {e}")

    for uid in STARTUP_NOTICE_USERS:
        try:
            await bot.send_private_msg(user_id=uid, message=message)
        except Exception as e:
            logging.error(f"发送私聊 {uid} 提示失败: {e}")

@driver.on_bot_connect
async def _on_bot_connect(bot: Bot):
    # 启动连接检查任务
    asyncio.create_task(check_ollama_connection())
    
    if not HELP_ENABLED:
        return
        
    await asyncio.sleep(2)  # 等待连接稳定
    
    if OLLAMA_AVAILABLE:
        await _broadcast_simple(bot, f"{BOT_PREFIX}AI聊天插件启动完成！发送 /help 查看可用功能~")
    else:
        await _broadcast_simple(bot, f"{BOT_PREFIX}AI聊天插件已启动，但Ollama未连接，进入待机状态。连接恢复后将自动启用AI功能~")

@driver.on_bot_disconnect
async def _on_bot_disconnect(bot: Bot):
    if not HELP_ENABLED:
        return
    await _broadcast_simple(bot, f"{BOT_PREFIX}AI聊天插件即将下线，感谢使用~")