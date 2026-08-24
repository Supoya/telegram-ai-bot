"""
stickers.py —— 小星的「表情包收藏」模块
- 你发给她的贴纸，她收藏起来（存 file_id，去重）
- 回复时若模型想发表情包（输出 [[sticker]] 标记），她从收藏里随机挑一个发；
  收藏为空时用默认贴纸包 (Kitties) 兜底。
"""
import json
import logging
import os
import random

LOG = logging.getLogger("xiaoxing")

STICKER_FILE = os.environ.get("STICKER_FILE", "stickers.json")
DEFAULT_PACK = os.environ.get("DEFAULT_STICKER_PACK", "Kitties")  # 兜底贴纸包
MAX_SAVED = int(os.environ.get("MAX_SAVED_STICKERS", "60"))

_saved: list = []      # [{file_id, emoji?}] 收藏（唯一来源）
_loaded = False
_default_ids: list = []  # 默认包的 file_id 缓存


def load() -> list:
    global _saved, _loaded
    if _loaded:
        return _saved
    try:
        with open(STICKER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _saved = data if isinstance(data, list) else []
    except Exception:
        _saved = []
    _loaded = True
    LOG.info("表情包收藏加载完成，共 %d 个", len(_saved))
    return _saved


def save():
    try:
        with open(STICKER_FILE, "w", encoding="utf-8") as f:
            json.dump(_saved, f, ensure_ascii=False)
    except Exception as e:
        LOG.warning("表情包收藏保存失败: %s", e)


def remember(file_id: str):
    """收藏你发来的贴纸 file_id（去重）。"""
    load()
    if file_id in _saved:
        return False
    _saved.append(file_id)
    if len(_saved) > MAX_SAVED:
        del _saved[:-MAX_SAVED]
    save()
    LOG.info("收藏新贴纸，当前共 %d 个", len(_saved))
    return True


async def fetch_default(bot):
    """从默认贴纸包拉取 file_id 作为兜底（启动时预热）。"""
    global _default_ids
    try:
        st = await bot.get_sticker_set(DEFAULT_PACK)
        _default_ids = [s.file_id for s in st.stickers]
        LOG.info("默认贴纸包 %s 加载 %d 个", DEFAULT_PACK, len(_default_ids))
    except Exception as e:
        LOG.warning("默认贴纸包加载失败: %s", e)


def pick():
    """优先从收藏随机挑；收藏空则用默认包(可能为空则返回None)。"""
    load()
    if _saved:
        return random.choice(_saved)
    if _default_ids:
        return random.choice(_default_ids)
    return None


def count():
    load()
    return len(_saved)