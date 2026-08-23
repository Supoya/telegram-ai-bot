"""
providers.py —— 机器人依赖的三块能力：
1. OpenCode-go  = 大脑：文本 + 视觉（认图片/贴纸），OpenAI 兼容接口
2. Gemini        = 耳朵：语音转文字（Gemini 原生支持 audio inline）
3. edge-tts      = 嘴：中文语音合成（XiaoxiaoNeural，无需 API key）
"""
import asyncio
import base64
import io
import os
import re
import tempfile

import edge_tts
import httpx
from openai import AsyncOpenAI

# ---------- 环境配置 ----------
OPENCODE_GO_API_KEY = os.environ["OPENCODE_GO_API_KEY"]
OPENCODE_GO_BASE_URL = os.environ.get("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1")
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
LLM_MODEL = os.environ.get("OPENCODE_LLM_MODEL", "deepseek-v4-flash-vision-exp")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
TTS_VOICE = os.environ.get("TTS_VOICE", "zh-CN-XiaoxiaoNeural")
TTS_RATE = os.environ.get("TTS_RATE", "+0%")
MAX_TOKENS = int(os.environ.get("REPLY_MAX_TOKENS", "350"))

_client = AsyncOpenAI(api_key=OPENCODE_GO_API_KEY, base_url=OPENCODE_GO_BASE_URL)


# ---------- 1) 大脑：对话 + 视觉 ----------
def _messages_to_api(history, new_text="", image_bytes=None, mime="image/jpeg"):
    """history: [{role, content}] 纯文本历史；把当前这轮（文字+可选图片）加工成 API 消息。"""
    msgs = [{"role": m["role"], "content": m["content"]} for m in history]
    if image_bytes:
        data_url = "data:{};base64,{}".format(mime, base64.b64encode(image_bytes).decode())
        content = [
            {"type": "text", "text": new_text or "请你描述一下这张图片。"},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
    else:
        content = new_text or "你好"
    msgs.append({"role": "user", "content": content})
    return msgs


async def llm_chat(history, new_text="", image_bytes=None, mime="image/jpeg"):
    """调用 OpenCode-go 的视觉模型，返回回复文本。"""
    msgs = _messages_to_api(history, new_text, image_bytes, mime)
    resp = await _client.chat.completions.create(
        model=LLM_MODEL,
        messages=msgs,
        max_tokens=MAX_TOKENS,
    )
    text = resp.choices[0].message.content or ""
    return text.strip()


# ---------- 2) 耳朵：Gemini 语音转文字 ----------
_STT_INSTRUCTION = (
    "把这段语音逐字转写成简体中文。你只能输出转写后的文字本身："
    "禁止输出任何解释、翻译、建议、感言、markdown 符号、星号或分隔线。"
)


def _clean_stt(text: str) -> str:
    """去掉 Gemini 偶尔附加的说明性文字/格式，只留转写内容。"""
    if not text:
        return ""
    t = text.replace("**", "").replace("##", "").replace("---", "").strip()
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    # 丢弃明显不是转写的开头行（“为您转写/以下是/顺便……”等）
    bad = re.compile(r"^(您|让|以下?(是|为)|转写|顺便|注(意)?|——|—|\((.*?)\)|【(.*?)】|—|$)", re.I)
    keep = [ln for ln in lines if ln and not bad.match(ln)]
    out = " ".join(keep).strip()
    return out or text


async def transcribe_voice(audio_bytes, mime="audio/ogg"):
    """用 Gemini 把语音字节转成文字。mime 例如 audio/ogg、audio/mpeg。"""
    payload = {
        "contents": [
            {
                "parts": [
                    {"inline_data": {"mime_type": mime, "data": base64.b64encode(audio_bytes).decode()}},
                    {"text": _STT_INSTRUCTION},
                ]
            }
        ]
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(url, headers={"x-goog-api-key": GEMINI_API_KEY}, json=payload)
        r.raise_for_status()
        d = r.json()
    parts = d["candidates"][0]["content"]["parts"]
    raw = "".join(p.get("text", "") for p in parts).strip()
    return _clean_stt(raw)


# ---------- 3) 嘴：edge-tts 语音合成 ----------
async def tts_mp3(text):
    """edge-tts 合成中文语音，返回 mp3 字节。"""
    buf = io.BytesIO()
    comm = edge_tts.Communicate(text, TTS_VOICE, rate=TTS_RATE)
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    return buf.getvalue()


async def tts_ogg_opus(text):
    """mp3 转成 Telegram 语音条要求的 OGG/Opus，返回 ogg 字节（适配 sendVoice）。"""
    mp3 = await tts_mp3(text)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _mp3_to_ogg, mp3)


def _mp3_to_ogg(mp3_bytes) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as fin:
        fin.write(mp3_bytes)
        inp = fin.name
    out = inp + ".ogg"
    try:
        rc = os.system(
            f'ffmpeg -y -i "{inp}" -c:a libopus -b:a 48k -ar 48000 -ac 1 "{out}" >/dev/null 2>&1'
        )
        if rc != 0 or not os.path.exists(out):
            return None
        with open(out, "rb") as f:
            return f.read()
    finally:
        for p in (inp, out):
            if os.path.exists(p):
                os.remove(p)
