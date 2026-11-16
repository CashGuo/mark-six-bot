# 六合彩记账（微信消息推送版）

把微信群 / 好友里的下注文本自动解析、展开成 49 个号码的金额并累加的老虎机式记账工具。

**消息来源已从 Telegram 换成 WxPushTool 的微信监听推送**：WxPushTool 在 Windows 上监听本机微信，把指定会话的新消息以 HTTP POST 推给本程序，本程序验签、AI 解析、记账、落盘。

```
┌─────────────────┐        HTTP POST /wx/hook          ┌──────────────────┐
│  Windows 微信    │ ←── WxPushTool 监听 ──→ 推送 ──→  │  本程序 :8899     │
│  指定群 / 好友    │      HMAC 签名 + 时间戳            │  验签 → AI → 记账  │
└─────────────────┘                                    └──────────────────┘
                                                              ↓
                                                     state/<账号>/numbers_state.json
                                                     records.jsonl / logs
                                                              ↓
                                                     HTTP 查询（浏览器 / curl）
```

---

## 1. 环境准备

```powershell
cd D:\Users\Administrator\Python\mark-six-bot
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

依赖：`openai`（OpenAI 兼容客户端）、`flask`（收推送 + 看板）、`lunardate`（农历生肖）、
`python-dotenv`、`psutil`（进程锁）。**不再依赖 python-telegram-bot。**

## 2. 配置 `.env`

复制 `.env.example` 为 `.env`，填三项：

```ini
# 由 WxPushTool 厂商签发客户授权时下发的 64 位十六进制密钥
WX_PUSH_SECRET=your_64_hex_secret_here

# 任何 OpenAI 兼容端点都能用，默认是阿里云百炼的 qwen3.8-flash
LLM_API_KEY=your_api_key_here
LLM_BASE_URL=https://llm-o8ou6he0iyxcz0ns.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen3.8-flash
LLM_THINKING=0
LLM_TIMEOUT=60

PORT=8899
WX_SKEW_SEC=300

# 可选：一个接收端接多个微信号时，按「客户编号:密钥」配多把（见 6.2）
# WX_ACCOUNT_SECRETS=C0001:984a04eb...,C0002:另一把64位十六进制
```

- `WX_PUSH_SECRET` 必须与厂商给你的那串**完全一致**，否则所有推送都会被 401 拒掉。
- `WX_SKEW_SEC` 是时间戳容差，超过这个窗口的推送视为重放、直接丢弃，默认 5 分钟。
- `LLM_THINKING=0` 关闭思考模式。**实测必须关**，见下面的对照：

| 模式 | 8 条下注文本总耗时 | 平均单条 | `03头` 解析结果 |
|---|---|---|---|
| 开思考（默认） | 86.9 秒 | 10.9 秒 | `heads:["0"]`（**错，落到 0 头**） |
| 关思考（`LLM_THINKING=0`） | 8.6 秒 | 1.07 秒 | `heads:["03"]`（代码归一成 3 头，正确） |

开思考时 `03头` 一条就烧了 46.9 秒、2221 个 reasoning token，最后还是解析错。
关闭后 8/8 正确、全部 1 秒内出结果。

- 想换成别的模型，改 `LLM_BASE_URL` / `LLM_MODEL` 即可，代码不绑定厂商。
- 只填旧名 `DEEPSEEK_API_KEY` 也能跑（会自动走 `api.deepseek.com` / `deepseek-chat`）。
- `WX_SKIP_VERIFY=1` 只在调试时用，生产环境**不要**打开。

## 3. 启动

```powershell
.\venv\Scripts\python.exe main.py
```

正常会打印：

```
📅 当年生肖：马 (01, 13, 25, 37, 49)
🔐 验签：开启
📡 接收地址：http://0.0.0.0:8899/wx/hook
📊 查看数据：http://127.0.0.1:8899/top?format=text
✅ 已就绪，按 Ctrl+C 停止
```

WxPushTool 那边把推送地址填成 `http://<本机内网IP>:8899/wx/hook`。
若第三方程序与 WxPushTool 跑在同一台机器，写 `http://127.0.0.1:8899/wx/hook` 即可。

