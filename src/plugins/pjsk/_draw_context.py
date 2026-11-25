from __future__ import annotations

from services.pjsk_draw import PjskDrawContext, has_context, set_context

from ._autoask import pjsk_update_manager
from ._config import SERVER_MAP
from ._utils import (
    async_load_master_data,
    load_master_data,
    master_data_by_id,
    master_data_first_by_id,
)


def install_draw_context(force: bool = False) -> None:
    if has_context() and not force:
        return

    from ._card_utils import cardtype, getcharaname, is_fes_card

    set_context(
        PjskDrawContext(
            get_asset=pjsk_update_manager.get_asset,
            update_assets=pjsk_update_manager.update_assets,
            load_master_data=load_master_data,
            async_load_master_data=async_load_master_data,
            master_data_by_id=master_data_by_id,
            master_data_first_by_id=master_data_first_by_id,
            server_map=SERVER_MAP,
            cardtype=cardtype,
            is_fes_card=is_fes_card,
            getcharaname=getcharaname,
        )
    )
