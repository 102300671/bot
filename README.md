# Bot 项目集合

这是一个包含多个机器人项目的集合仓库，目前包含以下项目：

## 项目结构

```
├── minecraft/          # Minecraft 相关机器人
├── qqbot/             # QQ 机器人
├── .git/              # Git 版本控制
└── .gitignore         # Git 忽略文件配置
```

## 项目介绍

### Minecraft 机器人

Minecraft 机器人项目，包含以下内容：
- `plugins/` - Minecraft 插件目录
- `pyproject.toml` - Python 项目配置文件
- `.env.prod` - 生产环境配置文件
- `README.md` - Minecraft 项目说明文档
- `.venv/` - Python 虚拟环境

### QQ 机器人

QQ 机器人项目，包含以下子项目：
- `codetf/` - 代码相关功能
- `yuri/` - Yuri 相关功能

## 开发环境

### 依赖管理

Minecraft 项目使用 Python 虚拟环境进行依赖管理：
```bash
# 激活虚拟环境
cd minecraft
source .venv/bin/activate

# 安装依赖
pip install -e .
```

## 使用方法

### Minecraft 机器人

1. 进入 minecraft 目录
2. 激活虚拟环境
3. 配置 `.env.prod` 文件
4. 运行相关脚本

### QQ 机器人

进入 qqbot 目录，根据具体子项目的说明文档进行配置和运行。

## 贡献

1. Fork 本仓库
2. 创建 feature 分支
3. 提交代码
4. 发起 Pull Request

## 许可证

本项目采用 MIT 许可证。
