# Mnemo · 面向岗位交接的组织记忆系统

把散在即时消息、工单、周报里的岗位信息，沉淀成可查询、可纠正、可追溯的记忆资产。
新人接手时不用再问「这活儿现在归谁」「截止日改过没有」「当时为什么这么定」。

Python + LangGraph 编排，SQLite 单文件存储。两种问答模式：**固定流水线**（默认，零外部依赖）和 **Agent 循环**（模型自主决定调哪个工具、何时停）。

| 能力 | 说明 |
|---|---|
| 从原始消息抽记忆 | 规则模板（正则 + 句式骨架）为主，小模型兜底；抽成「实体 — 谓词 — 断言」三元组，带来源、时间、置信度 |
| 记住改过什么 | 同一事实的新值不覆盖旧值，走版本链；旧值降权保留，能回答「当时是谁改的、依据哪句话」 |
| 只注入相关的 | 按当前问题检索记忆子图，相关度 / 时效 / 重要性三维打分，在 token 预算内择优注入 |
| 冲突不瞎猜 | 同一事实有多个版本时按时间窗判定现值；判不了就并列输出「现值 + 候选 + 置信」，标成待确认 |
| 越用越准 | 面板里改错，系统同时留下人工确认值、绑定原句的负例约束、一条泛化规则；规则可查看、可停用 |
| 自主检索（Agent） | 模型在多轮循环里自己选工具：检索、读版本链、查冲突、溯源原文；自己判断信息够不够、何时停 |
| 过程可观测 | SSE 逐节点回传；Agent 模式逐步回传「思考 / 调用 / 结果」 |

```
parse ──▶ retrieve ──▶(召回不足)──▶ widen ──┐
             │◀─────────────────────────────┘
             ▼
          resolve ──▶ assemble ──▶ compose
        （冲突消解）  （压进预算）   （现值+轨迹）
```

---

## 一、启动

前提：Python 3.10+。入口文件是项目根目录的 `main.py`。

### 在 IDE 里启动

1. PyCharm 打开项目目录 `D:\coding\python\mnemo`（打开这一层，不要打开它的上级目录）。
2. 右下角解释器选项目里的 `.venv\Scripts\python.exe`。
   没有的话点右下角 → `Add Interpreter` → `Existing` → 选该文件。
3. 打开根目录的 `main.py`。
4. 点右上角绿色三角 `▶`，或点行号栏 `if __name__ == "__main__":` 旁的小三角。
   首次会自动生成名为 `main` 的运行配置，之后一直用它。
5. Run 窗口出现面板地址即启动成功：

   ```
     Mnemo 记忆面板   http://127.0.0.1:8731/
   ```

6. 浏览器打开该地址。
7. 停止：点 Run 窗口的红色方块 ■，或 Ctrl+C。

启动的是 `main.py`，不是 `mnemo/api.py`。`app` 定义在 `api.py` 里，但它不含启动语句。

### 命令行启动

```powershell
cd D:\coding\python\mnemo
.\.venv\Scripts\python.exe main.py
```

可选参数：`--port 8741` 指定端口，`--no-browser` 不自动开浏览器。
默认从 8731 起，端口被占自动顺延到 8732、8733……

### 双击启动

双击 `run.bat`。首次运行会建 `.venv`、装依赖（默认源失败换清华镜像）、灌 78 条演示语料，
然后起服务并开浏览器。关掉窗口即停止。

PATH 里没有 `python` 时，用环境变量 `MNEMO_PYTHON` 指到 `python.exe` 全路径。

### 环境自检

```powershell
.\.venv\Scripts\python.exe scripts\bootstrap.py --check
```

只检查不启动，缺依赖会直接装。

### 启动失败

| 报错 | 处理 |
|---|---|
| `ModuleNotFoundError: No module named 'fastapi'` | 解释器不是 `.venv`。右下角切回 `.venv\Scripts\python.exe` |
| `ImportError: cannot import name 'ArgsKwargs'` / `DLL load failed` | `.venv` 里有别的 Python 版本的编译包。删掉 `.venv` 目录，双击 `run.bat` 重建 |
| `attempted relative import with no known parent package` | 运行的是 `mnemo/api.py` 等包内文件。改跑根目录的 `main.py` |
| 程序一闪就退出、无报错 | 同上，跑的文件里没有启动语句 |
| `[Errno 10048]` / `Address already in use` | 旧实例没停。看 Run 窗口的 URL（已自动顺延），或 `--port 8741` |
| 页面空白 | 服务没起来，看 Run 窗口是否有 `Application startup complete` |
| `UnicodeDecodeError: 'gbk' codec can't decode byte 0xa1` | `git pull` 更新 `requirements.txt`（现为纯 ASCII） |
| 记忆面板没有数据 | `.\.venv\Scripts\python.exe scripts\seed.py` |

