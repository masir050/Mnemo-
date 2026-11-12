# Mnemo · 面向岗位交接的组织记忆系统

把散在即时消息、工单、周报里的岗位信息，沉淀成**可查询、可纠正、可追溯**的记忆资产。
新人接手时不用再问「这活儿现在归谁」「截止日改过没有」「当时为什么这么定」。

Python + LangGraph 编排，SQLite 单文件存储，不依赖任何外部服务。

| 能力 | 说明 |
|---|---|
| 从原始消息抽记忆 | 规则模板（正则 + 句式骨架）为主，小模型兜底；抽成「实体 — 谓词 — 断言」三元组，带来源、时间、置信度 |
| 记住改过什么 | 同一事实的新值不覆盖旧值，走版本链；旧值降权保留，能回答「当时是谁改的、依据哪句话」 |
| 只注入相关的 | 按当前问题检索记忆子图，相关度 / 时效 / 重要性三维打分，在 token 预算内择优注入 |
| 冲突不瞎猜 | 同一事实有多个版本时按时间窗判定现值；判不了就并列输出「现值 + 候选 + 置信」，标成待确认 |
| 越用越准 | 面板里改错，系统同时留下人工确认值、绑定原句的负例约束、一条泛化规则；规则可查看、可停用 |
| 过程可观测 | SSE 逐节点回传「解析 → 检索 → 消解 → 组装 → 成文」，答错了能分清是没召回到、抽错了，还是被预算裁掉了 |

```
parse ──▶ retrieve ──▶(召回不足)──▶ widen ──┐
             │◀─────────────────────────────┘
             ▼
          resolve ──▶ assemble ──▶ compose
        （冲突消解）  （压进预算）   （现值+轨迹）
```

---

## 一、在 IDE 里点绿三角启动（推荐）

前提：装好 Python 3.10+。**入口文件是项目根目录的 `main.py`**，不是 `mnemo/api.py`。

1. PyCharm 打开项目目录 `D:\coding\python\mnemo`（直接从这一层打开，别打开它的上级目录）。
2. 看右下角解释器，路径必须是项目里的 `.venv\Scripts\python.exe`（列表里名字可能写成 “Python 3.13 virtualenv at
   ...\mnemo\.venv”，实际版本是 3.12.7，不影响，认路径就行）。不是的话点它 → `Add Interpreter` → `Existing` → 选
   `.venv\Scripts\python.exe`。
3. 在左侧文件树双击打开 **`main.py`**（根目录那个，只有 30 行）。
4. 点右上角绿色三角 `▶`，或者点编辑区左侧行号栏 `if __name__ == "__main__":` 旁边的小三角。
   第一次会在右上角自动生成名为 `main` 的运行配置，之后一直用它。
5. 下方 Run 窗口出现这两行就成功了，点链接或复制到浏览器：

   ```
   [*] 解释器：D:\coding\python\mnemo\.venv\Scripts\python.exe
     Mnemo 记忆面板   http://127.0.0.1:8731/
   ```

6. 浏览器打开 `http://127.0.0.1:8731/`，首页就是「提问 / 记忆 / 规则」三块面板。
7. 停服务：点 Run 窗口左侧红色方块 ■，或 Ctrl+C。**下次再启动前先确认上一次已经停了**（否则端口被占，
   程序会自动顺延到 8732，看 Run 窗口打印的 URL 为准）。

命令行等价（在项目根目录执行）：

```powershell
.\.venv\Scripts\python.exe main.py
```

可选参数：`--port 8741` 指定端口，`--no-browser` 不自动开浏览器。

### 为什么之前点了半天起不来

两个原因，都跟「点哪个文件」和「`.venv` 是坏的」有关，现在都已经修掉了：

