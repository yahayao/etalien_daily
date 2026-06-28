"""SQLite 数据持久化模块。

零外部依赖（仅 stdlib sqlite3）。替代文档中的明文 JSON 方案。
使用 WAL 模式支持并发读写，外键约束保证引用完整性。

数据库位置:
    优先使用环境变量 ETALIEN_CONFIG_DIR，其次为项目根目录下的 config/。
    数据库文件: <config_dir>/etalien.db
"""

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ── 路径解析 ──────────────────────────────────────────────────────

_DEFAULT_CONFIG_DIR: str | None = None


def set_config_dir(path: str) -> None:
    """设置自定义配置目录（用于测试）。"""
    global _DEFAULT_CONFIG_DIR
    _DEFAULT_CONFIG_DIR = path


def get_db_path() -> str:
    """获取数据库文件路径，确保目录存在。"""
    if "ETALIEN_CONFIG_DIR" in os.environ:
        config_dir = os.environ["ETALIEN_CONFIG_DIR"]
    elif _DEFAULT_CONFIG_DIR:
        config_dir = _DEFAULT_CONFIG_DIR
    else:
        # 项目根目录下的 config/
        config_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config",
        )
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, "etalien.db")


# ── 数据库初始化 ──────────────────────────────────────────────────

def init_db(db_path: str | None = None) -> None:
    """初始化数据库：创建表、PRAGMA 设置、写入默认值（幂等）。"""
    if db_path is None:
        db_path = get_db_path()

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        conn.executescript(_SCHEMA_SQL)

        # 写入默认设置（如果不存在）
        now = time.time()
        defaults = [
            ("max_concurrent", "10"),
            ("request_interval", "1.0"),
            ("max_rounds", "21"),
            ("schedule_time", "08:00"),
            ("schedule_enabled", "false"),
            ("schedule_method", "schtasks"),
        ]
        for key, value in defaults:
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    phone      TEXT NOT NULL UNIQUE,
    name       TEXT DEFAULT '',
    remark     TEXT DEFAULT '',
    enabled    INTEGER DEFAULT 1,
    auth_token TEXT DEFAULT NULL,
    user_id    INTEGER DEFAULT 0,
    device_id  TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    INTEGER NOT NULL REFERENCES accounts(id),
    claimed_at    REAL NOT NULL,
    vip_before    INTEGER DEFAULT 0,
    vip_after     INTEGER DEFAULT 0,
    claimed_count INTEGER DEFAULT 0,
    failed_count  INTEGER DEFAULT 0,
    status        TEXT NOT NULL
);
"""


# ── 连接工厂 ─────────────────────────────────────────────────────

def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """获取数据库连接（WAL 模式，支持并发读）。"""
    if db_path is None:
        db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


# ── 数据模型 ──────────────────────────────────────────────────────

@dataclass
class Account:
    name: str = ""
    phone: str = ""
    remark: str = ""
    enabled: bool = True
    auth_token: str | None = None
    user_id: int = 0
    device_id: str = ""
    id: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """转换为 dict（不含敏感 token，用于 API 返回）。"""
        return {
            "name": self.name,
            "phone": self.phone,
            "remark": self.remark,
            "enabled": self.enabled,
            "user_id": self.user_id,
            "device_id": self.device_id,
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _account_from_row(row: sqlite3.Row) -> Account:
    """从数据库行转换为 Account 对象。"""
    return Account(
        id=row["id"],
        name=row["name"],
        phone=row["phone"],
        remark=row["remark"],
        enabled=bool(row["enabled"]),
        auth_token=row["auth_token"],
        user_id=row["user_id"],
        device_id=row["device_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ── 账号 CRUD ─────────────────────────────────────────────────────

def get_accounts(enabled_only: bool = True, db_path: str | None = None) -> list[Account]:
    """获取所有账号。"""
    conn = get_connection(db_path)
    try:
        if enabled_only:
            rows = conn.execute(
                "SELECT * FROM accounts WHERE enabled = 1 ORDER BY id"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM accounts ORDER BY id"
            ).fetchall()
        return [_account_from_row(r) for r in rows]
    finally:
        conn.close()


def get_account(phone: str, db_path: str | None = None) -> Account | None:
    """根据手机号获取单个账号。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM accounts WHERE phone = ?", (phone,)
        ).fetchone()
        return _account_from_row(row) if row else None
    finally:
        conn.close()


