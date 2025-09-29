from nonebot import on_message, on_command, get_driver
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent, Message, MessageSegment
from nonebot.typing import T_State
from nonebot.params import CommandArg
import re
import logging
import time
import asyncio
import json
import aiohttp
import requests
from collections import defaultdict, deque
import functools

# ================= 配置 =================
OLLAMA_URL = "http://127.0.0.1:11434"
MODEL_CODE = "code"
MODEL_CTF = "ctf"

SENSITIVE_WORDS = []  # 代码CTF场景不需要敏感词过滤
REPLACEMENTS = []

MAX_CONTEXT = 5
CONTEXT_TTL = 600
BOT_PREFIX = "代码助手："

HELP_ENABLED = True
STARTUP_NOTICE_GROUPS = []  # 可以添加需要通知的群组
STARTUP_NOTICE_USERS = []   # 可以添加需要通知的用户

# ================= 自定义工具函数实现（替代concurrent_utils）=================

class RateLimiter:
    def __init__(self, max_calls, time_window):
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls = defaultdict(deque)

    async def acquire(self, key):
        current_time = time.time()
        # 清理过期的调用记录
        while self.calls[key] and self.calls[key][0] < current_time - self.time_window:
            self.calls[key].popleft()
        # 检查是否超过限制
        if len(self.calls[key]) < self.max_calls:
            self.calls[key].append(current_time)
            return True
        return False

class TaskManager:
    def __init__(self):
        self.running_tasks = set()

    async def execute(self, coro):
        task = asyncio.create_task(coro)
        self.running_tasks.add(task)
        task.add_done_callback(lambda t: self.running_tasks.discard(t))
        try:
            return await task
        except Exception:
            pass

task_manager = TaskManager()

def retry_with_backoff(max_retries=3, base_delay=0.1):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for i in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if i < max_retries - 1:
                        delay = base_delay * (2 ** i)
                        await asyncio.sleep(delay)
            # 最后一次失败，抛出异常
            raise last_exception
        return wrapper
    return decorator

# 速率限制器 - AI功能
ai_rate_limiter = RateLimiter(max_calls=20, time_window=60.0)  # 每分钟最多20次AI调用

# ================= 可用性检测与开关 =================
def _is_ollama_available() -> bool:
    try:
        # 同步检查，用于初始化
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=2)
        return resp.ok
    except Exception:
        return False

async def _is_ollama_available_async() -> bool:
    try:
        # 异步检查，用于运行时
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{OLLAMA_URL}/api/tags", timeout=2) as response:
                if response.ok:
                    # 检查响应内容是否包含预期的模型信息
                    try:
                        data = await response.json()
                        return "models" in data
                    except Exception:
                        # 如果无法解析JSON，至少确认连接正常
                        return True
                return False
    except aiohttp.ClientError as e:
        logging.warning(f"Ollama连接检查客户端错误: {str(e)}")
        return False
    except asyncio.TimeoutError:
        logging.warning(f"Ollama连接检查超时")
        return False
    except Exception as e:
        logging.warning(f"Ollama连接检查未知错误: {str(e)}")
        return False

# ================= 全局状态变量
PLUGIN_ENABLED = _is_ollama_available()
OLLAMA_AVAILABLE = PLUGIN_ENABLED
last_connection_check = time.time()
CONNECTION_CHECK_INTERVAL = 30  # 每30秒检查一次连接

# 存储已呼叫机器人名字的用户，值为下次响应的时间戳
awaiting_response_users = {}

async def check_ollama_connection():
    """定期检查Ollama连接状态"""
    global PLUGIN_ENABLED, OLLAMA_AVAILABLE, last_connection_check
    
    while True:
        try:
            current_time = time.time()
            if current_time - last_connection_check >= CONNECTION_CHECK_INTERVAL:
                was_available = OLLAMA_AVAILABLE
                # 使用异步版本检查连接
                OLLAMA_AVAILABLE = await _is_ollama_available_async()
                last_connection_check = current_time
                
                # 状态变化时记录日志
                if was_available != OLLAMA_AVAILABLE:
                    if OLLAMA_AVAILABLE:
                        logging.info(f"{BOT_PREFIX}Ollama连接已恢复，AI功能已启用")
                        PLUGIN_ENABLED = True
                    else:
                        logging.warning(f"{BOT_PREFIX}Ollama连接断开，AI功能已进入待机状态")
                        PLUGIN_ENABLED = False
                
        except Exception as e:
            logging.error(f"检查Ollama连接时出错: {str(e)}")
            import traceback
            logging.error(f"完整错误堆栈: {traceback.format_exc()}")
        
        await asyncio.sleep(CONNECTION_CHECK_INTERVAL)

