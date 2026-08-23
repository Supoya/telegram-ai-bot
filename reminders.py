"""
reminders.py —— 「小星」的定时提醒模块
支持：
  - 倒计时：  3分钟后提醒我 / 一小时后叫我 / 半小时后
  - 具体时刻：今天下午3点提醒我 / 明天上午9点叫我晨跑
  - 每天重复：每天晚上8点提醒我喝水 / 每天早上7点叫我起床
持久化到 reminders.json，重启不丢；到点主动给主人发消息。返回 None 表示这
条消息不构成提醒指令（走普通聊天）；否则返回一个解析出的 Reminder。
"""
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta

LOG = logging.getLogger("xiaoxing")

REMINDER_FILE = os.environ.get("REMINDER_FILE", "reminders.json")

# 触发词：消息里出现这些才尝试解析为提醒
TRIGGER = re.compile(r"(提醒|叫我|叫一下|定时|到点|设个闹钟|叫我一次)", re.I)

# 中文数字
_CNMAP = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
SPECIAL_HALF = "HALF"  # 半 小时/点

def _cn2int(s: str):
    """中文/阿拉伯数字串 → int；特殊返回 'HALF'；解析失败返回 None。"""
    s = s.strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s == "半":
        return SPECIAL_HALF
    total, cur = 0, 0
    for ch in s:
        if ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch in _CNMAP:
            cur = _CNMAP[ch]
        else:
            return None
    return total + cur

# ---- 相对时长： (数)(分钟|小时|秒|天)后 ，可省略数字(如 "半小时后") ----
_RE_NUM = r"(?:\d+|[一二两三四五六七八九十半]+)"
_RE_REL = re.compile(
    rf"(?P<num>{_RE_NUM})?\s*个?\s*(?P<unit>分钟|分|小时|个钟|时|秒|天|周|星期)\s*(?:之)?后"
)

# ---- 绝对时刻： [今天|明天|后天|每天|天天][早上|上午|下午|晚上|晚上]H(点(半|mm)?) ----
_RE_DAY = r"(今天|今晚|明天|明天|后天|每天|天天|每天早上|每天)"
_RE_ABS = re.compile(
    rf"(?P<day>每天|天天|每天早上|每天早上|今天|今晚|明天|后天)?"
    rf"\s*(?P<per>清晨|早上|早晨|上午|中午|下午|傍晚|晚上|夜里|凌晨)?"
    rf"\s*(?P<h>{_RE_NUM})点\s*(?:(?P<m>半|{_RE_NUM})分?)?"
)


class Reminder:
    __slots__ = ("when", "repeat_daily", "text")

    def __init__(self, when: datetime, repeat_daily: bool, text: str):
        self.when = when          # 绝对时间 datetime（repeat 时取"分/时"部分）
        self.repeat_daily = repeat_daily
        self.text = text          # 到点要发的话

    def to_dict(self):
        return {
            "when": self.when.strftime("%Y-%m-%d %H:%M:%S"),
            "repeat_daily": self.repeat_daily,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            datetime.strptime(d["when"], "%Y-%m-%d %H:%M:%S"),
            bool(d.get("repeat_daily", False)),
            d["text"],
        )


def _clean_text(text: str) -> str:
    """去掉触发词/时间之后的残留引导语，只留真正要提醒的话；空则给默认。"""
    text = text.strip(" ，,。!！?？")
    # 去掉开头的"提醒(我/你)""叫我""到点""定时"等
    text = re.sub(r"^(?:提醒(?:我|你)?|叫我|叫我一下|定时(?:提醒)?|到点(?:了)?|设个(?:闹钟|提醒)?|该)", "", text).strip()
    text = re.sub(r"^(?:让|帮)(?:我|你)?", "", text).strip(" ，,。!！?？")
    if not text:
        return "到点啦，这件事该做啦！⏰"
    return text


