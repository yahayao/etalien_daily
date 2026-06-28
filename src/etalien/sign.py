"""请求签名算法模块。

外星仔 App 内置 SignInterceptor 对所有 HTTP 请求进行签名校验。
本模块实现参数排序、SHA-256 签名计算和签名 URL 构建。
"""

import hashlib
import time
import uuid
from urllib.parse import quote, unquote

HOST = "api.et-api.com"
VER = "2023-08-28"


def get_sort_parameters(query_params: dict[str, str] | None = None) -> tuple[str, dict[str, str]]:
    """对请求参数排序并追加签名必需的 ts/nonce/ver 参数。

    Args:
        query_params: 原始请求参数，可为 None 表示无额外参数。

    Returns:
        (排序后的查询字符串, 包含所有参数的字典)
    """
    params = dict(query_params) if query_params else {}
    params["ts"] = str(int(time.time()))
    params["nonce"] = uuid.uuid4().hex
    params["ver"] = VER

    sorted_keys = sorted(params.keys())
    parts = []
    for key in sorted_keys:
        val = params[key]
        if val is not None:
            parts.append(f"{key}={quote(str(val), safe='')}")
        else:
            parts.append(f"{key}=")

    return "&".join(parts), params


def get_sign(data: str) -> str:
    """计算 SHA-256 签名。

    Args:
        data: 签名原文（URL 编码格式）。

    Returns:
        十六进制签名字符串。
    """
    decoded = unquote(data)
    sha256 = hashlib.sha256(decoded.encode("utf-8")).digest()
    return sha256.hex()


def sign_url(
    method: str,
    path: str,
    query_params: dict[str, str] | None = None,
    host: str = HOST,
    port: int | None = None,
) -> tuple[str, dict[str, str]]:
    """构建带签名的完整请求 URL。

    Args:
        method: HTTP 方法，如 "GET" 或 "POST"（大写）。
        path: 请求路径，如 "/v2/account/pc/ad/config"。
        query_params: 额外的请求参数，可为 None。
        host: 服务端主机名。
        port: 非标准端口号（80/443 除外）。

    Returns:
        (完整签名 URL, 参数字典) 其中参数字典包含 ts/nonce/ver/sig 用于日志。
    """
    sorted_query, params = get_sort_parameters(query_params)

    # 构建签名原文
    if port and port not in (80, 443):
        base = f"{host}:{port}{path}?{sorted_query}"
    else:
        base = f"{host}{path}?{sorted_query}"

    sign_str = get_sign(f"{method}{base}")

    # 构建完整 URL
    url = f"https://{host}{path}?{sorted_query}&sig={sign_str}"
    params["sig"] = sign_str

    return url, params
