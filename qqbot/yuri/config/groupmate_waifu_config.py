from nonebot import get_plugin_config
from pydantic import BaseModel

# 娶群友插件配置
groupmate_waifu_config = get_plugin_config()

# 设置字体为系统中已有的中文字体文件绝对路径
groupmate_waifu_config.groupmate_waifu_fontname = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"

# 可选：如果需要，可以在这里添加更多自定义配置
# groupmate_waifu_config.groupmate_waifu_he = 50  # 修改成功概率
# groupmate_waifu_config.groupmate_waifu_be = 30  # 修改失败概率