## 4. 怎么看结果（网页看板）

启动后浏览器直接打开 **http://127.0.0.1:8899/**

版式参照 Excel 记账盘：

- **顶部条**：总金额（红色大字）、生肖年、有注号码数、今日已记账 / 未解析、当前模型与验签状态，
  右侧三个按钮（立即备份 / 刷新 / 清空数据）
- **左侧号码盘**：12 行生肖，每行列出该生肖名下的号码（按 +12 递增，马 = 01/13/25/37/49），
  号码块底色就是该号的波色（红波 / 蓝波 / 绿波），右侧挂累计金额，有金额的加粗
- **右侧总额大小排序**：前 10 名，序号 / 号码 / 生肖 / 金额 / 占比
- **下方**：最近记账表（时间、会话、原文、号码数、本单额、累计）和未解析表

每 **5 秒自动刷新**。模板是单独文件 `templates/dashboard.html`，改配色或版式直接改它，
改完重启一次服务即可生效。页面无任何外部 CDN，断网也能打开。

## 5. 验证

```powershell
# 模拟一次真实推送（验签、去重、记账、拒收全套）
.\venv\Scripts\python.exe tests\verify_push.py --secret <你的WX_PUSH_SECRET>

# 离线验证记账核心（号码表 / 累加 / 落盘 / 备份恢复，不耗 AI 额度）
.\venv\Scripts\python.exe tests\verify_record.py

# 验证「监听对象」清单链路（19 项，纯 HTTP，不打 AI，不碰微信）
.\venv\Scripts\python.exe tests\verify_contacts.py --secret <你的WX_PUSH_SECRET>

# 验证多采集端（多微信账号）：多密钥路由、分账号账本、跨号去重、监听配置隔离（31 项）
.\venv\Scripts\python.exe tests\verify_multi.py --secret <你的WX_PUSH_SECRET>
```

`verify_multi.py` 覆盖 31 项：`C0001`/`C0002` 各用自己的密钥过验签、A 的签名过不了 B 的验签、
时间戳超窗拒收、账号名路径穿越被净化、两个账号各自记账互不影响、合计 = 各账号之和、
第二个微信号收到同一条 → 跳过不重复记账、两个账号的监听 `version` 各自独立、
`GET /wx/targets?account=` 各拉各的、错误签名 401、`/api/accounts` 列出全部账号。
它只往 `t_a` / `t_b` 两个临时账号写数据，跑完自动删掉，不碰真实账本。
（这个脚本用标准库 `urllib`，本项目 venv 里没有 `requests`。）

`verify_contacts.py` 覆盖 19 项：无签名 401、清单上报 200 且落盘、界面可读、
初始未勾选、勾选保存、version 递增、`/wx/targets` 无签名 401 / 带 GET 签名 200、
只写 id 能补名字、重复上报幂等、`items` 非数组返回 400、勾选状态回读一致。

`verify_push.py` 覆盖 9 项：正常推送、重复 msg_id 跳过、错误密钥 401、
过期时间戳 401、非文本消息跳过、`/top`、`/status`、`/recent`。

## 6. 接口清单

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | **网页看板**（浏览器打开就是这个页面；`/?account=xxx` 直接进单号视图） |
| GET | `/health` | 健康检查，返回 `OK` |
| GET | `/api/dashboard` | 看板聚合数据（前端每 5 秒拉一次；`?account=` 只看单号，不传=全部账号合计） |
| GET | `/api/accounts` | 每个账号的累计金额、今日单数、清单项数、监听条数 |
| POST | `/wx/hook` | 接收 WxPushTool 推送（**需要验签**） |
| POST | `/wx/contacts` | 接收 WxPushTool 上报的会话清单（**需要验签**） |
| GET | `/wx/targets` | 供 WxPushTool 拉取监听对象（**GET 专用验签**；多账号带 `?account=`） |
| GET | `/api/contacts?account=` | 页面用：某个账号的会话清单 + 勾选状态（仅本机） |
| POST | `/api/targets` | 页面用：保存勾选并 version+1（仅本机，body 需带 `account`） |
| GET | `/top?n=10&format=text` | 风险 Top N；`format=text` 出纯文本，默认 JSON |
| GET | `/status` | 累计金额、有注号码数、今日记账数 / 未解析数（支持 `?account=`） |
| GET | `/recent?n=10` | 最近处理过的单（内存保留 50 条） |
| GET | `/logs` | 今日日志尾巴（20 行） |
| POST | `/reset?account=` | 清空**某个账号**的数据（必须指定账号，防误清全部，**慎用**） |
| POST | `/backup` | 手动备份全部账号，文件名 `numbers_state_YYYY-MM-DD.json` |
| POST | `/restore/<日期>?account=` | 恢复某账号某天的备份，如 `/restore/2026-11-12?account=j693395002` |

