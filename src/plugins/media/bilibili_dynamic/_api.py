import re
from dataclasses import dataclass
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

import httpx

_ENDPOINT = 'https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space'
_KINDS = {
    'DYNAMIC_TYPE_WORD',
    'DYNAMIC_TYPE_DRAW',
    'DYNAMIC_TYPE_AV',
    'DYNAMIC_TYPE_ARTICLE',
    'DYNAMIC_TYPE_FORWARD',
}
_SKIP_KINDS = {
    'DYNAMIC_TYPE_LIVE_RCMD',
    'DYNAMIC_TYPE_LIVE',
    'DYNAMIC_TYPE_AD',
    'DYNAMIC_TYPE_BANNER',
    'DYNAMIC_TYPE_NONE',
}
_MAX_TEXT = 3000
_MAX_IMAGES = 9


@dataclass
class Dynamic:
    id: int
    uid: str
    name: str
    published_at: int
    kind: str
    text: str
    images: tuple[str, ...] = ()

    @property
    def url(self) -> str:
        return f'https://t.bilibili.com/{self.id}'


class DynamicBatch(list):
    def __init__(self, items, high_watermark=0):
        super().__init__(items)
        self.high_watermark = high_watermark


class BilibiliError(RuntimeError):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def parse_uid(text: str) -> str | None:
    if not isinstance(text, str) or not re.fullmatch(r'[0-9]{1,20}', text):
        return None
    value = text.lstrip('0')
    return value or None


def _invalid() -> BilibiliError:
    return BilibiliError(-1, 'B站返回的数据格式异常，请稍后重试。')


def _object(value) -> dict:
    if not isinstance(value, dict):
        raise _invalid()
    return value


def _text(value) -> str:
    if not isinstance(value, str):
        raise _invalid()
    return value[:_MAX_TEXT]


def _image(value) -> str | None:
    if not isinstance(value, str):
        raise _invalid()
    try:
        parts = urlsplit(value)
        host = parts.hostname or ''
        if (
            parts.scheme in ('http', 'https')
            and not parts.username
            and not parts.password
            and (host == 'hdslb.com' or host.endswith('.hdslb.com'))
            and parts.port in (None, 443)
        ):
            # 旧封面可能是 http，统一转 https
            return parts._replace(scheme='https').geturl()
    except ValueError:
        pass
    return None


def _content(item: dict, depth: int = 0) -> tuple[str, tuple[str, ...]]:
    modules = _object(item.get('modules'))
    dynamic = _object(modules.get('module_dynamic'))
    fragments = []
    images = []
    desc = dynamic.get('desc')
    if desc is not None:
        fragments.append(_text(_object(desc).get('text')))
    elif item['type'] == 'DYNAMIC_TYPE_WORD':
        raise _invalid()
    major = dynamic.get('major')
    if major is None and item['type'] in {
        'DYNAMIC_TYPE_DRAW',
        'DYNAMIC_TYPE_AV',
        'DYNAMIC_TYPE_ARTICLE',
    }:
        raise _invalid()
    if major is not None:
        major = _object(major)
        kind = item['type']
        field = {'DYNAMIC_TYPE_AV': 'archive', 'DYNAMIC_TYPE_ARTICLE': 'article', 'DYNAMIC_TYPE_DRAW': 'draw'}.get(kind)
        if major.get('opus') is not None:
            # 新版图文/专栏走 opus 字段
            body = _object(major['opus'])
            if body.get('title') is not None:
                fragments.append(_text(body['title']))
            if body.get('summary') is not None:
                fragments.append(_text(_object(body['summary']).get('text')))
            pictures = body.get('pics', [])
            if not isinstance(pictures, list):
                raise _invalid()
            for picture in pictures[:_MAX_IMAGES]:
                url = _image(_object(picture).get('url'))
                if url:
                    images.append(url)
        elif field:
            body = _object(major.get(field))
            if field == 'draw':
                pictures = body.get('items')
                if not isinstance(pictures, list):
                    raise _invalid()
                for picture in pictures[:_MAX_IMAGES]:
                    url = _image(_object(picture).get('src'))
                    if url:
                        images.append(url)
            else:
                fragments.append(_text(body.get('title')))
                summary = body.get('desc')
                if summary is not None:
                    fragments.append(_text(summary))
                covers = body.get('covers', []) if field == 'article' else [body.get('cover')]
                if not isinstance(covers, list):
                    raise _invalid()
                for cover in covers[:_MAX_IMAGES]:
                    if cover is not None:
                        url = _image(cover)
                        if url:
                            images.append(url)
    if item['type'] == 'DYNAMIC_TYPE_FORWARD' and depth < 2:
        original = item.get('orig')
        if original is not None:
            original = _object(original)
            original_kind = original.get('type')
            if not isinstance(original_kind, str):
                raise _invalid()
            if original_kind in _KINDS:
                orig_text, orig_images = _content(original, depth + 1)
                fragments.append('转发：' + orig_text)
                images.extend(orig_images)
    return '\n'.join(filter(None, fragments))[:_MAX_TEXT], tuple(dict.fromkeys(images))[:_MAX_IMAGES]


