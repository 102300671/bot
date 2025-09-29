from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Message, MessageSegment
from nonebot.plugin import PluginMetadata
from nonebot import on_command
from nonebot.params import CommandArg
from nonebot_plugin_htmlrender import text_to_pic, md_to_pic, html_to_pic, template_to_pic, get_new_page
import logging
import datetime
import asyncio
import base64
from collections import defaultdict, deque
import time
import re
import html as html_escape

# 自定义RateLimiter类，替代原来从concurrent_utils导入的版本
class RateLimiter:
    def __init__(self, max_calls: int, time_window: float):
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls = defaultdict(lambda: deque())
    
    async def acquire(self, key: str) -> bool:
        current_time = time.time()
        
        # 移除过期的调用记录
        while self.calls[key] and self.calls[key][0] < current_time - self.time_window:
            self.calls[key].popleft()
        
        # 检查是否超过限制
        if len(self.calls[key]) < self.max_calls:
            self.calls[key].append(current_time)
            return True
        return False

# 插件元数据
def get_plugin_metadata():
    return PluginMetadata(
        name="HTML渲染演示",
        description="将文本、Markdown和HTML转换为图片的功能演示",
        usage="使用命令：文本转图片 [文本内容]、markdown转图片 [markdown内容]、自定义网页、浏览器操作",
        type="application",
        homepage="N/A",
        supported_adapters={"~onebot.v11"},
    )

__plugin_meta__ = get_plugin_metadata()

# ================= 配置 =================
BOT_PREFIX = "小豆泥："

# 速率限制器
rate_limiter = RateLimiter(max_calls=20, time_window=60.0)  # 每分钟最多20次操作

# ================= 全局黑名单配置 =================
URL_BLACKLIST = [
    "127.0.0.1",
    "localhost",
    "file://",
    "file:",
    "0.0.0.0",
    "::1",
    "169.254.",  # 链路本地地址
    "192.168.",  # 私有地址
    "10.",       # 私有地址
    "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", 
    "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.",  # 私有地址
]

# ================= 辅助函数 =================
def setup():
    logging.info("HTML渲染演示插件已加载")

def is_url_blocked(url: str) -> bool:
    """检查URL是否在黑名单中"""
    url_lower = url.lower()
    for blocked in URL_BLACKLIST:
        if blocked in url_lower:
            return True
    return False

def contains_dangerous_content(html_content: str) -> bool:
    """检查HTML内容是否包含危险的代码"""
    # 方法1：直接检查黑名单内容（处理简单拼接）
    html_lower = html_content.lower()
    for blocked in URL_BLACKLIST:
        if blocked in html_lower:
            return True
    
    # 方法2：尝试解码HTML实体
    try:
        # 简单的HTML实体解码
        decoded_html = html_content
        decoded_html = decoded_html.replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"').replace('&#39;', "'").replace('&amp;', '&')
        
        # 检查解码后的内容
        decoded_lower = decoded_html.lower()
        for blocked in URL_BLACKLIST:
            if blocked in decoded_lower:
                return True
    except:
        pass
    
    # 方法3：检查script标签中的字符串拼接模式
    script_patterns = [
        r"document\.write\s*\(",
        r"document\.writeln\s*\(",
        r"innerHTML\s*=",
        r"outerHTML\s*=",
        r"insertAdjacentHTML\s*\(",
        r"eval\s*\(",
        r"setTimeout\s*\(",
        r"setInterval\s*\(",
        r"Function\s*\(",
    ]
    
    for pattern in script_patterns:
        if re.search(pattern, html_lower, re.IGNORECASE):
            return True
    
    return False

