import os


PUBLIC_MODE = "public"
LAN_MODE = "lan"
VALID_DEPLOYMENT_MODES = {PUBLIC_MODE, LAN_MODE}
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def load_deployment_mode(value: str | None = None) -> str:
    """读取并校验服务器端部署模式。

    Args:
        value: 显式指定的部署模式；为空时读取环境变量。

    Returns:
        规范化后的 ``public`` 或 ``lan`` 模式名称。

    Raises:
        RuntimeError: 部署模式不在允许列表中。
    """
    mode = (value if value is not None else os.environ.get(
        "GPU_MONITOR_DEPLOYMENT_MODE",
        LAN_MODE,
    )).strip().lower()
    if mode not in VALID_DEPLOYMENT_MODES:
        allowed = ", ".join(sorted(VALID_DEPLOYMENT_MODES))
        raise RuntimeError(
            f"Invalid GPU_MONITOR_DEPLOYMENT_MODE={mode!r}; expected one of: {allowed}"
        )
    return mode


def load_boolean_setting(name: str, default: bool) -> bool:
    """读取支持多种字符串写法的布尔环境变量。

    Args:
        name: 环境变量名称。
        default: 环境变量未设置时使用的默认值。

    Returns:
        解析后的布尔值。

    Raises:
        RuntimeError: 环境变量内容无法识别为布尔值。
    """
    value = os.environ.get(name)
    if value is None:
        return bool(default)
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise RuntimeError(f"Invalid {name}={value!r}; expected a boolean value")
