# 外星仔免广告领取加速时长 - 实现原理文档

> 本文档记录外星仔（ET/Alien）加速器的 API 协议、签名算法、业务逻辑等核心实现原理，供重写软件时参考。

---

## 目录

1. [概述](#1-概述)
2. [API 端点汇总](#2-api-端点汇总)
3. [请求签名算法](#3-请求签名算法)
4. [Protobuf 消息定义](#4-protobuf-消息定义)
5. [认证流程](#5-认证流程)
6. [核心原理：广告回调绕过](#6-核心原理广告回调绕过)
7. [领取流程](#7-领取流程)
8. [数据存储结构](#8-数据存储结构)
9. [客户端伪装](#9-客户端伪装)
10. [错误处理](#10-错误处理)
11. [GUI 架构](#11-gui-架构)
12. [CLI 架构](#12-cli-架构)
13. [打包部署](#13-打包部署)
14. [重写注意事项](#14-重写注意事项)

---

## 1. 概述

**目标**：自动领取外星仔加速器的 VIP 时长，无需观看广告。

**核心思路**：逆向还原外星仔 App 的网络协议（签名 + Protobuf），直接调用服务端的"广告已看完"回调接口，让服务端以为用户观看了广告，从而获得时长奖励。

**技术栈**：Python 3.11 + requests + protobuf + Flask + pywebview + PyInstaller

**架构图**：

```
┌──────────────────────────────────────┐
│              表现层                    │
│  ┌──────────┐    ┌───────────────┐    │
│  │ GUI 窗口  │    │  CLI 入口      │    │
│  │ (pywebview│    │  (main.py)    │    │
│  │  + Flask) │    │               │    │
│  └─────┬─────┘    └───────┬───────┘    │
│        │                  │            │
│  ┌─────┴──────────────────┴───────┐    │
│  │          REST API 层            │    │
│  │         (gui/api.py)           │    │
│  └───────────────┬────────────────┘    │
│                  │                     │
├──────────────────┼─────────────────────┤
│              业务层                     │
│  ┌───────────────┴────────────────┐    │
│  │       service.py               │    │
│  │  · 账号管理 · 登录/验证        │    │
│  │  · 并发领取 · 状态查询         │    │
│  │  · 定时任务 · 进度追踪         │    │
│  └───────────────┬────────────────┘    │
│                  │                     │
│  ┌───────────────┴────────────────┐    │
│  │       client.py                │    │
│  │  · HTTP 请求/重试              │    │
│  │  · Protobuf 序列化/反序列化     │    │
│  │  · 认证 token 管理             │    │
│  └───────────────┬────────────────┘    │
│                  │                     │
│  ┌───────────────┴────────────────┐    │
│  │         sign.py                │    │
│  │  · 参数排序 · SHA-256 签名     │    │
│  │  · URL 构建                    │    │
│  └───────────────────────────────┘     │
│                                        │
│  ┌────────────────────────────────┐    │
│  │       config.py                │    │
│  │  · 账号 JSON 读写              │    │
│  │  · 设置 JSON 读写              │    │
│  │  · 原子写入 · 验证            │    │
│  └────────────────────────────────┘    │
└────────────────────────────────────────┘
          │
          ▼
   api.et-api.com (外星仔服务端)
```

---

## 2. API 端点汇总

| 方法 | 路径 | 用途 | 请求体 Protobuf |
|------|------|------|----------------|
| GET | `/account/v1/get_login_verification_code` | 获取短信验证码 | `GetLoginVerificationCodeRequest` |
| POST | `/account/v1/login` | 验证码登录 | `LoginRequest` |
| POST | `/v2/account/pc/ad/config` | 获取广告任务列表 | `PcAdConfigRequest`（空） |
| POST | `/v2/account/pc/ad/callback/backup` | **广告奖励补发（核心）** | `PcAdCallbackBackupRequest` |
| POST | `/v2/account/remain/duration` | 查询剩余时长 | `GetUserRemainDurationRequest`（空） |

### 2.1 未使用的端点（proto 中已定义）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/account/v1/login/by-one-click` | 一键登录（本工具未实现） |
| GET | `/account/v1/my_profile` | 用户信息（未使用） |
| POST | `/v2/account/refresh/token` | 刷新 token（未使用） |
| POST | `/v2/account/update/pause/state` | 暂停/恢复加速（未使用） |
| POST | `/v2/account/mobile/pc_product/list` | PC 产品列表（未使用） |

---

## 3. 请求签名算法

外星仔 App 内置了 `SignInterceptor` 对所有 HTTP 请求进行签名校验。签名算法如下：

### 3.1 参数排序

```
输入: method, host, path, query_params, port

1. 复制原始 query_params（如果有）
2. 追加三个固定参数:
   - ts:    当前 Unix 时间戳（秒级）
   - nonce:  uuid4().hex (32位随机十六进制字符串)
   - ver:   "2023-08-28" (固定版本号)
3. 对所有参数的 key 按字母序排序
4. 拼接: key1=URL编码(val1)&key2=URL编码(val2)&...
   - 值为 None 的 key 只拼 "key="
```

### 3.2 签名计算

```
签名原文 = method + "host" + path + "?" + 排序后的参数字符串

其中:
  - method: "GET" 或 "POST" (大写)
  - host:   "api.et-api.com"
  - path:   请求路径, 如 "/v2/account/pc/ad/config"
  - 端口如果不是 80/443 则拼入 host:port

签名值 = SHA-256(URL解码后的签名原文).hex()
```

### 3.3 最终 URL

```
最终URL = "https://api.et-api.com" + path + "?" + 排序参数字符串 + "&sig=" + 签名值
```

### 3.4 Python 参考实现

```python
import hashlib
import time
import uuid
from urllib.parse import quote, unquote

def get_sort_parameters(query_params: dict | None) -> str:
    params = dict(query_params) if query_params else {}
    params["ts"] = str(int(time.time()))
    params["nonce"] = uuid.uuid4().hex
    params["ver"] = "2023-08-28"
    sorted_keys = sorted(params.keys())
    parts = []
    for key in sorted_keys:
        val = params[key]
        if val is not None:
            parts.append(f"{key}={quote(str(val), safe='')}")
        else:
            parts.append(f"{key}=")
    return "&".join(parts)

def get_sign(data: str) -> str:
    decoded = unquote(data)
    sha256 = hashlib.sha256(decoded.encode("utf-8")).digest()
    return sha256.hex()

def sign_url(method: str, host: str, path: str, query_params=None, port=None) -> str:
    sorted_query = get_sort_parameters(query_params)
    if port and port not in (80, 443):
        base = f"{host}:{port}{path}?{sorted_query}"
    else:
        base = f"{host}{path}?{sorted_query}"
    sign_str = get_sign(f"{method}{base}")
    return f"{base}&sig={sign_str}"
```

---

## 4. Protobuf 消息定义

所有 API 通信使用 **Protocol Buffers** 序列化，Content-Type 为 `application/x-protobuf`。

### 4.1 account.proto

```protobuf
syntax = "proto3";
package account;

// --- 获取验证码 ---
// GET /account/v1/get_login_verification_code
message GetLoginVerificationCodeRequest {
    string phone_number = 1;  // 带国家码的手机号，如 "+8613800138000"
}

// --- 验证码登录 ---
// POST /account/v1/login
message LoginRequest {
    string phone_number = 1;
    string verification_code = 2;
    string password = 3;       // 未使用
    string channel = 4;        // 未使用
}

message LoginResponse {
    int64 user_id = 1;
    string authorization = 2;  // Bearer token，后续请求的 Authorization header
}
```

### 4.2 apiv2.proto

```protobuf
syntax = "proto3";
package apiv2;

// --- 广告任务配置 ---
// POST /v2/account/pc/ad/config
message PcAdConfigRequest {}  // 空请求体

message PcAdConfigItem {
    int64 id = 1;
    int64 award_unix = 2;     // 奖励时间戳
    int64 level = 3;           // 等级
    bool is_watch = 4;         // 是否已观看
    string title = 5;          // 广告标题
}

message PcAdConfigLevelItem {
    int64 level = 1;                    // 广告等级/分类
    repeated PcAdConfigItem list = 2;    // 该等级下的广告列表
    int64 watch_cnt = 3;                // 已观看数量
    string text = 4;                    // 说明文本
    string tag = 5;                     // 标签
    string title = 6;                   // 标题
}

message PcAdConfigResponse {
    repeated PcAdConfigLevelItem list = 1;
}

// --- 广告奖励补发（核心接口） ---
// POST /v2/account/pc/ad/callback/backup
message PcAdCallbackBackupRequest {
    string ad_id = 1;       // 广告 ID，固定 "103334281"
    int64 business = 2;      // 业务类型: 1, 2, 3
}

message PcAdCallbackBackupResponse {
    bool is_verify = 1;     // true=奖励发放成功
}

// --- 剩余时长查询 ---
// POST /v2/account/remain/duration
message GetUserRemainDurationRequest {}  // 空

message GetUserRemainDurationResponse {
    int64 vip_duration_second = 1;   // VIP 时长（秒）
    int64 free_duration_second = 2;  // 免费时长（秒）
    int64 timestamp = 3;
    int64 pause_state = 4;           // 暂停状态
    bool is_first_award = 5;         // 是否首次奖励
    int64 pc_vip_state = 7;          // PC VIP 状态
}
```

### 4.3 error.proto

```protobuf
syntax = "proto3";
package error;

message Error {
    int32 code = 1;
    string msg = 2;
}
```

当 HTTP 状态码 >= 400 时，响应体使用此格式。

---

## 5. 认证流程

### 5.1 整体流程

```
1. 生成 device_id（uuid4 hex 前25位）
2. 发送手机号 → GET /account/v1/get_login_verification_code
3. 收到短信验证码
4. 发送手机号+验证码 → POST /account/v1/login
5. 获取 authorization token
6. 后续请求在 Header 中携带: Authorization: <token>
```

### 5.2 关键细节

- **device_id**：首次创建账号时生成，与账号绑定持久化存储。每个账号有独立的 device_id。
- **手机号格式**：国内号码自动添加 `+86` 前缀，已有 `+` 则不添加。
- **Token 过期判断**：调用 `fetch_pc_duration` 接口，如果返回 `code == 16` 或 HTTP 401/403，则 token 已过期。
- **验证码冷却**：错误码 60 和 1000 表示处于验证码发送冷却期，实际验证码已发送。

### 5.3 Provider 定义

这是核心请求头的格式：

```python
headers = {
    "User-Agent": "okhttp/4.12.0",
    "Accept": "application/x-protobuf",
    "Content-Type": "application/x-protobuf",
    "x-eta": f"os=0&ver=3.11.10&dvc={device_id}&ch=default",
}
# 登录后追加:
headers["Authorization"] = auth_token
```

`x-eta` 头字段含义：
- `os=0`：操作系统类型（0=Android）
- `ver=3.11.10`：App 版本号
- `dvc=<device_id>`：设备唯一标识
- `ch=default`：渠道

---

## 6. 核心原理：广告回调绕过

### 6.1 正常流程 vs 绕过流程

```
正常流程:
  打开App → 看到广告任务 → 点击观看广告 → 等待广告播放完毕
  → App发送回调给服务端 → 服务端发放奖励 → 时长增加

绕过流程:
  获取广告任务列表 → 直接发送回调请求 → 服务端发放奖励 → 时长增加
  （跳过"观看广告"这一步）
```

### 6.2 回调接口详情

```
POST /v2/account/pc/ad/callback/backup

请求体:
  PcAdCallbackBackupRequest {
    ad_id: "103334281"    ← 写死的广告 ID
    business: 1 | 2 | 3  ← 三种广告业务类型，逐一领取
  }

响应体:
  PcAdCallbackBackupResponse {
    is_verify: true/false  ← true 表示奖励发放成功
  }
```

### 6.3 business 参数说明

外星仔有 3 种广告业务类型（`business=1, 2, 3`），领取时按顺序对每种 business 循环调用回调接口，直到该类型的广告任务全部完成。

---

## 7. 领取流程

### 7.1 单账号领取流程

```
claim_for_account(account, settings):
  │
  ├─ 1. init_client(account)
  │     ├─ 检查本地是否有 token 和 device_id
  │     ├─ 调用 check_token_valid() 验证 token
  │     │    └─ 请求 /v2/account/remain/duration 看是否返回认证错误
  │     └─ 返回 client 或 None（需登录）
  │
  ├─ 2. fetch_pc_ad_config()
  │     获取广告任务列表，检查是否有未完成任务
  │     全部已完成 → 直接返回 "already_done"
  │
  ├─ 3. fetch_pc_duration() → 记录领取前的 VIP 时长
  │
  ├─ 4. 对 business = 1, 2, 3 逐一执行:
  │     └─ _claim_business_phase(business):
  │          │
  │          ├─ loop:
  │          │   ├─ fetch_pc_ad_config() 看还有多少未完成
  │          │   ├─ 全部完成 → 退出循环
  │          │   ├─ pc_ad_callback_backup(business) → 发送回调
  │          │   ├─ sleep(interval)  ← 可配置的请求间隔
  │          │   ├─ fetch_pc_ad_config() 看进展
  │          │   └─ 连续 3 轮无进展 → 退出循环（防死循环）
  │          │
  │          └─ 返回 (claimed 成功次数, failed 失败次数)
  │
  └─ 5. fetch_pc_duration() → 记录领取后的 VIP 时长
       返回结果 (status, vip_before, vip_after, claimed, failed)
```

### 7.2 多账号并发领取

使用 `ThreadPoolExecutor`，线程池大小由 `settings.max_concurrent` 控制（默认 10）。

```
run_concurrent_claim(accounts, settings):
  对每个启用的账号:
    ThreadPoolExecutor.submit(claim_for_account, account, settings)
  等待全部完成 → 汇总结果
```

### 7.3 退出码（CLI 模式）

| 退出码 | 含义 |
|--------|------|
| 0 | 全部成功 |
| 1 | 部分成功（有的成功有的失败） |
| 2 | 全部失败 |
| 3 | 有账号 token 过期需要重新登录 |
| 4 | 没有启用的账号 |

---

## 8. 数据存储结构

### 8.1 账号文件 (`config/accounts.json`)

```json
[
  {
    "name": "备注名",
    "phone": "13800138000",
    "remark": "备注",
    "enabled": true,
    "auth_token": "登录后的 authorization token",
    "user_id": 123456,
    "device_id": "abc123def456...",
    "saved_at": 1719500000.0
  }
]
```

### 8.2 设置文件 (`config/settings.json`)

```json
{
  "max_concurrent": 10,
  "request_interval": 1.0,
  "max_rounds": 21,
  "schedule_time": "08:00"
}
```

| 字段 | 类型 | 范围 | 说明 |
|------|------|------|------|
| `max_concurrent` | int | 1-50 | 最大并发领取账号数 |
| `request_interval` | float | 0.1-30.0 | 每次回调请求后的等待秒数 |
| `max_rounds` | int | 1-200 | 每个 business 最大循环轮数 |
| `schedule_time` | str | `HH:MM` | 每天定时领取时间 |

### 8.3 注意事项

- 账号数据**明文存储**，`auth_token` 是敏感信息
- 写入使用**原子操作**（`mkstemp` + `os.replace`），防止写入一半时断电损坏数据
- 读写通过 `accounts_lock`（`threading.Lock`）实现线程安全

---

## 9. 客户端伪装

### 9.1 HTTP Headers

```python
session.headers = {
    "User-Agent": "okhttp/4.12.0",
    "Accept": "application/x-protobuf",
    "Content-Type": "application/x-protobuf",
    "x-eta": f"os=0&ver=3.11.10&dvc={device_id}&ch=default",
}
```

### 9.2 关键伪装点

- **User-Agent**：伪装成 OkHttp 4.12.0（Android 网络库）
- **Content-Type**：使用 `application/x-protobuf` 而非 JSON
- **x-eta**：携带 App 版本 3.11.10 和设备 ID
- **device_id**：从 uuid4 生成，与账号绑定，不随机变化

### 9.3 请求方式说明

- **GET 请求**：验证码接口虽然是 GET，但请求体通过 `data` 参数发送（非标准但服务端接受）
- **POST 请求**：标准 POST，请求体为 Protobuf 序列化后的二进制

---

## 10. 错误处理

### 10.1 重试策略

- 最大重试 2 次（共 3 次尝试）
- 重试触发条件：
  - HTTP 5xx 服务端错误
  - `requests.ConnectionError`
  - `requests.Timeout`
- 重试间隔：1 秒

### 10.2 认证错误判断

```python
def _is_auth_error(result):
    if not result.get("_error"):
        return False
    code = result.get("code")
    if code in (401, 403):  # HTTP 状态码
        return True
    if isinstance(code, int) and code == 16:  # Protobuf 错误码
        return True
    return False
```

### 10.3 防死循环机制

在 `_claim_business_phase` 中，如果连续 3 轮回调后未观看广告数没有减少，则停止循环。防止服务端状态异常时无限循环。

### 10.4 请求超时

所有请求默认超时 30 秒。

---

## 11. GUI 架构

### 11.1 整体结构

```
┌─────────────────────────────────┐
│ pywebview 窗口                  │
│ (无边框, WebView2 渲染)         │
│                                 │
│  ┌───────────────────────────┐  │
│  │ 前端页面 (HTML/CSS/JS)    │  │
│  │ gui/static/index.html     │  │
│  │ gui/static/style.css      │  │
│  │ gui/static/app.js         │  │
│  └───────────┬───────────────┘  │
│              │ HTTP (localhost)  │
│  ┌───────────┴───────────────┐  │
│  │ Flask 服务器               │  │
│  │ gui/api.py                │  │
│  │ (127.0.0.1:随机端口)       │  │
│  └───────────┬───────────────┘  │
│              │                   │
│  ┌───────────┴───────────────┐  │
│  │ 业务层 (core/)            │  │
│  └───────────────────────────┘  │
└─────────────────────────────────┘
```

### 11.2 窗口特性

- **无边框窗口**（`frameless=True`），自行实现标题栏拖拽和按钮
- **WebView2 渲染**：pywebview 调用系统 Edge WebView2 Runtime
- **窗口 API 暴露**：通过 `window.expose()` 将 Python 方法暴露给 JS
  - `minimize()`, `maximize()`, `restore()`, `close()`
  - `is_maximized()`, `get_position()`, `move_window(x, y)`
- **启动端口**：优先使用 52137，被占用则向上扫描到 52200
- **关闭保护**：关闭窗口时等待领取任务完成（最多等 30 秒），然后关闭 Flask 服务器

### 11.3 REST API 路由

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 前端页面 |
| GET | `/api/accounts` | 账号列表（不含 token 等敏感字段） |
| GET | `/api/accounts/<phone>` | 单个账号详情 |
| POST | `/api/accounts` | 添加账号 |
| PUT | `/api/accounts/<phone>` | 修改账号 |
| DELETE | `/api/accounts/<phone>` | 删除账号 |
| POST | `/api/login/<phone>` | 发送验证码 |
| POST | `/api/login/<phone>/verify` | 验证码登录 |
| GET | `/api/status` | 所有账号状态（时长、任务进度） |
| POST | `/api/claim` | 开始领取 |
| GET | `/api/claim/progress` | 领取进度（SSE 轮询） |
| GET | `/api/settings` | 获取设置 |
| PUT | `/api/settings` | 更新设置 |
| GET | `/api/schedule` | 查询定时任务 |
| POST | `/api/schedule` | 创建定时任务 |
| DELETE | `/api/schedule` | 删除定时任务 |

### 11.4 ClaimManager 进度追踪

- `ClaimManager` 是一个线程安全的状态管理器
- `start()` 设 running=true，重复调用返回 false 防止并发
- `add_progress_entry()` 为每个账号添加进度条目
- `update_progress_entry()` 实时更新单个账号的进度
- 前端通过轮询 `/api/claim/progress` 获取进度

---

## 12. CLI 架构

### 12.1 入口

CLI 入口在 `main.py`，GUI 入口在 `gui/app.py`。

打包后在 `gui/app.py` 中检测 `--cli` 参数：

```python
if "--cli" in sys.argv:
    # 分配控制台 → 导入 main.py 的 main() → 执行
    # --auto-close: 完成后自动关闭，不等待回车
```

### 12.2 CLI 执行流程

```
main.py main():
  1. 加载设置和账号
  2. 过滤已启用的账号
  3. 无启用账号 → sys.exit(4)
  4. 并发领取 run_concurrent_claim(enabled, settings)
  5. 汇总结果，打印每个账号的状态和时长变化
  6. 按结果返回对应退出码
```

### 12.3 定时任务

通过 Windows Task Scheduler 实现：

```bash
# 创建
schtasks /create /tn "EtAlienAuto_DailyClaim" /tr "<exe> --cli --auto-close" /sc daily /st 08:00 /f

# 查询
schtasks /query /tn "EtAlienAuto_DailyClaim"

# 删除
schtasks /delete /tn "EtAlienAuto_DailyClaim" /f
```

任务命令（打包后）：`"<exe_path>" --cli --auto-close`
任务命令（源码运行）：`"<python.exe>" "<main.py路径>"`

---

## 13. 打包部署

### 13.1 PyInstaller 打包

使用 `.spec` 文件控制打包：

- 入口：`gui/app.py`
- 输出：`dist/etalien-auto/`（目录形式，非单文件）
- 图标：`logo/logo.ico`
- 控制台：`console=False`（GUI 模式不显示控制台）
- UPX 压缩：启用
- 需要手动声明 `hiddenimports` 确保依赖被正确打包

### 13.2 运行时依赖

- **.NET Framework ≥ 4.6.2**（WebView2 依赖）
- **Edge WebView2 Runtime ≥ 86.x**（Win10/11 通常已自带）
- 启动时检测缺失依赖，弹窗提示安装

---

## 14. 重写注意事项

### 14.1 必须还原的部分

1. **签名算法**（`sign.py`）：服务端强制校验，签名错误直接拒绝
2. **Protobuf 消息格式**：字段编号和类型必须与 `.proto` 完全一致
3. **HTTP Headers 伪装**：`User-Agent`、`x-eta`、`Content-Type` 必须正确
4. **device_id 持久化**：同一账号不能每次都换 device_id，否则可能触发风控
5. **三种 business 逐一领取**：`business=1, 2, 3` 必须都走一遍

### 14.2 可以改进的地方

1. **状态存储**：当前明文 JSON 可以改用 SQLite，支持加密
2. **token 刷新**：当前 token 过期只能重新登录，可以实现 `/v2/account/refresh/token`
3. **断点续领**：当前领取中途关闭窗口会丢失进度
4. **日志系统**：可以增加更完善的日志和错误上报

### 14.3 风险点

- **API 可能变化**：外星仔服务端随时可能修改签名算法或接口
- **风控**：高频率调用回调接口可能触发风控
- **device_id 唯一性**：多账号用同一 device_id 可能被关联
- **token 时效**：token 有有效期，需定期重新登录

### 14.4 接口调用顺序总结

```
首次使用:
  生成 device_id → 获取验证码 → 登录 → 保存 token

每次领取:
  验证 token → 查任务 → 查时长(before) → 回调(business=1) → ...

  回调(business=1) 循环:
    查任务 → 发回调 → sleep(interval) → 查任务 → 判断进展 → 继续或停止

  ... → 回调(business=2) 循环 → 回调(business=3) 循环 → 查时长(after) → 完成
```
