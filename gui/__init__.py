"""GUI 模块 — ClaimManager 进度追踪器。

线程安全的状态管理器，供 Flask API 和业务层之间传递领取进度。
"""

import copy
import threading
from typing import Any


class ClaimManager:
    """线程安全的领取进度追踪器。

    进度条目结构:
        {
            "phone": str,
            "status": "running" | "done" | "partial" | "error" | "need_login" | "already_done",
            "current": int,       # 已完成数
            "total": int,         # 总数
            "vip_before": int,    # 领取前 VIP 时长
            "vip_after": int,     # 领取后 VIP 时长
            "error": str | None,  # 错误描述
        }
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._running = False
        self._progress: list[dict[str, Any]] = []

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> bool:
        """开始一次领取。返回 False 表示已有领取在运行。"""
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._progress = []
            return True

    def finish(self) -> None:
        """结束领取。"""
        with self._lock:
            self._running = False

    def get_progress(self) -> dict:
        """获取当前进度（前端轮询）。

        Returns:
            {"running": bool, "progress": list[dict]}
        """
        with self._lock:
            return {
                "running": self._running,
                "progress": copy.deepcopy(self._progress),
            }

    def add_progress_entry(self, entry: dict[str, Any]) -> None:
        """添加一个账号的初始进度条目。"""
        with self._lock:
            self._progress.append(entry)

    def update_progress_entry(self, phone: str, updates: dict[str, Any]) -> None:
        """更新指定账号的进度。"""
        with self._lock:
            for entry in self._progress:
                if entry["phone"] == phone:
                    entry.update(updates)
                    break


# 全局单例
claim_manager = ClaimManager()
