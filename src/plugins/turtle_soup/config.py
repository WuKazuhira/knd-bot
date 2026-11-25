from nonebot import get_plugin_config
from pydantic import BaseModel, Field


class Config(BaseModel):
    # 生成谜题
    ats_openai_generate_api_key: str = Field(default="")
    ats_openai_generate_base_url: str = Field(default="")
    ats_openai_generate_model: str = Field(default="")

    # 评判问题
    ats_openai_judge_api_key: str = Field(default="")
    ats_openai_judge_base_url: str = Field(default="")
    ats_openai_judge_model: str = Field(default="")

    # 出题方式: ai / local
    ats_puzzle_source: str = Field(default="ai")

    # 本地题库路径（单个 json 或目录），留空用内置题库
    ats_local_puzzles_path: str = Field(default="")

    ats_max_questions: int = Field(default=50)
    ats_timeout: int = Field(default=7200)


plugin_config = get_plugin_config(Config)
