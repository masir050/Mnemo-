# Mnemo · 面向岗位交接的组织记忆系统

把散在即时消息、工单、周报里的岗位信息，沉淀成可查询、可纠正、可追溯的记忆资产。
新人接手时不用再问「这活儿现在归谁」「截止日改过没有」「当时为什么这么定」。

Python + LangGraph 编排，SQLite 单文件存储，不依赖任何外部服务。

| 能力 | 说明 |
|---|---|
| 从原始消息抽记忆 | 规则模板（正则 + 句式骨架）为主，小模型兜底；抽成「实体 — 谓词 — 断言」三元组，带来源、时间、置信度 |
| 记住改过什么 | 同一事实的新值不覆盖旧值，走版本链；旧值降权保留，能回答「当时是谁改的、依据哪句话」 |
| 只注入相关的 | 按当前问题检索记忆子图，相关度 / 时效 / 重要性三维打分，在 token 预算内择优注入 |
| 冲突不瞎猜 | 同一事实有多个版本时按时间窗判定现值；判不了就并列输出「现值 + 候选 + 置信」，标成待确认 |
| 越用越准 | 面板里改错，系统同时留下人工确认值、绑定原句的负例约束、一条泛化规则；规则可查看、可停用 |
| 过程可观测 | SSE 逐节点回传「解析 → 检索 → 消解 → 组装 → 成文」 |

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
- **记忆**：按实体列出现值、状态、置信度；点一条看变更链。
- **纠错**：记忆条目右侧「修正」，输入新值 = 改正，输入 `!` = 标记误抽，直接回车 = 确认现值。
- **规则**：查看学到/手写的规则，可停用。

改完再问一遍同一个问题，答案里的「⚠ 另有记录，需确认」会消失。

## 三、灌数据

语料是 JSON Lines，一行一条消息（`data/corpus.jsonl` 有样例）：

```json
{"id": "m001", "channel": "im", "speaker": "张伟", "ts": "2026-10-08T09:12:00+08:00", "thread": "支付网关交接", "text": "支付网关的负责人是张伟。"}
```

- `channel` 决定来源权重：`ticket` 0.90 > `im` 0.78 > `weekly` 0.66。
- 单条灌入：`POST /api/ingest {"text": "...", "speaker": "...", "ts": "..."}`。
- 重灌：`.\.venv\Scripts\python.exe scripts\seed.py`（重建库）。

## 四、接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/ask?q=` | 一次问答，返回答案 + 事实 + 注入内容 + trace |
| GET | `/api/ask/stream?q=` | 同上，SSE 逐节点回传 |
| POST | `/api/ingest` | 灌一条原始消息并抽取 |
| POST | `/api/corrections` | `correct` / `reject` / `confirm` |
| GET | `/api/entities` · `/api/assertions` · `/api/chain/{id}` · `/api/messages/{id}` | 面板数据与回溯 |
| GET | `/api/rules` · POST `/api/rules/{id}/toggle` | 规则查看 / 启停 |
| GET | `/api/stats` · `/api/health` | 运行状态 |

完整列表：`http://127.0.0.1:8731/docs`。

## 五、验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```
```
66 passed in 2.01s
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

终端演示完整闭环：

```powershell
.\.venv\Scripts\python.exe scripts\demo.py
```

## 六、目录

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
├── graph_app.py   LangGraph 编排
├── feedback.py    纠正 → 负例 → 泛化规则
├── api.py         HTTP + SSE
└── web/index.html 记忆面板
scripts/  bootstrap.py · seed.py · demo.py · eval_context_cost.py · eval_convergence.py
data/     corpus.jsonl（78 条演示语料）· questions.json（金标准）
tests/    66 个用例
```

## 七、配置

环境变量，均有默认值（见 `mnemo/config.py`）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `MNEMO_DB` | `data/mnemo.db` | 库路径 |
| `MNEMO_PORT` | `8731` | 起始端口 |
| `MNEMO_TOKEN_BUDGET` | `900` | 单次注入预算（token） |
| `MNEMO_MIN_EXTRACT_CONF` | `0.35` | 低于此值不进库 |
| `MNEMO_LLM_PROVIDER` | `offline` | 设 `openai` 并配 `MNEMO_LLM_BASE_URL` / `MNEMO_LLM_API_KEY` 启用小模型兜底 |

## 八、已知边界

- 检索是词面匹配（中文二元组）+ 意图词表，没有向量召回。
- 规则表针对交接话术（负责人 / 备份 / 截止 / 状态 / 流程 / 风险 / 环境），换领域需补谓词表和规则。
- 实体归一为精确 / 别名 / 相似度三档，阈值 0.88；被截断的名字会建成两个实体。
- SQLite + 单进程，面向单团队、几万条消息规模。
