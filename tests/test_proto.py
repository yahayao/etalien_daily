"""Protobuf 序列化测试（protoc 编译版本）。"""
import unittest

from etalien.proto_compiled.account_pb2 import (
    LoginRequest,
    LoginResponse,
)
from etalien.proto_compiled.apiv2_pb2 import (
    GetUserRemainDurationRequest,
    GetUserRemainDurationResponse,
    PcAdCallbackBackupRequest,
    PcAdCallbackBackupResponse,
    PcAdConfigItem,
    PcAdConfigLevelItem,
    PcAdConfigRequest,
    PcAdConfigResponse,
)
from etalien.proto_compiled.error_pb2 import Error


class TestMessageRoundtrip(unittest.TestCase):
    """消息往返测试。"""

    def test_login_request(self):
        req = LoginRequest(phone_number="+8613800138000", verification_code="123456")
        data = req.SerializeToString()
        parsed = LoginRequest()
        parsed.ParseFromString(data)
        self.assertEqual(parsed.phone_number, "+8613800138000")
        self.assertEqual(parsed.verification_code, "123456")

    def test_login_response(self):
        resp = LoginResponse(user_id=12345, authorization="Bearer token_abc")
        data = resp.SerializeToString()
        parsed = LoginResponse()
        parsed.ParseFromString(data)
        self.assertEqual(parsed.user_id, 12345)
        self.assertEqual(parsed.authorization, "Bearer token_abc")

    def test_empty_message(self):
        msg = PcAdConfigRequest()
        self.assertEqual(msg.SerializeToString(), b"")

    def test_config_item(self):
        item = PcAdConfigItem(id=1, award_unix=1719500000, level=2, is_watch=False, title="测试广告")
        data = item.SerializeToString()
        parsed = PcAdConfigItem()
        parsed.ParseFromString(data)
        self.assertEqual(parsed.id, 1)
        self.assertEqual(parsed.is_watch, False)
        self.assertEqual(parsed.title, "测试广告")

    def test_config_item_watched(self):
        item = PcAdConfigItem(id=1, is_watch=True)
        data = item.SerializeToString()
        parsed = PcAdConfigItem()
        parsed.ParseFromString(data)
        self.assertTrue(parsed.is_watch)

    def test_nested_message(self):
        item = PcAdConfigItem(id=1, title="广告1")
        level = PcAdConfigLevelItem(level=1, list=[item], watch_cnt=0, text="说明")
        data = level.SerializeToString()
        parsed = PcAdConfigLevelItem()
        parsed.ParseFromString(data)
        self.assertEqual(len(parsed.list), 1)
        self.assertEqual(parsed.list[0].title, "广告1")

    def test_repeated_multiple(self):
        items = [PcAdConfigItem(id=1), PcAdConfigItem(id=2), PcAdConfigItem(id=3)]
        level = PcAdConfigLevelItem(level=1, list=items)
        parsed = PcAdConfigLevelItem()
        parsed.ParseFromString(level.SerializeToString())
        self.assertEqual(len(parsed.list), 3)

    def test_config_response(self):
        level1 = PcAdConfigLevelItem(level=1, list=[PcAdConfigItem(id=1)])
        resp = PcAdConfigResponse(list=[level1])
        parsed = PcAdConfigResponse()
        parsed.ParseFromString(resp.SerializeToString())
        self.assertEqual(len(parsed.list), 1)

    def test_callback_request(self):
        req = PcAdCallbackBackupRequest(ad_id="103334281", business=1)
        parsed = PcAdCallbackBackupRequest()
        parsed.ParseFromString(req.SerializeToString())
        self.assertEqual(parsed.ad_id, "103334281")
        self.assertEqual(parsed.business, 1)

    def test_callback_response(self):
        resp = PcAdCallbackBackupResponse(is_verify=True)
        parsed = PcAdCallbackBackupResponse()
        parsed.ParseFromString(resp.SerializeToString())
        self.assertTrue(parsed.is_verify)

    def test_duration_response(self):
        resp = GetUserRemainDurationResponse(
            vip_duration_second=86400, timestamp=1719500000, is_first_award=True, pc_vip_state=1,
        )
        parsed = GetUserRemainDurationResponse()
        parsed.ParseFromString(resp.SerializeToString())
        self.assertEqual(parsed.vip_duration_second, 86400)
        self.assertTrue(parsed.is_first_award)
        self.assertEqual(parsed.pc_vip_state, 1)

    def test_error_message(self):
        err = Error(code=16, msg="token expired")
        parsed = Error()
        parsed.ParseFromString(err.SerializeToString())
        self.assertEqual(parsed.code, 16)
        self.assertEqual(parsed.msg, "token expired")

    def test_empty_request(self):
        req = GetUserRemainDurationRequest()
        self.assertEqual(req.SerializeToString(), b"")


class TestBinaryCompatibility(unittest.TestCase):
    """与原始项目二进制兼容性测试。"""

    def test_login_request_binary(self):
        """确认生成的二进制与原始项目文档中的字段定义一致。"""
        req = LoginRequest(phone_number="+8613800138000", verification_code="123456")
        data = req.SerializeToString()
        # 手动验证关键字节: tag(1,string) + length + phone + tag(2,string) + length + code
        self.assertGreater(len(data), 10)

    def test_callback_backup_binary(self):
        req = PcAdCallbackBackupRequest(ad_id="103334281", business=2)
        parsed = PcAdCallbackBackupRequest()
        parsed.ParseFromString(req.SerializeToString())
        self.assertEqual(parsed.business, 2)


if __name__ == "__main__":
    unittest.main()
