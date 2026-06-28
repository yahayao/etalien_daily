"""业务逻辑层。

编排完整的领取流程：
- 单账号领取: claim_for_account()
- 并发领取: run_concurrent_claim()
- 防死循环 + 认证错误检测 + 进度回调
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from etalien.client import ApiClient
from etalien.db import (
    Account,
    add_claim_record,
    get_settings,
    update_account,
    update_account_token,
)

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────

AD_ID = "103334281"            # 固定广告 ID
BUSINESS_TYPES = [1, 2, 3]     # 三种广告业务类型
MAX_STALLED_ROUNDS = 3         # 连续无进展最大轮数（防死循环）
BUSINESS_SLEEP = 3.0           # 业务类型切换间隔（秒）


# ── 结果状态 ──────────────────────────────────────────────────────

STATUS_OK = "ok"
STATUS_ALREADY_DONE = "already_done"
STATUS_AUTH_ERROR = "auth_error"
STATUS_NEED_LOGIN = "need_login"
STATUS_ERROR = "error"


# ── 客户端初始化 ──────────────────────────────────────────────────

def init_client(account: Account) -> ApiClient | None:
    """为账号初始化 ApiClient。

    - 如果账号有 token，验证其有效性
    - 如果 token 有效，返回带 token 的 client
    - 如果 token 过期，返回不带 token 的 client（调用方需先登录）

    Returns:
        ApiClient 实例，异常时返回 None。
    """
    try:
        client = ApiClient(
            device_id=account.device_id,
            auth_token=account.auth_token,
        )
    except Exception as e:
        logger.error("初始化 ApiClient 失败 (%s): %s", account.phone, e)
        return None

    # 如果有 token，验证有效性
    if account.auth_token:
        if client.check_token_valid():
            logger.debug("Token 有效: %s", account.phone)
        else:
            logger.info("Token 已过期: %s", account.phone)
            client.clear_auth_token()

    return client


# ── 单账号领取 ────────────────────────────────────────────────────

def claim_for_account(
    account: Account,
    settings: dict[str, Any] | None = None,
    progress_callback: Callable | None = None,
) -> dict[str, Any]:
    """对单个账号执行完整领取流程。

    Args:
        account: 账号对象（含 token 和 device_id）。
        settings: 设置 dict，为 None 时自动加载。
        progress_callback: 进度回调，签名为 callback(phone, step, detail)。

    Returns:
        {
            "phone": str,
            "status": str,       # ok / already_done / auth_error / need_login / error
            "vip_before": int,
            "vip_after": int,
            "claimed": int,
            "failed": int,
            "error_msg": str | None,
        }
    """
    if settings is None:
        settings = get_settings()

    phone = account.phone
    base_result = {
        "phone": phone,
        "status": STATUS_ERROR,
        "vip_before": 0,
        "vip_after": 0,
        "claimed": 0,
        "failed": 0,
        "error_msg": None,
    }

    # 1. 初始化客户端
    _report(progress_callback, phone, "init", "初始化客户端")
    client = init_client(account)
    if client is None:
        base_result["status"] = STATUS_ERROR
        base_result["error_msg"] = "初始化客户端失败"
        return base_result

    if not client.get_auth_token():
        base_result["status"] = STATUS_NEED_LOGIN
        base_result["error_msg"] = "未登录或 token 已过期"
        return base_result

    # 2. 查询领取前 VIP 时长
    _report(progress_callback, phone, "before", "查询当前时长")
    before = client.fetch_pc_duration()
    if before.get("_error"):
        if _is_auth_error(before):
            base_result["status"] = STATUS_AUTH_ERROR
            base_result["error_msg"] = "token 已过期"
            return base_result
        base_result["error_msg"] = f"查询时长失败: {before.get('msg')}"
        return base_result
    vip_before = int(before.get("vip_duration_second", 0))
    base_result["vip_before"] = vip_before

    # 3. 获取广告任务列表
    _report(progress_callback, phone, "config", "获取广告任务")
    config = client.fetch_pc_ad_config()
    if config.get("_error"):
        base_result["error_msg"] = f"获取任务列表失败: {config.get('msg')}"
        return base_result

    # 检查是否全部已完成
    if _all_ads_watched(config):
        _report(progress_callback, phone, "done", "所有广告已观看完毕")
        base_result["status"] = STATUS_ALREADY_DONE
        base_result["vip_after"] = vip_before
        _save_claim_record(account.id, base_result)
        return base_result

    # 4. 对每种 business 逐一领取
    total_claimed = 0
    total_failed = 0

    for idx, business in enumerate(BUSINESS_TYPES):
        # 切换 business 类型时短暂等待
        if idx > 0:
            time.sleep(BUSINESS_SLEEP)

        _report(progress_callback, phone, f"business_{business}", f"领取 business={business}")

        claimed, failed = _claim_business_phase(
            client, business, settings, phone, progress_callback,
        )

        total_claimed += claimed
        total_failed += failed

        # 检查认证错误（token 中途过期）
        if failed > 0 and not client.get_auth_token():
            # token 被清除说明遇到了认证错误
            pass

    base_result["claimed"] = total_claimed
    base_result["failed"] = total_failed

    # 5. 查询领取后 VIP 时长
    _report(progress_callback, phone, "after", "查询领取后时长")
    after = client.fetch_pc_duration()
    if after.get("_error"):
        if _is_auth_error(after):
            base_result["status"] = STATUS_AUTH_ERROR
            base_result["error_msg"] = "领取后 token 过期"
            _save_claim_record(account.id, base_result)
            return base_result

    vip_after = int(after.get("vip_duration_second", vip_before))
    base_result["vip_after"] = vip_after

    # 判断最终状态
    if total_failed > 0 and total_claimed == 0:
        base_result["status"] = STATUS_ERROR
        base_result["error_msg"] = base_result["error_msg"] or "所有回调均失败"
    else:
        base_result["status"] = STATUS_OK

    _save_claim_record(account.id, base_result)
    return base_result


# ── 单 business 领取阶段 ──────────────────────────────────────────

def _claim_business_phase(
    client: ApiClient,
    business: int,
    settings: dict,
    phone: str,
    progress_callback: Callable | None = None,
) -> tuple[int, int]:
    """对单个 business 类型执行回调循环。

    循环逻辑:
    1. 查广告任务 → 看该 business 还有多少未观看
    2. 全部完成 → 退出
    3. 发送回调 → sleep(interval)
    4. 再查任务 → 比较 watch_cnt 有无变化
    5. 连续 MAX_STALLED_ROUNDS 轮无进展 → 退出（防死循环）

    Returns:
        (claimed 成功次数, failed 失败次数)
    """
    claimed = 0
    failed = 0
    stalled_rounds = 0
    request_interval = settings.get("request_interval", 1.0)
    max_rounds = settings.get("max_rounds", 21)

    for round_num in range(max_rounds):
        # 检查 token
        if not client.get_auth_token():
            logger.warning("Token 无效，停止领取: %s business=%d", phone, business)
            break

        # 查任务
        config = client.fetch_pc_ad_config()
        if config.get("_error"):
            logger.warning("获取任务列表失败: %s", config.get("msg"))
            failed += 1
            continue

        # 找到对应 business 的 level
        level_item = _find_business_level(config, business)
        if level_item is None:
            # 该 business 没有任务，跳过
            break

        watch_cnt_before = int(level_item.get("watch_cnt", 0))
        total_cnt = len(level_item.get("list", []))
        if total_cnt > 0 and watch_cnt_before >= total_cnt:
            # 该 business 全部完成
            _report(progress_callback, phone, f"b{business}", f"business={business} 已完成 ({watch_cnt_before}/{total_cnt})")
            break

        # 发送回调
        _report(progress_callback, phone, f"b{business}_r{round_num}",
                f"business={business} 第{round_num+1}轮 ({watch_cnt_before}/{total_cnt})")
        result = client.pc_ad_callback_backup(AD_ID, business)

        if result.get("_error"):
            if _is_auth_error(result):
                client.clear_auth_token()
                failed += 1
                break
            logger.warning("回调失败 (b=%d): %s", business, result.get("msg"))
            failed += 1
        elif result.get("is_verify"):
            claimed += 1
        else:
            failed += 1

        # 等待间隔
        time.sleep(request_interval)

        # 再查任务看进展
        config2 = client.fetch_pc_ad_config()
        if config2.get("_error"):
            continue

        level_item2 = _find_business_level(config2, business)
        if level_item2 is None:
            break

        watch_cnt_after = int(level_item2.get("watch_cnt", 0))
        if watch_cnt_after > watch_cnt_before:
            stalled_rounds = 0  # 有进展，重置
        else:
            stalled_rounds += 1
            if stalled_rounds >= MAX_STALLED_ROUNDS:
                _report(progress_callback, phone, f"b{business}", f"business={business} 连续{MAX_STALLED_ROUNDS}轮无进展，停止")
                break

    return claimed, failed


# ── 多账号并发领取 ────────────────────────────────────────────────

def run_concurrent_claim(
    accounts: list[Account],
    settings: dict[str, Any] | None = None,
    progress_callback: Callable | None = None,
) -> list[dict[str, Any]]:
    """并发领取多个账号。

    Args:
        accounts: 已启用的账号列表。
        settings: 设置 dict。
        progress_callback: 进度回调。

    Returns:
        结果列表（完成顺序，非提交顺序）。
    """
    if settings is None:
        settings = get_settings()

    max_workers = min(settings.get("max_concurrent", 10), len(accounts))
    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_account = {
            executor.submit(claim_for_account, acc, settings, progress_callback): acc
            for acc in accounts
        }
        for future in as_completed(future_to_account):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                acc = future_to_account[future]
                logger.error("领取异常 (%s): %s", acc.phone, e)
                results.append({
                    "phone": acc.phone,
                    "status": STATUS_ERROR,
                    "vip_before": 0,
                    "vip_after": 0,
                    "claimed": 0,
                    "failed": 0,
                    "error_msg": str(e),
                })

    return results


# ── 辅助函数 ──────────────────────────────────────────────────────

def _report(callback: Callable | None, phone: str, step: str, detail: str) -> None:
    """调用进度回调（如果提供）。"""
    if callback:
        try:
            callback(phone, step, detail)
        except Exception as e:
            logger.warning("进度回调异常: %s", e)


def _is_auth_error(result: dict) -> bool:
    """判断响应是否为认证错误。"""
    if not result.get("_error"):
        return False
    return result.get("code") in (16, 401, 403)


def _all_ads_watched(config: dict) -> bool:
    """检查所有广告是否都已观看。"""
    levels = config.get("list", [])
    if not levels:
        return True
    for level in levels:
        items = level.get("list", [])
        if not items:
            continue
        watch_cnt = int(level.get("watch_cnt", 0))
        if watch_cnt < len(items):
            return False
    return True


def _find_business_level(config: dict, business: int) -> dict | None:
    """在任务列表中查找指定 business 的 level 条目。"""
    levels = config.get("list", [])
    for level in levels:
        if int(level.get("level", 0)) == business:
            return level
    return None


def _save_claim_record(account_id: int, result: dict) -> None:
    """保存领取记录到数据库。"""
    try:
        add_claim_record(
            account_id=account_id,
            status=result["status"],
            vip_before=result.get("vip_before", 0),
            vip_after=result.get("vip_after", 0),
            claimed_count=result.get("claimed", 0),
            failed_count=result.get("failed", 0),
        )
    except Exception as e:
        logger.warning("保存领取记录失败: %s", e)
