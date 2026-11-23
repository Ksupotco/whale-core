"""channels/：Channel 抽象与 IO 渠道（onebot/QQ …）。

外部消息经渠道进入同一个 ``AgentRuntime``；出站发送是带风险等级的 Capability；
投递回执三态如实上报。见 ``base``（抽象与宿主 sink）、``onebot``（QQ）、
``manager``（config 驱动的生命周期 + ``channels.list`` RPC）。
"""

from .base import Channel, CoreSink, PluginSink, Sink
from .manager import Channels
from .onebot import NameDirectory, OneBotChannel, clean_name, validate_endpoints

__all__ = ["Channel", "Channels", "CoreSink", "PluginSink", "Sink",
           "NameDirectory", "OneBotChannel", "clean_name", "validate_endpoints"]