| 现象 | 原因 | 现在的状态 |
|---|---|---|
| 点 `mnemo/api.py` 的三角，报 `ImportError: attempted relative import with no known parent package` | 包内模块是相对导入（`from .config import ...`），被当成裸脚本执行时没有父包 | `api.py` 里加了兜底，直接跑它也能起；但入口仍是 `main.py` |
| 点 `mnemo/api.py` 的三角，程序一闪就结束、什么也没打印 | `api.py` 只定义 `app`，没有启动语句，被导入一遍就退出了 | 同上，`main.py` 里显式拉起 uvicorn |
| 报 `ImportError: cannot import name 'ArgsKwargs' from 'pydantic_core._pydantic_core' (unknown location)` | `.venv` 是 3.12，但里面装的是 3.13 的二进制包（cp313 的 `.pyd`），版本对不上 | 已按 3.12 重装，`import fastapi` 通过 |
| 报 `ModuleNotFoundError: No module named 'fastapi'` | IDE 选的不是项目 `.venv`（选到系统 Python 了） | 按上面第 2 步确认解释器 |

### 起不来时先跑这一句

在项目根目录执行，它只检查不启动，缺什么、坏在哪会直接打出来，能顺手修的就修了：

```powershell
.\.venv\Scripts\python.exe scripts\bootstrap.py --check
```

正常输出：

```
[*] 解释器：D:\coding\python\mnemo\.venv\Scripts\python.exe
[*] 版本：3.12.7（64 位）
[*] 依赖就绪

[*] 环境检查通过。接下来两种启动方式都行：
    - IDE 里点 main.py 的绿色三角
    - 命令行 "D:\coding\python\mnemo\.venv\Scripts\python.exe" main.py
```

它判断依赖的方式是**真的 import 一遍**，而不是看包目录在不在——今天这个坑就是「目录在、加载不了」。

### 对不上号时查这张表

| 报错 / 现象 | 处理 |
|---|---|
| `ModuleNotFoundError: No module named 'fastapi'` | IDE 解释器不是 `.venv`：右下角切回 `.venv`，或 `bootstrap.py --check` 装一遍 |
| `ImportError: cannot import name 'ArgsKwargs'` / `DLL load failed` | `.venv` 里混了别的 Python 版本的编译包。删掉整个 `.venv` 目录，再双击 `run.bat`（会自动重建并装依赖） |
| `attempted relative import with no known parent package` | 你在跑 `mnemo/api.py` 或包里其它模块。改跑根目录的 `main.py` |
| 程序跑完立刻退出、没报错 | 同上，跑的是没有启动语句的模块 |
| `[Errno 10048]` / `Address already in use` | 上次的实例没停。看 Run 窗口的 URL，程序已自动顺延到 8732；或手动 `--port 8741` |
| URL 打开是空白页 / 转圈 | 服务没起来，先看 Run 窗口有没有 `Application startup complete` |
| `UnicodeDecodeError: 'gbk' codec can't decode byte 0xa1` | 老版 `requirements.txt` 带中文注释被 pip 按 GBK 解码。现在是纯 ASCII，`git pull` 一下 |
| 面板里一条记忆都没有 | 库没灌数据：`.\.venv\Scripts\python.exe scripts\seed.py` |

---

## 二、不想开 IDE：双击 `run.bat`

首次运行会自动建 `.venv`、装依赖（默认源失败自动换清华镜像）、灌 78 条演示语料，然后起服务并开面板。
端口被占会自动顺延。关掉窗口即停止服务。

PATH 里没有 `python` 的话，用环境变量 `MNEMO_PYTHON` 指到 `python.exe` 全路径。

手动分步（一行一行执行，PowerShell 里**不要整段粘贴**，多行会被拼成一条命令）：

```powershell
cd D:\coding\python\mnemo
```

```powershell
python -m venv .venv
```

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

```powershell
.\.venv\Scripts\python.exe scripts\seed.py
```

```powershell
.\.venv\Scripts\python.exe main.py
```

---

## 三、面板里做什么

