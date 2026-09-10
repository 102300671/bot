# timetable

NoneBot2 课表查询机器人：调用应急管理大学教务系统公开接口，按班级/学期查询课表，
渲染成图片发送到 QQ 群/私聊；指定周次、星期时返回过滤后的课表图片（渲染失败自动回退文本）。
机器人上线时自动注册 QQ「指令面板」，用户点击即可把 `/timetable` 填入输入框。

## 功能特性

- 按班级查询整学期课表，自动渲染为图片（星期一到星期日 × 1~10 节）
- 支持指定学期、周次、星期，过滤后同样渲染为图片：按周次只显示该周上课的课程，按星期只渲染对应单列，副标题自动标注过滤条件
- 标准命令风格：`/timetable -c 班级 [-s 学期] [-w 周次] [-d 星期]`，GNU 风格长/短选项
- 指令面板自动注册：机器人上线后自动在单聊/群聊场景创建面板（幂等，重启不会重复创建），点击面板指令自动填入输入框
- 自适应布局：同一时段多门课自动同格堆叠，跨节次课程与其它课自动分轨并排，列数按天动态生成，不同班级课表均无错位、无多余空列
- 连续周次自动合并（如 1-6 周 + 7-14 周 → 1-14 周）
- 图片渲染失败自动回退为纯文本，不影响使用

## 环境要求

- Python 3.10 ~ 3.12（本仓库在 3.12 下开发）
- NoneBot2 + QQ 官方机器人适配器（`nonebot-adapter-qq` ≥ 1.7.2）
- 首次渲染图片时自动下载 chromium headless shell（约 200MB，缓存于 `data/` 下）

## 安装与启动

```bash
# 1. 安装依赖（首次）
pip install -e .

# 2. 从模板创建配置文件，填入机器人 AppID / ClientSecret 等
cp .env.prod.example .env.prod
vim .env.prod

# 3. 启动机器人
nb run
```

## 配置

所有配置项见 [.env.prod.example](.env.prod.example)（带逐项注释），关键项：

```ini
# 命令前缀：仅 / 开头触发标准命令
COMMAND_START=["/"]

# NoneBot 驱动（QQ 适配器要求）
DRIVER="~fastapi+~httpx+~websockets"

# QQ 官方机器人配置（JSON 数组，use_websocket:true 走 WebSocket，无需公网回调地址）
QQ_BOTS='[{"id": "AppID", "token": "任意非空值", "secret": "ClientSecret",
           "intent": {"c2c_group_at_messages": true}, "use_websocket": true}]'
QQ_IS_SANDBOX=false

# 插件可选配置（不填使用默认值）
TIMETABLE_BASE_URL="https://jw.cidp.edu.cn"   # 教务系统地址
TIMETABLE_TIMEOUT=30.0                        # 查询接口超时时间（秒）
```

> 插件配置项按大写转换读取：代码中的 `timetable_base_url` 对应环境变量 `TIMETABLE_BASE_URL`。
> `.env.prod` 含密钥，已加入 `.gitignore`，请勿提交到仓库。
> 沙箱调试时设 `QQ_IS_SANDBOX=true`；Webhook 模式（`use_websocket:false`）需要公网 HTTPS 回调地址（`/qq/webhook`）。

## 用法

```
/timetable -c 班级 [-s 学期] [-w 周次] [-d 星期]
```

| 选项 | 缩写 | 说明 |
|---|---|---|
| `--class <班级>` | `-c` | 班级名（必填），如 `软件B241` |
| `--semester <学期>` | `-s` | 学期，省略则查当前学期，如 `2025-2026春季`（支持模糊匹配） |
| `--week <周次>` | `-w` | 周次，如 `3`（也支持 `第3周`、`3周`） |
| `--day <星期>` | `-d` | 星期，如 `星期一` / `周一` / `一` / `1`（1 = 周一） |
| `--help` | `-h` | 显示用法帮助 |

示例：

| 命令 | 说明 |
|---|---|
| `/timetable -c 软件B241` | 当前学期完整课表图片 |
| `/timetable -c 软件B241 -w 3` | 第 3 周课表图片（只显示该周有课的课程） |
| `/timetable -c 软件B241 -w 3 -d 一` | 第 3 周星期一课表图片（单列） |
| `/timetable -c 软件B241 -d 周一` | 当前学期星期一课表，周次可省略 |
| `/timetable -c 软件B241 -s 2025-2026春季 -w 18` | 指定学期的第 18 周课表图片 |

> 严格选项制：不接受位置参数，未知选项/缺少参数值会返回用法提示。选项值与选项之间用空格分隔（也支持 `--class=软件B241` 形式）。
> QQ 群聊中需 @机器人 后再发送命令。

## 指令面板

QQ 官方机器人「指令面板」接口（`/v2/panels`）未被 `nonebot-adapter-qq` 1.7.2 内置封装，
本项目在 [qq_panels.py](qq_panels.py) 中按适配器约定（`Request` + `Bot._request`，鉴权与错误处理复用）
封装了 6 个接口并挂载到 `Bot` 上：创建、列表、详情、修改、删除、增删关联对象。

机器人 WebSocket 连接成功后自动在 **单聊（c2c）/ 群聊（group）** 场景各注册一个全局面板，
内容为指令项 `/timetable`（点击填入输入框，不带参数）。注册是**幂等**的：

- 已存在且内容一致 → 跳过；
- 内容有变化（如修改了面板配置）→ 更新原面板；
- 不存在 → 创建。

每个机器人最多 20 个面板、每个面板最多 20 个元素；接口失败只记日志，不影响机器人启动。
如需调整面板内容，修改 `plugins/timetable.py` 中的 `_build_panel()`，下次启动自动更新。

## 项目结构

```
timetable/
├── plugins/
│   └── timetable.py    # 课表查询插件主体（命令、查询、HTML 渲染、文本兜底、面板自动注册）
├── qq_panels.py        # QQ 指令面板 OpenAPI 封装（Bot 方法扩展，放在根目录供插件导入）
├── data/               # htmlrender 浏览器缓存与临时产物（已忽略，不入库）
├── .env.prod           # 本地配置（含密钥，已忽略，不入库）
├── .env.prod.example   # 配置模板（入库，复制为 .env.prod 后填写）
├── .gitignore
├── pyproject.toml
└── README.md
```

## 常见问题

- **查询无结果**：检查班级名是否正确、该学期课表是否已发布
- **图片渲染失败**：会提示后自动改用文本回复；可检查网络与 `data/nonebot_plugin_htmlrender/` 下浏览器文件是否完整
- **命令不触发**：确认命令以 `/` 开头（`.env.prod` 中 `COMMAND_START=["/"]`）；群聊中需 @机器人
- **插件导入报错 `No module named 'qq_panels'`**：`qq_panels.py` 必须放在项目根目录而不是 `plugins/` 下——nonebot 按 `plugins.xxx` 命名空间加载插件，插件目录不在 `sys.path` 上
