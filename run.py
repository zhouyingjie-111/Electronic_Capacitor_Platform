"""Start the packaged local website; optional --check performs a smoke check."""

import argparse
import importlib.util
import os
from pathlib import Path
import socket
import sys
import threading
import time
from urllib.request import urlopen
import webbrowser

from settings import ROOT, configure


def choose_port(host, preferred):
    """Use a nearby free port so an existing development server stays intact."""
    if not 1 <= preferred <= 65535:
        raise SystemExit("PORT 必须在 1–65535 之间。")
    for candidate in range(preferred, min(preferred + 30, 65536)):
        with socket.socket() as probe:
            try:
                probe.bind((host, candidate))
                return candidate
            except OSError:
                continue
    raise SystemExit("没有可用端口，请在 config.json 修改 HOST 或 PORT。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="检查依赖、页面和数据，不连接外设"
    )
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    configure()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / "webapp/vendor_public"))
    required = {
        "flask": "Flask",
        "httpx": "httpx",
        "PIL": "Pillow",
        "serial": "pyserial",
        "waitress": "waitress",
        "numpy": "numpy",
    }
    missing = [
        package
        for module, package in required.items()
        if importlib.util.find_spec(module) is None
    ]
    if missing:
        raise SystemExit(
            "缺少依赖：" + ", ".join(missing) + "\n请先运行“安装依赖.bat”。"
        )
    sys.path.insert(0, str(ROOT / "webapp"))
    from app import app

    if args.check:
        from release_checks import smoke_check

        smoke_check(app)
        return
    host, port = os.environ["HOST"], int(os.environ["PORT"])
    selected = choose_port(host, port)
    if selected != port:
        print(f"端口 {port} 已占用，改用 {selected}。", flush=True)
    port = selected
    os.environ["PORT"] = str(port)
    url = f"http://127.0.0.1:{port}/"
    if not args.no_browser:

        def open_when_ready():
            for _ in range(60):
                try:
                    with urlopen(url + "health", timeout=1) as response:
                        if response.status == 200:
                            webbrowser.open(url)
                            return
                except OSError:
                    time.sleep(0.5)

        threading.Thread(target=open_when_ready, daemon=True).start()
    from project_portal import run_local_server

    run_local_server(app)


if __name__ == "__main__":
    main()
