"""HTTP 客户端模块。

封装外星仔 API 的所有 HTTP 交互：
- 请求签名（URL + 参数）
- 客户端伪装（Headers 模拟 OkHttp/Android）
- Protobuf 请求体序列化 / 响应体反序列化
- 自动重试（网络错误 + 5xx）
- 统一错误返回格式
- Token 管理
"""

import logging
import time
from typing import Any

import requests

from etalien import proto_compiled as proto
from google.protobuf.json_format import MessageToDict
from etalien.sign import HOST, sign_url

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────

BASE_URL = f"https://{HOST}"
APP_VERSION = "3.11.10"
MAX_RETRIES = 2          # 重试次数（共 3 次尝试）
RETRY_DELAY = 1.0        # 重试间隔（秒）
REQUEST_TIMEOUT = 30.0   # 请求超时（秒）

# 认证错误码
AUTH_ERROR_CODES = {16, 401, 403}


# ── ApiClient ─────────────────────────────────────────────────────

class ApiClient:
    """外星仔 API 客户端。

    每个账号对应一个 ApiClient 实例（独立的 device_id 和 auth_token）。
    """

    def __init__(self, device_id: str, auth_token: str | None = None):
        """初始化客户端。

        Args:
            device_id: 设备唯一标识（25 位 hex，与账号绑定持久化）。
            auth_token: 登录后的 Bearer token，可为 None（未登录状态）。
        """
        self.device_id = device_id
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "okhttp/4.12.0",
            "Accept": "application/x-protobuf",
            "Content-Type": "application/x-protobuf",
            "x-eta": f"os=0&ver={APP_VERSION}&dvc={device_id}&ch=default",
        })
        if auth_token:
            self.update_auth_token(auth_token)

    # ── Token 管理 ─────────────────────────────────────────────

    def update_auth_token(self, token: str) -> None:
        """设置或更新 Authorization header。"""
        self._session.headers["Authorization"] = token

    def clear_auth_token(self) -> None:
        """移除 Authorization header。"""
        self._session.headers.pop("Authorization", None)

    def get_auth_token(self) -> str | None:
        """获取当前的 Authorization header 值。"""
        return self._session.headers.get("Authorization")

    # ── API 方法 ───────────────────────────────────────────────

    def get_verification_code(self, phone_number: str) -> dict[str, Any]:
        """获取短信验证码。

        GET /account/v1/get_login_verification_code
        请求体: GetLoginVerificationCodeRequest（GET 带 body，非标准但服务端接受）

        Returns:
            成功: {"_ok": True, ...}
            失败: {"_error": True, "code": ..., "msg": ...}
            验证码冷却期: {"_error": False, "code": 60|1000, ...}（验证码已发送）
        """
        req = proto.GetLoginVerificationCodeRequest(phone_number=phone_number)
        body = req.SerializeToString()
        return self._retry_request(
            method="GET",
            path="/account/v1/get_login_verification_code",
            body_data=body,
            is_get_with_body=True,
        )

    def login(self, phone_number: str, verification_code: str) -> dict[str, Any]:
        """验证码登录。

        POST /account/v1/login
        请求体: LoginRequest
        响应: LoginResponse

        成功时自动保存 auth_token 到 session headers。

        Returns:
            成功: {"_ok": True, "user_id": ..., "authorization": ...}
            失败: {"_error": True, "code": ..., "msg": ...}
        """
        req = proto.LoginRequest(
            phone_number=phone_number,
            verification_code=verification_code,
        )
        body = req.SerializeToString()

        result = self._retry_request(
            method="POST",
            path="/account/v1/login",
            body_data=body,
            response_cls=proto.LoginResponse,
        )

        # 登录成功，自动保存 token
        if not result.get("_error") and result.get("authorization"):
            self.update_auth_token(result["authorization"])

        return result

    def fetch_pc_ad_config(self) -> dict[str, Any]:
        """获取广告任务列表。

        POST /v2/account/pc/ad/config
        请求体: PcAdConfigRequest（空）

        Returns:
            成功: {"_ok": True, "list": [...]}  其中 list 为 PcAdConfigLevelItem 的 dict 列表
            失败: {"_error": True, "code": ..., "msg": ...}
        """
        req = proto.PcAdConfigRequest()
        body = req.SerializeToString()  # b""

        result = self._retry_request(
            method="POST",
            path="/v2/account/pc/ad/config",
            body_data=body,
            response_cls=proto.PcAdConfigResponse,
        )
        return result

    def pc_ad_callback_backup(self, ad_id: str, business: int) -> dict[str, Any]:
        """发送广告奖励补发回调（核心接口）。

        POST /v2/account/pc/ad/callback/backup
        请求体: PcAdCallbackBackupRequest

        Returns:
            成功: {"_ok": True, "is_verify": True/False}
            失败: {"_error": True, "code": ..., "msg": ...}
        """
        req = proto.PcAdCallbackBackupRequest(ad_id=ad_id, business=business)
        body = req.SerializeToString()

        result = self._retry_request(
            method="POST",
            path="/v2/account/pc/ad/callback/backup",
            body_data=body,
            response_cls=proto.PcAdCallbackBackupResponse,
        )
        return result

    def fetch_pc_duration(self) -> dict[str, Any]:
        """查询剩余时长。

        POST /v2/account/remain/duration
        请求体: GetUserRemainDurationRequest（空）

        Returns:
            成功: {"_ok": True, "vip_duration_second": ..., ...}
            失败: {"_error": True, "code": ..., "msg": ...}
        """
        req = proto.GetUserRemainDurationRequest()
        body = req.SerializeToString()  # b""

        result = self._retry_request(
            method="POST",
            path="/v2/account/remain/duration",
            body_data=body,
            response_cls=proto.GetUserRemainDurationResponse,
        )
        return result

    def check_token_valid(self) -> bool:
        """检查当前 auth_token 是否有效。

        通过调用 fetch_pc_duration 判断，如果返回认证错误码则 token 无效。

        Returns:
            True: token 有效
            False: token 过期或无效
        """
        if not self.get_auth_token():
            return False
        result = self.fetch_pc_duration()
        return not _is_auth_error(result)

    def fetch_my_profile(self) -> dict[str, Any]:
        """获取用户信息。

        GET /account/v1/my_profile

        Returns:
            成功: {"_ok": True, "user_id": ..., "nickname": ..., "avatar_url": ...}
            失败: {"_error": True, "code": ..., "msg": ...}
        """
        return self._retry_request(
            method="GET",
            path="/account/v1/my_profile",
            response_cls=proto.MyProfileResponse,
        )

    # ── 内部方法 ───────────────────────────────────────────────

    def _retry_request(
        self,
        method: str,
        path: str,
        body_data: bytes | None = None,
        is_get_with_body: bool = False,
        response_cls: type | None = None,
    ) -> dict[str, Any]:
        """带重试的请求包装。

        重试条件: ConnectionError, Timeout, HTTP 5xx
        不重试: HTTP 4xx, 认证错误
        最大重试 MAX_RETRIES 次（共 MAX_RETRIES + 1 次尝试）。
        """
        last_result = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                result = self._request(
                    method=method,
                    path=path,
                    body_data=body_data,
                    is_get_with_body=is_get_with_body,
                    response_cls=response_cls,
                )

                # 判断是否需要重试
                if _is_retryable(result):
                    if attempt < MAX_RETRIES:
                        logger.debug(
                            "请求 %s 可重试 (attempt %d/%d): %s",
                            path, attempt + 1, MAX_RETRIES + 1,
                            result.get("msg", result.get("code", "")),
                        )
                        time.sleep(RETRY_DELAY)
                        last_result = result
                        continue
                return result

            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt < MAX_RETRIES:
                    logger.debug(
                        "网络错误 %s (attempt %d/%d): %s",
                        path, attempt + 1, MAX_RETRIES + 1, e,
                    )
                    time.sleep(RETRY_DELAY)
                    last_result = {"_error": True, "msg": str(e)}
                    continue
                return {"_error": True, "msg": str(e)}

        return last_result or {"_error": True, "msg": "max retries exceeded"}

    def _request(
        self,
        method: str,
        path: str,
        body_data: bytes | None = None,
        is_get_with_body: bool = False,
        response_cls: type | None = None,
    ) -> dict[str, Any]:
        """核心请求方法。

        1. 构建签名 URL
        2. 发送请求
        3. 解析响应（protobuf 或 error）
        4. 返回统一 dict 格式
        """
        # 1. 签名 URL
        url, _ = sign_url(method=method.upper(), path=path)

        # 2. 发送请求
        try:
            if method.upper() == "GET":
                resp = self._session.get(
                    url,
                    data=body_data if is_get_with_body else None,
                    timeout=REQUEST_TIMEOUT,
                )
            else:
                resp = self._session.post(
                    url,
                    data=body_data if body_data else None,
                    timeout=REQUEST_TIMEOUT,
                )
        except requests.Timeout as e:
            raise  # 由 _retry_request 处理
        except requests.ConnectionError as e:
            raise  # 由 _retry_request 处理
        except requests.RequestException as e:
            return {"_error": True, "msg": str(e)}

        # 3. 解析响应
        return _parse_response(resp, response_cls)