def sanitize_html_content(html_content: str) -> str:
    """彻底清理HTML内容，移除所有脚本和危险元素"""
    
    # 移除所有script标签
    html_content = re.sub(r'<script\b[^>]*>.*?</script>', '', html_content, flags=re.IGNORECASE | re.DOTALL)
    
    # 移除所有事件处理器 (onclick, onload, etc.)
    event_handlers = [
        'onabort', 'onblur', 'onchange', 'onclick', 'ondblclick', 'onerror', 'onfocus',
        'onkeydown', 'onkeypress', 'onkeyup', 'onload', 'onmousedown', 'onmousemove',
        'onmouseout', 'onmouseover', 'onmouseup', 'onreset', 'onresize', 'onselect',
        'onsubmit', 'onunload'
    ]
    
    for handler in event_handlers:
        html_content = re.sub(f'{handler}\\s*=\\s*["\'][^"\']*["\']', '', html_content, flags=re.IGNORECASE)
        html_content = re.sub(f'{handler}\\s*=\\s*[^\\s>]+', '', html_content, flags=re.IGNORECASE)
    
    # 移除危险的标签
    dangerous_tags = ['iframe', 'object', 'embed', 'frame', 'frameset', 'meta']
    for tag in dangerous_tags:
        html_content = re.sub(f'<{tag}\\b[^>]*>.*?</{tag}>', '', html_content, flags=re.IGNORECASE | re.DOTALL)
        html_content = re.sub(f'<{tag}\\b[^>]*>', '', html_content, flags=re.IGNORECASE)
    
    # 移除javascript: URL
    html_content = re.sub(r'\bhref\s*=\s*["\']\s*javascript:\s*[^"\']*["\']', 'href="#"', html_content, flags=re.IGNORECASE)
    html_content = re.sub(r'\bsrc\s*=\s*["\']\s*javascript:\s*[^"\']*["\']', 'src="#"', html_content, flags=re.IGNORECASE)
    
    # 移除包含黑名单URL的任何属性
    for blocked in URL_BLACKLIST:
        html_content = re.sub(f'\\b(?:href|src|action|data)\\s*=\\s*["\'][^"\']*{re.escape(blocked)}[^"\']*["\']', '', html_content, flags=re.IGNORECASE)
    
    return html_content

def create_safe_html_template(html_content: str) -> str:
    """创建安全的HTML模板，禁用所有JavaScript执行"""
    safe_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>安全渲染</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 20px;
                background: white;
            }}
            .security-notice {{
                background: #fff3cd;
                border: 1px solid #ffeaa7;
                padding: 10px;
                margin-bottom: 20px;
                border-radius: 5px;
                color: #856404;
            }}
        </style>
    </head>
    <body>
        <div class="security-notice">
            安全提示：此内容已通过安全过滤，JavaScript已被禁用。
        </div>
        <div id="content">
            {html_content}
        </div>
    </body>
    </html>
    """
    return safe_html

# 示例1：将文本转换为图片
text_to_image_cmd = on_command("text2img", aliases={"文本转图片"}, priority=5, block=True)

@text_to_image_cmd.handle()
async def handle_text_to_image(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    # 速率限制检查
    rate_key = f"htmlrender_{event.get_user_id()}"
    if not await rate_limiter.acquire(rate_key):
        await text_to_image_cmd.finish(f"{BOT_PREFIX}操作过于频繁，请稍后再试")
        return
    
    # 跟踪命令是否已完成
    finished = False
    
    # 获取用户输入的文本
    text = args.extract_plain_text()
    if not text:
        await text_to_image_cmd.finish(f"{BOT_PREFIX}请输入要转换的文本，例如：文本转图片 你好世界")
        finished = True
        return
    
    # 调用text_to_pic方法将文本转换为图片
    try:
        img_bytes = await text_to_pic(
            text=text,
            width=600,
            height=400,
            font_size=20
        )
        
        # 发送图片
        await text_to_image_cmd.finish(MessageSegment.image(f"base64://{base64.b64encode(img_bytes).decode()}"))
        finished = True
        return
    except Exception as e:
        logging.error(f"文本转图片出错: {e}")
        if not finished:
            try:
                await text_to_image_cmd.finish(f"{BOT_PREFIX}生成图片失败，请稍后重试")
                finished = True
            except:
                pass

# 示例2：将Markdown转换为图片
md_to_image_cmd = on_command("md2img", aliases={"markdown转图片"}, priority=5, block=True)

@md_to_image_cmd.handle()
async def handle_md_to_image(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    # 速率限制检查
    rate_key = f"htmlrender_{event.get_user_id()}"
    if not await rate_limiter.acquire(rate_key):
        await md_to_image_cmd.finish(f"{BOT_PREFIX}操作过于频繁，请稍后再试")
        return
    
    # 跟踪命令是否已完成
    finished = False
    
    # 获取用户输入的Markdown文本
    md_text = args.extract_plain_text()
    if not md_text:
        # 如果用户没有输入内容，使用默认的Markdown示例
        md_text = """# 标题示例
