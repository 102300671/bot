# timetable

NoneBot2 课表查询插件：调用应急管理大学教务系统公开接口，按班级/学期查询课表，
渲染成图片发送到 QQ 群/私聊；指定周次、星期时返回过滤后的文本课表。

## 功能特性

- 按班级查询整学期课表，自动渲染为图片（星期一到星期日 × 1~10 节）
- 支持指定学期、周次、星期，返回过滤后的文本课表
- 重叠课程自动分轨横向并排（如课程设计跨 1-8 节时不再错位）
- 连续周次自动合并（如 1-6 周 + 7-14 周 → 1-14 周）
- 图片渲染失败自动回退为纯文本，不影响使用

## 环境要求

- Python 3.10 ~ 3.12（本仓库在 3.12 下开发）
- NoneBot2 + QQ 官方机器人适配器（`nonebot-adapter-qq`）
- 首次渲染图片时自动下载 chromium headless shell（约 200MB，缓存于 `data/` 下）

## 安装与启动

```bash
# 1. 安装依赖（首次）
pip install -e .

# 2. 启动机器人
nb run
```

## 配置

在 `.env.prod`（或 `.env`）中配置：

```ini
# 命令前缀：设为空数组后，QQ 私聊/群消息不带 / 前缀即可触发命令
COMMAND_START=[""]

# NoneBot 基础配置
DRIVER="~fastapi+~httpx+~websockets"
HOST=127.0.0.1
PORT=8080

# QQ 官方机器人配置
QQ_BOTS='[{"id": "应用ID", "token": "机器人令牌", "secret": "密钥"}]'
QQ_IS_SANDBOX=false

# htmlrender 渲染配置（可选，使用默认值即可）
RENDER__PROVIDER="playwright"
RENDER__STARTUP="auto"

# 插件可选配置（不填使用默认值）
TIMETABLE_BASE_URL="https://jw.cidp.edu.cn"   # 教务系统地址
TIMETABLE_TIMEOUT=30.0                        # 查询接口超时时间（秒）
```

> 插件配置项按大写转换读取：代码中的 `timetable_base_url` 对应环境变量 `TIMETABLE_BASE_URL`。
> `.env.prod` 含密钥，已加入 `.gitignore`，请勿提交到仓库。

## 用法

```
查课表 [学期] 班级 [周次] [星期]
```

| 命令 | 说明 |
|---|---|
| `查课表 软件B241` | 当前学期完整课表图片 |
| `查课表 软件B241 3` | 第 3 周课表（文本） |
| `查课表 软件B241 3 星期一` | 第 3 周星期一课表（文本） |
| `查课表 软件B241 周一` | 当前周星期一课表，周次可省略 |
| `查课表 2025-2026春季 软件B241 18` | 指定学期的第 18 周课表 |

- 学期可省略，默认取当前学期；关键词支持如 `2025-2026春季`，可模糊匹配
- 星期支持：`星期一` / `周一` / `礼拜一` / `1`~`7`（1 = 周一）
- 周次支持：`3` / `3周` / `第3周`

## 项目结构

```
timetable/
├── plugins/
│   └── timetable.py    # 课表查询插件主体（查询、HTML 渲染、文本兜底）
├── data/               # htmlrender 浏览器缓存与临时产物（已忽略，不入库）
├── .env.prod           # 本地配置（含密钥，已忽略，不入库）
├── .gitignore
├── pyproject.toml
└── README.md
```

## 常见问题

- **查询无结果**：检查班级名是否正确、该学期课表是否已发布
- **图片渲染失败**：会提示后自动改用文本回复；可检查网络与 `data/nonebot_plugin_htmlrender/` 下浏览器文件是否完整
- **命令带 / 前缀才生效**：检查 `.env.prod` 中 `COMMAND_START=[""]` 是否配置正确
