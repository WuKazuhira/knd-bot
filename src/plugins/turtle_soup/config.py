from nonebot import get_plugin_config
from pydantic import BaseModel, Field


class Config(BaseModel):
    """插件配置类"""
    
    # OpenAI API 配置 - 生成谜题
    ats_openai_generate_api_key: str = Field(default="")
    ats_openai_generate_base_url: str = Field(default="")
    ats_openai_generate_model: str = Field(default="")
    
    # OpenAI API 配置 - 评判问题
    ats_openai_judge_api_key: str = Field(default="")
    ats_openai_judge_base_url: str = Field(default="")
    ats_openai_judge_model: str = Field(default="")
    
    # 出题方式: "ai" - AI 生成谜题 / "local" - 从本地题库选题
    ats_puzzle_source: str = Field(default="ai")

    # 本地题库配置
    # 自定义题库路径，可以是单个 json 文件或包含多个 json 文件的目录
    # 留空则使用插件内置题库
    ats_local_puzzles_path: str = Field(default="")

    # 游戏配置
    ats_max_questions: int = Field(default=50)
    ats_timeout: int = Field(default=7200)


# 从 .env 文件加载配置
plugin_config = get_plugin_config(Config)
