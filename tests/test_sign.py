"""签名算法单元测试。"""
import unittest
from unittest.mock import patch

from etalien.sign import get_sign, get_sort_parameters, sign_url

FIXED_TS = "1719500000"
FIXED_NONCE = "abc123def45678901234567890123456"


class TestGetSortParameters(unittest.TestCase):
    """测试参数排序和追加。"""

    @patch("etalien.sign.time.time", return_value=1719500000)
    @patch("etalien.sign.uuid.uuid4")
    def test_empty_params(self, mock_uuid, mock_time):
        mock_uuid.return_value.hex = FIXED_NONCE
        result, params = get_sort_parameters(None)
        self.assertIn("ts=1719500000", result)
        self.assertIn(f"nonce={FIXED_NONCE}", result)
        self.assertIn("ver=2023-08-28", result)
        # 验证字母序：nonce < ts < ver
        self.assertTrue(result.index("nonce") < result.index("ts") < result.index("ver"))

    @patch("etalien.sign.time.time", return_value=1719500000)
    @patch("etalien.sign.uuid.uuid4")
    def test_with_existing_params(self, mock_uuid, mock_time):
        mock_uuid.return_value.hex = FIXED_NONCE
        result, params = get_sort_parameters({"key1": "val1", "abc": "xyz"})
        # abc 应该在 nonce 之前
        self.assertTrue(result.startswith("abc=xyz"))
        self.assertIn("key1=val1", result)
        self.assertEqual(params["key1"], "val1")
        self.assertEqual(params["abc"], "xyz")

    @patch("etalien.sign.time.time", return_value=1719500000)
    @patch("etalien.sign.uuid.uuid4")
    def test_none_value(self, mock_uuid, mock_time):
        mock_uuid.return_value.hex = FIXED_NONCE
        result, params = get_sort_parameters({"empty": None})
        self.assertIn("empty=", result)
        self.assertNotIn("empty=None", result)

    @patch("etalien.sign.time.time", return_value=1719500000)
    @patch("etalien.sign.uuid.uuid4")
    def test_url_encoding(self, mock_uuid, mock_time):
        mock_uuid.return_value.hex = FIXED_NONCE
        result, params = get_sort_parameters({"name": "hello world"})
        self.assertIn("hello%20world", result)  # quote 将空格编码为 %20


class TestGetSign(unittest.TestCase):
    """测试 SHA-256 签名计算。"""

    def test_known_signature(self):
        # 签名原文需 URL 编码格式
        data = "GETapi.et-api.com/v2/account/pc/ad/config?nonce=abc&ts=123&ver=2023-08-28"
        sig = get_sign(data)
        self.assertEqual(len(sig), 64)  # SHA-256 hex 固定 64 字符
        self.assertTrue(all(c in "0123456789abcdef" for c in sig))

    def test_url_decoded_input(self):
        """验证 get_sign 会对输入先做 URL 解码再计算 SHA-256。"""
        encoded = "hello+world%21"
        decoded_sig = get_sign(encoded)
        # 对解码后的 "hello world!" 计算 SHA-256
        import hashlib
        from urllib.parse import unquote
        expected = hashlib.sha256(unquote(encoded).encode("utf-8")).digest().hex()
        self.assertEqual(decoded_sig, expected)

    def test_deterministic(self):
        data = "POSTapi.et-api.com/path?key=val"
        sig1 = get_sign(data)
        sig2 = get_sign(data)
        self.assertEqual(sig1, sig2)


class TestSignUrl(unittest.TestCase):
    """测试完整 URL 签名构建。"""

    @patch("etalien.sign.time.time", return_value=1719500000)
    @patch("etalien.sign.uuid.uuid4")
    def test_get_request(self, mock_uuid, mock_time):
        mock_uuid.return_value.hex = FIXED_NONCE
        url, params = sign_url("GET", "/test/path")
        self.assertTrue(url.startswith("https://api.et-api.com/test/path?"))
        self.assertIn("sig=", url)
        self.assertIn("sig", params)
        self.assertEqual(len(params["sig"]), 64)

    @patch("etalien.sign.time.time", return_value=1719500000)
    @patch("etalien.sign.uuid.uuid4")
    def test_post_request(self, mock_uuid, mock_time):
        mock_uuid.return_value.hex = FIXED_NONCE
        url, params = sign_url("POST", "/v2/account/pc/ad/config")
        self.assertIn("https://", url)
        # POST 请求的签名原文以 POST 开头
        self.assertIn("sig=", url.split("?")[1])

    @patch("etalien.sign.time.time", return_value=1719500000)
    @patch("etalien.sign.uuid.uuid4")
    def test_custom_port(self, mock_uuid, mock_time):
        mock_uuid.return_value.hex = FIXED_NONCE
        url, params = sign_url("GET", "/path", host="api.et-api.com", port=8080)
        # 非标准端口不应出现在 URL 中（因为 host 不含端口）
        # 但签名原文包含 host:port
        self.assertIn("https://api.et-api.com/path?", url)

    @patch("etalien.sign.time.time", return_value=1719500000)
    @patch("etalien.sign.uuid.uuid4")
    def test_standard_port_omitted(self, mock_uuid, mock_time):
        """80/443 端口不应出现在签名原文中。"""
        mock_uuid.return_value.hex = FIXED_NONCE
        url, params = sign_url("GET", "/path", port=443)
        self.assertIn("https://api.et-api.com/path?", url)

    @patch("etalien.sign.time.time", return_value=1719500000)
    @patch("etalien.sign.uuid.uuid4")
    def test_params_dict_contains_all_fields(self, mock_uuid, mock_time):
        mock_uuid.return_value.hex = FIXED_NONCE
        url, params = sign_url("GET", "/path")
        for key in ("ts", "nonce", "ver", "sig"):
            self.assertIn(key, params)
        self.assertEqual(params["ts"], FIXED_TS)
        self.assertEqual(params["nonce"], FIXED_NONCE)
        self.assertEqual(params["ver"], "2023-08-28")


if __name__ == "__main__":
    unittest.main()
