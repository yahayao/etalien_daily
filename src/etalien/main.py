"""CLI 入口模块。

支持子命令:
    etalien [--auto-close] [--account PHONE]   默认: 执行领取
    etalien account add <phone>                 添加账号
    etalien account list                        列出账号
    etalien account login <phone>               登录获取 token
    etalien account remove <phone>              删除账号
    etalien account toggle <phone>              启用/禁用账号
    etalien account info <phone>                查看账号详情
    etalien settings                            查看设置
    etalien settings set <key> <value>          修改设置
"""

import argparse
import logging
import sys
import time
from typing import Any

from etalien import __version__
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
from etalien.service import (
    STATUS_ALREADY_DONE,
    STATUS_AUTH_ERROR,
    STATUS_ERROR,
    STATUS_NEED_LOGIN,
    STATUS_OK,
    claim_for_account,
    run_concurrent_claim,
)

# ── 退出码 ────────────────────────────────────────────────────────

EXIT_ALL_OK = 0
EXIT_PARTIAL = 1
EXIT_ALL_FAIL = 2
EXIT_NEED_LOGIN = 3
EXIT_NO_ENABLED = 4


# ── 日志 ──────────────────────────────────────────────────────────

def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        level=level,
        datefmt="%H:%M:%S",
    )


# ── 参数解析 ──────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="etalien",
        description="ET免广告领取加速时长",
    )
    parser.add_argument(
        "--version", action="version", version=f"etalien-daily v{__version__}",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="输出详细调试信息",
    )

    sub = parser.add_subparsers(dest="command")

    # ── 默认: 领取 ──
    # (不设子命令时的参数放在根 parser)
    parser.add_argument(
        "--auto-close",
        action="store_true",
        help="完成后自动关闭，不等待回车（用于定时任务）",
    )
    parser.add_argument(
        "--account",
        type=str,
        default=None,
        metavar="PHONE",
        help="仅对指定手机号执行",
    )

    # ── account add ──
    p_add = sub.add_parser("account", help="账号管理")
    acc_sub = p_add.add_subparsers(dest="account_action")

    p_add_acc = acc_sub.add_parser("add", help="添加账号")
    p_add_acc.add_argument("phone", help="手机号（如 13800138000）")
    p_add_acc.add_argument("--name", "-n", default="", help="备注名")
    p_add_acc.add_argument("--remark", "-r", default="", help="备注")

    # ── account list ──
    p_list = acc_sub.add_parser("list", help="列出所有账号")
    p_list.add_argument("--all", "-a", action="store_true", help="包含已禁用的账号")

    # ── account login ──
    p_login = acc_sub.add_parser("login", help="登录获取 token")
    p_login.add_argument("phone", help="手机号")
    p_login.add_argument("--code", "-c", default=None, help="验证码（不指定则先发送验证码）")

    # ── account remove ──
    p_rm = acc_sub.add_parser("remove", help="删除账号")
    p_rm.add_argument("phone", help="手机号")
    p_rm.add_argument("--force", "-f", action="store_true", help="跳过确认")

    # ── account toggle ──
    p_toggle = acc_sub.add_parser("toggle", help="启用/禁用账号")
    p_toggle.add_argument("phone", help="手机号")

    # ── account info ──
    p_info = acc_sub.add_parser("info", help="查看账号详情")
    p_info.add_argument("phone", help="手机号")

    # ── settings ──
    p_set = sub.add_parser("settings", help="设置管理")
    set_sub = p_set.add_subparsers(dest="settings_action")

    p_set_show = set_sub.add_parser("show", help="查看当前设置")
    p_set_set = set_sub.add_parser("set", help="修改设置")
    p_set_set.add_argument("key", help="设置项 (max_concurrent/request_interval/max_rounds/schedule_time)")
    p_set_set.add_argument("value", help="设置值")

    return parser


# ── 主入口 ────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    # --service 模式：作为 Windows 服务运行
    if argv is None:
        argv = sys.argv[1:]
    if "--service" in (argv or []):
        from etalien.service_wrapper import run_service
        run_service()
        return

    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    init_db()

    if args.command == "account":
        _handle_account(args)
    elif args.command == "settings":
        _handle_settings(args)
    else:
        _handle_claim(args)


# ── 领取流程 ──────────────────────────────────────────────────────

