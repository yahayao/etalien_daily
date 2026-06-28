"""Windows Service 包装器（纯 ctypes，零外部依赖）。

作为 Windows 服务运行，每日在 schedule_time 自动执行 CLI 领取。
用 ``sc create`` 安装、``sc delete`` 卸载，或通过 GUI 设置页管理。

入口：
    etalien --service       启动为 Windows 服务
"""

import ctypes
import logging
import sys
import time
from ctypes import wintypes
from datetime import datetime, date

logger = logging.getLogger(__name__)

# ── Win32 常量 ──────────────────────────────────────────────────────

SERVICE_WIN32_OWN_PROCESS = 0x00000010
SERVICE_ACCEPT_STOP = 0x00000001
SERVICE_ACCEPT_SHUTDOWN = 0x00000004
SERVICE_RUNNING = 0x00000004
SERVICE_STOPPED = 0x00000001
SERVICE_START_PENDING = 0x00000002
SERVICE_STOP_PENDING = 0x00000003

SERVICE_CONTROL_STOP = 0x00000001
SERVICE_CONTROL_SHUTDOWN = 0x00000005
SERVICE_CONTROL_INTERROGATE = 0x00000004

NO_ERROR = 0
WAIT_OBJECT_0 = 0x00000000
INFINITE = 0xFFFFFFFF

# ── 结构体 ──────────────────────────────────────────────────────────


class SERVICE_STATUS(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", wintypes.DWORD),
        ("dwCurrentState", wintypes.DWORD),
        ("dwControlsAccepted", wintypes.DWORD),
        ("dwWin32ExitCode", wintypes.DWORD),
        ("dwServiceSpecificExitCode", wintypes.DWORD),
        ("dwCheckPoint", wintypes.DWORD),
        ("dwWaitHint", wintypes.DWORD),
    ]


class SERVICE_TABLE_ENTRYW(ctypes.Structure):
    _fields_ = [
        ("lpServiceName", wintypes.LPWSTR),
        ("lpServiceProc", ctypes.c_void_p),
    ]


# 回调类型
HandlerExProc = ctypes.WINFUNCTYPE(
    wintypes.DWORD,  # return
    wintypes.DWORD,  # dwControl
    wintypes.DWORD,  # dwEventType
    wintypes.LPVOID,  # lpEventData
    wintypes.LPVOID,  # lpContext
)

ServiceMainProc = ctypes.WINFUNCTYPE(
    None,
    wintypes.DWORD,  # dwNumServicesArgs
    ctypes.POINTER(wintypes.LPWSTR),  # lpServiceArgVectors
)

# ── 全局状态 ────────────────────────────────────────────────────────

_status_handle: int = 0
_stop_event: int = 0  # Win32 event handle
_service_status: SERVICE_STATUS | None = None


def _report_status(
    current_state: int,
    exit_code: int = NO_ERROR,
    wait_hint: int = 0,
) -> None:
    global _service_status
    if _service_status is None:
        _service_status = SERVICE_STATUS()
        _service_status.dwServiceType = SERVICE_WIN32_OWN_PROCESS
    _service_status.dwCurrentState = current_state
    _service_status.dwWin32ExitCode = exit_code
    _service_status.dwWaitHint = wait_hint
    if current_state == SERVICE_RUNNING:
        _service_status.dwControlsAccepted = SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN
    else:
        _service_status.dwControlsAccepted = 0
    _service_status.dwCheckPoint += 1
    ctypes.windll.advapi32.SetServiceStatus(
        wintypes.HANDLE(_status_handle),
        ctypes.byref(_service_status),
    )


# ── 控制处理器 ─────────────────────────────────────────────────────


@HandlerExProc
def _handler(control: int, _event_type: int, _event_data, _context) -> int:
    if control in (SERVICE_CONTROL_STOP, SERVICE_CONTROL_SHUTDOWN):
        logger.info("收到服务停止信号 (control=%d)", control)
        _report_status(SERVICE_STOP_PENDING)
        # 设置停止事件
        ctypes.windll.kernel32.SetEvent(wintypes.HANDLE(_stop_event))
        return NO_ERROR
    elif control == SERVICE_CONTROL_INTERROGATE:
        return NO_ERROR
    return 0x00000000  # ERROR_CALL_NOT_IMPLEMENTED 返回给其他 control


# ── 服务主循环 ─────────────────────────────────────────────────────


