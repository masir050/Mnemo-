"""项目入口：在 IDE 里点本文件的绿色三角即可起服务。

    python main.py                # 默认 8731，端口被占自动顺延
    python main.py --port 8741    # 指定端口
    python main.py --no-browser   # 不自动开浏览器
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mnemo.serve import serve  # noqa: E402

if __name__ == "__main__":
    argv = sys.argv[1:]
    port = None
    if "--port" in argv:
        port = int(argv[argv.index("--port") + 1])
    sys.exit(serve(port=port, open_browser="--no-browser" not in argv))