浏览器直接打开 `http://127.0.0.1:8899/top?format=text` 就能看当前风险排行。

### 6.1 在网页上配置监听哪些群 / 好友

看板中间的「监听对象」区块就是干这个的，**不用改任何配置文件**：

1. WxPushTool 启动后会把本机微信的会话清单推过来（`POST /wx/contacts`），落成 `contacts/<account>.json`。
2. 页面里按名称搜索、按类型（群 / 好友 / 公众号）过滤，勾选要监听的，点「保存并下发」。
3. 保存只写 `chat_id` 进 `targets/<account>.json`，同时 `version` 加 1；采集端每 60 秒来
   `GET /wx/targets` 拉一次，**拿到更大的 version 才覆盖自己的监听列表**，1 分钟内生效。

要点：

- **清单只含"有过会话"的对象**。微信只把有会话记录的对象的消息写进本地库；从没聊过的联系人，
  监听了也永远收不到消息，所以不列出来。
- **勾选存 id 不存名字**。群会改名、会重名，只有 `@chatroom` / `wxid_` 这种 id 稳定。
- 清单**有变动才上报**（加群、退群、改名），平时不重复推；`hash` 可以拿来判断界面要不要刷新。
- 群排在最前面（公众号可能上百个，不排序会把真正想监听的群挤到几屏之后）。
- 也可以手改：直接编辑 `targets/<account>.json` 的 `targets` 数组，`version` 保持递增。
- `/api/*` 只允许本机访问（`WX_CONFIG_LOCAL_ONLY=1`，默认开），防止局域网里别人改配置。

**两套签名绝不能混用**：

| 场景 | 签名内容 |
|---|---|
| POST 报文（`/wx/hook`、`/wx/contacts`） | `hex(HMAC_SHA256(secret, "{ts}.{原始body}"))` |
| GET 拉取（`/wx/targets`） | `hex(HMAC_SHA256(secret, "GET./wx/targets.{ts}"))` |

### 6.2 接多个采集端（多个微信账号）

一个接收端可以同时接 2~5 个微信号（多台电脑、或同一台机器跑多个 WxPushTool 实例）。
每个账号的账本、会话清单、监听配置**各自独立**，看板默认看合计，也能切到单号。

**怎么认出是哪个号**：靠三样东西串起来，缺一不可。

| 环节 | 谁带 | 干什么 |
|---|---|---|
| `X-Wx-Cust` 请求头 | 采集端（取自 license 水印的客户编号，如 `C0001`） | 接收端据此**挑密钥**；不参与验签 |
| `X-Wx-Signature` | 采集端 | 用挑出来的那把密钥验签 |
| `account`（body 或 `?account=`） | 采集端（license 绑定的微信号） | 接收端据此**分账本、分清单、分监听配置** |

**配置**（`.env`）：

```ini
# 一个客户编号一把密钥，逗号分隔
WX_ACCOUNT_SECRETS=C0001:984a04eb584d...,C0002:另一把64位十六进制
```

- 配了 `WX_ACCOUNT_SECRETS` 之后，`WX_PUSH_SECRET` 变成**兜底密钥**：请求头没带 `X-Wx-Cust`、
  或者带了但没配过这个编号，就用它验签 —— 所以现在只有一台采集端的老部署不用改任何东西。