---

## 二、面板

打开首页是三块：

- **提问**：输入「支付网关现在谁负责？」，右侧滚动各阶段事件，答案下方挂引用，点开可看原始消息。
- **模式切换**：提问框右上角 `流水线 / Agent`。流水线是固定编排，毫秒级；Agent 由模型自主调工具，几秒到十几秒，面板会逐步显示它的思考、调了哪个工具、工具返回什么。
- **记忆**：按实体列出现值、状态、置信度；点一条看变更链。
- **纠错**：记忆条目右侧「修正」，输入新值 = 改正，输入 `!` = 标记误抽，直接回车 = 确认现值。
- **规则**：查看学到/手写的规则，可停用。

改完再问一遍同一个问题，答案里的「⚠ 另有记录，需确认」会消失。

## 三、Agent 模式

Agent 不是替换流水线，是并列的第二条链路。它把已有的检索、版本链、冲突、溯源包成 5 个工具，交给模型自己决定调用顺序：

| 工具 | 作用 |
|---|---|
| `search_memory` | 自然语言检索，返回候选断言（带 id、置信度、原文） |
| `read_slot` | 读某实体某字段的完整版本链，回答「变过没有」 |
| `list_entities` | 列出实体与已有字段，用来确认叫法（模型检索不到时会自己调这个） |
| `check_conflicts` | 列出所有多值 / 待确认字段 |
| `trace_source` | 给定断言 id，返回原始消息（谁、何时、原话） |

循环有硬上限：最多 6 步、最多 10 次工具调用；超了就让模型基于已有观察收口，并在答案里说明。答案里的 `[a_xxx]` 引用会被逐一核对，引用了不存在的 id 会在界面上标红。

**启用**：复制 `.env.example` 为 `.env`，填三项（任意 OpenAI 兼容端点，DeepSeek / vLLM / Ollama 都行）：

```
MNEMO_LLM_PROVIDER=openai
MNEMO_LLM_BASE_URL=https://api.deepseek.com
MNEMO_LLM_MODEL=deepseek-chat
MNEMO_LLM_API_KEY=你的 key
```

`.env` 已在 `.gitignore` 里，不会被提交。没配也能跑——Agent 按钮会显示「未配置」，流水线不受影响。

## 四、灌数据

语料是 JSON Lines，一行一条消息（`data/corpus.jsonl` 有样例）：

```json
{"id": "m001", "channel": "im", "speaker": "张伟", "ts": "2026-10-08T09:12:00+08:00", "thread": "支付网关交接", "text": "支付网关的负责人是张伟。"}
```

- `channel` 决定来源权重：`ticket` 0.90 > `im` 0.78 > `weekly` 0.66。
- 单条灌入：`POST /api/ingest {"text": "...", "speaker": "...", "ts": "..."}`。
- 重灌：`.\.venv\Scripts\python.exe scripts\seed.py`（重建库）。

## 五、接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/ask?q=` | 一次问答（流水线），返回答案 + 事实 + 注入内容 + trace |
| GET | `/api/ask/stream?q=` | 同上，SSE 逐节点回传 |
| POST | `/api/agent` | 一次问答（Agent），返回答案 + 逐步轨迹 + 引用校验 |
| GET | `/api/agent/stream?q=` | 同上，SSE 每步推一条 |
| GET | `/api/agent/info` | 模型是否就绪、可用工具、步数上限 |
| POST | `/api/ingest` | 灌一条原始消息并抽取 |
| POST | `/api/corrections` | `correct` / `reject` / `confirm` |
| GET | `/api/entities` · `/api/assertions` · `/api/chain/{id}` · `/api/messages/{id}` | 面板数据与回溯 |
| GET | `/api/rules` · POST `/api/rules/{id}/toggle` | 规则查看 / 启停 |
| GET | `/api/stats` · `/api/health` | 运行状态 |

完整列表：`http://127.0.0.1:8731/docs`。

## 六、验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```
```
74 passed in 2.56s
```

```powershell
.\.venv\Scripts\python.exe scripts\eval_context_cost.py
```
```
平均注入成本 8.7%（对比全量拼接）
```

```powershell
.\.venv\Scripts\python.exe scripts\eval_convergence.py
```
```
冲突 3 → 0，需人工确认 2 → 0
```

