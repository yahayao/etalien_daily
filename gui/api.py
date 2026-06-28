"""Flask REST API 服务器。

GUI 后端的 HTTP API，封装 db.py / client.py / service.py 的功能。
所有响应为 JSON 格式，账号返回时过滤敏感字段。
"""

import logging
import os
import socket
import subprocess
import sys
import threading

from flask import Flask, jsonify, request, send_from_directory

from etalien.client import ApiClient
from etalien.db import (
    Account,
    add_account,
    delete_account,
    get_account,
    get_accounts,
    get_claim_history,
    get_settings,
    init_db,
    update_account,
    update_account_token,
    update_settings,
)
from etalien.service import run_concurrent_claim
from gui import claim_manager

logger = logging.getLogger(__name__)

# ── 静态文件路径 ─────────────────────────────────────────────────

def _get_static_dir() -> str:
    """获取静态文件目录（兼容 PyInstaller 打包）。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "gui", "static")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


# ── 临时客户端缓存 ──────────────────────────────────────────────

_pending_clients: dict[str, ApiClient] = {}


def _normalize_phone(phone: str) -> str:
    """标准化手机号：11 位国内号码自动加 +86 前缀。"""
    phone = phone.strip()
    if not phone.startswith("+") and len(phone) == 11 and phone.isdigit():
        return "+86" + phone
    return phone


def _get_client(phone: str) -> ApiClient:
    if phone not in _pending_clients:
        acc = get_account(phone)
        device_id = acc.device_id if acc else ""
        _pending_clients[phone] = ApiClient(device_id=device_id)
    return _pending_clients[phone]


# ── 账号敏感字段过滤 ────────────────────────────────────────────

_ACCOUNT_PUBLIC_FIELDS = {
    "id", "phone", "name", "remark", "enabled",
    "user_id", "device_id", "created_at", "updated_at",
}


def _account_public(acc: Account) -> dict:
    """返回不含 token 等敏感字段的账号 dict。"""
    d = acc.to_dict()
    return {k: v for k, v in d.items() if k in _ACCOUNT_PUBLIC_FIELDS}


# ── Flask App ────────────────────────────────────────────────────

def create_app() -> Flask:
    app = Flask(__name__)
    static_dir = _get_static_dir()

    # ── 请求校验 ────────────────────────────────────────────

    @app.before_request
    def check_json():
        if request.method in ("POST", "PUT"):
            if request.content_length and not request.is_json:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "请求体需为 JSON"}), 400

    # ── 前端页面 ────────────────────────────────────────────

    @app.route("/")
    def index():
        return send_from_directory(static_dir, "index.html")

    @app.route("/<path:path>")
    def static_files(path):
        return send_from_directory(static_dir, path)

    # ── 账号管理 ────────────────────────────────────────────

    @app.route("/api/accounts", methods=["GET"])
    def list_accounts():
        accounts = get_accounts(enabled_only=False)
        return jsonify([_account_public(acc) for acc in accounts])

    @app.route("/api/accounts/<phone>", methods=["GET"])
    def get_account_api(phone):
        acc = get_account(phone)
        if not acc:
            return jsonify({"error": "账号不存在"}), 404
        return jsonify(_account_public(acc))

    @app.route("/api/accounts", methods=["POST"])
    def create_account():
        data = request.get_json(silent=True) or {}
        phone = data.get("phone", "").strip()
        if not phone:
            return jsonify({"error": "手机号不能为空"}), 400
        if get_account(phone):
            return jsonify({"error": "账号已存在"}), 409
        acc = add_account(
            phone=phone,
            name=data.get("name", ""),
            remark=data.get("remark", ""),
        )
        return jsonify(_account_public(acc)), 201

    @app.route("/api/accounts/<phone>", methods=["PUT"])
    def update_account_api(phone):
        data = request.get_json(silent=True) or {}
        allowed = {"name", "remark", "enabled"}
        fields = {k: v for k, v in data.items() if k in allowed}

        # 如果修改手机号，清空旧凭证
        new_phone = data.get("phone", "").strip()
        if new_phone and new_phone != phone:
            delete_account(phone)
            acc = add_account(phone=new_phone, name=data.get("name", ""), remark=data.get("remark", ""))
            return jsonify(_account_public(acc))

        if not fields:
            return jsonify({"error": "没有可更新的字段"}), 400
        ok = update_account(phone, **fields)
        if not ok:
            return jsonify({"error": "账号不存在"}), 404
        acc = get_account(phone)
        return jsonify(_account_public(acc) if acc else {})

    @app.route("/api/accounts/<phone>", methods=["DELETE"])
    def delete_account_api(phone):
        ok = delete_account(phone)
        if not ok:
            return jsonify({"error": "账号不存在"}), 404
        return jsonify({"ok": True})

    # ── 登录 ────────────────────────────────────────────────

    @app.route("/api/login/<phone>", methods=["POST"])
    def send_code(phone):
        acc = get_account(phone)
        if not acc:
            return jsonify({"error": "账号不存在"}), 404
        client = _get_client(phone)
        result = client.get_verification_code(phone_number=_normalize_phone(phone))
        if result.get("_error"):
            code = result.get("code", 0)
            # 60/1000 表示冷却期，但验证码可能已发送
            if code in (60, 1000):
                return jsonify({"ok": True, "msg": "验证码冷却中，请检查短信"})
            return jsonify({"error": result.get("msg", "发送失败")}), 500
        return jsonify({"ok": True, "msg": "验证码已发送"})

    @app.route("/api/login/<phone>/verify", methods=["POST"])
    def verify_code(phone):
        data = request.get_json(silent=True) or {}
        code = data.get("code", "").strip()
        if not code:
            return jsonify({"error": "验证码不能为空"}), 400
        client = _get_client(phone)
        result = client.login(phone_number=_normalize_phone(phone), verification_code=code)
        if result.get("_error"):
            return jsonify({"error": result.get("msg", "登录失败")}), 401
        token = result.get("authorization", "")
        user_id = result.get("user_id", 0)
        update_account_token(phone, token, user_id)
        _pending_clients.pop(phone, None)

        # 获取用户昵称，如果备注名为空则自动设置
        try:
            profile = client.fetch_my_profile()
            nickname = profile.get("nickname", "")
            if nickname:
                acc = get_account(phone)
                if acc and not acc.name:
                    update_account(phone, name=nickname)
        except Exception:
            pass

        return jsonify({"ok": True, "user_id": user_id})

    # ── 状态 ────────────────────────────────────────────────

    @app.route("/api/status", methods=["GET"])
    def account_status():
        """获取所有启用账号的状态（VIP 时长等）。"""
        accounts = get_accounts(enabled_only=True)
        if not accounts:
            return jsonify([])

        from concurrent.futures import ThreadPoolExecutor

        def _fetch_status(acc):
            client = ApiClient(device_id=acc.device_id, auth_token=acc.auth_token)
            if not acc.auth_token or not client.check_token_valid():
                return {"phone": acc.phone, "name": acc.name, "status": "need_login", "vip_seconds": 0}
            dur = client.fetch_pc_duration()
            if dur.get("_error"):
                return {"phone": acc.phone, "name": acc.name, "status": "error", "vip_seconds": 0}
            return {
                "phone": acc.phone,
                "name": acc.name,
                "status": "ok",
                "vip_seconds": dur.get("vip_duration_second", 0),
                "free_seconds": dur.get("free_duration_second", 0),
            }

        with ThreadPoolExecutor(max_workers=min(len(accounts), 10)) as executor:
            results = list(executor.map(_fetch_status, accounts))
        return jsonify(results)

    # ── 领取 ────────────────────────────────────────────────

    @app.route("/api/claim", methods=["POST"])
    def start_claim():
        if not claim_manager.start():
            return jsonify({"error": "领取任务已在运行中"}), 409

        accounts = get_accounts(enabled_only=True)
        if not accounts:
            claim_manager.finish()
            return jsonify({"error": "没有启用的账号"}), 400

        settings = get_settings()

        # 为每个账号添加初始进度条目
        for acc in accounts:
            claim_manager.add_progress_entry({
                "phone": acc.phone,
                "status": "running",
                "current": 0,
                "total": 0,
                "vip_before": 0,
                "vip_after": 0,
                "error": None,
            })

        def _progress_callback(phone, step, detail):
            """将 service 的回调转为进度条目更新。"""
            updates = {}
            if step == "done" or step == "after":
                updates["status"] = "done"
            elif step == "already_done":
                updates["status"] = "already_done"
            elif step == "error":
                updates["status"] = "error"
                updates["error"] = detail
            elif step == "auth_error":
                updates["status"] = "need_login"
                updates["error"] = detail
            elif step.startswith("b"):
                # 业务类型回调，detail 格式: "business=X 第N轮 (W/T)"
                updates["detail"] = detail
            claim_manager.update_progress_entry(phone, updates)

        def _run():
            try:
                results = run_concurrent_claim(accounts, settings, progress_callback=_progress_callback)
                # 用最终结果更新进度
                for r in results:
                    claim_manager.update_progress_entry(r["phone"], {
                        "status": r["status"],
                        "vip_before": r.get("vip_before", 0),
                        "vip_after": r.get("vip_after", 0),
                        "current": r.get("claimed", 0),
                        "total": r.get("claimed", 0) + r.get("failed", 0),
                        "error": r.get("error_msg"),
                    })
            except Exception as e:
                logger.error("领取异常: %s", e)
            finally:
                claim_manager.finish()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return jsonify({"ok": True, "account_count": len(accounts)})

    @app.route("/api/claim/progress", methods=["GET"])
    def claim_progress():
        return jsonify(claim_manager.get_progress())

    # ── 设置 ────────────────────────────────────────────────

    @app.route("/api/settings", methods=["GET"])
    def settings_get():
        return jsonify(get_settings())

    @app.route("/api/settings", methods=["PUT"])
    def settings_update():
        data = request.get_json(silent=True) or {}
        allowed = {"max_concurrent", "request_interval", "max_rounds", "schedule_time"}
        fields = {k: v for k, v in data.items() if k in allowed}
        if not fields:
            return jsonify({"error": "没有可更新的字段"}), 400
        update_settings(**fields)
        return jsonify(get_settings())

    # ── 定时任务 ────────────────────────────────────────────

    TASK_NAME = "EtAlienAuto_DailyClaim"

    def _get_exe_path() -> str:
        if getattr(sys, "frozen", False):
            return sys.executable
        return sys.executable  # Python 解释器路径

    @app.route("/api/schedule", methods=["GET"])
    def schedule_get():
        try:
            result = subprocess.run(
                ["schtasks", "/query", "/tn", TASK_NAME],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                return jsonify({"enabled": False})
            # 简单解析
            return jsonify({"enabled": True, "detail": result.stdout.strip()})
        except FileNotFoundError:
            return jsonify({"enabled": False, "error": "schtasks 不可用"})

    @app.route("/api/schedule", methods=["POST"])
    def schedule_create():
        data = request.get_json(silent=True) or {}
        schedule_time = data.get("time", "08:00")

        exe_path = _get_exe_path()
        if getattr(sys, "frozen", False):
            cmd = f'"{exe_path}" --cli --auto-close'
        else:
            main_py = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "src", "etalien", "main.py",
            )
            cmd = f'"{sys.executable}" "{main_py}"'

        try:
            subprocess.run([
                "schtasks", "/create", "/tn", TASK_NAME,
                "/tr", cmd,
                "/sc", "daily", "/st", schedule_time, "/f",
            ], capture_output=True, text=True, check=True)
            return jsonify({"ok": True, "time": schedule_time})
        except subprocess.CalledProcessError as e:
            return jsonify({"error": e.stderr.strip()}), 500
        except FileNotFoundError:
            return jsonify({"error": "schtasks 不可用（非 Windows 系统）"}), 500

    @app.route("/api/schedule", methods=["DELETE"])
    def schedule_delete():
        try:
            subprocess.run(
                ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
                capture_output=True, text=True, check=True,
            )
            return jsonify({"ok": True})
        except subprocess.CalledProcessError as e:
            return jsonify({"error": e.stderr.strip()}), 500
        except FileNotFoundError:
            return jsonify({"ok": True})

    # ── 历史 ────────────────────────────────────────────────

    @app.route("/api/history", methods=["GET"])
    def claim_history():
        limit = request.args.get("limit", 50, type=int)
        history = get_claim_history(limit=limit)
        return jsonify(history)

    return app


# ── 端口扫描 ────────────────────────────────────────────────────

PORT_START = 52137
PORT_END = 52200


def find_free_port() -> int:
    for port in range(PORT_START, PORT_END + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"端口范围 {PORT_START}-{PORT_END} 均已占用")
