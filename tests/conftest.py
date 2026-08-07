import sys
import types
from unittest.mock import MagicMock

def _ensure_stub():
    try:
        import gateway.platforms.base  # noqa: F401
        return
    except Exception:
        pass

    _gateway = types.ModuleType("gateway")
    _gateway_platforms = types.ModuleType("gateway.platforms")

    class _SendResult:
        def __init__(self, success=False, message_id="", error=""):
            self.success = success
            self.message_id = message_id
            self.error = error

    class _Platform:
        def __init__(self, name):
            self.name = name
        def __eq__(self, other):
            return isinstance(other, _Platform) and other.name == self.name
        def __hash__(self):
            return hash(self.name)
        def __repr__(self):
            return f"Platform({self.name!r})"

    class _MessageType:
        TEXT = "text"

    class _MessageEvent:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class _PlatformConfig:
        def __init__(self, enabled=False, token="", extra=None):
            self.enabled = enabled
            self.token = token
            self.extra = extra or {}

    class _Config:
        def __init__(self, platforms=None):
            self.platforms = platforms or {}

    class _GatewayConfig(_Config):
        pass

    _gateway_config = types.ModuleType("gateway.config")
    _gateway_config.Platform = _Platform
    _gateway_config.PlatformConfig = _PlatformConfig
    _gateway_config.GatewayConfig = _GatewayConfig

    _base = types.ModuleType("gateway.platforms.base")

    class BasePlatformAdapter:
        def __init__(self, config, platform):
            self.config = config
            self.platform = platform
            self._connected = False

        def _mark_connected(self):
            self._connected = True

        def _mark_disconnected(self):
            self._connected = False

        async def dispatch(self, event):
            raise NotImplementedError

    _base.BasePlatformAdapter = BasePlatformAdapter
    _base.SendResult = _SendResult
    _base.MessageEvent = _MessageEvent
    _base.MessageType = _MessageType

    _gateway_platforms.base = _base
    _gateway.platforms = _gateway_platforms
    _gateway.config = _gateway_config

    sys.modules.setdefault("gateway", _gateway)
    sys.modules.setdefault("gateway.platforms", _gateway_platforms)
    sys.modules.setdefault("gateway.config", _gateway_config)
    sys.modules.setdefault("gateway.platforms.base", _base)


_ensure_stub()
