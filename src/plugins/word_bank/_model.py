import random
import uuid
from datetime import datetime
from io import BytesIO
from typing import Any, List, Optional, Tuple, Union

import imagehash
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from PIL import Image

from config.path_config import DATA_PATH
from services.db_context import db
from utils.http_utils import AsyncHttpx
from utils.imageutils import get_img_hash
from utils.word_entries import (
    ANSWER_PLACEHOLDER,
    QUESTION_PLACEHOLDER,
    regex_matches,
    render_placeholders,
    validate_word_pattern,
)

from ._config import int2type

path = DATA_PATH / "word_bank"


class WordBank(db.Model):
    __tablename__ = "word_bank2"

    id = db.Column(db.Integer(), primary_key=True)
    user_qq = db.Column(db.BigInteger(), nullable=False)
    group_id = db.Column(db.Integer())
    word_scope = db.Column(
        db.Integer(), nullable=False, default=0
    )  # 生效范围 0: 全局 1: 群聊 2: 私聊
    word_type = db.Column(
        db.Integer(), nullable=False, default=0
    )  # 词条类型 0: 完全匹配 1: 模糊 2: 正则
    status = db.Column(db.Boolean(), nullable=False, default=True)  # 词条状态
    problem = db.Column(db.String(), nullable=False)  # 问句
    answer = db.Column(db.String(), nullable=False)  # 答句
    problem_placeholder = db.Column(db.String())  # 问句的占位符
    answer_placeholder = db.Column(db.String())  # 答句的占位符
    create_time = db.Column(db.DateTime(), nullable=False)
    update_time = db.Column(db.DateTime(), nullable=False)

    @classmethod
    async def exist_any_problem(cls, group_id: int) -> bool:
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
        problem: str,
        answer: Optional[str],
        word_scope: Optional[int] = None,
        word_type: Optional[int] = None,
    ) -> bool:
        """
        说明:
            检测问题是否存在
            用于添加词条时检测词条是否重复
        参数:
            :param user_id: 用户id
            :param group_id: 群号
            :param problem: 问题
            :param answer: 回答
            :param word_scope: 词条范围
            :param word_type: 词条类型
        """
        query = cls.query.where(cls.problem == problem)
        if user_id:
            query = query.where(cls.user_qq == user_id)
        if group_id:
            query = query.where(cls.group_id == group_id)
        if answer:
            query = query.where(cls.answer == answer)
        if word_type:
            query = query.where(cls.word_type == word_type)
        if word_scope:
            query = query.where(cls.word_scope == word_scope)
        return bool(await query.gino.first())

    @classmethod
    async def add_problem_answer(
        cls,
        user_id: int,
        group_id: Optional[int],
        word_scope: int,
        word_type: int,
        problem: Union[str, Message],
        answer: Union[str, Message],
    ):
        """
        说明:
            添加或新增一个问答
        参数:
            :param user_id: 用户id
            :param group_id: 群号
            :param word_scope: 词条范围,
            :param word_type: 词条类型,
            :param problem: 问题
            :param answer: 回答
        """
        problem, _problem_list = await cls._problem2format(problem, user_id, group_id)
        problem = problem.strip()
        if word_type == 2:
            await validate_word_pattern(problem)
        answer, _answer_list = await cls._answer2format(answer, user_id, group_id)
        if not await cls.exists(user_id, group_id, problem, answer, word_scope, word_type):
            await cls.create(
                user_qq=user_id,
                group_id=group_id,
                word_scope=word_scope,
                word_type=word_type,
                status=True,
                problem=problem,
                answer=answer.strip(),
                problem_placeholder=",".join(_problem_list),
                answer_placeholder=",".join(_answer_list),
                create_time=datetime.now().replace(microsecond=0),
                update_time=datetime.now().replace(microsecond=0),
            )

    @classmethod
    async def _problem2format(
            cls, problem: Union[str, Message], user_id: int, group_id: int
    ) -> Tuple[str, List[Any]]:
        """
        说明:
            将CQ码转化为特定格式(face、at、image)
            用于添加词条时格式化问句
        参数:
            :param problem: 问题内容
            :param user_id: 用户id
            :param group_id: 群号
        """
        _list = []
        text = ""
        problem = problem if isinstance(problem, Message) else Message(problem)
        for seg in problem:
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
                relative_path = f"problem/{group_id}/{user_id}_{uuid.uuid4().hex}.jpg"
                file = path / relative_path
                if not await AsyncHttpx.download_file(url, file):
                    raise RuntimeError("下载词条图片失败")
                text += f"[image:{get_img_hash(file)}]"
                _list.append(relative_path)
        return text, _list

    @classmethod
    async def message2problem(cls, message: Message) -> Union[str, Message]:
        """
        说明:
            将原始Message转化为格式化问题
            用于格式化用户发言便于rule检测中check的进行
        参数:
            :param message: 待转化的message
        """
        # print('发言处理前：', message)
        text = ""
        for seg in message:
            # print(seg)
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
        # print('发言处理后：', text)
        return text

    @classmethod
    async def _answer2format(
        cls, answer: Union[str, Message], user_id: int, group_id: int
    ) -> Tuple[str, List[Any]]:
        """
        说明:
            将CQ码转化为占位符(face、at、image)
            用于添加词条时格式化答句
        参数:
            :param answer: 回答内容
            :param user_id: 用户id
            :param group_id: 群号
        """
        answer = answer if isinstance(answer, Message) else Message(answer)
        _list = []
        text = ""
        for seg in answer:
            if isinstance(seg, str):
                text += seg
            elif seg.type == "text":
                text += seg.data["text"]
            elif seg.type == "face":
                text += f"[face:placeholder_{len(_list)}]"
                _list.append(seg.data['id'])
            elif seg.type == "at":
                text += f"[at:placeholder_{len(_list)}]"
                _list.append(seg.data["qq"])
            elif url := seg.data.get("url"):
                relative_path = f"answer/{group_id}/{user_id}_{uuid.uuid4().hex}.jpg"
                if not await AsyncHttpx.download_file(url, path / relative_path):
                    raise RuntimeError("下载词条图片失败")
                text += f"[image:placeholder_{len(_list)}]"
                _list.append(relative_path)
        return text, _list

    @classmethod
    def _format2answer(cls, answer: "WordBank", *, mention: bool = False) -> Message:
        if answer.answer_placeholder:
            return render_placeholders(
                answer.answer, answer.answer_placeholder, ANSWER_PLACEHOLDER, path, mention=mention
            )
        return Message(MessageSegment.text(answer.answer)) if answer.answer else Message()

    @classmethod
    async def check(
        cls,
        event: MessageEvent,
        problem: str,
        word_scope: Optional[int] = None,
        word_type: Optional[int] = None,
    ) -> Optional[List["WordBank"]]:
        query = cls.query
        if word_scope is not None:
            query = query.where(cls.word_scope == word_scope)
        elif isinstance(event, GroupMessageEvent):
            query = query.where((cls.group_id == event.group_id) | (cls.word_scope == 0))
        else:
            query = query.where((cls.word_scope == 2) | (cls.word_scope == 0))

        if word_type in (None, 0):
            matches = await query.where((cls.word_type == 0) & (cls.problem == problem)).gino.all()
            if matches:
                return matches
        if word_type in (None, 1):
            like = db.literal(problem).like(db.literal("%") + cls.problem + "%")
            matches = await query.where((cls.word_type == 1) & like).gino.all()
            if matches:
                return matches
        if word_type in (None, 2):
            regex = db.literal(problem).op("~")(cls.problem)
            matches = await regex_matches(
                query.where((cls.word_type == 2) & (cls.word_scope != 999) & regex)
            )
            if matches:
                return matches
        return None

    @classmethod
    async def get_answer(
        cls,
        event: MessageEvent,
        problem: str,
        word_scope: Optional[int] = None,
        word_type: Optional[int] = None,
    ) -> Optional[Union[str, Message]]:
        matches = await cls.check(event, problem, word_scope, word_type)
        if matches:
            return cls._format2answer(random.choice(matches), mention=True)
        return None

    @classmethod
    async def get_problem_all_answer(
        cls,
        problem: str,
        index: Optional[int] = None,
        group_id: Optional[int] = None,
        word_scope: Optional[int] = 0,
    ) -> List[Union[str, Message]]:
        """
        说明:
            获取指定问题所有回答
        参数:
            :param problem: 问题
            :param index: 下标
            :param group_id: 群号
            :param word_scope: 词条范围
        """
        if index is not None:
            if group_id:
                problem = (await cls.query.where(cls.group_id == group_id).gino.all())[index]
            else:
                problem = (
                    await cls.query.where(
                        cls.word_scope == (word_scope or 0)
                    ).gino.all()
                )[index]
            problem = problem.problem
        answer = cls.query.where(cls.problem == problem)
        if group_id:
            answer = answer.where(cls.group_id == group_id)
        return [
            cls._format2answer(row) for row in await answer.gino.all()
        ]

    @classmethod
    async def delete_group_problem(
        cls,
        problem: str,
        group_id: int,
        index: Optional[int] = None,
        word_scope: int = 0,
    ):
        """
        说明:
            删除指定问题全部或指定答句
        参数:
            :param problem: 问句
            :param group_id: 群号，无群号时默认查询全局词条
            :param index: 答句下标
            :param word_scope: 词条范围
        """
        # 有下标时删除指定答句
        if index is not None:
            if group_id:
                query = await cls.query.where(
                    (cls.group_id == group_id) & (cls.problem == problem)
                ).gino.all()
            else:
                query = await cls.query.where(
                    (cls.word_scope == word_scope) & (cls.problem == problem)
                ).gino.all()
            await query[index].delete()
        # 无下标时删除全部答句
        else:
            if group_id:
                await WordBank.delete.where(
                    (cls.group_id == group_id) & (cls.problem == problem)
                ).gino.status()
            else:
                await WordBank.delete.where(
                    (cls.word_scope == word_scope) & (cls.problem == problem)
                ).gino.status()

    @classmethod
    async def get_group_all_problem(
        cls, group_id: int
    ) -> List[Tuple[Any, Union[MessageSegment, str]]]:
        """
        说明:
            获取群聊结构化词条列表
        参数:
            :param group_id: 群号
        """
        return cls._handle_problem(
            await cls.query.where(cls.group_id == group_id).gino.all()
        )

    @classmethod
    async def get_problem_by_scope(cls, word_scope: int):
        """
        说明:
            通过词条范围获取结构化词条列表
        参数:
            :param word_scope: 词条范围
        """
        return cls._handle_problem(
            await cls.query.where(cls.word_scope == word_scope).gino.all()
        )

    @classmethod
    async def get_problem_by_type(cls, word_type: int):
        """
        说明:
            通过词条类型获取结构化词条列表
        参数:
            :param word_type: 词条类型
        """
        return cls._handle_problem(
            await cls.query.where(cls.word_type == word_type).gino.all()
        )

    @classmethod
    def _handle_problem(
            cls,
            msg_list: List["WordBank"]
    ) -> List[Tuple[Any, Union[Message, MessageSegment, str]]]:
        """
        说明:
            格式化处理问句，返回元组(格式化前问句，格式化后问句)
        参数:
            :param msg_list: 从数据库取出来的结构化词条列表
        """
        # print('_handle_problem开始')
        _tmp = []
        problem_list = []
        for q in msg_list:
            if q.problem not in _tmp:
                # 含CQ码的问句
                if q.problem_placeholder:
                    fin_problem = render_placeholders(
                        q.problem, q.problem_placeholder, QUESTION_PLACEHOLDER, path
                    )
                    problem = (q.problem, fin_problem)
                # 纯文本的问句
                else:
                    problem = (q.problem, f"[{int2type[q.word_type]}] " + q.problem)
                problem_list.append(problem)
                _tmp.append(q.problem)
        # print('格式化后：', problem_list)
        return problem_list

    @classmethod
    async def _move(
        cls,
        user_id: int,
        group_id: Optional[int],
        problem: Union[str, Message],
        answer: Union[str, Message],
        placeholder: str
    ):
        """
        说明:
            旧词条图片移动方法
        参数:
            :param user_id: 用户id
            :param group_id: 群号
            :param problem: 问题
            :param answer: 回答
            :param placeholder: 占位符
        """
        word_scope = 0
        word_type = 0
        # 对图片做额外处理
        if not await cls.exists(user_id, group_id, problem, answer, word_scope, word_type):
            await cls.create(
                user_qq=user_id,
                group_id=group_id,
                word_scope=word_scope,
                word_type=word_type,
                status=True,
                problem=problem,
                answer=answer,
                problem_placeholder=None,
                answer_placeholder=placeholder,
                create_time=datetime.now().replace(microsecond=0),
                update_time=datetime.now().replace(microsecond=0),
            )
