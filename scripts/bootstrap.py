"""一键启动：准备环境 -> 装依赖 -> 灌演示数据 -> 起服务。

被 run.bat 调用，也可以在终端里直接跑：

    python scripts/bootstrap.py            # 启动服务并打开面板
    python scripts/bootstrap.py --check    # 只检查环境，不起服务（排错用）
    python scripts/bootstrap.py --no-browser

为什么要有这个文件：run.bat 只能判断"命令有没有报错"，判断不了"依赖装没装"。
把流程放进 Python，出错能打印人能看懂的原因，而不是一个窗口一闪就没。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQUIREMENTS = ROOT / "requirements.txt"
DB = ROOT / "data" / "mnemo.db"
SEED = ROOT / "scripts" / "seed.py"

# 运行期真正要 import 到的包；有一个缺失就重装
NEEDED = ("fastapi", "uvicorn", "langgraph")

DEFAULT_PORT = 8731
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


def say(msg: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(msg, flush=True)


def missing_deps(py: str) -> list[str]:
    code = "import importlib.util as u,sys;sys.exit(0 if all(u.find_spec(m) for m in %r) else 1)" % (NEEDED,)
    return [] if subprocess.run([py, "-c", code], capture_output=True).returncode == 0 else list(NEEDED)


def pip_install(py: str) -> bool:
    base = [py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(REQUIREMENTS)]
    for extra in ([], ["-i", MIRROR]):
        label = "默认源" if not extra else "清华镜像"
        say(f"[*] 安装依赖（{label}）...")
        if subprocess.run(base + extra).returncode == 0:
            return True
        say(f"[!] {label}装失败了，换下一个方式")
    return False


def pick_port(want: int) -> int:
    """端口被占（常见：上次的服务没关干净）就顺延，别让用户去改配置。"""
    for port in range(want, want + 10):
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    return want


def main() -> int:
    check_only = "--check" in sys.argv

    # ---- 1. 让当前解释器「跑进」项目的 .venv -------------------------------
    if not VENV_PY.exists() and os.environ.get("MNEMO_NO_VENV") != "1":
        say(f"[*] 首次运行，创建虚拟环境 .venv（用 {sys.executable}）...")
        if subprocess.run([sys.executable, "-m", "venv", str(VENV)]).returncode != 0:
            say("[!] 建 venv 失败，退回用当前解释器（依赖会装到它身上）")
            os.environ["MNEMO_NO_VENV"] = "1"
    if VENV_PY.exists() and Path(sys.executable).resolve() != VENV_PY.resolve():
        return subprocess.call([str(VENV_PY), str(Path(__file__).resolve())] + sys.argv[1:])

    py = sys.executable
    say(f"[*] 解释器：{py}")

    # ---- 2. 依赖：按 import 结果判断，不看文件 ------------------------------
    if missing_deps(py):
        if not pip_install(py) or missing_deps(py):
            say("")
            say("[x] 依赖装不上。请手动执行下面这行，看具体报错：")
            say(f'    "{py}" -m pip install -r "{REQUIREMENTS}" -i {MIRROR}')
            return 1
    say("[*] 依赖就绪")

    # ---- 3. 演示数据 --------------------------------------------------------
    if not DB.exists():
        say("[*] 库里没有数据，灌演示语料...")
        if subprocess.run([py, str(SEED)]).returncode != 0:
            say("[x] 灌数据失败")
            return 1

    if check_only:
        say("[*] 环境检查通过。去掉 --check 就会启动服务。")
        return 0

    # ---- 4. 起服务 ----------------------------------------------------------
    port = pick_port(int(os.environ.get("MNEMO_PORT", DEFAULT_PORT)))
    url = f"http://127.0.0.1:{port}/"
    say("")
    say(f"  Mnemo 记忆面板  {url}")
    say("  关掉这个窗口 / 按 Ctrl+C 即停止服务")
    say("")

    if "--no-browser" not in sys.argv:
        threading.Thread(target=lambda: (time.sleep(2.0), webbrowser.open(url)), daemon=True).start()

    try:
        return subprocess.call(
            [py, "-m", "uvicorn", "mnemo.api:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(ROOT),
        )
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
