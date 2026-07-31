"""实体归一化工具。"""

from __future__ import annotations

from typing import Mapping

import pandas as pd


FOUR_REPORTING_TEAMS = (
    "McLaren",
    "Red Bull Racing",
    "Ferrari",
    "Mercedes",
)


def normalize_team_name(name: object, aliases: Mapping[str, str]) -> str:
    """把数据源车队名映射为稳定实体名。"""

    value = str(name).strip()
    return str(aliases.get(value, value))


def normalize_team_entities(
    frame: pd.DataFrame, aliases: Mapping[str, str]
) -> pd.DataFrame:
    """复制数据并规范化 ``team_name``，避免修改调用方数据。"""

    result = frame.copy()
    result["team_name"] = result["team_name"].map(
        lambda value: normalize_team_name(value, aliases)
    )
    return result