def _handle_claim(args: argparse.Namespace) -> None:
    """处理默认的领取命令。"""
    settings = get_settings()
    accounts = get_accounts(enabled_only=True)

    if args.account:
        acc = get_account(args.account)
        if acc is None:
            print(f"错误: 账号 {args.account} 不存在")
            sys.exit(EXIT_NO_ENABLED)
        if not acc.enabled:
            print(f"提示: 账号 {args.account} 未启用，将强制执行")
        accounts = [acc]

    if not accounts:
        print("没有启用的账号。请先添加账号并登录。")
        print()
        print("使用方法:")
        print("  etalien account add <手机号>     添加账号")
        print("  etalien account login <手机号>   登录获取 token")
        sys.exit(EXIT_NO_ENABLED)

    print(f"ET免广告领取加速时长 v{__version__}")
    print("=" * 50)
    print(f"账号数: {len(accounts)} | 并发数: {settings['max_concurrent']}")
    print(f"请求间隔: {settings['request_interval']}s | 最大轮数: {settings['max_rounds']}")
    print("=" * 50)
    print()

    start_time = time.time()
    results = run_concurrent_claim(accounts, settings)
    elapsed = time.time() - start_time

    _print_results(results)
    print()
    _print_summary(results, elapsed)

    if not args.auto_close:
        print()
        try:
            input("按回车键退出...")
        except (EOFError, KeyboardInterrupt):
            print()

    sys.exit(_determine_exit_code(results))


# ── 账号管理 ──────────────────────────────────────────────────────

def _handle_account(args: argparse.Namespace) -> None:
    action = args.account_action

    if action == "add":
        _cmd_account_add(args)
    elif action == "list":
        _cmd_account_list(args)
    elif action == "login":
        _cmd_account_login(args)
    elif action == "remove":
        _cmd_account_remove(args)
    elif action == "toggle":
        _cmd_account_toggle(args)
    elif action == "info":
        _cmd_account_info(args)
    else:
        print("请指定账号子命令: add | list | login | remove | toggle | info")
        print("使用 etalien account -h 查看帮助")


def _cmd_account_add(args: argparse.Namespace) -> None:
    phone = args.phone
    if get_account(phone):
        print(f"错误: 手机号 {phone} 已存在")
        sys.exit(1)

    acc = add_account(phone=phone, name=args.name, remark=args.remark)
    print(f"账号已添加: {acc.phone}")
    print(f"  device_id: {acc.device_id}")
    print(f"  备注名: {acc.name or '-'}")
    print()
    print("下一步: etalien account login", phone)


def _cmd_account_list(args: argparse.Namespace) -> None:
    accounts = get_accounts(enabled_only=not args.all)
    if not accounts:
        print("没有账号")
        return

    print(f"{'手机号':<16} {'状态':<6} {'备注名':<12} {'Token':<10} {'领取次数':<8}")
    print("-" * 56)
    for acc in accounts:
        status = "启用" if acc.enabled else "禁用"
        token = "有效" if acc.auth_token else "无"
        history = get_claim_history(account_id=acc.id, limit=1000)
        claim_count = len([h for h in history if h["status"] in (STATUS_OK, STATUS_ALREADY_DONE)])
        print(f"{acc.phone:<16} {status:<6} {acc.name or '-':<12} {token:<10} {claim_count:<8}")


def _normalize_phone(phone: str) -> str:
    """标准化手机号：11 位国内号码自动加 +86 前缀。"""
    phone = phone.strip()
    if not phone.startswith("+") and len(phone) == 11 and phone.isdigit():
        return "+86" + phone
    return phone


def _fetch_and_set_nickname(client, phone: str) -> None:
    """登录后获取用户昵称，如果备注名为空则自动设置。"""
    try:
        profile = client.fetch_my_profile()
        nickname = profile.get("nickname", "")
        if nickname:
            acc = get_account(phone)
            if acc and not acc.name:
                update_account(phone, name=nickname)
                print(f"  昵称: {nickname} (已自动设为备注名)")
    except Exception:
        pass