## 二级标题
这是一个**粗体**文本和*斜体*文本的示例。

- 列表项1
- 列表项2

```python
print("Hello, World!")
```"""
    
    try:
        # 调用md_to_pic方法将Markdown转换为图片
        img_bytes = None
        try:
            img_bytes = await md_to_pic(
                md=md_text,
                width=800
            )
        except Exception as inner_e:
            logging.error(f"Markdown转图片过程出错: {inner_e}")
            raise
        
        if img_bytes:
            # 准备base64编码的图片数据
            try:
                base64_img = f"base64://{base64.b64encode(img_bytes).decode()}"
                message = MessageSegment.image(base64_img)
                # 发送图片
                await md_to_image_cmd.finish(message)
                finished = True
                return  # 发送成功后直接返回，避免后续异常
            except Exception as send_e:
                logging.error(f"发送图片失败: {send_e}")
                # 不再调用finish，避免FinishedException
                return  # 发送失败也直接返回
    except Exception as e:
        logging.error(f"Markdown转图片总体出错: {e}")
        if not finished:
            try:
                await md_to_image_cmd.finish(f"{BOT_PREFIX}生成图片失败，请稍后重试")
                finished = True
            except:
                pass

# 示例3：使用自定义HTML模板
custom_html_cmd = on_command("customhtml", aliases={"自定义网页"}, priority=5, block=True)

@custom_html_cmd.handle()
async def handle_custom_html(bot: Bot, event: MessageEvent):
    # 速率限制检查
    rate_key = f"htmlrender_{event.get_user_id()}"
    if not await rate_limiter.acquire(rate_key):
        await custom_html_cmd.finish(f"{BOT_PREFIX}操作过于频繁，请稍后再试")
        return
    
    # 跟踪命令是否已完成
    finished = False
    
    # 自定义HTML内容
    html_content = """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>自定义网页示例</title>
        <style>
            body {
                font-family: 'Microsoft YaHei', sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                text-align: center;
                padding: 50px;
                margin: 0;
            }
            h1 {
                font-size: 36px;
                margin-bottom: 20px;
            }
            .content {
                background: rgba(255, 255, 255, 0.1);
                padding: 30px;
                border-radius: 10px;
                backdrop-filter: blur(10px);
            }
        </style>
    </head>
    <body>
        <div class="content">
            <h1>欢迎使用HTML渲染插件</h1>
            <p>这是一个使用nonebot-plugin-htmlrender生成的自定义网页</p>
            <p>当前时间: {{ current_time }}</p>
        </div>
    </body>
    </html>
    """
    
    try:
        # 准备模板数据
        template_data = {
            "current_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        # 使用template_to_pic方法渲染自定义HTML
        # 直接将HTML内容作为第一个参数传入，不使用命名参数
        img_bytes = await template_to_pic(
            html_content,  # 直接传入HTML内容作为模板
            wait=1,
            viewport={
                "width": 800,
                "height": 600
            },** template_data
        )
        
        # 发送图片
        await custom_html_cmd.finish(MessageSegment.image(f"base64://{base64.b64encode(img_bytes).decode()}"))
        finished = True
        return
    except Exception as e:
        logging.error(f"自定义HTML渲染出错: {e}")
        if not finished:
            try:
                await custom_html_cmd.finish(f"{BOT_PREFIX}生成图片失败，请稍后重试")
                finished = True
            except:
                pass

# 示例4：使用get_new_page直接操作浏览器页面
browser_operation_cmd = on_command("browser", aliases={"浏览器操作"}, priority=5, block=True)

@browser_operation_cmd.handle()
async def handle_browser_operation(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    # 速率限制检查
    rate_key = f"htmlrender_{event.get_user_id()}"
    if not await rate_limiter.acquire(rate_key):
        await browser_operation_cmd.finish(f"{BOT_PREFIX}操作过于频繁，请稍后再试")
        return

    # 跟踪命令是否已完成
    finished = False

    # 获取用户输入的URL
    url = args.extract_plain_text().strip()

    # 使用全局黑名单校验
    if is_url_blocked(url):
        await browser_operation_cmd.finish(f"{BOT_PREFIX}禁止访问该地址")
        return

    # 如果用户没有提供URL或URL为空，使用默认URL
    if not url:
        url = "https://www.baidu.com"
        await browser_operation_cmd.send(f"{BOT_PREFIX}正在访问默认网页：{url}")
    else:
        # 简单的URL格式验证和补充
        if not url.startswith(("http://", "https://")):
            url = 'https://' + url
        await browser_operation_cmd.send(f"{BOT_PREFIX}正在访问网页：{url}")

    img_bytes = None
    try:
        # 获取一个新的浏览器页面 (使用async with正确语法)
        async with get_new_page() as page:
            # 导航到用户指定的网页
            await page.goto(url, timeout=60000)  # 设置60秒超时

            # 等待页面加载完成
            await page.wait_for_load_state("networkidle", timeout=60000)

            # 截图
            img_bytes = await page.screenshot(full_page=True)

        # 在页面资源释放后再发送图片，避免嵌套的异步操作导致问题
        if img_bytes:
            try:
                # 准备图片数据
                base64_img = f"base64://{base64.b64encode(img_bytes).decode()}"
                message = MessageSegment.image(base64_img)
                # 发送图片
                await browser_operation_cmd.finish(message)
                finished = True
                return
            except Exception as send_e:
                logging.error(f"发送图片失败: {send_e}")
                # 不再调用finish，避免FinishedException
    except Exception as e:
        logging.error(f"浏览器操作出错: {e}")
        if not finished:
            try:
                # 只有在命令尚未完成的情况下才尝试发送失败消息
                await browser_operation_cmd.finish(f"{BOT_PREFIX}获取网页截图失败，请稍后重试")
                finished = True
            except:
                pass
            
# 示例5：使用自定义html_to_pic函数转换HTML为图片
html_to_image_cmd = on_command("html2img", aliases={"html转图片"}, priority=5, block=True)

@html_to_image_cmd.handle()
async def handle_html_to_image(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    rate_key = f"htmlrender_{event.get_user_id()}"
    if not await rate_limiter.acquire(rate_key):
        await html_to_image_cmd.finish(f"{BOT_PREFIX}操作过于频繁，请稍后再试")
        return

    html_text = args.extract_plain_text()
    if not html_text:
        html_text = """
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {background-color: powderblue;}
                h1 {color: blue;}
                p {color: red;}
            </style>
        </head>
        <body>
            <h1>这是一个标题</h1>
            <p>这是一个段落。</p>
        </body>
        </html>
        """
    else:
        # 严格的安全检查
        if contains_dangerous_content(html_text):
            await html_to_image_cmd.finish(f"{BOT_PREFIX}检测到危险的HTML内容，禁止渲染")
            return
        
        # 彻底清理HTML内容
        html_text = sanitize_html_content(html_text)
        
        # 再次检查清理后的内容
        if contains_dangerous_content(html_text):
            await html_to_image_cmd.finish(f"{BOT_PREFIX}HTML内容无法安全处理，禁止渲染")
            return
        
        # 使用安全模板包装用户内容
        html_text = create_safe_html_template(html_text)

    try:
        img_bytes = await html_to_pic(
            html=html_text,
            wait=1000,
            type="png",
            device_scale_factor=2.0,
            full_page=True
        )

        if img_bytes:
            base64_img = f"base64://{base64.b64encode(img_bytes).decode()}"
            message = MessageSegment.image(base64_img)
            await html_to_image_cmd.finish(message)
            return

        await html_to_image_cmd.finish(f"{BOT_PREFIX}生成图片失败，图片数据为空")
        return

    except Exception as e:
        if type(e).__name__ != "FinishedException":
            logging.error(f"HTML转图片出错: {e}")
        # 不发送任何消息给用户
        pass

# 初始化插件
setup()