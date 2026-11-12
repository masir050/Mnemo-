# Mnemo · 面向岗位交接的组织记忆系统

把散在即时消息、工单、周报里的岗位信息，沉淀成**可查询、可纠正、可追溯**的记忆资产。
新人接手时不用再问「这活儿现在归谁」「截止日改过没有」「当时为什么这么定」。

Python + LangGraph 编排，SQLite 单文件存储，不依赖任何外部服务，克隆下来就能跑。

---

## 项目做什么

| 能力 | 说明 |
|---|---|
| 从原始消息抽记忆 | 规则模板（正则 + 句式骨架）为主，小模型兜底；抽成「实体 — 谓词 — 断言」三元组，带来源、时间、置信度 |
| 记住改过什么 | 同一事实的新值不覆盖旧值，走版本链；旧值降权保留，能回答「当时是谁改的、依据哪句话」 |
| 只注入相关的 | 按当前问题检索记忆子图，相关度 / 时效 / 重要性三维打分，在 token 预算内择优注入，不是把历史全塞进去 |
| 冲突不瞎猜 | 同一事实有多个版本时按时间窗判定现值；判不了就并列输出「现值 + 候选 + 置信」，标成待确认 |
| 越用越准 | 在记忆面板里改错，系统同时留下人工确认值、绑定原句的负例约束、一条泛化规则；规则可查看、可停用 |
| 过程可观测 | SSE 逐节点回传「解析 → 检索 → 消解 → 组装 → 成文」，答错了能立刻分清是没召回到、抽错了，还是被预算裁掉了 |

一次提问的链路：

```
parse ──▶ retrieve ──▶(召回不足)──▶ widen ──┐
             │◀─────────────────────────────┘
             ▼
          resolve ──▶ assemble ──▶ compose
        （冲突消解）  （压进预算）   （现值+轨迹）
```

---

## 怎么用

### 1. Windows：双击 `run.bat`

首次运行会自动装依赖、灌 78 条演示语料（群聊 / 工单 / 周报混排），然后起服务并打开面板
`http://127.0.0.1:8731`。关掉窗口即停止服务。

它做三件事：建项目内虚拟环境 `.venv`、按 `import` 结果装依赖（缺什么装什么，默认源失败自动换清华镜像）、
没有数据就灌演示语料。端口被占（比如上次没关干净）会自动顺延到 8732、8733……

前提只有一个：装好 Python 3.10+。PATH 里没有 `python` 的话，用环境变量 `MNEMO_PYTHON` 指到 `python.exe` 全路径。

### 2. 命令行

**不想手动折腾就用这个**（等价于双击 run.bat）：

```powershell
python scripts\bootstrap.py
```

排错时用 `python scripts\bootstrap.py --check`：只检查环境不起服务，缺什么、错在哪会直接打出来。

#### 手动分步（一行一行执行）

> PowerShell 里**不要整段粘贴**：多行会被拼成一条命令（提示符变成 `>>` 就是被续行了），
> 于是 `pip install` 被当成 `uvicorn` 的参数，第一步就崩。

第 1 步，进目录并建虚拟环境（只做一次）：

```powershell
cd D:\coding\python\mnemo
```

```powershell
python -m venv .venv
```

第 2 步，装依赖 —— 注意用 `.venv` 里的 python，别用系统的 `python`：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

第 3 步，灌演示语料（重复执行会重建库）：

```powershell
.\.venv\Scripts\python.exe scripts\seed.py
```

第 4 步，起服务：

```powershell
.\.venv\Scripts\python.exe -m uvicorn mnemo.api:app --port 8731
```

#### 起不来时对照这张表

| 报错 | 原因 | 处理 |
|---|---|---|
| `ModuleNotFoundError: No module named 'fastapi'` | 用的是系统 Python，依赖装到别处了 | 第 2/3/4 步都改用 `.\.venv\Scripts\python.exe`；或直接 `run.bat` |
| `UnicodeDecodeError: 'gbk' codec can't decode byte 0xa1` | 旧版 `requirements.txt` 带中文注释，pip 按 GBK 解码 | 已改纯 ASCII，本地已是新版；从 Git 拉旧版的话 `git pull` 即可 |
| `[Errno 10048]` / 端口被占 | 上次的服务没关干净 | 换端口 `--port 8741`，或 `run.bat`（会自动顺延） |
| `Address already in use` 后窗口闪退 | 同上 | 同上 |

不想开网页，终端里看完整闭环（问 → 答 → 纠错 → 再问）：

```powershell
.\.venv\Scripts\python.exe scripts\demo.py
```

### 3. 在面板里做什么

打开首页就是三块：**提问**、**记忆**、**规则**。

