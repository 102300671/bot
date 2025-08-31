from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Event, Message
import httpx
from bs4 import BeautifulSoup

# 搜索小说命令
search_novel = on_command("/小说搜索", aliases={"/搜索小说"}, priority=5)

@search_novel.handle()
async def handle_search(bot: Bot, event: Event, message: Message = None):
    if message:
        keyword = message.extract_plain_text().strip()
        if not keyword:
            await search_novel.finish("请提供小说关键词")
    else:
        await search_novel.finish("请提供小说关键词")
    
    search_url = f"https://www.bilinovel.com/search?query={keyword}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(search_url, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for item in soup.select(".novel-item"):
        title_tag = item.select_one(".novel-title")
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)
        link = "https://www.bilinovel.com" + title_tag.get("href", "")
        author_tag = item.select_one(".novel-author")
        author = author_tag.get_text(strip=True) if author_tag else "未知"
        results.append(f"{title} - 作者: {author}\n链接: {link}")

    if results:
        await search_novel.finish("\n\n".join(results[:5]))
    else:
        await search_novel.finish("未找到相关小说")

# 获取章节内容命令
get_chapter = on_command("/小说章节", aliases={"/看章节"}, priority=5)

@get_chapter.handle()
async def handle_chapter(bot: Bot, event: Event, message: Message = None):
    if message:
        url = message.extract_plain_text().strip()
        if not url.startswith("http"):
            await get_chapter.finish("请提供有效的章节链接")
    else:
        await get_chapter.finish("请提供章节链接")

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")

    content_tag = soup.select_one(".chapter-content")
    if not content_tag:
        await get_chapter.finish("无法获取章节内容")
    
    content = content_tag.get_text("\n", strip=True)
    if len(content) > 1000:
        content = content[:1000] + "\n...\n[内容过长，未完全显示]"
    
    await get_chapter.finish(content)