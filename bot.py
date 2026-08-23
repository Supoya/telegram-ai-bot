"""
bot.py —— 私人 AI 伙伴机器人「小星」
- 文本 / 照片 / 贴纸 ---> OpenCode-go 视觉模型直接理解
- 语音 / 音频 ---> Gemini 转文字 ---> OpenCode-go ---> edge-tts 语音条回复
- 每天随机 1-2 次主动搭话（语音条）
只响应主人（OWNER_CHAT_ID）。
"""
import asyncio
import io
import json
import logging
import os
import random
import time
from datetime import datetime, timedelta

import aiofiles
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, BufferedInputFile
from aiogram import BaseMiddleware

import prompts
from providers import llm_chat, tts_ogg_opus, transcribe_voice
import reminders

LOG = logging.getLogger("xiaoxing")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---------- 配置 ----------
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OWNER = int(os.environ["OWNER_CHAT_ID"])  # 唯一允许使用机器人的聊天 ID
MAX_HISTORY = int(os.environ.get("MAX_HISTORY", "16"))
PROACTIVE_AS_VOICE = os.environ.get("PROACTIVE_AS_VOICE", "1") == "1"
PROACTIVE_MIN_DAY = int(os.environ.get("PROACTIVE_MIN_DAY", "1"))
PROACTIVE_MAX_DAY = int(os.environ.get("PROACTIVE_MAX_DAY", "2"))
HISTORY_FILE = os.environ.get("HISTORY_FILE", "conversations.json")

bot = Bot(token=TOKEN)  # 纯文本发送，避免模型输出特殊字符触发 HTML 解析错误
dp = Dispatcher()
router = Router()
dp.include_router(router)

# 会话记忆（每个 chat 一轮轮的 {role, content}，持久化到 json，重启不丢）
history: dict = {}


async def _load_history():
    global history
    try:
        async with aiofiles.open(HISTORY_FILE, "r") as f:
            history = json.loads(await f.read())
    except Exception:
        history = {}


async def _save_history():
    try:
        async with aiofiles.open(HISTORY_FILE, "w") as f:
            await f.write(json.dumps(history, ensure_ascii=False))
    except Exception as e:
        LOG.warning("history 保存失败: %s", e)


def _get_hist(key) -> list:
    return history.setdefault(str(key), [])


def _push(key, role, content):
    h = _get_hist(key)
    h.append({"role": role, "content": content})
    del h[:-MAX_HISTORY]
    return h


# ---------- 只响应主人 ----------
class OwnerOnly(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is not None and user.id == OWNER:
            return await handler(event, data)
        return None


router.message.middleware(OwnerOnly())


# ---------- 工具：下载文件字节 ----------
async def _download(file_id) -> bytes:
    f = await bot.get_file(file_id)
    buf = io.BytesIO()
    await bot.download_file(f.file_path, destination=buf)
    return buf.getvalue()


# ---------- 回复 ----------
async def _reply_chat(chat_id, history_key, user_text, *, image_bytes=None, mime="image/jpeg", voice=False):
    await bot.send_chat_action(chat_id, ChatAction.TYPING)
    try:
        hist = [{"role": "system", "content": prompts.SYSTEM_PROMPT}] + _get_hist(history_key)
        resp = await llm_chat(hist, user_text, image_bytes, mime)
    except Exception as e:
        LOG.warning("llm 调用失败: %s", e)
        resp = "嗯……我这边好像接不上话，你再说一遍？"
    if not resp or not resp.strip():
        resp = "嗯……我这边好像接不上话，你再说一遍？"
    _push(history_key, "user", user_text or ("图片" if image_bytes else ""))
    _push(history_key, "assistant", resp)
    await _save_history()
    if voice:
        await _send_voice(chat_id, resp)
    else:
        await bot.send_message(chat_id, resp)


async def _send_voice(chat_id, text):
    await bot.send_chat_action(chat_id, ChatAction.RECORD_VOICE)
    try:
        ogg = await tts_ogg_opus(text)
    except Exception as e:
        LOG.warning("tts 失败: %s", e)
        await bot.send_message(chat_id, text)
        return
    if not ogg:
        await bot.send_message(chat_id, text)
        return
    await bot.send_voice(chat_id, BufferedInputFile(ogg, filename="voice.ogg"))


# ---------- 命令 ----------
@router.message(CommandStart())
async def cmd_start(m: Message):
    await m.answer(
        "嗨，我是小星 ✨ 已经上线。\n"
        "你可以给我发：文字、照片、贴纸、语音（我会用语音条回你）。\n"
        "我还会偶尔主动找你聊两句。"
    )


# ---------- 文本 ----------
@router.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message):
    # 先看是否是定时提醒指令
    rem = reminders.parse_reminder(m.text)
    if rem is not None:
        await reminders.add_reminder(rem)
        when_s = rem.when.strftime("%H:%M" if rem.when.date() == datetime.now().date() else "%m-%d %H:%M")
        if rem.repeat_daily:
            await m.answer(f"好，以后每天 {rem.when.strftime('%H:%M')} 我都提醒你：「{rem.text}」✅")
        else:
            await m.answer(f"收到，{when_s} 提醒你：「{rem.text}」⏰")
        return
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    await _reply_chat(m.chat.id, m.chat.id, m.text)


