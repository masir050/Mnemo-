"""项目入口 —— 在 IDE 里点这个文件左边行号栏的绿色三角就能起服务。

    PyCharm：打开 main.py，右上角运行配置会自动出现 main，点绿三角。
    VS Code：打开 main.py，右上角 ▷ Run Python File。

为什么入口是这个文件，而不是 mnemo/api.py：
包内模块之间用的是相对导入（from .config import ...），直接当脚本执行时没有父包，
第一行 import 就会抛 ImportError: attempted relative import with no known parent package。
api.py 里也加了兜底（跑它也能起），但入口还是认准 main.py 更省事。

命令行等价用法：
    .venv\\Scripts\\python.exe main.py                # 默认 8731，端口被占自动顺延
    .venv\\Scripts\\python.exe main.py --port 8741    # 指定端口
    .venv\\Scripts\\python.exe main.py --no-browser   # 不自动开浏览器
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
