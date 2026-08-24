"""
web_tools.py —— 小星的联网工具箱
1) web_search: 搜 DuckDuckGo(免key)，返回标题+链接，供她推 YouTube/网站
2) send_photo: 从 picsum 免费图库抓一张应景图发给你（按关键词定 seed）
由模型通过 tool calling 触发。
"""
import asyncio
import html
import logging
import random
import re
import urllib.parse

import httpx

LOG = logging.getLogger("xiaoxing")

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


async def web_search(query: str, max_results: int = 5) -> list:
    """DuckDuckGo 搜索（html + lite 双端点、重试、抗限流），返回 [{title, url}]。失败返回空。"""
    out = []
    attempts = 0
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        while attempts < 4 and not out:
            attempts += 1
            endpoint = "html" if attempts % 2 == 1 else "lite"
            url = (
                f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
                if endpoint == "html"
                else f"https://lite.duckduckgo.com/lite/?q={urllib.parse.quote(query)}"
            )
            ua = random_ua()
            try:
                r = await c.get(url, headers={"User-Agent": ua})
                if r.status_code != 200:
                    await asyncio.sleep(1)
                    continue
                if endpoint == "html":
                    pat = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
                    pairs = pat.findall(r.text)
                else:
                    # lite 端点：<a rel="nofollow" class="result-link" href="...">标题</a>
                    pat = re.compile(r'class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
                    pairs = pat.findall(r.text)
                for href, title in pairs[:max_results]:
                    urlx = href
                    m = re.search(r"uddg=([^&]+)", href)
                    if m:
                        urlx = urllib.parse.unquote(m.group(1))
                    title_txt = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
                    if urlx and title_txt:
                        out.append({"title": title_txt, "url": urlx})
                if not out:
                    await asyncio.sleep(1)
            except Exception as e:
                LOG.warning("web_search 第%d次失败: %s", attempts, e)
                await asyncio.sleep(1)
    return out


def random_ua():
    uas = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.4 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15",
    ]
    return random.choice(uas)


async def search_for_links(query: str):
    """返回给模型的搜索结果文本（标题+链接），供它选一条合适的推给你。"""
    results = await web_search(query)
    if not results:
        return "（搜索无结果，请礼貌说明暂时没找到）"
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['url']}")
    return "\n".join(lines)


async def fetch_photo(seed_key: str, width: int = 800, height: int = 500) -> bytes | None:
    """从 picsum 抓一张图。seed_key 决定图（用关键词做 seed，稳定对应同一张）。"""
    # seed 只要字母数字，去掉空格/斜杠等
    seed = re.sub(r"[^A-Za-z0-9一-龥]+", "", seed_key or "daily") or "daily"
    url = f"https://picsum.photos/seed/{seed}/{width}/{height}"
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as c:
            r = await c.get(url)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                return r.content
    except Exception as e:
        LOG.warning("fetch_photo 失败: %s", e)
    return None