def parse_reminder(msg: str) -> Reminder | None:
    """从一条消息解析提醒；不是提醒/解析不了则返回 None。"""
    if not msg or not TRIGGER.search(msg):
        return None

    now = datetime.now()

    # 1) 相对倒计时
    m = _RE_REL.search(msg)
    if m:
        num = _cn2int(m.group("num")) if m.group("num") else (SPECIAL_HALF if False else None)
        unit = m.group("unit").replace("个钟", "小时").replace("分分钟", "分钟")
        if unit == "分":
            unit = "分钟"
        elif unit == "时":
            unit = "小时"
        mult = {"秒": 1, "分钟": 60, "小时": 3600, "天": 86400, "周": 604800, "星期": 604800}.get(unit)
        if mult is None or (num is None or num == SPECIAL_HALF):
            # 只有 "半小时后" 这类（无数字）或 "半"：按半处理
            if num == SPECIAL_HALF:
                mult = {"分钟": 30, "小时": 1800, "分": 30, "秒": 0.5}.get(unit, 1800)
                delta = timedelta(seconds=mult)
            else:
                return None
        else:
            delta = timedelta(seconds=num * mult) if num else None
            if delta is None:
                return None
        when = now + delta
        text = _clean_text(msg[m.end():]) if m.end() < len(msg) else _clean_text(msg)
        return Reminder(when, False, text)

    # 2) 绝对时刻 / 每天重复
    m = _RE_ABS.search(msg)
    if m:
        day = (m.group("day") or "").strip()
        per = (m.group("per") or "").strip()
        h = _cn2int(m.group("h"))
        mpart = m.group("m")
        if h is None or (h == SPECIAL_HALF):
            return None
        if mpart == SPECIAL_HALF:
            minute = 30
        elif mpart:
            minute = _cn2int(mpart)
            minute = 0 if minute is None or minute == SPECIAL_HALF else minute
        else:
            minute = 0
        # 12/24: 下午/晚上/傍晚/夜里/凌晨
        if per in ("下午", "傍晚", "晚上", "夜里") and 0 <= h < 12:
            h += 12
        elif per == "凌晨" and h == 12:
            h = 0
        if not (0 <= h <= 23 and 0 <= minute <= 59):
            return None

        repeat_daily = "每天" in day
        if repeat_daily or day in ("每天", "天天"):
            when = now.replace(hour=h, minute=minute, second=0, microsecond=0)
            if when <= now:
                when += timedelta(days=1)
        elif "明天" in day:
            when = (now + timedelta(days=1)).replace(hour=h, minute=minute, second=0, microsecond=0)
        elif "后天" in day:
            when = (now + timedelta(days=2)).replace(hour=h, minute=minute, second=0, microsecond=0)
        else:  # 今天 / 今晚 / 默认今天
            when = now.replace(hour=h, minute=minute, second=0, microsecond=0)
            if when <= now:
                when += timedelta(days=1)  # 时刻已过 → 顺延到明天
        text = _clean_text(msg[m.end():]) if m.end() < len(msg) else _clean_text(msg)
        return Reminder(when, repeat_daily, text)

    return None


# ---------- 共享内存存储（一个全局列表，供 bot.py 写入、reminder_loop 读取） ----------
_ITEMS: list = []          # 内存中的提醒列表（唯一真源）
_LOADED = False

async def _load() -> list:
    """把磁盘 JSON 读进全局 _ITEMS（仅启动时调用一次）。"""
    global _ITEMS, _LOADED
    if _LOADED:
        return _ITEMS
    try:
        with open(REMINDER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _ITEMS = [Reminder.from_dict(d) for d in data]
    except Exception:
        _ITEMS = []
    _LOADED = True
    return _ITEMS


def _sort():
    _ITEMS.sort(key=lambda r: r.when)


async def _save():
    data = [r.to_dict() for r in _ITEMS]
    with open(REMINDER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


async def add_reminder(reminder: Reminder) -> None:
    """新增一条提醒：写入全局列表并落盘。供 bot.py 调用。"""
    await _load()
    _ITEMS.append(reminder)
    _sort()
    await _save()


# ---------- 调度循环 ----------
async def reminder_loop(bot, owner_id, *, send_voice_fn):
    """持续后台：共享列表到点就主动给主人发提醒。repeat 的按天重新排。"""
    await asyncio.sleep(int(os.environ.get("REMINDER_START_DELAY", "15")))
    await _load()
    LOG.info("提醒加载完成，当前 %d 条", len(_ITEMS))
    while True:
        now = datetime.now()
        due = [r for r in _ITEMS if r.when <= now]
        if due:
            for r in due:
                try:
                    if send_voice_fn:
                        await send_voice_fn(owner_id, r.text)
                    else:
                        await bot.send_message(owner_id, r.text)
                    LOG.info("提醒触发: %s", r.text[:40])
                except Exception as e:
                    LOG.warning("提醒发送失败: %s", e)
            # 移除触发过的一次性；repeat 的排到下一天
            kept = []
            for r in _ITEMS:
                if r.when > now:
                    kept.append(r)
                elif r.repeat_daily:
                    nxt = r.when + timedelta(days=1)
                    r.when = nxt
                    kept.append(r)
            _ITEMS.clear()
            _ITEMS.extend(kept)
            await _save()
        await asyncio.sleep(int(os.environ.get("REMINDER_CHECK_INTERVAL", "20")))