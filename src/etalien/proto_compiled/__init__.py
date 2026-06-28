"""Protobuf 消息类型（由 protoc 从 .proto 文件编译生成）。

替代手写 proto.py，使用 Google protobuf 库进行序列化。
"""

from .account_pb2 import (
    GetLoginVerificationCodeRequest,
    GetLoginVerificationCodeResponse,
    LoginByOneClickRequest,
    LoginByOneClickResponse,
    LoginRequest,
    LoginResponse,
    MyProfileResponse,
    RefreshTokenRequest,
    RefreshTokenResponse,
)
from .apiv2_pb2 import (
    GetUserRemainDurationRequest,
    GetUserRemainDurationResponse,
    MobilePcProductItem,
    MobilePcProductListRequest,
    MobilePcProductListResponse,
    PcAdCallbackBackupRequest,
    PcAdCallbackBackupResponse,
    PcAdConfigItem,
    PcAdConfigLevelItem,
    PcAdConfigRequest,
    PcAdConfigResponse,
    UpdatePauseStateRequest,
    UpdatePauseStateResponse,
)
from .error_pb2 import Error

# 响应类型注册表
_RESPONSE_TYPES: dict[str, type] = {
    "/account/v1/login": LoginResponse,
    "/v2/account/pc/ad/config": PcAdConfigResponse,
    "/v2/account/pc/ad/callback/backup": PcAdCallbackBackupResponse,
    "/v2/account/remain/duration": GetUserRemainDurationResponse,
}


def get_response_type(path: str) -> type | None:
    return _RESPONSE_TYPES.get(path)