def _cmd_account_login(args: argparse.Namespace) -> None:
    phone = args.phone
    acc = get_account(phone)
    if acc is None:
        print(f"错误: 账号 {phone} 不存在，请先添加")
        print(f"  etalien account add {phone}")
        sys.exit(1)

    api_phone = _normalize_phone(phone)
    client = ApiClient(device_id=acc.device_id)

    if args.code:
        # 已有验证码，直接登录
        print(f"正在登录 {phone} ...")
        result = client.login(phone_number=api_phone, verification_code=args.code)
        if result.get("_error"):
            print(f"登录失败: {result.get('msg')} (code={result.get('code')})")
            sys.exit(1)

        token = result.get("authorization", "")
        user_id = result.get("user_id", 0)
        update_account_token(phone, token, user_id)
        print(f"登录成功!")
        print(f"  user_id: {user_id}")
        print(f"  token: {token[:20]}...")
        _fetch_and_set_nickname(client, phone)
    else:
        # 先发送验证码
        print(f"正在向 {phone} 发送验证码 ...")
        result = client.get_verification_code(phone_number=api_phone)
        if result.get("_error"):
            code = result.get("code", 0)
            if code in (60, 1000):
                print(f"验证码冷却中 (code={code})，但可能已发送，请检查短信")
            else:
                print(f"发送验证码失败: {result.get('msg')} (code={code})")
                sys.exit(1)
        else:
            print("验证码已发送，请检查短信")

        # 交互式输入验证码
        try:
            verify_code = input("请输入验证码: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)

        if not verify_code:
            print("未输入验证码，已取消")
            sys.exit(0)

        print("正在登录 ...")
        result = client.login(phone_number=api_phone, verification_code=verify_code)
        if result.get("_error"):
            print(f"登录失败: {result.get('msg')} (code={result.get('code')})")
            sys.exit(1)

        token = result.get("authorization", "")
        user_id = result.get("user_id", 0)
        update_account_token(phone, token, user_id)
        print(f"登录成功!")
        print(f"  user_id: {user_id}")
        print(f"  token: {token[:20]}...")
        _fetch_and_set_nickname(client, phone)


def _cmd_account_remove(args: argparse.Namespace) -> None:
    phone = args.phone
    acc = get_account(phone)
    if acc is None:
        print(f"错误: 账号 {phone} 不存在")
        sys.exit(1)

    if not args.force:
        try:
            confirm = input(f"确认删除账号 {phone} ({acc.name or '无备注'})? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if confirm not in ("y", "yes"):
            print("已取消")
            return

    delete_account(phone)
    print(f"已删除账号: {phone}")


def _cmd_account_toggle(args: argparse.Namespace) -> None:
    phone = args.phone
    acc = get_account(phone)
    if acc is None:
        print(f"错误: 账号 {phone} 不存在")
        sys.exit(1)

    new_enabled = not acc.enabled
    update_account(phone, enabled=new_enabled)
    state = "启用" if new_enabled else "禁用"
    print(f"账号 {phone} 已{state}")


def _cmd_account_info(args: argparse.Namespace) -> None:
    phone = args.phone
    acc = get_account(phone)
    if acc is None:
        print(f"错误: 账号 {phone} 不存在")
        sys.exit(1)

    print(f"手机号:    {acc.phone}")
    print(f"备注名:    {acc.name or '-'}")
    print(f"备注:      {acc.remark or '-'}")
    print(f"状态:      {'启用' if acc.enabled else '禁用'}")
    print(f"User ID:   {acc.user_id or '-'}")
    print(f"Token:     {(acc.auth_token[:30] + '...') if acc.auth_token else '无'}")
    print(f"Device ID: {acc.device_id}")
    print(f"创建时间:  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(acc.created_at)) if acc.created_at else '-'}")

    # 最近领取记录
    history = get_claim_history(account_id=acc.id, limit=5)
    if history:
        print()
        print("最近领取记录:")
        for h in history:
            t = time.strftime("%m-%d %H:%M", time.localtime(h["claimed_at"]))
            gained = h["vip_after"] - h["vip_before"]
            print(f"  {t}  {h['status']:<14}  +{_fmt_duration(gained)}  ({h['claimed_count']}成功/{h['failed_count']}失败)")


# ── 设置管理 ──────────────────────────────────────────────────────

def _handle_settings(args: argparse.Namespace) -> None:
    action = args.settings_action

    if action == "set":
        _cmd_settings_set(args)
    else:
        _cmd_settings_show(args)


def _cmd_settings_show(args: argparse.Namespace) -> None:
    settings = get_settings()
    print("当前设置:")
    for key, value in settings.items():
        print(f"  {key}: {value}")


def _cmd_settings_set(args: argparse.Namespace) -> None:
    key = args.key
    value = args.value

    allowed = {"max_concurrent", "request_interval", "max_rounds", "schedule_time", "schedule_enabled", "schedule_method"}
    if key not in allowed:
        print(f"错误: 未知设置项 '{key}'，允许: {', '.join(sorted(allowed))}")
        sys.exit(1)

    update_settings(**{key: value})
    settings = get_settings()
    print(f"已更新: {key} = {settings[key]}")


# ── 结果输出 ──────────────────────────────────────────────────────

def _print_results(results: list[dict]) -> None:
    if not results:
        return

    header = f"{'手机号':<16} {'状态':<10} {'领取前':>8} {'领取后':>8} {'增长':>8} {'成功':>5} {'失败':>5}"
    print(header)
    print("-" * len(header))

    status_labels = {
        STATUS_OK: "成功",
        STATUS_ALREADY_DONE: "已完成",
        STATUS_AUTH_ERROR: "认证失败",
        STATUS_NEED_LOGIN: "需登录",
        STATUS_ERROR: "错误",
    }

    for r in results:
        phone = r["phone"]
        status = status_labels.get(r["status"], r["status"])
        before = _fmt_duration(r.get("vip_before", 0))
        after = _fmt_duration(r.get("vip_after", 0))
        gained = r.get("vip_after", 0) - r.get("vip_before", 0)
        gained_str = _fmt_duration(gained) if gained > 0 else "-"
        claimed = str(r.get("claimed", 0))
        failed = str(r.get("failed", 0))

        print(f"{phone:<16} {status:<10} {before:>8} {after:>8} {gained_str:>8} {claimed:>5} {failed:>5}")


def _print_summary(results: list[dict], elapsed: float) -> None:
    ok_count = sum(1 for r in results if r["status"] == STATUS_OK)
    done_count = sum(1 for r in results if r["status"] == STATUS_ALREADY_DONE)
    auth_err = sum(1 for r in results if r["status"] == STATUS_AUTH_ERROR)
    need_login = sum(1 for r in results if r["status"] == STATUS_NEED_LOGIN)
    err_count = sum(1 for r in results if r["status"] == STATUS_ERROR)
    total_gained = sum(
        r.get("vip_after", 0) - r.get("vip_before", 0)
        for r in results
        if r["status"] in (STATUS_OK, STATUS_ALREADY_DONE)
    )
    total_claimed = sum(r.get("claimed", 0) for r in results)
    total_failed = sum(r.get("failed", 0) for r in results)

    parts = []
    if ok_count: parts.append(f"{ok_count} 成功")
    if done_count: parts.append(f"{done_count} 已完成")
    if auth_err: parts.append(f"{auth_err} 认证失败")
    if need_login: parts.append(f"{need_login} 需登录")
    if err_count: parts.append(f"{err_count} 错误")

    print(f"汇总: {', '.join(parts)}")
    if total_gained > 0:
        print(f"总增长: {_fmt_duration(total_gained)}")
    print(f"回调: {total_claimed} 成功 / {total_failed} 失败 | 耗时: {elapsed:.1f}s")


def _determine_exit_code(results: list[dict]) -> int:
    if not results:
        return EXIT_NO_ENABLED
    statuses = [r["status"] for r in results]
    has_login = STATUS_NEED_LOGIN in statuses
    ok_count = sum(1 for s in statuses if s in (STATUS_OK, STATUS_ALREADY_DONE))
    fail_count = sum(1 for s in statuses if s in (STATUS_AUTH_ERROR, STATUS_ERROR))

    if has_login: return EXIT_NEED_LOGIN
    if fail_count == len(results): return EXIT_ALL_FAIL
    if ok_count > 0 and fail_count > 0: return EXIT_PARTIAL
    return EXIT_ALL_OK


def _fmt_duration(seconds: int) -> str:
    if seconds <= 0:
        return "-"
    hours, remainder = divmod(abs(seconds), 3600)
    minutes = remainder // 60
    if hours > 0:
        return f"{hours}h{minutes}m"
    elif minutes > 0:
        return f"{minutes}m"
    else:
        return f"{seconds}s"


if __name__ == "__main__":
    main()