# ================= 上下文管理 =================
user_contexts = defaultdict(lambda: {"messages": deque(maxlen=MAX_CONTEXT), "last_time": 0})
memory_enabled = defaultdict(lambda: False)

# 获取机器人名称（去掉冒号）
BOT_NAME = BOT_PREFIX.strip('：')

def sanitize(text: str) -> str:
    for w, r in zip(SENSITIVE_WORDS, REPLACEMENTS):
        text = re.sub(w, r, text, flags=re.IGNORECASE)
    return text

def _clear_expired_context(user_id: str):
    now = time.time()
    ctx = user_contexts[user_id]
    if now - ctx["last_time"] > CONTEXT_TTL:
        ctx["messages"].clear()

def add_to_context(user_id: str, role: str, content: str):
    _clear_expired_context(user_id)
    ctx = user_contexts[user_id]
    ctx["messages"].append({"role": role, "content": content})
    ctx["last_time"] = time.time()

def get_context(user_id: str):
    _clear_expired_context(user_id)
    return list(user_contexts[user_id]["messages"])

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
            {"role": "system", "content": "你是一个专业的编程助手，擅长代码编写、调试、优化和解释。能够处理多种编程语言：Python, JavaScript, Java, C++, Go, Rust等。提供简洁高效的代码解决方案，包含必要的注释和文档。能够分析代码错误并提供修复建议。遵循最佳实践和代码规范。"} if model == MODEL_CODE else
            {"role": "system", "content": "你是一个CTF（Capture The Flag）网络安全竞赛专家。擅长：逆向工程、密码学、二进制漏洞、Web安全、取证分析、隐写术。能够分析各种CTF题目，提供解题思路和步骤。熟悉常见工具：GDB, Wireshark, Burp Suite, IDA Pro, Ghidra等。提供详细的解题方法和学习资源推荐。注重实战技巧和漏洞利用原理。"},
            {"role": "user", "content": user_message}
        ]
    else:
        add_to_context(user_id, "user", user_message)
        messages = [{"role": "system", "content": "你是一个专业的编程助手，擅长代码编写、调试、优化和解释。能够处理多种编程语言：Python, JavaScript, Java, C++, Go, Rust等。提供简洁高效的代码解决方案，包含必要的注释和文档。能够分析代码错误并提供修复建议。遵循最佳实践和代码规范。"} if model == MODEL_CODE else
                    {"role": "system", "content": "你是一个CTF（Capture The Flag）网络安全竞赛专家。擅长：逆向工程、密码学、二进制漏洞、Web安全、取证分析、隐写术。能够分析各种CTF题目，提供解题思路和步骤。熟悉常见工具：GDB, Wireshark, Burp Suite, IDA Pro, Ghidra等。提供详细的解题方法和学习资源推荐。注重实战技巧和漏洞利用原理。"}]
        messages.extend(get_context(user_id))
    
    data = {"model": model, "messages": messages, "stream": True}
    
    try:
        # 使用异步aiohttp代替同步requests
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{OLLAMA_URL}/api/chat", json=data, timeout=60) as response:
                response.raise_for_status()
                
                content = ""
                # 异步读取流式响应，增加读取行的超时处理
                last_line_time = time.time()
                read_timeout = 30  # 单个行的读取超时时间（秒）
                
                try:
                    while True:
                        # 检查读取是否超时
                        if time.time() - last_line_time > read_timeout:
                            raise asyncio.TimeoutError(f"流式读取响应超时({read_timeout}秒)")
                        
                        # 设置单次读取的超时
                        try:
                            line = await asyncio.wait_for(response.content.readline(), timeout=read_timeout)
                            if not line:
                                # 响应结束
                                break
                            last_line_time = time.time()
                            
                            try:
                                resp = json.loads(line.decode("utf-8"))
                                if "message" in resp and "content" in resp["message"]:
                                    content += resp["message"]["content"]
                                elif "done" in resp and resp["done"]:
                                    break
                            except json.JSONDecodeError:
                                continue
                        except asyncio.TimeoutError as e:
                            # 单次读取超时，检查是否已经有内容，如果有就返回
                            if content:
                                logging.warning(f"流式读取超时，但已获取部分内容，返回已读取内容")
                                break
                            raise e
                except asyncio.TimeoutError:
                    # 流式读取完全超时，尝试非流式调用
                    logging.warning(f"流式调用超时，尝试使用非流式调用")
                    content = ""
    
        if not content:
            try:
                data_no_stream = {"model": model, "messages": messages, "stream": False}
                # 非流式调用超时时间设置为90秒，给模型更多时间响应
                async with aiohttp.ClientSession() as session:
                    async with session.post(f"{OLLAMA_URL}/api/chat", json=data_no_stream, timeout=90) as response:
                        response.raise_for_status()
                        resp = await response.json()
                        if "message" in resp and "content" in resp["message"]:
                            content = resp["message"]["content"]
            except Exception as fallback_error:
                logging.error(f"非流式调用也失败: {str(fallback_error)}")
                # 记录完整的异常堆栈信息以便调试
                import traceback
                logging.error(f"完整错误堆栈: {traceback.format_exc()}")
                content = f"（AI 没有生成任何内容，错误信息：{str(fallback_error)}）"
    
        content = sanitize(content)
    
        if memory_enabled[user_id] and content:
            add_to_context(user_id, "assistant", content)
    
        return content
    
    except Exception as e:
        logging.error(f"Ollama 调用错误: {str(e)}")
        # 记录完整的异常堆栈信息以便调试
        import traceback
        logging.error(f"完整错误堆栈: {traceback.format_exc()}")
        
        # 更友好的错误处理：根据不同类型的错误返回不同的提示信息
        error_type = type(e).__name__
        if error_type == 'asyncio.TimeoutError':
            # 超时错误，可能是Ollama处理时间过长或网络问题
            return f"（AI思考时间太长了呢，可能是网络不太好或者Ollama正忙，请稍后再试哦~）"
        elif error_type == 'aiohttp.ClientError':
            # 客户端错误，可能是连接问题
            return f"（连接Ollama服务失败了呢，请检查服务是否正常运行~）"
        elif error_type == 'json.JSONDecodeError':
            # JSON解析错误，可能是响应格式问题
            return f"（收到的AI响应格式有些问题，请稍后再试哦~）"
        else:
            # 其他未预期的错误
            return f"（AI调用出错了呢，错误类型：{error_type}，请稍后再试~）"

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

