import json
import os
from typing import Set, Dict, List
from pathlib import Path

BOT_SCOPE_CONFIG_FILE = Path(__file__).parent.parent / "data" / "bot_scope.json"

class BotScopeConfig:
    def __init__(self):
        self.enabled_groups: Set[int] = set()
        self.enabled_users: Set[int] = set()
        self.disabled_groups: Set[int] = set()
        self.disabled_users: Set[int] = set()
        self.mode: str = "whitelist"
        self.admin_users: Set[int] = set()
        
        self._load_config()
    
    def _load_config(self):
        if BOT_SCOPE_CONFIG_FILE.exists():
            try:
                with open(BOT_SCOPE_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.enabled_groups = set(data.get('enabled_groups', []))
                    self.enabled_users = set(data.get('enabled_users', []))
                    self.disabled_groups = set(data.get('disabled_groups', []))
                    self.disabled_users = set(data.get('disabled_users', []))
                    self.mode = data.get('mode', 'whitelist')
                    self.admin_users = set(data.get('admin_users', []))
            except Exception as e:
                print(f"加载bot范围配置失败: {e}")
                self._set_defaults()
        else:
            self._set_defaults()
            self._save_config()
    
    def _set_defaults(self):
        self.enabled_groups = {284205050, 908188794}
        self.enabled_users = {2193807541, 1185329732}
        self.disabled_groups = set()
        self.disabled_users = set()
        self.mode = "whitelist"
        self.admin_users = {2193807541, 1185329732}
    
    def _save_config(self):
        try:
            BOT_SCOPE_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                'enabled_groups': list(self.enabled_groups),
                'enabled_users': list(self.enabled_users),
                'disabled_groups': list(self.disabled_groups),
                'disabled_users': list(self.disabled_users),
                'mode': self.mode,
                'admin_users': list(self.admin_users)
            }
            with open(BOT_SCOPE_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"保存bot范围配置失败: {e}")
    
    def is_enabled_for(self, user_id: int, group_id: int = None) -> bool:
        if self.mode == "whitelist":
            if group_id:
                return (group_id in self.enabled_groups) or (user_id in self.enabled_users)
            else:
                return user_id in self.enabled_users
        else:
            if group_id:
                return (group_id not in self.disabled_groups) and (user_id not in self.disabled_users)
            else:
                return user_id not in self.disabled_users
    
    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_users
    
    def add_enabled_group(self, group_id: int):
        self.enabled_groups.add(group_id)
        self.disabled_groups.discard(group_id)
        self._save_config()
    
    def remove_enabled_group(self, group_id: int):
        self.enabled_groups.discard(group_id)
        self._save_config()
    
    def add_enabled_user(self, user_id: int):
        self.enabled_users.add(user_id)
        self.disabled_users.discard(user_id)
        self._save_config()
    
    def remove_enabled_user(self, user_id: int):
        self.enabled_users.discard(user_id)
        self._save_config()
    
    def add_disabled_group(self, group_id: int):
        self.disabled_groups.add(group_id)
        self.enabled_groups.discard(group_id)
        self._save_config()
    
    def remove_disabled_group(self, group_id: int):
        self.disabled_groups.discard(group_id)
        self._save_config()
    
    def add_disabled_user(self, user_id: int):
        self.disabled_users.add(user_id)
        self.enabled_users.discard(user_id)
        self._save_config()
    
    def remove_disabled_user(self, user_id: int):
        self.disabled_users.discard(user_id)
        self._save_config()
    
    def set_mode(self, mode: str):
        if mode in ['whitelist', 'blacklist']:
            self.mode = mode
            self._save_config()
    
    def add_admin(self, user_id: int):
        self.admin_users.add(user_id)
        self._save_config()
    
    def remove_admin(self, user_id: int):
        self.admin_users.discard(user_id)
        self._save_config()
    
    def get_status(self) -> Dict:
        return {
            'mode': self.mode,
            'enabled_groups': sorted(list(self.enabled_groups)),
            'enabled_users': sorted(list(self.enabled_users)),
            'disabled_groups': sorted(list(self.disabled_groups)),
            'disabled_users': sorted(list(self.disabled_users)),
            'admin_users': sorted(list(self.admin_users))
        }

bot_scope_config = BotScopeConfig()