def _parse(item: dict, uid: str) -> Dynamic | None:
    kind = item.get('type')
    if not isinstance(kind, str):
        raise _invalid()
    if kind in _SKIP_KINDS:
        return None
    author = _object(_object(item.get('modules')).get('module_author'))
    mid = author.get('mid')
    if isinstance(mid, bool) or not isinstance(mid, (int, str)):
        raise _invalid()
    author_uid = parse_uid(str(mid))
    timestamp = author.get('pub_ts')
    # pub_ts 偶尔会返回数字字符串
    if isinstance(timestamp, str) and re.fullmatch(r'[0-9]{1,12}', timestamp):
        timestamp = int(timestamp)
    if author_uid != uid or type(timestamp) is not int or not 0 <= timestamp <= 253402300799:
        raise _invalid()
    name = _text(author.get('name'))
    if kind in _KINDS:
        text, images = _content(item)
    else:
        # 未知类型只发摘要和链接
        content = _object(item['modules'].get('module_dynamic') or {})
        desc = content.get('desc')
        text = _text(_object(desc).get('text')) if desc is not None else '该动态类型暂不支持预览，请打开原文查看。'
        images = ()
    return Dynamic(int(item['id_str']), uid, name, timestamp, kind, text, images)


def _pinned(item: dict) -> bool:
    tag = _object(item.get('modules')).get('module_tag')
    if tag is None:
        return False
    return _object(tag).get('text') == '置顶'


class DynamicClient:
    def __init__(self, cookie: str = '', proxy: str = '', timeout: int = 20):
        self._cookie = cookie
        self._proxy = proxy
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            try:
                cookies = SimpleCookie()
                cookies.load(self._cookie)
                jar = httpx.Cookies()
                for key, value in cookies.items():
                    jar.set(key, value.value, domain='.bilibili.com', path='/')
                self._client = httpx.AsyncClient(
                    cookies=jar,
                    proxy=self._proxy or None,
                    timeout=self._timeout,
                    trust_env=False,
                    headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://space.bilibili.com/'},
                )
            except Exception:
                raise BilibiliError(-1, 'B站请求配置无效，请检查网络配置。') from None
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch(self, uid: str, after_id: int | None = None) -> list[Dynamic]:
        uid = parse_uid(uid)
        if uid is None:
            raise BilibiliError(-1, '请输入有效的B站UID。')
        if after_id is not None and (type(after_id) is not int or after_id < 0):
            raise BilibiliError(-1, '动态游标无效。')
        found: dict[int, Dynamic] = {}
        high_watermark = after_id or 0
        offset = ''
        seen_offsets = set()
        for _ in range(5):
            try:
                response = await self._get_client().get(
                    _ENDPOINT,
                    params={'host_mid': uid, 'offset': offset},
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                status = error.response.status_code
                message = 'B站请求过于频繁，请稍后重试。' if status == 429 else 'B站请求失败，请稍后重试。'
                raise BilibiliError(status, message) from None
            except httpx.HTTPError:
                raise BilibiliError(-1, '连接B站失败，请检查网络后重试。') from None
            try:
                payload = _object(response.json())
            except ValueError, UnicodeError:
                raise _invalid() from None
            code = payload.get('code')
            if type(code) is not int:
                raise _invalid()
            if code != 0:
                messages = {
                    -101: 'B站需要登录，请联系部署者配置有效Cookie后重启。',
                    -404: '没有找到该B站用户，请检查UID。',
                    -400: 'B站UID或请求参数无效，请检查UID。',
                }
                message = messages.get(code, 'B站接口请求失败，请稍后重试。')
                if code in (-412, -352, 429):
                    message = 'B站暂时限制访问，请稍后重试；持续失败请联系部署者检查Cookie。'
                raise BilibiliError(code, message)
            data = _object(payload.get('data'))
            items = data.get('items')
            has_more = data.get('has_more')
            if not isinstance(items, list) or type(has_more) not in (int, bool) or has_more not in (0, 1):
                raise _invalid()
            covered = False
            for raw in items:
                item = _object(raw)
                identifier = item.get('id_str')
                if (
                    not isinstance(identifier, str)
                    or not re.fullmatch(r'[0-9]{1,30}', identifier)
                    or int(identifier) == 0
                ):
                    raise _invalid()
                high_watermark = max(high_watermark, int(identifier))
                dynamic = _parse(item, uid)
                if after_id and int(identifier) <= after_id and not _pinned(item):
                    covered = True
                if dynamic is not None and (after_id is None or dynamic.id > after_id):
                    found[dynamic.id] = dynamic
            if after_id in (None, 0) or covered or not has_more:
                return DynamicBatch(sorted(found.values(), key=lambda dynamic: dynamic.id), high_watermark)
            next_offset = data.get('offset')
            if not isinstance(next_offset, str) or not next_offset or next_offset in seen_offsets:
                raise _invalid()
            seen_offsets.add(next_offset)
            offset = next_offset
        raise BilibiliError(-1, 'B站动态积压超过查询上限，请稍后重试或重新订阅。')
