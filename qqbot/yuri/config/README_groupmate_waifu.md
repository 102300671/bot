# 娶群友插件配置说明

## 问题说明

你在运行机器人时遇到了 `ValueError: Font:simsun not found` 或 `ValueError: Font:WenQuanYi Micro Hei not found` 错误，这是因为系统中没有安装指定的中文字体或字体查找机制无法找到字体。

## 解决方案

我已经做了以下修改：

1. 修改了插件源码中的字体设置，将默认的 'simsun' 字体替换为系统中存在的字体文件绝对路径 `/usr/share/fonts/truetype/wqy/wqy-microhei.ttc`
2. 创建了用户级别的配置文件 `groupmate_waifu_config.py`，使用字体文件绝对路径确保你可以自定义插件设置且配置在插件更新后也能生效

## 如何使用配置文件

1. 确保 `groupmate_waifu_config.py` 文件位于 `config` 目录下
2. 在 `pyproject.toml` 文件中，确保 `config` 目录被包含在 Python 路径中
3. 重启机器人，配置将会自动生效

## 自定义其他设置

你可以在 `groupmate_waifu_config.py` 文件中取消注释并修改其他配置项，例如：

- 成功概率 `groupmate_waifu_he`
- 失败概率 `groupmate_waifu_be`
- NTR概率 `groupmate_waifu_ntr`
- 背景图片路径 `groupmate_waifu_bg_image`

## 注意事项

- 直接修改插件源码可能会在插件更新时被覆盖，所以推荐使用用户级别的配置文件
- 如果你想要使用其他字体，确保该字体已经安装在系统中，可以使用 `fc-list :lang=zh` 命令查看系统中可用的中文字体