- **提问**：输入「支付网关现在谁负责？」，右边实时滚出各阶段事件，答案下面挂着引用——点开能看到原始消息。
- **记忆**：按实体列出现值、状态、置信度；点一条可以看变更链（谁在什么时候改成什么）。
- **纠错**：在记忆条目上点改，输入正确值并确认。系统会立刻生成新现值，并把这次纠正回写成负例约束 + 泛化规则。
- **规则**：查看学到/手写的规则，可停用；停用后同一句话重放就会重新被抽取到。

改完再问一遍同一个问题，答案里那条「⚠ 另有记录，需确认」就消失了——这是纠错闭环在做功。

### 4. 灌自己的数据

语料格式是 JSON Lines，一行一条消息（`data/corpus.jsonl` 有样例）：

```json
{"id": "m001", "channel": "im", "speaker": "张伟", "ts": "2026-10-08T09:12:00+08:00", "thread": "支付网关交接", "text": "支付网关的负责人是张伟。"}
```

- `channel` 决定来源权重：`ticket` 0.90 > `weekly` 0.72 > `im` 0.60；
- 单条灌入不用改文件：`POST /api/ingest {"text": "...", "speaker": "...", "ts": "..."}`。

### 5. 常用接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/ask?q=` | 一次问答，返回答案 + 事实 + 注入内容 + 全链路 trace |
| GET | `/api/ask/stream?q=` | 同上，SSE 逐节点回传 |
| POST | `/api/ingest` | 灌一条原始消息并抽取 |
| POST | `/api/corrections` | `correct` / `reject` / `confirm` |
| GET | `/api/entities` · `/api/assertions` · `/api/chain/{id}` · `/api/messages/{id}` | 面板数据与回溯 |
| GET | `/api/rules` · POST `/api/rules/{id}/toggle` | 学习规则查看 / 启停 |
| GET | `/api/stats` · `/api/health` | 运行状态 |

完整列表见服务起来后的 `http://127.0.0.1:8731/docs`。

### 6. 验证与评测

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q        # 66 个用例：存储 / 抽取 / 组装 / 纠错闭环 / 编排
```
```powershell
.\.venv\Scripts\python.exe scripts\eval_context_cost.py   # 注入成本：平均 8.7%（对比全量拼接）
```
```powershell
.\.venv\Scripts\python.exe scripts\eval_convergence.py    # 纠错收敛：冲突 3 → 0，待确认 2 → 0
```

---

## 目录

```
mnemo/
├── models.py      实体 / 消息 / 断言 / 状态机
├── db.py          schema 与索引（SQLite，WAL）
├── store.py       写断言（版本链、冲突判定）、倒排索引、审计
├── lexicons.py    谓词表、意图词、实体类型、重要性/半衰期
├── extract/       分句 → 规则 → 兜底 → 归一 → 落库
├── retrieve.py    查询解析、召回、三维打分、子图扩展
├── conflict.py    按槽位聚合、现值判定、冲突标注
├── assemble.py    token 预算内的分组渲染
├── graph_app.py   LangGraph 编排（无 langgraph 时走兼容层）
├── feedback.py    纠正 → 负例 → 泛化规则
├── api.py         HTTP + SSE
└── web/index.html 记忆面板（无构建、无前端框架）
scripts/  bootstrap.py（一键启动）· seed.py · demo.py · eval_context_cost.py · eval_convergence.py
data/     corpus.jsonl（演示语料）· questions.json（金标准）
tests/    66 个用例
```

---

## 配置

全部走环境变量，都有默认值（见 `mnemo/config.py`）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `MNEMO_DB` | `data/mnemo.db` | 库路径 |
| `MNEMO_TOKEN_BUDGET` | 900 | 单次注入预算（token） |
| `MNEMO_MIN_EXTRACT_CONF` | 0.35 | 低于此值不进库 |
| `MNEMO_LLM_PROVIDER` | `offline` | 设 `openai` 并配 `MNEMO_LLM_BASE_URL` / `MNEMO_LLM_API_KEY` 启用小模型兜底 |

`offline` 模式零依赖、可离线跑，抽取质量靠规则；接小模型只影响「规则没覆盖的句式」的召回。

---

## 已知边界

- 检索是词面匹配（中文二元组）+ 意图词表，**没有向量召回**；完全没见过的同义说法会召不回来。
- 规则表针对交接话术（负责人 / 备份 / 截止 / 状态 / 流程 / 风险 / 环境），换领域要先补谓词表和规则。
- 实体归一靠精确 / 别名 / 相似度三档，阈值 0.88；被截断的名字（「治理专项」vs「数据治理专项」）会建成两个实体，面板里能合。
- SQLite + 单进程，面向「一个团队、几万条消息」，不是多租户服务。