def get_account_by_id(account_id: int, db_path: str | None = None) -> Account | None:
    """根据 ID 获取单个账号。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        return _account_from_row(row) if row else None
    finally:
        conn.close()


def add_account(
    phone: str,
    name: str = "",
    remark: str = "",
    device_id: str | None = None,
    db_path: str | None = None,
) -> Account:
    """添加新账号。

    自动生成 device_id（如果未提供）和时间戳。
    """
    if device_id is None:
        device_id = uuid.uuid4().hex[:25]

    now = time.time()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO accounts (phone, name, remark, device_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (phone, name, remark, device_id, now, now),
        )
        conn.commit()
        return Account(
            phone=phone,
            name=name,
            remark=remark,
            device_id=device_id,
            created_at=now,
            updated_at=now,
            id=conn.execute("SELECT last_insert_rowid()").fetchone()[0],
        )
    finally:
        conn.close()


def update_account(phone: str, db_path: str | None = None, **fields) -> bool:
    """更新账号字段。

    支持的字段: name, remark, enabled, auth_token, user_id, device_id
    自动更新 updated_at。
    """
    allowed = {"name", "remark", "enabled", "auth_token", "user_id", "device_id"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False

    updates["updated_at"] = time.time()

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [phone]

    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            f"UPDATE accounts SET {set_clause} WHERE phone = ?",
            values,
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def update_account_token(
    phone: str,
    token: str,
    user_id: int,
    db_path: str | None = None,
) -> bool:
    """登录成功后保存 token 和 user_id。"""
    return update_account(phone, auth_token=token, user_id=user_id, db_path=db_path)


def delete_account(phone: str, db_path: str | None = None) -> bool:
    """删除账号及其领取历史。"""
    conn = get_connection(db_path)
    try:
        # 先查 id
        row = conn.execute(
            "SELECT id FROM accounts WHERE phone = ?", (phone,)
        ).fetchone()
        if not row:
            return False
        account_id = row["id"]
        # 删除关联的领取历史
        conn.execute("DELETE FROM claim_history WHERE account_id = ?", (account_id,))
        # 删除账号
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        conn.commit()
        return True
    finally:
        conn.close()


# ── 设置 CRUD ─────────────────────────────────────────────────────

_DEFAULT_SETTINGS = {
    "max_concurrent": "10",
    "request_interval": "1.0",
    "max_rounds": "21",
    "schedule_time": "08:00",
    "schedule_enabled": "false",
    "schedule_method": "schtasks",
}

_SETTINGS_VALIDATORS = {
    "max_concurrent": lambda v: max(1, min(50, int(v))),
    "request_interval": lambda v: max(0.1, min(30.0, float(v))),
    "max_rounds": lambda v: max(1, min(200, int(v))),
    "schedule_time": lambda v: str(v),
    "schedule_enabled": lambda v: "true" if str(v).lower() in ("true", "1", "yes") else "false",
    "schedule_method": lambda v: v if str(v) in ("schtasks", "service") else "schtasks",
}


def get_settings(db_path: str | None = None) -> dict[str, Any]:
    """获取所有设置，返回带类型转换的 dict。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        result = dict(_DEFAULT_SETTINGS)
        for row in rows:
            result[row["key"]] = row["value"]

        # 类型转换
        if "max_concurrent" in result:
            result["max_concurrent"] = int(result["max_concurrent"])
        if "request_interval" in result:
            result["request_interval"] = float(result["request_interval"])
        if "max_rounds" in result:
            result["max_rounds"] = int(result["max_rounds"])
        if "schedule_enabled" in result:
            result["schedule_enabled"] = result["schedule_enabled"] == "true"

        return result
    finally:
        conn.close()


def update_settings(db_path: str | None = None, **kwargs) -> bool:
    """更新设置（部分更新），自动验证范围。"""
    allowed = {"max_concurrent", "request_interval", "max_rounds", "schedule_time", "schedule_enabled", "schedule_method"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False

    # 验证并钳位
    for key, validator in _SETTINGS_VALIDATORS.items():
        if key in updates:
            updates[key] = str(validator(updates[key]))

    conn = get_connection(db_path)
    try:
        for key, value in updates.items():
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        conn.commit()
        return True
    finally:
        conn.close()


# ── 领取历史 ──────────────────────────────────────────────────────

def add_claim_record(
    account_id: int,
    status: str,
    vip_before: int = 0,
    vip_after: int = 0,
    claimed_count: int = 0,
    failed_count: int = 0,
    db_path: str | None = None,
) -> None:
    """写入一条领取记录。"""
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO claim_history
               (account_id, claimed_at, vip_before, vip_after,
                claimed_count, failed_count, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (account_id, time.time(), vip_before, vip_after,
             claimed_count, failed_count, status),
        )
        conn.commit()
    finally:
        conn.close()


def get_claim_history(
    account_id: int | None = None,
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict]:
    """查询领取历史。"""
    conn = get_connection(db_path)
    try:
        if account_id is not None:
            rows = conn.execute(
                """SELECT ch.*, a.phone
                   FROM claim_history ch
                   JOIN accounts a ON ch.account_id = a.id
                   WHERE ch.account_id = ?
                   ORDER BY ch.claimed_at DESC
                   LIMIT ?""",
                (account_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT ch.*, a.phone
                   FROM claim_history ch
                   JOIN accounts a ON ch.account_id = a.id
                   ORDER BY ch.claimed_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