- **提问**：输入「支付网关现在谁负责？」，右边实时滚出各阶段事件，答案下面挂引用——点开能看到原始消息。
- **记忆**：按实体列出现值、状态、置信度；点一条看变更链（谁在什么时候改成什么）。
- **纠错**：在记忆条目上点改，输入正确值确认。系统立刻生成新现值，并把这次纠正回写成负例约束 + 泛化规则。
- **规则**：查看学到/手写的规则，可停用；停用后同一句话重放会重新被抽取到。

改完再问一遍同一个问题，答案里那条「⚠ 另有记录，需确认」就消失了——这是纠错闭环在做功。

## 四、灌自己的数据

语料是 JSON Lines，一行一条消息（`data/corpus.jsonl` 有样例）：

```json
{"id": "m001", "channel": "im", "speaker": "张伟", "ts": "2026-10-08T09:12:00+08:00", "thread": "支付网关交接", "text": "支付网关的负责人是张伟。"}
```

- `channel` 决定来源权重：`ticket` 0.90 > `weekly` 0.72 > `im` 0.60；
- 单条灌入不用改文件：`POST /api/ingest {"text": "...", "speaker": "...", "ts": "..."}`；
- 重灌：`.\.venv\Scripts\python.exe scripts\seed.py`（会重建库）。

## 五、接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/ask?q=` | 一次问答，返回答案 + 事实 + 注入内容 + 全链路 trace |
| GET | `/api/ask/stream?q=` | 同上，SSE 逐节点回传 |
| POST | `/api/ingest` | 灌一条原始消息并抽取 |
| POST | `/api/corrections` | `correct` / `reject` / `confirm` |
| GET | `/api/entities` · `/api/assertions` · `/api/chain/{id}` · `/api/messages/{id}` | 面板数据与回溯 |
| GET | `/api/rules` · POST `/api/rules/{id}/toggle` | 学习规则查看 / 启停 |
| GET | `/api/stats` · `/api/health` | 运行状态 |

完整列表看服务起来后的 `http://127.0.0.1:8731/docs`。

## 六、验证与评测

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
冲突 3 → 0，待确认 2 → 0
```

## 七、目录

```
main.py            入口：IDE 点三角跑这个
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
├── graph_app.py   LangGraph 编排（无 langgraph 时走兼容层）
├── feedback.py    纠正 → 负例 → 泛化规则
├── api.py         HTTP + SSE
└── web/index.html 记忆面板（无构建、无前端框架）
scripts/  bootstrap.py（一键启动/体检）· seed.py · demo.py · eval_context_cost.py · eval_convergence.py
data/     corpus.jsonl（78 条演示语料）· questions.json（金标准）
tests/    66 个用例
```

## 八、配置

全部走环境变量，都有默认值（见 `mnemo/config.py`）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `MNEMO_DB` | `data/mnemo.db` | 库路径 |
| `MNEMO_PORT` | `8731` | 起始端口，被占自动顺延 |
| `MNEMO_TOKEN_BUDGET` | `900` | 单次注入预算（token） |
| `MNEMO_MIN_EXTRACT_CONF` | `0.35` | 低于此值不进库 |
| `MNEMO_LLM_PROVIDER` | `offline` | 设 `openai` 并配 `MNEMO_LLM_BASE_URL` / `MNEMO_LLM_API_KEY` 启用小模型兜底 |

`offline` 模式零依赖、可离线跑，抽取质量靠规则；接小模型只影响「规则没覆盖的句式」的召回。

## 九、已知边界

- 检索是词面匹配（中文二元组）+ 意图词表，**没有向量召回**；完全没见过的同义说法会召不回来。
- 规则表针对交接话术（负责人 / 备份 / 截止 / 状态 / 流程 / 风险 / 环境），换领域要先补谓词表和规则。
- 实体归一靠精确 / 别名 / 相似度三档，阈值 0.88；被截断的名字（「治理专项」vs「数据治理专项」）会建成两个实体，面板里能合。
- SQLite + 单进程，面向「一个团队、几万条消息」，不是多租户服务。
