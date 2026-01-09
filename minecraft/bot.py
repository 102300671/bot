import nonebot
from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11Adapter

from nonebot.adapters.telegram import Adapter as TELEGRAMAdapter

from nonebot.adapters.minecraft import Adapter as MINECRAFTAdapter

from nonebot.adapters.discord import Adapter as DISCORDAdapter



nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(ONEBOT_V11Adapter)

driver.register_adapter(TELEGRAMAdapter)

driver.register_adapter(MINECRAFTAdapter)

driver.register_adapter(DISCORDAdapter)

nonebot.load_builtin_plugins('echo', 'single_session')


nonebot.load_from_toml("pyproject.toml")

if __name__ == "__main__":
    nonebot.run()