- 不配 `WX_ACCOUNT_SECRETS` 也能跑，所有推送共用 `WX_PUSH_SECRET`（等于单账号模式）。
- 密钥就是各客户 license 派生的 `final_secret`，签发 license 时一并给客户。

**数据怎么分**（每个账号一套，互不覆盖）：

```
state/<account>/numbers_state.json     # 这个号的 49 键账本
state/<account>/records.jsonl
state/<account>/unparsed.jsonl
contacts/<account>.json                # 这个号上报的会话清单
targets/<account>.json                 # 这个号的监听对象 + version
backups/<account>/numbers_state_YYYY-MM-DD.json
```

`account` 会被净化（只留字母数字和 `_@.-`，最长 64，去掉首尾 `.`/`-`/空格），
`../../evil` 这种会变成 `evil`，不会跳出数据目录。

**跨账号去重（重要）**：`msg_id` 的格式是 `会话ID-消息序号`。**同一个群被两个微信号同时监听时，
两个号看到的消息序号不一样**，所以光靠 `msg_id` 全局去重会漏 —— 同一单会被记两次、金额翻倍。
为此加了内容指纹：`sha1(chat_id + create_time + sender + content)[:32]`，
存 `seen_fingerprints.json`（上限 50000 条，先进先出）。指纹命中就跳过，两个号的账本都不动。

**看板**：

- 右上角有账号下拉。只有 1 个账号时自动隐藏（合计就等于它）；≥2 个账号时可选
  「全部账号（合计）」或某一个号。
- 合计视图：总金额是各账号之和，最近记账按时间跨账号混排。
- 单号视图：只看这个号的账本。也可以直接开 `http://127.0.0.1:8899/?account=<微信号>`。
- **监听对象必须先在右上角选一个账号**才能勾 —— 因为它按账号存，合计视图下没有"存到哪"可言。

**四个要注意的地方**：

1. `/api/targets` 不传 `account` 时：只有唯一账号才自动选中它，多账号时会落到 `default`。
   页面已经强制先选号；第三方直接调 API 时**必须带 `account`**。
2. `/reset` **必须**带 `account`（否则拒收），避免一个手滑清掉所有号的账本。
3. `/restore/<日期>` 也是按账号恢复，`?account=` 必填。
4. 账号规模按 2~5 个设计（手写密钥 + 界面切号），**没有账号增删管理界面**。

### 6.3 从单账号升级上来

首次用新代码启动时，会自动把旧文件搬进按账号分的目录（用旧 `contacts.json` 里记的 `account`
决定搬到哪个目录，没记录就放 `default`）：`numbers_state.json` / `records.jsonl` /
`unparsed.jsonl` / `contacts.json` / `listener_targets.json` 一次性迁走，账本金额不丢。

## 7. 推送报文格式

WxPushTool 推过来的 body：

```json
{
  "batch_id": "1794448277-5",
  "ts": 1794448277,
  "account": "j693395002",
  "count": 1,
  "messages": [
    {
      "msg_id": "12345678@chatroom-8821",
      "target_id": "12345678@chatroom",
      "chat_name": "华东群",
      "chat_id": "12345678@chatroom",
      "sender": "wxid_abc",
      "sender_name": "张经理",
      "msg_type": "文本",
      "create_time": 1794448270,
      "content": "马鼠鸡各号15米",
      "account": "j693395002",
      "collected_at": "2026-11-12 14:18:09"
    }
  ]
}
```

请求头：

| 头 | 说明 |
|---|---|
| `Content-Type` | `application/json; charset=utf-8` |
| `X-Wx-Timestamp` | 秒级 Unix 时间戳 |
| `X-Wx-Signature` | `hex(HMAC_SHA256(secret, "{ts}.{原始body}"))` |
| `X-Wx-Cust` | 客户编号（如 `C0001`），不参与验签，仅用于排查 |

验签用**原始字节**计算，不做 JSON 重组；比较用常数时间比较，防时序侧信道。

本程序的接收策略：

