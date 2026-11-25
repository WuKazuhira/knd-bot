import uuid
from datetime import datetime
from io import BytesIO
from typing import Any, List, Optional, Tuple, Union

import imagehash
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from PIL import Image

from config.path_config import DATA_PATH
from services.db_context import db
from utils.http_utils import AsyncHttpx
from utils.imageutils import get_img_hash
from utils.word_entries import (
    QUESTION_PLACEHOLDER,
    regex_matches,
    render_placeholders,
    validate_word_pattern,
)

from ._config import int2type

path = DATA_PATH / "withdraw_base"
path.mkdir(parents=True, exist_ok=True)


class WithdrawBase(db.Model):
    __tablename__ = "withdraw_base"

    id = db.Column(db.Integer(), primary_key=True)
    user_qq = db.Column(db.BigInteger(), nullable=False)
    group_id = db.Column(db.Integer())
    word_type = db.Column(
        db.Integer(), nullable=False, default=0
    )  # 词条类型 0: 完全匹配 1: 模糊 2: 正则
    entry = db.Column(db.String(), nullable=False)  # 问句
    entry_placeholder = db.Column(db.String())  # 问句的占位符
    create_time = db.Column(db.DateTime(), nullable=False)
    update_time = db.Column(db.DateTime(), nullable=False)

    @classmethod
    async def exist_any_entry(cls, group_id: int) -> bool:
        """
        说明:
            检测群内是否有设置过词条
        参数:
            :param group_id: 群号
        """
        query = cls.query.where(cls.group_id == group_id)
        return bool(await query.gino.first())

    @classmethod
    async def exists(
        cls,
        user_id: Optional[int],
        group_id: Optional[int],
        entry: str,
        word_type: Optional[int] = None,
    ) -> bool:
        """
        说明:
            检测撤回词条是否存在
            用于添加词条时检测词条是否重复
        参数:
            :param user_id: 用户id
            :param group_id: 群号
            :param entry: 问题
            :param word_type: 词条类型
        """
        query = cls.query.where(cls.entry == entry)
        if user_id:
            query = query.where(cls.user_qq == user_id)
        if group_id:
            query = query.where(cls.group_id == group_id)
        if word_type:
            query = query.where(cls.word_type == word_type)
        return bool(await query.gino.first())

    @classmethod
    async def add_entry_answer(
        cls,
        user_id: int,
        group_id: Optional[int],
        word_type: int,
        entry: Union[str, Message],
    ):
        """
        说明:
            添加或新增一个撤回词条
        参数:
            :param user_id: 用户id
            :param group_id: 群号
            :param word_type: 词条类型,
            :param entry: 词条
        """
        entry, _entry_list = await cls._entry2format(entry, user_id, group_id)
        entry = entry.strip()
        if word_type == 2:
            await validate_word_pattern(entry)
        if not await cls.exists(user_id, group_id, entry, word_type):
            await cls.create(
                user_qq=user_id,
                group_id=group_id,
                word_type=word_type,
                entry=entry,
                entry_placeholder=",".join(_entry_list),
                create_time=datetime.now().replace(microsecond=0),
                update_time=datetime.now().replace(microsecond=0),
            )

    @classmethod
    async def _entry2format(
            cls, entry: Union[str, Message], user_id: int, group_id: int
    ) -> Tuple[str, List[Any]]:
        """
        说明:
            将CQ码转化为特定格式(face、at、image)
            用于添加词条时格式化问句
        参数:
            :param entry: 词条内容
            :param user_id: 用户id
            :param group_id: 群号
        """
        _list = []
        text = ""
        entry = entry if isinstance(entry, Message) else Message(entry)
        for seg in entry:
            if isinstance(seg, str):
                text += seg
            elif seg.type == "text":
                text += seg.data["text"]
            elif seg.type == "face":
                text += f"[face:{seg.data['id']}]"
                _list.append(seg.data['id'])
            elif seg.type == "at":
                text += f"[at:{seg.data['qq']}]"
                _list.append(seg.data['qq'])
            elif url := seg.data.get("url"):
                relative_path = f"{group_id}/{user_id}_{uuid.uuid4().hex}.jpg"
                file = path / relative_path
                if not await AsyncHttpx.download_file(url, file):
                    raise RuntimeError("下载词条图片失败")
                text += f"[image:{get_img_hash(file)}]"
                _list.append(relative_path)
        return text, _list

    @classmethod
    async def message2entry(cls, message: Message) -> Union[str, Message]:
        """
        说明:
            将原始Message转化为格式化问题
            用于格式化用户发言便于rule检测中check的进行
        参数:
            :param message: 待转化的message
        """
        text = ""
        for seg in message:
            if isinstance(seg, str):
                text += seg
            elif seg.type == "text":
                text += seg.data["text"]
            elif seg.type == "face":
                text += f"[face:{seg.data['id']}]"
            elif seg.type == "at":
                text += f"[at:{seg.data['qq']}]"
            else:
                url = seg.data.get("url", None)
                if url:
                    r = await AsyncHttpx.get(url)
                    text += f"[image:{str(imagehash.average_hash(Image.open(BytesIO(r.content))))}]"
        # # print('发言处理后：', text)
        return text

    @classmethod
    async def check(
        cls,
        event: GroupMessageEvent,
        entry: Union[str, Message],
        word_type: Optional[int] = None,
    ) -> bool:
        """
        说明:
            检测数据库中是否包含该问句并获取所有答句
            用于匹配用户发言的rule检测
        参数:
            :param event: event
            :param entry: 问句
            :param word_type: 词条类型 0/1/2:精准/模糊/正则
        """
        query = cls.query.where(cls.group_id == event.group_id)
        if word_type in (None, 0):
            if await query.where((cls.word_type == 0) & (cls.entry == entry)).gino.first():
                return True
        if word_type in (None, 1):
            like = db.literal(entry).like(db.literal("%") + cls.entry + "%")
            if await query.where((cls.word_type == 1) & like).gino.first():
                return True
        if word_type in (None, 2):
            regex = db.literal(entry).op("~")(cls.entry)
            return bool(await regex_matches(query.where((cls.word_type == 2) & regex), first=True))
        return False

    @classmethod
    async def delete_group_entry(
        cls,
        entry: str,
        group_id: int,
    ):
        """
        说明:
            删除指定问题全部或指定答句
        参数:
            :param entry: 词条
            :param group_id: 群号
        """
        await WithdrawBase.delete.where(
            (cls.group_id == group_id) & (cls.entry == entry)
        ).gino.status()

    @classmethod
    async def get_group_all_entry(
        cls, group_id: int
    ) -> List[Tuple[str, Union[Message, MessageSegment, str]]]:
        """
        说明:
            获取群聊结构化词条列表
        参数:
            :param group_id: 群号
        """
        return cls._handle_entry(
            await cls.query.where(cls.group_id == group_id).gino.all()
        )

    @classmethod
    async def get_entry_by_type(cls, word_type: int):
        """
        说明:
            通过词条类型获取结构化词条列表
        参数:
            :param word_type: 词条类型
        """
        return cls._handle_entry(
            await cls.query.where(cls.word_type == word_type).gino.all()
        )

    @classmethod
    def _handle_entry(
            cls,
            msg_list: List["WithdrawBase"]
    ) -> List[Tuple[str, Union[Message, MessageSegment, str]]]:
        """
        说明:
            格式化处理问句，返回元组(格式化前问句，格式化后问句)
        参数:
            :param msg_list: 从数据库取出来的结构化词条列表
        """
        # # print('_handle_entry开始')
        _tmp = []
        entry_list = []
        for q in msg_list:
            if q.entry not in _tmp:
                # 含CQ码的问句
                if q.entry_placeholder:
                    fin_entry = render_placeholders(
                        q.entry, q.entry_placeholder, QUESTION_PLACEHOLDER, path
                    )
                    entry = (q.entry, fin_entry)
                # 纯文本的问句
                else:
                    entry = (q.entry, f"[{int2type[q.word_type]}] " + q.entry)
                entry_list.append(entry)
                _tmp.append(q.entry)
        # # print('格式化后：', entry_list)
        return entry_list
