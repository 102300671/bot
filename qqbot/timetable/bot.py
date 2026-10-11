"""timetable 机器人启动入口。

在启动 NoneBot 之前自动检测运行环境，为 nonebot-plugin-htmlrender
选择合适的 chromium 渲染后端：

- Termux PRoot 等受限容器环境：GPU 初始化会崩溃，强制使用 swiftshader 软件渲染
- 无 GPU 的设备：使用 swiftshader 软件渲染保证兼容性
- 有 GPU 的设备：使用默认硬件加速

检测结果通过环境变量 RENDER__PROVIDER_CONFIG__LAUNCH_ARGS 传给 htmlrender；
若 .env.prod 中已显式配置该变量，则以配置为准（不覆盖）。
"""

import os
import sys


def _is_proot() -> bool:
    """检测是否运行在 PRoot 类容器（Termux PRoot 等）中。"""
    # 1) /proc/self/status 中 TracerPid 非 0（proot 通过 ptrace 实现）
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("TracerPid:"):
                    if int(line.split(":", 1)[1].strip()) != 0:
                        return True
                    break
    except (OSError, ValueError):
        pass

    # 2) 内核版本字符串含 PRoot
    try:
        with open("/proc/sys/kernel/osrelease", "r") as f:
            if "PRoot" in f.read():
                return True
    except OSError:
        pass

    # 3) Termux 数据目录存在
    if os.path.exists("/data/data/com.termux"):
        return True

    return False


def _has_gpu() -> bool:
    """检测是否存在可用的 GPU 渲染设备。"""
    dri = "/dev/dri"
    if not os.path.isdir(dri):
        return False
    try:
        for dev in os.listdir(dri):
            if dev.startswith("renderD") or dev.startswith("card"):
                return True
    except OSError:
        pass
    return False


def detect_render_launch_args() -> str:
    """根据设备环境返回 chromium 启动参数（空串表示使用默认硬件加速）。"""
    if _is_proot():
        # PRoot 环境下 GPU 初始化会导致 TargetClosedError，必须软件渲染
        return "--use-gl=swiftshader"
    if not _has_gpu():
        # 无 GPU 的设备（如纯服务器），使用软件渲染保证兼容性
        return "--use-gl=swiftshader"
    return ""


def setup_render_env() -> None:
    """根据检测结果设置渲染环境变量（不覆盖已有的显式配置）。"""
    existing = os.environ.get("RENDER__PROVIDER_CONFIG__LAUNCH_ARGS")
    if existing is not None and existing.strip():
        print(f"[render] 使用 .env.prod 中配置的启动参数: {existing}")
        return

    args = detect_render_launch_args()
    if args:
        os.environ["RENDER__PROVIDER_CONFIG__LAUNCH_ARGS"] = args
        env = "PRoot 容器" if _is_proot() else "无 GPU 设备"
        print(f"[render] 检测到{env}，自动启用软件渲染: {args}")
    else:
        print("[render] 检测到 GPU，使用硬件加速渲染")


def main() -> None:
    setup_render_env()
    import nonebot

    nonebot.run()


if __name__ == "__main__":
    main()
