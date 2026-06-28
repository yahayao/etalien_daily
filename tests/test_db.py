"""SQLite 数据层单元测试。"""
import os
import tempfile
import unittest

from etalien.db import (
    Account,
    add_account,
    add_claim_record,
    delete_account,
    get_account,
    get_account_by_id,
    get_accounts,
    get_claim_history,
    get_connection,
    get_db_path,
    get_settings,
    init_db,
    set_config_dir,
    update_account,
    update_account_token,
    update_settings,
)


class TestDbBase(unittest.TestCase):
    """使用临时目录的测试基类。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        set_config_dir(self.tmpdir)
        init_db()

    def tearDown(self):
        set_config_dir(None)
        # 清理临时文件
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestInitDb(TestDbBase):
    """数据库初始化测试。"""

    def test_db_file_created(self):
        self.assertTrue(os.path.exists(get_db_path()))

    def test_init_idempotent(self):
        """多次调用 init_db 不报错。"""
        init_db()
        init_db()
        init_db()
        # 不应有重复数据
        conn = get_connection()
        rows = conn.execute("SELECT COUNT(*) FROM settings").fetchone()
        self.assertEqual(rows[0], 4)  # 只有 4 个默认设置
        conn.close()

    def test_tables_exist(self):
        conn = get_connection()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [r[0] for r in tables]
        self.assertIn("accounts", table_names)
        self.assertIn("settings", table_names)
        self.assertIn("claim_history", table_names)
        conn.close()

    def test_default_settings(self):
        settings = get_settings()
        self.assertEqual(settings["max_concurrent"], 10)
        self.assertEqual(settings["request_interval"], 1.0)
        self.assertEqual(settings["max_rounds"], 21)
        self.assertEqual(settings["schedule_time"], "08:00")


class TestAccountCRUD(TestDbBase):
    """账号 CRUD 测试。"""

    def test_add_account(self):
        acc = add_account(phone="13800138000", name="测试账号", remark="备注")
        self.assertEqual(acc.phone, "13800138000")
        self.assertEqual(acc.name, "测试账号")
        self.assertEqual(acc.enabled, True)
        self.assertEqual(len(acc.device_id), 25)  # uuid4().hex[:25]
        self.assertGreater(acc.created_at, 0)
        self.assertEqual(acc.created_at, acc.updated_at)

    def test_add_account_duplicate_phone(self):
        add_account(phone="13800138000")
        with self.assertRaises(Exception):  # UNIQUE 约束
            add_account(phone="13800138000")

    def test_get_account(self):
        add_account(phone="13800138000", name="测试")
        acc = get_account("13800138000")
        self.assertIsNotNone(acc)
        self.assertEqual(acc.name, "测试")

    def test_get_account_not_found(self):
        acc = get_account("99999999999")
        self.assertIsNone(acc)

    def test_get_accounts_enabled_only(self):
        add_account(phone="13800000001", name="启用")
        add_account(phone="13800000002", name="禁用")
        update_account("13800000002", enabled=False)

        enabled = get_accounts(enabled_only=True)
        self.assertEqual(len(enabled), 1)
        self.assertEqual(enabled[0].phone, "13800000001")

        all_accs = get_accounts(enabled_only=False)
        self.assertEqual(len(all_accs), 2)

    def test_get_account_by_id(self):
        acc = add_account(phone="13800138000")
        found = get_account_by_id(acc.id)
        self.assertIsNotNone(found)
        self.assertEqual(found.phone, "13800138000")

    def test_update_account(self):
        add_account(phone="13800138000", name="旧名称")
        result = update_account("13800138000", name="新名称", remark="新备注")
        self.assertTrue(result)
        acc = get_account("13800138000")
        self.assertEqual(acc.name, "新名称")
        self.assertEqual(acc.remark, "新备注")
        # updated_at 应被更新
        self.assertGreater(acc.updated_at, acc.created_at)

    def test_update_account_not_found(self):
        result = update_account("99999999999", name="x")
        self.assertFalse(result)

    def test_update_account_token(self):
        add_account(phone="13800138000")
        result = update_account_token("13800138000", "Bearer xyz", user_id=42)
        self.assertTrue(result)
        acc = get_account("13800138000")
        self.assertEqual(acc.auth_token, "Bearer xyz")
        self.assertEqual(acc.user_id, 42)

    def test_delete_account(self):
        add_account(phone="13800138000")
        self.assertTrue(delete_account("13800138000"))
        self.assertIsNone(get_account("13800138000"))

    def test_delete_account_not_found(self):
        self.assertFalse(delete_account("99999999999"))

    def test_to_dict_excludes_token(self):
        acc = add_account(phone="13800138000", name="测试")
        update_account_token("13800138000", "Bearer secret", user_id=1)
        acc = get_account("13800138000")
        d = acc.to_dict()
        self.assertNotIn("auth_token", d)
        self.assertIn("phone", d)
        self.assertIn("name", d)

    def test_custom_device_id(self):
        acc = add_account(phone="13800138000", device_id="custom_device_12345")
        self.assertEqual(acc.device_id, "custom_device_12345")


class TestSettingsCRUD(TestDbBase):
    """设置 CRUD 测试。"""

    def test_get_settings_returns_typed(self):
        settings = get_settings()
        self.assertIsInstance(settings["max_concurrent"], int)
        self.assertIsInstance(settings["request_interval"], float)
        self.assertIsInstance(settings["max_rounds"], int)
        self.assertIsInstance(settings["schedule_time"], str)

    def test_update_settings(self):
        update_settings(max_concurrent=5, request_interval=2.5)
        settings = get_settings()
        self.assertEqual(settings["max_concurrent"], 5)
        self.assertEqual(settings["request_interval"], 2.5)

    def test_update_settings_clamp(self):
        """设置超出范围应被钳位。"""
        update_settings(max_concurrent=100)  # 超过上限 50
        settings = get_settings()
        self.assertEqual(settings["max_concurrent"], 50)

        update_settings(max_concurrent=0)  # 低于下限 1
        settings = get_settings()
        self.assertEqual(settings["max_concurrent"], 1)

        update_settings(request_interval=0.0)  # 低于下限 0.1
        settings = get_settings()
        self.assertEqual(settings["request_interval"], 0.1)

    def test_update_settings_partial(self):
        """部分更新只影响指定的 key。"""
        default = get_settings()
        update_settings(max_concurrent=8)
        updated = get_settings()
        self.assertEqual(updated["max_concurrent"], 8)
        self.assertEqual(updated["request_interval"], default["request_interval"])


class TestClaimHistory(TestDbBase):
    """领取历史测试。"""

    def test_add_and_query(self):
        acc = add_account(phone="13800138000")
        add_claim_record(
            acc.id,
            status="ok",
            vip_before=3600,
            vip_after=7200,
            claimed_count=3,
            failed_count=0,
        )
        add_claim_record(
            acc.id,
            status="auth_error",
            vip_before=7200,
            vip_after=7200,
            claimed_count=0,
            failed_count=1,
        )

        records = get_claim_history(account_id=acc.id)
        self.assertEqual(len(records), 2)
        # 按时间倒序
        self.assertEqual(records[0]["status"], "auth_error")
        self.assertEqual(records[1]["status"], "ok")
        self.assertEqual(records[1]["claimed_count"], 3)

    def test_empty_history(self):
        acc = add_account(phone="13800138000")
        records = get_claim_history(account_id=acc.id)
        self.assertEqual(records, [])

    def test_delete_account_cascades(self):
        """删除账号时关联的领取历史也应删除。"""
        acc = add_account(phone="13800138000")
        add_claim_record(acc.id, status="ok")
        self.assertEqual(len(get_claim_history(account_id=acc.id)), 1)

        delete_account("13800138000")
        records = get_claim_history(account_id=acc.id)
        self.assertEqual(records, [])


class TestConcurrentAccess(TestDbBase):
    """并发访问测试。"""

    def test_concurrent_reads(self):
        """多线程并发读取不应报错。"""
        from concurrent.futures import ThreadPoolExecutor

        add_account(phone="13800138000", name="测试")

        def read():
            acc = get_account("13800138000")
            return acc.name

        with ThreadPoolExecutor(max_workers=5) as executor:
            results = list(executor.map(lambda _: read(), range(10)))

        self.assertEqual(len(results), 10)
        self.assertTrue(all(r == "测试" for r in results))


if __name__ == "__main__":
    unittest.main()