- `msg_type` 是中文标签，**只处理 `文本`**，`图片 / 语音 / 视频 / 文件 / 链接/卡片` 一律跳过（不耗 AI 额度）。
- `系统消息` 直接跳过。
- `msg_id` 是幂等键，重复推送会被跳过（去重记录持久化到 `processed_ids.json`，重启不丢，保留最近 2000 条）。
- `sender_name` 是采集端解析好的发送者显示名（微信备注优先、其次昵称），采集端解析不到时为空串；
  看板「发送人」列会显示它，为空时回退显示 `sender`。
- 群消息的 `wxid_xxx:\n` 前缀已在采集端剥掉，这里拿到的就是纯文本。
- 一律返回 200（只要验签通过），让 WxPushTool 不再重试；解析失败的单单独落 `unparsed.jsonl` 供人工核对。

## 8. 数据文件

按账号分目录（`<account>` 就是微信号，如 `j693395002`）：

| 文件 | 内容 |
|---|---|
| `state/<account>/numbers_state.json` | 这个号的 49 个号码累计金额，`{"01": 0.0, ...}` |
| `state/<account>/records.jsonl` | 每成功记账一单追加一行（原文、号码数、本单额、累计、Top10 快照） |
| `state/<account>/unparsed.jsonl` | AI 解析失败的单，格式同上但带 `reason` |
| `contacts/<account>.json` | 这个号的微信会话清单（只读，界面从这里出列表） |
| `targets/<account>.json` | 这个号勾选的监听对象，`{"version": n, "targets": [{"id","name"}]}` |
| `backups/<account>/` | 每日备份，保留 7 天 |
| `processed_ids.json` | 已处理的 `msg_id`（全局），用于同号重复推送去重，上限 2000 |
| `seen_fingerprints.json` | 内容指纹（全局），用于**跨账号**去重，上限 50000 |
| `logs/` | 按天滚动日志，保留 30 天 |
| `.bot.lock` | 进程锁，防止重复启动 |

## 9. 和 Telegram 版的差异

| 项 | Telegram 版 | 现在 |
|---|---|---|
| 消息来源 | Bot polling / webhook | WxPushTool 的 HTTP 推送 |
| 回复 | `update.message.reply_text()` 即时回 | **没有回复通道**（微信侧只能单向推） |
| 命令 | `/start /top /reset /backup /restore /logs` | 改成 HTTP 端点（见上表） |
| 查看结果 | 在聊天里看 | 浏览器 / curl / `records.jsonl` |
| 去重 | 内存 set，重启丢，上限 1000 | `processed_ids.json` 持久化，上限 2000 |
| AI 调用 | `async def` 里调同步客户端（假异步） | 改同步函数，逻辑不变 |

**为什么没有回复**：WxPushTool 只做监听和推送，没有"往微信发消息"的能力，
所以 Telegram 那种"发一句话就回你结果"的交互在微信侧做不到。记账结果的呈现改为：
写日志 + `records.jsonl` + HTTP 查询。

## 10. 注意

- **模型侧的两个坑已修在代码里**：
  1. 模型偶尔不按 `{"items":[...]}` 输出，会直接吐裸数组 `[{...}]`；`_extract_items()` 会兜住
     `items` / `data` / `result` / `bets` / 裸数组五种形态。
  2. `03头` 模型会输出 `"03"` 或 `"0"`，直接查表会**静默丢金额**；`norm_head()` 按
     "两位且首位为 0 → 取个位" 归一，`03头` 稳定落到 3 字头（30~39）。
- AI 解析只保证"尽力解析"，号码规则靠 `build_system_prompt` 约束 + Python 查表兜底（`expand_bets` 精确 100%）。
  解析失败不会记账，会落 `unparsed.jsonl` 并在看板上标红。
- 号码表按**农历当年生肖**动态生成，跨年后生肖对应的号码会自动变化，无需改代码。
- 账本**按账号分开**（每个微信号一个 49 键账本），不按群 / 好友分开。多个微信号同时看同一个群时，
  靠内容指纹去重，不会重复记两次。若需要再往下拆到"按群分账本"，得改数据层结构。
- 多密钥靠 `X-Wx-Cust` 选；**`X-Wx-Cust` 不参与验签**，改了它只会导致密钥挑错、验签失败，
  不能靠它伪造身份。