# ================= 代码生成（逐条生成并合并转发） =================
async def call_code_stream(bot: Bot, event: MessageEvent, prompt: str):
    user_id = str(event.user_id)
    
    # 速率限制检查
    rate_key = f"code_{user_id}"
    if not await ai_rate_limiter.acquire(rate_key):
        error_msg = f"{BOT_PREFIX}功能使用过于频繁，请稍后再试~"
        await bot.send(event, error_msg)
        return
    
    async def code_operation():
        try:
            content = await call_ollama(bot, event, user_id, prompt, model=MODEL_CODE)
                
            # 将生成的内容按段落分割成多条消息
            paragraphs = [p.strip() for p in re.split(r'\n{2,}', content) if p.strip()]
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
                # 群聊中@用户
                await bot.send_group_msg(group_id=event.group_id, message=MessageSegment.at(user_id) + error_msg)
            else:
                await bot.send_private_msg(user_id=event.user_id, message=error_msg)
    
    try:
        await task_manager.execute(code_operation())
    except Exception as e:
        # 记录异常但不影响其他任务
        logging.exception(f"执行代码生成任务时发生异常")
        import traceback
        logging.error(f"完整错误堆栈: {traceback.format_exc()}")

# ================= CTF解题（逐条生成并合并转发） =================
async def call_ctf_stream(bot: Bot, event: MessageEvent, prompt: str):
    user_id = str(event.user_id)
    
    # 速率限制检查
    rate_key = f"ctf_{user_id}"
    if not await ai_rate_limiter.acquire(rate_key):
        error_msg = f"{BOT_PREFIX}功能使用过于频繁，请稍后再试~"
        await bot.send(event, error_msg)
        return
    
    async def ctf_operation():
        try:
            content = await call_ollama(bot, event, user_id, prompt, model=MODEL_CTF)
                
            # 将生成的内容按段落分割成多条消息
            paragraphs = [p.strip() for p in re.split(r'\n{2,}', content) if p.strip()]
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
                # 群聊中@用户
                await bot.send_group_msg(group_id=event.group_id, message=MessageSegment.at(user_id) + error_msg)
            else:
                await bot.send_private_msg(user_id=event.user_id, message=error_msg)
    
    try:
        await task_manager.execute(ctf_operation())
    except Exception as e:
        # 记录异常但不影响其他任务
        logging.error(f"执行CTF解题任务时发生异常: {str(e)}")
        import traceback
        logging.error(f"完整错误堆栈: {traceback.format_exc()}")

