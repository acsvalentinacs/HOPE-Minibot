# -*- coding: utf-8 -*-
"""
P5: Watchdog Sanitizer

Process supervision with fail-closed restart logic.
"""

from minibot.watchdog.roles_registry import ROLES, get_role, RoleConfig
from minibot.watchdog.supervisor_v1 import WatchdogSupervisor

__all__ = ["ROLES", "get_role", "RoleConfig", "WatchdogSupervisor"]
