"""GUI 桌面窗口入口。

pywebview + Flask 桌面应用:
- 无边框窗口 (frameless)，自定义标题栏
- WindowApi 暴露给前端 (minimize/maximize/close/drag)
- 窗口关闭时等待领取任务完成 (最多 30s)
- --cli 参数转发到 CLI 模式
"""

import logging
import os
import sys
import threading
import time

import webview

from etalien.db import init_db
from gui import claim_manager
from gui.api import PORT_END, PORT_START, create_app, find_free_port

logger = logging.getLogger(__name__)


# ── WindowApi ────────────────────────────────────────────────────

class WindowApi:
    """通过 pywebview.expose() 暴露给前端 JS 的窗口控制 API。

    前端调用: pywebview.api.minimize() 等。
    """

    def __init__(self, window, server):
        self._window = window
        self._server = server
        self._shutdown_called = False

    def minimize(self):
        self._window.minimize()

    def maximize(self):
        if self._window.maximized:
            self._window.restore()
        else:
            self._window.maximize()

    def restore(self):
        self._window.restore()

    def is_maximized(self) -> bool:
        return bool(self._window.maximized)

    def get_position(self) -> dict:
        return {"x": self._window.x, "y": self._window.y}

    def move_window(self, x: int, y: int) -> None:
        self._window.move(x, y)

    def close(self):
        """关闭窗口，带任务保护。"""
        self._shutdown()

    def _shutdown(self):
        if self._shutdown_called:
            return
        self._shutdown_called = True
        logger.info("正在关闭...")

        max_wait = 30
        waited = 0
        while waited < max_wait:
            if not claim_manager.running:
                break
            if waited == 0:
                logger.warning("有领取任务正在运行，等待任务完成...")
            time.sleep(1)
            waited += 1

        if self._server:
            try:
                self._server.shutdown()
                logger.info("服务器已关闭")
            except Exception as e:
                logger.warning("关闭服务器时出错: %s", e)

        self._window.destroy()


# ── 环境检查 ─────────────────────────────────────────────────────

def _check_webview2_runtime() -> bool:
    """检查 Edge WebView2 Runtime 是否已安装。"""
    import platform
    if platform.system() != "Windows":
        return True  # 非 Windows 跳过

    try:
        import winreg
        paths = [
            r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
            r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        ]
        for p in paths:
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, p)
                pv, _ = winreg.QueryValueEx(key, "pv")
                winreg.CloseKey(key)
                # 版本 >= 86.x
                major = int(pv.split(".")[0])
                if major >= 86:
                    return True
            except OSError:
                continue
    except ImportError:
        return True  # 非 Windows 跳过

    return False


# ── 主入口 ───────────────────────────────────────────────────────

def main() -> None:
    # --cli 转发
    if "--cli" in sys.argv:
        _run_cli()
        return

    # 环境检查
    if not _check_webview2_runtime():
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            "需要 Edge WebView2 Runtime 才能运行 GUI 界面。\n\n"
            "请下载安装: https://go.microsoft.com/fwlink/p/?LinkId=2124703\n"
            "或使用 CLI 模式: etalien --cli",
            "环境缺失",
            0x30,  # MB_ICONWARNING
        )
        sys.exit(1)

    # 确保 stdout/stderr 不为 None（GUI 模式）
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w')

    # 初始化数据库
    init_db()

    # 启动 Flask
    app = create_app()
    port = find_free_port()

    # 用 Werkzeug 直接启动（可 shutdown）
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", port, app, threaded=True)

    def run_flask():
        server.serve_forever()

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # 等待 Flask 就绪
    import requests
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            requests.get(url, timeout=0.5)
            break
        except requests.RequestException:
            time.sleep(0.1)

    # 屏幕居中
    try:
        import tkinter as tk
        root_tk = tk.Tk()
        screen_w = root_tk.winfo_screenwidth()
        screen_h = root_tk.winfo_screenheight()
        root_tk.destroy()
    except Exception:
        screen_w, screen_h = 1920, 1080

    width, height = 960, 720
    x = (screen_w - width) // 2
    y = (screen_h - height) // 2

    # 创建窗口
    window = webview.create_window(
        title="外星仔加速器 - 免广告自动领时长",
        url=url,
        width=width,
        height=height,
        x=x,
        y=y,
        min_size=(720, 540),
        resizable=True,
        frameless=True,
        easy_drag=False,
        background_color="#0b0b0d",
    )

    # 暴露窗口 API
    api = WindowApi(window, server)
    window.expose(
        api.minimize, api.maximize, api.restore, api.close,
        api.is_maximized, api.get_position, api.move_window,
    )

    # 任务栏最小化修复 (frameless 窗口)
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            def _fix_taskbar():
                hwnd = window.native.Handle.ToInt32()
                GWL_STYLE = -16
                WS_MINIMIZEBOX = 0x20000
                style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_MINIMIZEBOX)
            window.events.shown += _fix_taskbar
        except Exception:
            pass

    webview.start(debug=False)

    # 兜底清理
    if not api._shutdown_called:
        if server:
            server.shutdown()
        logger.info("窗口已关闭")


def _run_cli() -> None:
    """--cli 模式: 转发到 CLI 入口。"""
    import ctypes

    if sys.platform == "win32":
        # 分配控制台
        ctypes.windll.kernel32.AllocConsole()
        sys.stdin = open("CONIN$", "r")
        sys.stdout = open("CONOUT$", "w")
        sys.stderr = open("CONOUT$", "w")

    from etalien.main import main as cli_main
    cli_argv = [a for a in sys.argv[1:] if a != "--cli"]
    cli_main(cli_argv)


if __name__ == "__main__":
    main()
