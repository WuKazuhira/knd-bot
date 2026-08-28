from typing import Optional

from config.path_config import DATA_PATH

from .admin_manager import AdminManager
from .configs_manager import Config
from .data_class import StaticData
from .group_manager import GroupManager
from .mute_manager import Mute, MuteDataManager
from .none_plugin_count_manager import NonePluginCountManager
from .plugins2block_manager import Plugins2blockManager
from .plugins2cd_manager import Plugins2cdManager
from .plugins2count_manager import Plugins2countManager
from .plugins2settings_manager import Plugins2settingsManager
from .plugins_manager import PluginsManager
from .requests_manager import RequestManager
from .resources_manager import ResourcesManager
from .super_manager import SuperManager
from .withdraw_message_manager import WithdrawMessageManager

# 群功能开关 | 群被动技能 | 群权限  管理
group_manager: Optional[GroupManager] = GroupManager(
    DATA_PATH / "manager" / "group_manager.json"
)

# 撤回消息管理
withdraw_message_manager: Optional[WithdrawMessageManager] = WithdrawMessageManager()

# 插件管理
plugins_manager: Optional[PluginsManager] = PluginsManager(
    DATA_PATH / "manager" / "plugins_manager.json"
)

# 插件基本设置管理
plugins2settings_manager: Optional[Plugins2settingsManager] = Plugins2settingsManager(
    DATA_PATH / "config" / "plugins2settings.yaml"
)

# 插件命令 cd 管理
plugins2cd_manager: Optional[Plugins2cdManager] = Plugins2cdManager(
    DATA_PATH / "config" / "plugins2cd.yaml"
)

# 插件命令 阻塞 管理
plugins2block_manager: Optional[Plugins2blockManager] = Plugins2blockManager(
    DATA_PATH / "config" / "plugins2block.yaml"
)

# 插件命令 每次次数限制 管理
plugins2count_manager: Optional[Plugins2countManager] = Plugins2countManager(
    DATA_PATH / "config" / "plugins2count.yaml"
)

# 资源管理
resources_manager: Optional[ResourcesManager] = ResourcesManager(
    DATA_PATH / "manager" / "resources_manager.json"
)

# 插件加载容忍管理
none_plugin_count_manager: Optional[NonePluginCountManager] = NonePluginCountManager(
    DATA_PATH / "manager" / "none_plugin_count_manager.json"
)

# 好友请求/群聊邀请 管理
requests_manager: Optional[RequestManager] = RequestManager(
    DATA_PATH / "manager" / "requests_manager.json"
)

# 管理员命令管理器
admin_manager = AdminManager()
super_manager = SuperManager()

# 刷屏禁言管理器
mute_manager = Mute()
mute_data_manager = MuteDataManager(DATA_PATH / "group_mute_data.json")


