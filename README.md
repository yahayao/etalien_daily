# 外星仔免广告领取加速时长

自动领取外星仔（ET/Alien）加速器 VIP 时长，无需观看广告。

基于 [etalien-auto](https://github.com/JiangXu26710/etalien-auto) 重写，SQLite 替代 JSON 存储，protoc 编译替代手写 protobuf，新增 CLI 子命令系统和单元测试。

## 功能

- **CLI 命令行** — 完整的账号管理、登录、领取、设置
- **GUI 桌面窗口** — 无边框窗口，暗色暖琥珀主题，实时进度，操作菜单
- **多账号并发** — ThreadPoolExecutor，可配置并发数和请求间隔
- **登录后自动获取昵称** — 调用 my_profile 接口，自动设为备注名
- **SQLite 存储** — 账号、设置、领取历史，WAL 模式并发读写
- **定时任务** — Windows Task Scheduler 集成
- **防死循环** — 连续 3 轮无进展自动停止

## 快速开始

```bash
# 安装依赖
uv sync

# CLI 模式
uv run etalien --help

# GUI 模式
uv run python -m gui.app
```

## CLI 使用

```bash
# 添加账号并登录
uv run etalien account add 13800138000 --name "主号"
uv run etalien account login 13800138000

# 管理账号
uv run etalien account list
uv run etalien account info 13800138000
uv run etalien account toggle 13800138000

# 领取
uv run etalien                           # 所有启用账号
uv run etalien --account 13800138000     # 指定账号
uv run etalien --auto-close              # 定时任务模式

# 设置
uv run etalien settings show
uv run etalien settings set max_concurrent 5
```

## 项目结构

```
etalien_daily/
├── src/etalien/
│   ├── sign.py              # SHA-256 签名算法
│   ├── client.py            # HTTP 客户端 + 重试
│   ├── service.py           # 业务逻辑 + 并发领取
│   ├── db.py                # SQLite 数据层
│   ├── main.py              # CLI 入口
│   └── proto_compiled/      # protoc 编译的 protobuf
├── gui/
│   ├── app.py               # pywebview 桌面窗口
│   ├── api.py               # Flask REST API
│   └── static/              # 前端 (HTML/CSS/JS)
├── tests/                   # 单元测试
└── docs/IMPLEMENTATION.md   # 协议逆向文档
```

## 参考

本项目重写自 [JiangXu26710/etalien-auto](https://github.com/JiangXu26710/etalien-auto)，主要变更：

- JSON 文件存储 → SQLite + 领取历史
- 手写 protobuf → protoc 编译
- 单文件 CLI → argparse 子命令系统
- 新增单元测试（52 个）
- GUI 布局重设计
- 修复 pywebview 6.x 兼容性
