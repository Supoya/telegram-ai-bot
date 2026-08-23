# 🤖 部署手册：把「小星」部署到 Northflank（免费常驻）

你的机器人代码已写好并在本地测通。下面是完整的上线步骤，照着做大约 10 分钟。

---

## 0. 你要准备的三样东西
- 一个 **Telegram Bot Token**（步骤 1）
- 一个 **GitHub 账号**（步骤 2，Northflank 从这个仓库构建）
- 你已经有的 Key：`OPENCODE_GO_API_KEY`、`GEMINI_API_KEY`
  （它们在 `~/.hermes/.env` 里；Northflank 那边手动粘贴，不经过我）

---

## 1. 在 Telegram 里建一个「新」机器人（拿 Bot Token）

> ⚠️ 一定要**新建**一个，**不要**用 Hermes 现在用的那个 token（会冲突）。

1. 打开 Telegram，搜索 **@BotFather**（蓝色认证号）。
2. 发送 `/newbot`
3. 给它起名（如 `Xiaoxing`），再给一个 `@用户名`（必须以 `bot` 结尾，如 `Xiaoxing_xbot`）。
4. BotFather 会发给你一长串 **Bot Token**，形如 `7890123456:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`。
5. **复制保存**，下一步要用。
6. （可选）给它设个中文描述：发送 `/setdescription` → 输入“你的私人 AI 伙伴小星✨”。

**小提示**：`OWNER_CHAT_ID` 默认填了 `391640859`（你在 Hermes 里的 Telegram 用户ID）。等机器人建好后，你给它发一句 `/start`，如果以后需要确认身份，可以用它返回的 chat id。想查自己的 id 也可以给 @userinfobot 发消息。

---

## 2. 把代码推到 GitHub

1. 在 GitHub 新建一个**私有仓库**（推荐 private），比如叫 `telegram-ai-bot`。
2. 在你的电脑终端执行（把地址换成你仓库的）：

```bash
cd ~/telegram-ai-bot
git init
git add -A
git commit -m "小星 AI 伙伴机器人"
git branch -M main
git remote add origin https://github.com/<你的用户名>/telegram-ai-bot.git
git push -u origin main
```

> 已在 `.gitignore` 里排除 `.env` 和 `conversations.json`，不会把你的 Key 提交上去。

---

## 3. 在 Northflank 创建服务

1. 打开 **app.northflank.com** → 注册账号（免费 Sandbox）。
   - **需要一张信用卡做验证，但不会扣费**（Sandbox 层 $0，直到你主动升级）。
2. 创建 **Project**（如 `xiaoxing`）。
3. 点 **+ Service** → 选 **Service**（从源码构建）。
4. 连接你的 **GitHub** → 选仓库 `telegram-ai-bot` → 选 `main` 分支。
5. 构建方式选 **Dockerfile**（它会用我们写好的 Dockerfile，自动装 ffmpeg）。
6. **Port**：留空让它用默认即可（我们的健康检查监听 `$PORT`，默认 8080）。
   - 资源大小：免费 Sandbox 只能选最小档。挑默认最小的就行（够用）。
7. 填 **环境变量（Environment Variables）**——这是最关键的一步：

   | 变量 | 值 |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | 步骤1拿到的 token |
   | `OWNER_CHAT_ID` | `391640859` |
   | `OPENCODE_GO_API_KEY` | 你 `~/.hermes/.env` 里的值 |
   | `OPENCODE_GO_BASE_URL` | `https://opencode.ai/zen/go/v1` |
   | `GEMINI_API_KEY` | 你 `~/.hermes/.env` 里的值 |
   | `TZ` | `Europe/Berlin` （主动搭话按你的本地时间）|

8. 点 **Deploy**。等构建完成（1-2 分钟）。

部署成功后，容器里的 `python bot.py` 会：
- 启动 **http 健康检查**（`/healthz` 返回 `ok`，平台由此确认进程常驻）
- 启动 Telegram **长轮询**，随时收你消息
- 后台跑**主动搭话调度**（每天随机 1-2 次，语音条）

---

## 4. 测试

1. 用你自己的 Telegram 给新机器人发：`你好` → 应回一段文字。
2. 发一张**照片** → 它会描述看到的内容。
3. 发一个**贴纸** → 认出来并回你。
4. 发一条**语音** → 它转文字→思考→用**语音条**回你。
5. 等几分钟后它会**主动搭话**（默认语音条）。

---

## 5. 常用配置（环境变量，可在 Northflank 随时改，改后 Redeploy）

| 变量 | 默认值 | 作用 |
|---|---|---|
| `PROACTIVE_AS_VOICE` | `1` | 主动搭话发语音条；设 `0` 改发文字 |
| `PROACTIVE_MIN_DAY` / `PROACTIVE_MAX_DAY` | `1` / `2` | 每天主动搭话的随机次数区间 |
| `MAX_HISTORY` | `16` | 保留对话轮数（省 token） |
| `REPLAY_MAX_TOKENS` | `350` | 每次回复最大 token |
| `TTS_VOICE` | `zh-CN-XiaoxiaoNeural` | 语音用的女声 |
| `TTS_RATE` | `+0%` | 语速（`+10%` 更快） |
| `OPENCODE_LLM_MODEL` | `deepseek-v4-flash-vision-exp` | 大脑模型 |
| `GEMINI_MODEL` | `gemini-3.6-flash` | 语音转文字模型 |

---

## 6. 常见问题

- **主动搭话没来**：`TZ` 没设成 `Europe/Berlin` 会导致它按 UTC 时段发。另外 Northflank 免费 Sandbox 若被平台限制为「休眠」则无法主动发——本方案依赖 Sandbox 常驻，如遇此情况需换 Oracle 永久免费主机。
- **语音回不了（只有文字）**：容器里没装 ffmpeg。确认部署时用的是 Dockerfile 构建（我们已在里面 `apt-get install ffmpeg`）。
- **改人格/语气**：编辑 `prompts.py` 里的 `SYSTEM_PROMPT`，重新提交推送，Northflank 会自动重构建。
- **想重置对话记忆**：删除运行目录下的 `conversations.json`（或直接在 Northflank 里重开容器）。