# ---------- 照片 ----------
@router.message(F.photo)
async def on_photo(m: Message):
    photo = m.photo[-1]
    try:
        data = await _download(photo.file_id)
    except Exception as e:
        LOG.warning("照片下载失败: %s", e)
        return
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    await _reply_chat(m.chat.id, m.chat.id, m.caption or "", image_bytes=data, mime="image/jpeg")


# ---------- 贴纸 ----------
@router.message(F.sticker)
async def on_sticker(m: Message):
    st = m.sticker
    # 动画/视频贴纸无法直接识别，退化为文字描述
    if st.is_animated or st.is_video:
        await _reply_chat(m.chat.id, m.chat.id, "[你发了一个贴纸]")
        return
    try:
        data = await _download(st.file_id)
    except Exception as e:
        LOG.warning("贴纸下载失败: %s", e)
        return
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    await _reply_chat(m.chat.id, m.chat.id, "[你发了一个贴纸]", image_bytes=data, mime="image/webp")


# ---------- 语音 / 音频（转文字 -> 思考 -> 语音条回） ----------
@router.message(F.voice)
async def on_voice(m: Message):
    await bot.send_chat_action(m.chat.id, ChatAction.RECORD_VOICE)
    try:
        data = await _download(m.voice.file_id)
    except Exception as e:
        LOG.warning("语音下载失败: %s", e)
        return
    try:
        text = await transcribe_voice(data, "audio/ogg")
    except Exception as e:
        LOG.warning("语音转写失败: %s", e)
        text = ""
    if not text:
        text = "[你发了语音，但没转出来，我就当没听见啦]"
    await _reply_chat(m.chat.id, m.chat.id, text, voice=True)


@router.message(F.audio | F.document)
async def on_audio(m: Message):
    # 处理音频文件（mp3/wav 等）
    audio = m.audio or m.document
    if audio is None:
        return
    await bot.send_chat_action(m.chat.id, ChatAction.RECORD_VOICE)
    try:
        data = await _download(audio.file_id)
    except Exception as e:
        LOG.warning("音频下载失败: %s", e)
        return
    mime = getattr(audio, "mime_type", None) or "audio/mpeg"
    try:
        text = await transcribe_voice(data, mime)
    except Exception as e:
        LOG.warning("音频转写失败: %s", e)
        text = ""
    if not text:
        text = "[收到一段音频，但我没听清内容]"
    await _reply_chat(m.chat.id, m.chat.id, text, voice=True)


# ---------- 主动搭话调度 ----------
async def _proactive_message():
    from prompts import PROACTIVE_PROMPT_TEMPLATE
    now = datetime.now()
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    tstr = now.strftime("%H:%M")
    prompt = PROACTIVE_PROMPT_TEMPLATE.format(weekday=weekday, time=tstr)
    try:
        hist = [{"role": "system", "content": prompts.SYSTEM_PROMPT}]
        text = await llm_chat(hist, prompt)
    except Exception as e:
        LOG.warning("主动搭话生成失败: %s", e)
        return
    if PROACTIVE_AS_VOICE:
        await _send_voice(OWNER, text)
    else:
        await bot.send_message(OWNER, text)
    LOG.info("已主动搭话: %s", text[:40])


def _todays_targets(now) -> list:
    """生成今天 1-2 个随机搭话时刻（本地时区，09:00-22:00）。"""
    n = random.randint(PROACTIVE_MIN_DAY, PROACTIVE_MAX_DAY)
    hours = []
    for _ in range(n):
        # 在 09:00~22:00 之间随机时刻
        mins = random.randint(9 * 60, 22 * 60)
        hours.append(mins)
    hours.sort()
    today = now.date()
    return [
        datetime.combine(today, datetime.min.time()) + timedelta(minutes=x)
        for x in hours
    ]


async def proactive_loop():
    await asyncio.sleep(20)  # 等 bot 起来
    await _load_history()
    current_day = None
    targets = []
    while True:
        now = datetime.now()
        if now.date() != current_day:
            current_day = now.date()
            targets = _todays_targets(now)
            LOG.info("今日主动搭话计划: %s", [t.strftime("%H:%M") for t in targets])
        upcoming = [t for t in targets if t > now]
        if not upcoming:
            # 今天的都发完了，等几分钟跨天
            await asyncio.sleep(600)
            continue
        wait = (min(upcoming) - now).total_seconds()
        await asyncio.sleep(max(wait, 2))
        now2 = datetime.now()
        fired = [t for t in targets if now < t <= now2]
        targets = [t for t in targets if t > now2]
        for _ in fired:
            try:
                await _proactive_message()
            except Exception as e:
                LOG.warning("主动搭话发送异常: %s", e)


# ---------- 入口 ----------
async def _health_handle(request):
    return web.Response(text="ok")


async def _http_server():
    """给平台一个常驻 HTTP 端点，确认进程活着（Telegram 走长轮询，不需要它）。"""
    app = web.Application()
    app.router.add_get("/healthz", _health_handle)
    port = int(os.environ.get("PORT", "8080"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    LOG.info("健康检查已监听 0.0.0.0:%s", port)


async def main():
    await _load_history()
    asyncio.create_task(_http_server())
    asyncio.create_task(proactive_loop())
    asyncio.create_task(reminders.reminder_loop(bot, OWNER, send_voice_fn=_send_voice))
    LOG.info("小星已启动，模型=%s, owner=%s", os.environ.get("OPENCODE_LLM_MODEL", "deepseek-v4-flash-vision-exp"), OWNER)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
