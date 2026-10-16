"""绘图服务的数据接入点。

绘图服务只负责「数据进、图片出」，本身不下载资源、不读主数据，
这些能力由 pjsk 插件在加载时通过 :func:`set_context` 注入。
这样 services 层不必反向 import plugins 层，也方便日后把绘图搬到独立进程。
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from io import BytesIO
from typing import Any, Awaitable, Callable, Dict, Iterator, Optional
from urllib.parse import quote

import httpx
from PIL import Image, UnidentifiedImageError

ASSET_SOURCE_POLICY_DEFAULT = 'default'
ASSET_SOURCE_POLICY_LOCAL_ONLY = 'local_only'
_ASSET_SOURCE_POLICIES = {
    ASSET_SOURCE_POLICY_DEFAULT,
    ASSET_SOURCE_POLICY_LOCAL_ONLY,
}
_ASSET_SOURCE_POLICY: ContextVar[str] = ContextVar(
    'pjsk_draw_asset_source_policy',
    default=ASSET_SOURCE_POLICY_DEFAULT,
)
_LOCAL_ASSET_TIMEOUT = float(os.getenv('PJSK_DRAW_LOCAL_ASSET_TIMEOUT', '15'))


class LocalOnlyAssetError(RuntimeError):
    """严格本地资产请求失败，调用方应等待镜像就绪后重试。"""


class LocalOnlyAssetNotFoundError(LocalOnlyAssetError, FileNotFoundError):
    """local-updater 尚未提供指定资产。"""


def normalize_asset_source_policy(value: Any) -> str:
    policy = str(value or ASSET_SOURCE_POLICY_DEFAULT).strip().lower()
    if policy not in _ASSET_SOURCE_POLICIES:
        raise ValueError(f'不支持的 asset_source_policy: {policy}')
    return policy


def get_asset_source_policy() -> str:
    return _ASSET_SOURCE_POLICY.get()


def is_local_only_asset_policy() -> bool:
    return get_asset_source_policy() == ASSET_SOURCE_POLICY_LOCAL_ONLY


@contextmanager
def use_asset_source_policy(value: Any) -> Iterator[str]:
    policy = normalize_asset_source_policy(value)
    token = _ASSET_SOURCE_POLICY.set(policy)
    try:
        yield policy
    finally:
        _ASSET_SOURCE_POLICY.reset(token)


def _local_asset_base_url(pjsk_type: int) -> str:
    from .config import SERVER_CONFIG, SERVER_MAP

    server_name = SERVER_MAP.get(pjsk_type, 'jp')
    sources = SERVER_CONFIG.get(server_name, {}).get('rip', {}).get('sources', [])
    for source in sources:
        if str(source.get('name') or '') == 'local-updater':
            base_url = str(source.get('base_url') or '').strip().rstrip('/')
            if base_url:
                return base_url
    raise LocalOnlyAssetError(f'{server_name} 未配置名为 local-updater 的本地资产源')


async def _fetch_local_only_asset(
    path: str,
    raw: str,
    pjsk_type: int = 0,
) -> Image.Image:
    relative = f'{path.strip("/")}/{raw.lstrip("/")}'
    if any(part == '..' for part in relative.split('/')):
        raise LocalOnlyAssetError(f'非法资产路径: {relative}')
    url = f'{_local_asset_base_url(pjsk_type)}/{quote(relative, safe="/")}'
    try:
        async with httpx.AsyncClient(timeout=_LOCAL_ASSET_TIMEOUT, trust_env=False) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise LocalOnlyAssetError(f'local-updater 请求失败: {relative}: {exc}') from exc
    if response.status_code == 404:
        raise LocalOnlyAssetNotFoundError(f'local-updater 资产不存在: {relative}')
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise LocalOnlyAssetError(f'local-updater 返回 HTTP {response.status_code}: {relative}') from exc
    try:
        with Image.open(BytesIO(response.content)) as source:
            return source.convert('RGBA')
    except (UnidentifiedImageError, OSError) as exc:
        raise LocalOnlyAssetError(f'local-updater 返回的不是可解码图片: {relative}') from exc


@dataclass(frozen=True)
class PjskDrawContext:
    """绘图服务运行所需的外部能力集合。"""

    # 资源（图片）获取：对应 pjsk_update_manager.get_asset
    get_asset: Callable[..., Awaitable[Optional[Image.Image]]]
    # 资源批量下载：对应 pjsk_update_manager.update_assets
    update_assets: Callable[..., Awaitable[Any]]
    # 主数据读取
    load_master_data: Callable[..., Any]
    async_load_master_data: Callable[..., Awaitable[Any]]
    master_data_by_id: Callable[..., Dict[Any, Any]]
    # 保留源列表中首条同 ID 记录，供原线性查找语义敏感的热路径使用。
    master_data_first_by_id: Callable[..., Dict[Any, Any]]
    # pjsk_type -> 服务器代号（jp/tw/cn）
    server_map: Dict[int, str]
    # 卡面类型、fes 判定与角色名，卡牌绘制需要
    cardtype: Callable[..., Any]
    is_fes_card: Callable[..., Any]
    getcharaname: Callable[..., Any]

    def server_name(self, pjsk_type: int) -> str:
        return self.server_map.get(pjsk_type, 'jp')


_context: Optional[PjskDrawContext] = None


def set_context(context: PjskDrawContext) -> None:
    global _context
    default_get_asset = context.get_asset

    async def get_asset(
        path: str,
        raw: str,
        pjsk_type: int = 0,
        block: bool = False,
        download: bool = True,
    ) -> Optional[Image.Image]:
        if is_local_only_asset_policy():
            # 严格模式直接读取 local-updater 返回的字节，不看共享落盘文件，
            # 也不调用 helper/插件下载链，避免旧缓存掩盖本地镜像缺失。
            return await _fetch_local_only_asset(path, raw, pjsk_type=pjsk_type)
        return await default_get_asset(
            path,
            raw,
            pjsk_type=pjsk_type,
            block=block,
            download=download,
        )

    _context = replace(context, get_asset=get_asset)


def get_context() -> PjskDrawContext:
    if _context is None:
        raise RuntimeError('pjsk 绘图服务尚未注入数据上下文（services.pjsk_draw.set_context）')
    return _context


def has_context() -> bool:
    return _context is not None
