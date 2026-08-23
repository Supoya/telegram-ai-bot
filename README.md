# 小星 ✨ —— 你的私人 Telegram AI 伙伴

一个部署在免费云平台、由你自己的 API 驱动的 Telegram 机器人。它像朋友一样：

- **文字 / 照片 / 贴纸** —— 直接理解（走 OpenCode-go 视觉模型）
- **语音** —— 转文字 → 思考 → **用语音条回你**（走 Gemini 转写 + edge-tts 合成）
- **主动搭话** —— 每天随机 1-2 次主动找你聊两句（默认语音条）

> 平台限制说明：Telegram 机器人无法发起真人语音/视频通话，因此「打电话」以**语音消息**形式实现。

## 技术栈
| 模块 | 用什么 |
|---|---|
| Telegram 机器人 | aiogram 3（长轮询） |
| 大脑（文本+视觉） | OpenCode-go `deepseek-v4-flash-vision-exp` |
| 认图/贴纸 | 同一视觉模型（图片以 base64 传入） |
| 语音→文字 | Gemini `gemini-3.6-flash` |
| 文字→语音条 | edge-tts `zh-CN-XiaoxiaoNeural` → ffmpeg 转 OGG/Opus |
| 部署 | Northflank 免费 Sandbox（常驻不睡） |

## 文件结构
- `bot.py` —— 入口：处理消息 + 主动搭话调度 + 健康检查
- `providers.py` —— OpenCode-go 对话 / Gemini 转写 / edge-tts 合成
- `prompts.py` —— 人格 + 主动搭话提示词（改语气就改这里）
- `Dockerfile` —— 含 ffmpeg，容器里直接跑
- `DEPLOY.md` —— **完整部署步骤（BotFather → GitHub → Northflank）**

## 快速开始（本地跑，方便调试）
```bash
cd ~/telegram-ai-bot
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
set -a; source ~/.hermes/.env; set +a     # 让 Key 生效
export TELEGRAM_BOT_TOKEN=你的新token OWNER_CHAT_ID=391640859
.venv/bin/python bot.py
```

## 部署到云端
看 [DEPLOY.md](DEPLOY.md)，约 10 分钟上线，永久免费、常驻不睡。
