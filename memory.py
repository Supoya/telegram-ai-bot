"""
memory.py —— 「小星」的跨对话长期记忆
把用户聊过的、值得长期记住的事实（偏好 / 约定 / 计划 / 人名 / 任务）沉淀成一份
长期档案，持久化到 longterm_memory.json。每次回复前注入到上下文，跨对话 / 跨天
也能想起来。

核心思路：
  - 定期从对话历史里用 LLM 提炼“值得长期记住的事实”，与已有记忆去重合并。
  - 回复时把长期记忆作为 system 背景注入（在人格提示词之后、当前对话之前）。
长记忆与运营中的 400 条“当前对话滚动记忆”互相独立、互补。
"""
import json
import logging
import os
from datetime import datetime

LOG = logging.getLogger("xiaoxing")

MEMORY_FILE = os.environ.get("MEMORY_FILE", "longterm_memory.json")
MEMORY_LIMIT = int(os.environ.get("MEMORY_LIMIT", "40"))       # 长期记忆条数上限
EXTRACT_EVERY = int(os.environ.get("MEMORY_EXTRACT_EVERY", "6"))   # 每聊N条提取一次

_items: list = []               # [{fact, ts}] 长期记忆（唯一真源）
_loaded = False


def load() -> list:
    """程序启动时调用一次，把磁盘长期记忆读进内存。"""
    global _items, _loaded
    if _loaded:
        return _items
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _items = data if isinstance(data, list) else []
    except Exception:
        _items = []
    _loaded = True
    LOG.info("长期记忆加载完成，当前 %d 条", len(_items))
    return _items


def save():
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(_items, f, ensure_ascii=False, indent=1)
    except Exception as e:
        LOG.warning("长期记忆保存失败: %s", e)


def all_facts() -> list:
    return [x["fact"] for x in _items]


def _fuzzy_dup(fact: str) -> bool:
    """简单去重：判断新事实是否与已有记忆高度重叠。"""
    if not _items:
        return False
    new = set(_norm(fact))
    for it in _items:
        old = set(_norm(it["fact"]))
        if len(new) > 0 and len(old) > 0 and len(old & new) / max(len(old), 1) >= 0.6:
            return True
    return False


def _norm(s: str):
    return [c for c in s.lower() if c.isalnum()]


async def extract_and_merge(history, llm_chat):
    """把最近那段对话里值得记住的事实提炼进长期记忆；已有的跳过，新去重后加入。"""
    new_facts = await _extract_facts(history, llm_chat)
    added = 0
    for fact in new_facts:
        fact = (fact or "").strip()
        if not fact or len(fact) > 120:
            continue
        if _fuzzy_dup(fact):
            continue
        _items.append({"fact": fact, "ts": datetime.now().strftime("%Y-%m-%d")})
        added += 1
    if added:
        # 触顶裁剪（保留最新）
        if len(_items) > MEMORY_LIMIT:
            del _items[:-MEMORY_LIMIT]
        save()
        LOG.info("长期记忆新增 %d 条，现有 %d 条", added, len(_items))
    return added


async def _extract_facts(history, llm_chat) -> list:
    """把历史文本交给 LLM，提炼成一条条简短事实，返回字符串列表。"""
    if not history:
        return []
    transcript = "\n".join(
        f"{'我' if m.get('role') == 'user' else '小星'}: {m.get('content','')}"
        for m in history[-12:]  # 只看最近一小段，控制成本
    )
    prompt = (
        "下面是一段我和用户的对话。请提炼出其中**值得长期记住**的、关于用户本人的"
        "稳定事实（例如：姓名、职业/专业、籍贯、家庭、喜欢的食物/音乐/运动、习惯、"
        "人生计划、重要日期、关系人物、讨厌的东西等）。\n"
        "- 忽略一次性的寒暄、情绪、临时想法、无关内容。\n"
        "- 一条一行，每行一条独立事实，用陈述句，简短（不超过一句）。\n"
        "- 如果整段没有值得长期记住的稳定事实，就只输出“无”。\n\n"
        + transcript
    )
    try:
        text = await llm_chat([], prompt)
    except Exception as e:
        LOG.warning("长期记忆提取失败: %s", e)
        return []
    facts = [ln.strip("-• ").strip() for ln in text.splitlines() if ln.strip()]
    facts = [f for f in facts if f and f not in ("无", "无。", "没有")]
    return facts


def context_block() -> str:
    """返回注入到 system 的长期记忆文本；没有则返回空串。"""
    if not _items:
        return ""
    lines = "\n".join(f"- {x['fact']}" for x in _items[-MEMORY_LIMIT:])
    return (
        "\n【你长期记得关于我的事实（跨对话记住，聊天中可自然引用，但别强行背名单）】\n"
        + lines
    )