# ================= 引用处理 =================
async def handle_with_reference(bot: Bot, event: MessageEvent, prompt: str, model: str):
    refs = []
    msg_segs = event.get_message()
    if isinstance(msg_segs, (list, tuple)):
        refs = [seg for seg in msg_segs if getattr(seg, "type", "") == "reply"]
    context_text = ""
    if refs:
        for ref in refs:
            try:
                ref_msg = await bot.get_msg(message_id=ref.data["id"])
                context_text += ref_msg["message"] + "\n"
            except Exception as e:
                logging.error(f"获取引用消息失败: {e}")
    final_prompt = (context_text or "") + prompt
    
    if model == MODEL_CODE:
        await call_code_stream(bot, event, final_prompt)
    else:
        await call_ctf_stream(bot, event, final_prompt)

# ================= AI插件命令处理 =================
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

# 代码生成命令
code_cmd = on_command("代码", aliases={"编程", "写代码"}, priority=10, block=True)

@code_cmd.handle()
async def handle_code(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_prompt = args.extract_plain_text().strip()
    if not user_prompt:
        await bot.send(event, "（请提供代码生成内容，例如：/代码 写一个Python的快速排序算法）")
        return
    await handle_with_reference(bot, event, user_prompt, model=MODEL_CODE)

# CTF解题命令
ctf_cmd = on_command("ctf", aliases={"CTF", "解题", "安全"}, priority=10, block=True)

@ctf_cmd.handle()
async def handle_ctf(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_prompt = args.extract_plain_text().strip()
    if not user_prompt:
        await bot.send(event, "（请提供CTF题目内容，例如：/ctf 分析这个密码学题目）")
        return
    # 按照modelfile中的要求，添加格式化前缀
    formatted_prompt = f"CTF题目分析：{user_prompt}\n请提供解题思路："
    await handle_with_reference(bot, event, formatted_prompt, model=MODEL_CTF)

# 帮助命令
help_cmd = on_command("help", aliases={"帮助"}, priority=10, block=True)

@help_cmd.handle()
async def handle_help(bot: Bot, event: MessageEvent):
    messages = [
        f"{BOT_PREFIX}AI编程与CTF助手功能说明：",
        "1. /代码 内容 - AI 代码生成与解释",
        "2. /ctf 内容 - AI CTF题目分析与解题思路",
        "3. /记忆 开/关 - 开启/关闭多轮上下文记忆",    
        "4. /清除记忆 - 清除记忆上下文",
        "5. /状态 - 查看记忆状态",
        "6. /提示开/提示关 - 开启/关闭启动提示",
        "7. 群聊需 @ 才会响应；私聊可直接对话",
        "8. 系统待机时无法使用AI功能，请等待连接恢复"
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
matcher = on_message(priority=10, block=False)

@matcher.handle()
async def handle_message(bot: Bot, event: MessageEvent, state: T_State):
    msg = event.get_plaintext().strip()
    is_private = not hasattr(event, "group_id") or event.group_id is None
    user_id = str(event.user_id)
    current_time = time.time()

    # 清理过期的等待响应用户（10分钟内未发送消息则过期）
    for uid in list(awaiting_response_users.keys()):
        if current_time - awaiting_response_users[uid] > 600:
            del awaiting_response_users[uid]

    # 检查是否为命令类消息（以/开头），如果是则不处理
    if msg.startswith("/"):
        return

    # 检查Ollama连接状态
    # if not OLLAMA_AVAILABLE:
    #     await matcher.finish(f"{BOT_PREFIX}系统正在待机中，等待Ollama连接恢复...")
    #     return

    # 仅@机器人时响应（群聊），私聊直接响应
    if is_private or getattr(event, "to_me", False) or getattr(event, "is_tome", lambda: False)():
        if not msg:
            await matcher.finish(f"{BOT_PREFIX}你想让我帮你解决什么问题呢？")
            return
        # 默认使用代码模型进行回复
        await call_code_stream(bot, event, msg)
    else:
        # 未@机器人，不处理消息
        return