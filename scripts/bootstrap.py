"""一键启动：准备环境 -> 装依赖 -> 灌演示数据 -> 起服务。

    python scripts/bootstrap.py            # 启动服务
    python scripts/bootstrap.py --check    # 只检查环境
    python scripts/bootstrap.py --no-browser
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQUIREMENTS = ROOT / "requirements.txt"
DB = ROOT / "data" / "mnemo.db"
SEED = ROOT / "scripts" / "seed.py"
ENTRY = ROOT / "main.py"

# 这些报错说明是「二进制包和解释器版本对不上」，重装整个依赖能救
ABI_HINTS = re.compile(
    r"ArgsKwargs|DLL load failed|unknown location|cannot import name|WinError 126",
    re.IGNORECASE,
)

DEFAULT_PORT = 8731
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


def say(msg: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(msg, flush=True)


def probe(py: str) -> tuple[bool, str]:
    """真正 import 一遍。find_spec 只能证明包目录在，证明不了能加载。"""
    code = "import fastapi, uvicorn, langgraph, pydantic"
    r = subprocess.run([py, "-c", code], capture_output=True, text=True, errors="replace")
    if r.returncode == 0:
        return True, ""
    return False, (r.stderr or r.stdout or "").strip()[-600:]


def pip_install(py: str, force: bool = False) -> bool:
    base = [py, "-m", "pip", "install", "--disable-pip-version-check", "-q"]
    if force:
        base.append("--force-reinstall")
    base += ["-r", str(REQUIREMENTS)]
    for extra in ([], ["-i", MIRROR]):
        label = "默认源" if not extra else "清华镜像"
        say(f"[*] {'重装' if force else '安装'}依赖（{label}）...")
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
    say(f"[*] 版本：{sys.version.split()[0]}（{sys.maxsize.bit_length() + 1} 位）")

    # ---- 2. 依赖：真的 import 一遍，不通过就重装 ----------------------------
    ok, err = probe(py)
    if not ok:
        say("[*] 依赖自检没过，重装...")
        pip_install(py, force=True)
        ok, err = probe(py)
    if not ok:
        say("")
        say("[x] 依赖装不上：")
        if ABI_HINTS.search(err):
            say("    .venv 与解释器版本不一致。删掉 .venv 目录，重跑 run.bat")
        else:
            say(f'    手动装："{py}" -m pip install -r "{REQUIREMENTS}" -i {MIRROR}')
        say("")
        say("    ---- 导入报错 ----")
        say(err or "(无输出)")
        return 1
    say("[*] 依赖就绪")

    # ---- 3. 演示数据 --------------------------------------------------------
    if not DB.exists():
        say("[*] 库里没有数据，灌演示语料...")
        if subprocess.run([py, str(SEED)]).returncode != 0:
            say("[x] 灌数据失败")
            return 1

    if check_only:
        say("")
        say("[*] 环境检查通过。接下来两种启动方式都行：")
        say(f'    - IDE 里点 {ENTRY.name} 的绿色三角')
        say(f'    - 命令行 "{py}" main.py')
        return 0

    # ---- 4. 起服务 ----------------------------------------------------------
    # 端口只在这里挑一次，URL 由 main.py 打印（它才是真正的入口，别印两遍）
    port = pick_port(int(os.environ.get("MNEMO_PORT", DEFAULT_PORT)))

    cmd = [py, str(ENTRY), "--port", str(port)]
    if "--no-browser" in sys.argv:
        cmd.append("--no-browser")
    try:
        return subprocess.call(cmd, cwd=str(ROOT))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