# ── 辅助函数 ──────────────────────────────────────────────────────

def _parse_response(
    resp: requests.Response,
    response_cls: type | None = None,
) -> dict[str, Any]:
    """解析 HTTP 响应为统一 dict 格式。

    - HTTP 200: 反序列化 protobuf → dict
    - HTTP >= 400: 反序列化 proto.Error → dict with _error
    - 空 body: {"_ok": True}
    """
    if resp.status_code >= 400:
        result = {"_error": True, "code": resp.status_code}
        if resp.content:
            try:
                err = proto.Error()
                err.ParseFromString(resp.content)
                result["code"] = err.code or resp.status_code
                result["msg"] = err.msg
            except Exception:
                result["msg"] = resp.text or "unknown error"
        return result

    # HTTP 200
    if not resp.content:
        return {"_ok": True}

    if response_cls is None:
        return {"_ok": True}

    try:
        msg = response_cls()
        msg.ParseFromString(resp.content)
        result = {"_ok": True}
        result.update(MessageToDict(msg, preserving_proto_field_name=True))
        return result
    except Exception as e:
        logger.warning("Protobuf 解析失败 (%s): %s", response_cls.__name__, e)
        return {"_error": True, "msg": f"protobuf parse error: {e}"}


def _is_retryable(result: dict) -> bool:
    """判断是否应该重试。

    重试条件: HTTP 5xx 或网络错误。
    不重试: HTTP 4xx（含认证错误）、成功。
    """
    if not result.get("_error"):
        return False
    code = result.get("code", 0)
    if isinstance(code, int) and 500 <= code < 600:
        return True
    # 网络错误（无 code，有 msg）
    if not code and result.get("msg"):
        return True
    return False


def _is_auth_error(result: dict) -> bool:
    """判断是否为认证错误。

    - HTTP 401/403
    - Protobuf Error code == 16
    """
    if not result.get("_error"):
        return False
    code = result.get("code")
    if code in AUTH_ERROR_CODES:
        return True
    return False