def _service_worker() -> None:
    """服务主循环：每日定时执行领取。"""
    from etalien.db import get_db_path, init_db
    from etalien.db import get_accounts as db_get_accounts
    from etalien.db import get_settings as db_get_settings
    from etalien.service import run_concurrent_claim

    init_db()
    db_path = get_db_path()
    last_claim_date: date | None = None

    logger.info("定时领取服务已启动")

    while True:
        # 等待 60 秒或停止信号
        ret = ctypes.windll.kernel32.WaitForSingleObject(
            wintypes.HANDLE(_stop_event),
            60000,  # 60 秒
        )
        if ret == WAIT_OBJECT_0:
            # 收到停止信号
            logger.info("服务正在停止")
            break

        try:
            settings = db_get_settings(db_path=db_path)
            if not settings.get("schedule_enabled", False):
                continue
            if settings.get("schedule_method", "schtasks") != "service":
                continue

            schedule_time = settings.get("schedule_time", "08:00")
            now = datetime.now()
            today = now.date()

            if last_claim_date == today:
                continue

            try:
                hour, minute = map(int, schedule_time.split(":"))
            except (ValueError, TypeError):
                continue

            scheduled_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if abs((now - scheduled_dt).total_seconds()) > 60:
                continue

            logger.info("到达定时时间 %s，开始执行领取", schedule_time)

            accounts = db_get_accounts(enabled_only=True, db_path=db_path)
            if not accounts:
                logger.warning("没有启用的账号，跳过")
                last_claim_date = today
                continue

            results = run_concurrent_claim(accounts, settings)
            ok = sum(1 for r in results if r["status"] in ("ok", "already_done"))
            fail = len(results) - ok
            logger.info("领取完成: %d 成功, %d 失败", ok, fail)
            last_claim_date = today

        except Exception:
            logger.exception("定时领取执行异常")

    _report_status(SERVICE_STOPPED)


# ── ServiceMain ────────────────────────────────────────────────────


@ServiceMainProc
def _service_main(_argc: int, _argv) -> None:
    global _status_handle, _stop_event

    # 报告启动中（避免 1053 超时）
    _report_status(SERVICE_START_PENDING, wait_hint=3000)

    # 注册控制处理器
    _status_handle = ctypes.windll.advapi32.RegisterServiceCtrlHandlerExW(
        "EtAlienDaily",
        _handler,
        None,
    )
    if not _status_handle:
        logger.error("RegisterServiceCtrlHandlerExW 失败")
        _report_status(SERVICE_STOPPED, exit_code=1)
        return

    # 创建停止事件
    _stop_event = ctypes.windll.kernel32.CreateEventW(None, True, False, None)
    if not _stop_event:
        logger.error("CreateEventW 失败")
        _report_status(SERVICE_STOPPED, exit_code=1)
        return

    # 报告运行中
    _report_status(SERVICE_RUNNING)

    _service_worker()


# ── 入口 ────────────────────────────────────────────────────────────


def run_service() -> None:
    """启动 Windows 服务调度器。

    调用 StartServiceCtrlDispatcherW，阻塞直到服务停止。
    仅在作为 Windows 服务启动时调用（sc start）。
    """
    # 配置日志：服务模式下写入 Windows 事件日志不可用，改用文件
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(
                _get_service_log_path(),
                encoding="utf-8",
            ),
        ],
    )

    svc_name = "EtAlienDaily"

    # 构建服务分发表
    entry = SERVICE_TABLE_ENTRYW()
    entry.lpServiceName = svc_name
    entry.lpServiceProc = ctypes.cast(_service_main, ctypes.c_void_p).value

    # 终止标记（最后一个 entry 必须全是 NULL/0）
    term = SERVICE_TABLE_ENTRYW()
    term.lpServiceName = None
    term.lpServiceProc = 0

    table = (SERVICE_TABLE_ENTRYW * 2)(entry, term)

    if not ctypes.windll.advapi32.StartServiceCtrlDispatcherW(table):
        err = ctypes.get_last_error()
        logger.error("StartServiceCtrlDispatcherW 失败 (错误码: %d)", err)
        sys.exit(1)


def _get_service_log_path() -> str:
    """服务日志文件路径，放在 config 目录下。"""
    import os
    from etalien.db import get_db_path
    config_dir = os.path.dirname(get_db_path())
    return os.path.join(config_dir, "service.log")
