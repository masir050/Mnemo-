"""启动服务：切到项目根、查依赖、没有数据先灌一遍、挑空闲端口、拉起 uvicorn。"""
from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "mnemo.db"
SEED = ROOT / "scripts" / "seed.py"

DEFAULT_PORT = 8731
NEEDED = ("fastapi", "uvicorn", "langgraph")


def say(msg: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(msg, flush=True)


def missing_deps() -> list[str]:
    return [m for m in NEEDED if importlib.util.find_spec(m) is None]


def pick_port(want: int) -> int:
    """端口被占就往后顺延。"""
    for port in range(want, want + 20):
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    return want


def ensure_data(py: str) -> bool:
    if DB.exists():
        return True
    say("[*] 记忆库还没建，先灌一遍演示语料（78 条）...")
    return subprocess.run([py, str(SEED)], cwd=str(ROOT)).returncode == 0


def serve(host: str = "127.0.0.1", port: int | None = None, open_browser: bool = True) -> int:
    # IDE 的运行键和工作目录不一定在项目根，先钉死，免得 data/ 跑到别处去
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    py = sys.executable
    say(f"[*] 解释器：{py}")

    lack = missing_deps()
    if lack:
        say("")
        say(f"[x] 这个解释器里缺依赖：{', '.join(lack)}")
        say("    说明 IDE 选的不是项目里的 .venv，或者 .venv 是坏的（用别的 Python 装过包）。")
        say("    修法（项目根目录跑一次，几十秒）：")
        say("        python scripts\\bootstrap.py --check")
        say("    装完再点运行键就能起。")
        return 1

    if not ensure_data(py):
        say("[x] 灌演示数据失败，看上面的报错")
        return 1

    port = pick_port(int(port or os.environ.get("MNEMO_PORT", DEFAULT_PORT)))
    url = f"http://{host}:{port}/"
    say("")
    say(f"  Mnemo 记忆面板   {url}")
    say(f"  接口文档         {url}docs")
    say("  停服务：控制台按 Ctrl+C，或点 IDE 的红色方块停止键")
    say("")

    if open_browser:
        threading.Thread(
            target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True
        ).start()

    import uvicorn

    uvicorn.run("mnemo.api:app", host=host, port=port, log_level="info")
    return 0