两条链路对比（15 道金标准题，都调模型，会花几十秒）：

```powershell
.\.venv\Scripts\python.exe scripts\eval_agent.py
```
```
命中   流水线 15/15 = 100.0%    Agent 15/15 = 100.0%
平均延迟  流水线 0.00s    Agent 9.28s
Agent 平均 token 5458/题（流水线不调模型，0）
```

单槽位查值这种题两条链路都满分，Agent 没有准确率收益，但慢 9 倍。差别在多跳和聚合类问题上：

| 问题 | 流水线 | Agent |
|---|---|---|
| 库里哪些字段有冲突、需要我确认？ | 召回 0 条，答「没有检索到」 | 调 `check_conflicts`，列出两处冲突及各自置信度 |
| 我下周接手这个岗位，先看什么？ | 只有「交接手册」一条 | 6 次工具调用，按优先级给出交接手册 / 待确认冲突 / 已暂停任务 |
| 张伟要是请假，支付网关谁接手？ | 把 5 条事实平铺，不回答「谁接手」 | 先查备份字段，再指出负责人本身有冲突 |

终端演示完整闭环：

```powershell
.\.venv\Scripts\python.exe scripts\demo.py
```

## 七、目录

```
main.py            入口
mnemo/
├── serve.py       起服务（挑端口、查依赖、没数据先灌）
├── models.py      实体 / 消息 / 断言 / 状态机
├── db.py          schema 与索引（SQLite，WAL）
├── store.py       写断言（版本链、冲突判定）、倒排索引、审计
├── lexicons.py    谓词表、意图词、实体类型、重要性/半衰期
├── extract/       分句 → 规则 → 兜底 → 归一 → 落库
├── retrieve.py    查询解析、召回、三维打分、子图扩展
├── conflict.py    按槽位聚合、现值判定、冲突标注
├── assemble.py    token 预算内的分组渲染
├── graph_app.py   LangGraph 编排（固定流水线）
├── agent/         Agent 循环
│   ├── tools.py   5 个工具 + JSON schema
│   ├── model.py   OpenAI 兼容客户端 / 脚本化假模型
│   ├── prompt.py  系统提示（只准用工具、必须给来源）
│   └── loop.py    循环、预算、引用校验、轨迹
├── feedback.py    纠正 → 负例 → 泛化规则
├── api.py         HTTP + SSE
└── web/index.html 记忆面板
scripts/  bootstrap.py · seed.py · demo.py · eval_context_cost.py · eval_convergence.py · eval_agent.py
data/     corpus.jsonl（78 条演示语料）· questions.json（金标准）
tests/    74 个用例
```

## 八、配置

环境变量，均有默认值（见 `mnemo/config.py`）。写在 `.env` 里会被自动读取，同名环境变量优先。

| 变量 | 默认 | 说明 |
|---|---|---|
| `MNEMO_DB` | `data/mnemo.db` | 库路径 |
| `MNEMO_PORT` | `8731` | 起始端口 |
| `MNEMO_TOKEN_BUDGET` | `900` | 单次注入预算（token） |
| `MNEMO_MIN_EXTRACT_CONF` | `0.35` | 低于此值不进库 |
| `MNEMO_LLM_PROVIDER` | `offline` | 设 `openai` 并配 `MNEMO_LLM_BASE_URL` / `MNEMO_LLM_API_KEY` 启用小模型兜底 |
| `MNEMO_LLM_MODEL` | `qwen2.5-1.5b-instruct` | 模型名，Agent 与小模型兜底共用 |
| `MNEMO_AGENT_MAX_STEPS` | `6` | Agent 最多几轮 |
| `MNEMO_AGENT_MAX_TOOL_CALLS` | `10` | Agent 最多调几次工具 |
| `MNEMO_AGENT_TIMEOUT` | `60` | 单次模型请求超时（秒） |

## 九、已知边界

- 检索是词面匹配（中文二元组）+ 意图词表，没有向量召回，换个说法就可能召不回。
- 规则表针对交接话术（负责人 / 备份 / 截止 / 状态 / 流程 / 风险 / 环境），换领域需补谓词表和规则。
- 实体归一为精确 / 别名 / 相似度三档，阈值 0.88；被截断的名字会建成两个实体。
- Agent 每题 5k 左右 token、10 秒上下，且完全依赖模型：模型把实体名编错时，循环会白跑几轮（有步数上限兜底，不会挂死）。
- SQLite + 单进程，面向单团队、几万条消息规模。
