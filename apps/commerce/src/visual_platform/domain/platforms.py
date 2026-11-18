"""Platforms specified by the immutable V4 task-create contract."""

from typing import Literal, get_args

Platform = Literal["amazon", "temu", "ozon"]
PLATFORMS = get_args(Platform)
DEFAULT_MARKETPLACES = {"amazon": "US", "temu": "US", "ozon": "RU"}
OFFICIAL_RULE_HOSTS = {
    "amazon": ("amazon.com",),
    "temu": ("temu.com", "kuajingmaihuo.com"),
    "ozon": ("ozon.ru", "global-help.ozon.com"),
}
