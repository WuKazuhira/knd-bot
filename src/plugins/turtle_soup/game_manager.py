"""
游戏管理器模块
"""
import json
import random
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from openai import AsyncOpenAI
from nonebot.exception import FinishedException
from nonebot.log import logger
from .config import plugin_config


class GameManager:
    """海龟汤游戏管理器"""
    
    def __init__(self):
        self.config = plugin_config
        self.games: Dict[str, Dict[str, Any]] = {}
        
        # 为生成谜题创建客户端
        self.generate_client = AsyncOpenAI(
            api_key=self.config.ats_openai_generate_api_key,
            base_url=self.config.ats_openai_generate_base_url
        )
        
        # 为评判问题创建客户端
        self.judge_client = AsyncOpenAI(
            api_key=self.config.ats_openai_judge_api_key,
            base_url=self.config.ats_openai_judge_base_url
        )
        
        # 加载提示词模板
        self._load_prompts()

        # 加载本地题库
        self.local_puzzles: List[Dict[str, Any]] = []
        self._load_local_puzzles()
    
    def _load_prompts(self):
        """从文件加载提示词"""
        prompts_dir = Path(__file__).parent / "prompts"
        
        # 读取生成谜题的提示词
        generate_prompt_path = prompts_dir / "generate.md"
        with open(generate_prompt_path, "r", encoding="utf-8") as f:
            self.generate_prompt = f.read()
        
        # 读取游戏主持的提示词
        gaming_prompt_path = prompts_dir / "gaming.md"
        with open(gaming_prompt_path, "r", encoding="utf-8") as f:
            self.gaming_prompt = f.read()
        
        # 读取进度计算的提示词
        progress_prompt_path = prompts_dir / "progress.md"
        with open(progress_prompt_path, "r", encoding="utf-8") as f:
            self.progress_prompt = f.read()
        
        # 读取评分的提示词
        rate_prompt_path = prompts_dir / "rate.md"
        with open(rate_prompt_path, "r", encoding="utf-8") as f:
            self.rate_prompt = f.read()
    
    def _load_local_puzzles(self):
        """加载本地题库

        题库路径由 ats_local_puzzles_path 配置指定，
        可以是单个 json 文件或包含多个 json 文件的目录。
        """
        if not self.config.ats_local_puzzles_path:
            if self.config.ats_puzzle_source == "local":
                logger.warning("出题方式为本地题库，但未配置题库路径 ATS_LOCAL_PUZZLES_PATH")
            return

        puzzles_path = Path(self.config.ats_local_puzzles_path)
        if not puzzles_path.exists():
            logger.warning(f"本地题库路径不存在: {puzzles_path}")
            return

        if puzzles_path.is_file():
            puzzle_files = [puzzles_path]
        else:
            puzzle_files = sorted(puzzles_path.glob("*.json"))

        for puzzle_file in puzzle_files:
            try:
                with open(puzzle_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if isinstance(data, dict):
                    data = [data]

                for puzzle in data:
                    if not isinstance(puzzle, dict):
                        continue
                    # 校验必需字段
                    missing = [
                        key for key in ("title", "puzzle_setting", "supplementary_info", "solution")
                        if not puzzle.get(key)
                    ]
                    if missing:
                        logger.warning(
                            f"题库 {puzzle_file.name} 中的题目 "
                            f"{puzzle.get('title', puzzle.get('id', '?'))} 缺少字段 {missing}，已跳过"
                        )
                        continue
                    self.local_puzzles.append(puzzle)
            except Exception as e:
                logger.warning(f"加载题库文件 {puzzle_file} 失败: {e}")

        logger.info(f"本地题库加载完成，共 {len(self.local_puzzles)} 个题目")

    def pick_local_puzzle(self, keyword: str = "") -> Optional[Dict[str, Any]]:
        """从本地题库中随机选择一个题目

        keyword 可以是题目 id、标题或标签，用于筛选；为空则全库随机。
        """
        if not self.local_puzzles:
            return None

        candidates = self.local_puzzles
        if keyword:
            keyword = keyword.strip()
            candidates = [
                p for p in self.local_puzzles
                if keyword == str(p.get("id", ""))
                or keyword in p.get("title", "")
                or any(keyword in tag for tag in p.get("tags", []))
            ]
            if not candidates:
                return None

        return random.choice(candidates)

    def has_active_game(self, session_id: str) -> bool:
        """检查会话是否有活跃的游戏"""
        if session_id not in self.games:
            return False
        
        # 检查超时
        game = self.games[session_id]
        if time.time() - game['start_time'] > self.config.ats_timeout:
            del self.games[session_id]
            return False
        
        return True
    
    def get_game(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取游戏状态"""
        return self.games.get(session_id)
    
    async def create_game(self, session_id: str, theme: str) -> Dict[str, Any]:
        """创建新游戏(AI 生成谜题)"""
        # 调用 AI 生成新谜题
        puzzle = await self._generate_puzzle(theme)
        self._init_game(session_id, puzzle)
        return puzzle

    def create_local_game(self, session_id: str, keyword: str = "") -> Optional[Dict[str, Any]]:
        """创建新游戏(本地题库谜题)

        返回选中的谜题；题库为空或关键词无匹配时返回 None。
        """
        puzzle = self.pick_local_puzzle(keyword)
        if puzzle is None:
            return None
        self._init_game(session_id, puzzle)
        return puzzle

    def _init_game(self, session_id: str, puzzle: Dict[str, Any]):
        """初始化游戏状态"""
        self.games[session_id] = {
            'puzzle': puzzle,
            'history': [],
            'percent': 0,
            'start_time': time.time(),
            'hint_index': 0  # 记录当前提示到第几条
        }
    
    async def _generate_puzzle(self, theme: str) -> Dict[str, Any]:
        """调用 AI 生成谜题"""
        if theme:
            theme = f"\n用户期望的谜题主题为: {theme}"
        else:
            theme = ""
        try:
            response = await self.generate_client.chat.completions.create(
                model=self.config.ats_openai_generate_model,
                messages=[
                    {"role": "system", "content": self.generate_prompt},
                    {"role": "user", "content": f"### **执行指令**\n\n现在，启动你的内容生成引擎。请遵循以上所有规则，为我生成 **1** 个全新的、符合JSON格式的海龟汤谜题。将它们包含在一个JSON数组中。{theme}"}
                ],
                temperature=0.9,
                response_format={"type": "json_object"}
            )
            
            content = response.choices[0].message.content
            # 处理返回内容可能带有 ```json 前缀和 ``` 后缀的情况
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]
            puzzles = json.loads(content.strip())
            
            # 如果返回的是数组,取第一个
            if isinstance(puzzles, list):
                puzzle = puzzles[0]
            # 如果返回的是对象但包含数组字段
            elif isinstance(puzzles, dict):
                # 尝试找到包含谜题的字段
                for key in ['puzzles', 'data', 'items']:
                    if key in puzzles and isinstance(puzzles[key], list):
                        puzzle = puzzles[key][0]
                        break
                else:
                    # 如果没有找到数组字段,假设整个对象就是谜题
                    puzzle = puzzles
            else:
                raise ValueError("Unexpected response format")
            
            return puzzle
        
        except FinishedException:
            return
        except Exception as e:
            raise Exception(f"生成谜题失败: {str(e)}")
    
    async def process_question(self, session_id: str, question: str) -> Dict[str, Any]:
        """处理玩家的问题"""
        if not self.has_active_game(session_id):
            raise ValueError("没有活跃的游戏")
        
        game = self.games[session_id]
        puzzle = game['puzzle']
        
        # 构建游戏数据
        game_data = {
            "puzzle_setting": puzzle['puzzle_setting'],
            "supplementary_info": puzzle['supplementary_info'],
            "solution": puzzle['solution'],
            "history": game['history'],
            "last_percentage": game['percent'],
            "player_question": question
        }
        
        try:
            # 调用 AI 进行判断
            response = await self.judge_client.chat.completions.create(
                model=self.config.ats_openai_judge_model,
                messages=[
                    {"role": "system", "content": self.gaming_prompt},
                    {"role": "user", "content": json.dumps(game_data, ensure_ascii=False)}
                ],
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            
            content = response.choices[0].message.content
            # 处理返回内容可能带有 ```json 前缀和 ``` 后缀的情况
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]
            result = json.loads(content)
            
            # 更新游戏状态
            game['history'].append({
                'question': question,
                'answer': result['reply'],
                'percent': result['percent']
            })
            game['percent'] = result['percent']
            
            return result
            
        except Exception as e:
            raise Exception(f"处理问题失败: {str(e)}")
    
    def end_game(self, session_id: str):
        """结束游戏"""
        if session_id in self.games:
            del self.games[session_id]
    
    def get_next_hint(self, session_id: str) -> Optional[dict]:
        """按顺序获取下一条附加信息作为提示"""
        if not self.has_active_game(session_id):
            return None
        
        game = self.games[session_id]
        puzzle = game['puzzle']
        
        if 'supplementary_info' in puzzle and puzzle['supplementary_info']:
            hints = puzzle['supplementary_info']
            current_index = game.get('hint_index', 0)
            
            # 如果已经展示完所有提示
            if current_index >= len(hints):
                return {
                    'hint': None,
                    'current': current_index,
                    'total': len(hints),
                    'finished': True
                }
            
            # 获取当前提示
            hint = hints[current_index]
            # 更新索引
            game['hint_index'] = current_index + 1
            
            return {
                'hint': hint,
                'current': current_index + 1,
                'total': len(hints),
                'finished': False
            }
        
        return None
    
    async def rate_puzzle(self, session_id: str) -> Dict[str, Any]:
        """对谜题进行评分"""
        if not self.has_active_game(session_id):
            raise ValueError("没有活跃的游戏")
        
        game = self.games[session_id]
        puzzle = game['puzzle']
        
        # 构建评分数据
        rate_data = {
            "puzzle_setting": puzzle['puzzle_setting'],
            "supplementary_info": puzzle['supplementary_info'],
            "solution": puzzle['solution']
        }
        
        try:
            # 调用 AI 进行评分
            response = await self.judge_client.chat.completions.create(
                model=self.config.ats_openai_judge_model,
                messages=[
                    {"role": "system", "content": self.rate_prompt},
                    {"role": "user", "content": json.dumps(rate_data, ensure_ascii=False)}
                ],
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            
            content = response.choices[0].message.content
            # 处理返回内容可能带有 ```json 前缀和 ``` 后缀的情况
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]
            result = json.loads(content.strip())
            
            return result
            
        except Exception as e:
            raise Exception(f"评分失败: {str(e)}")
    
    async def recalculate_progress(self, session_id: str) -> int:
        """重新计算游戏进度"""
        if not self.has_active_game(session_id):
            raise ValueError("没有活跃的游戏")
        
        game = self.games[session_id]
        puzzle = game['puzzle']
        
        # 构建进度重计算数据
        progress_data = {
            "puzzle_setting": puzzle['puzzle_setting'],
            "supplementary_info": puzzle['supplementary_info'],
            "solution": puzzle['solution'],
            "history": game['history']
        }
        
        try:
            # 调用 AI 重新计算进度
            response = await self.judge_client.chat.completions.create(
                model=self.config.ats_openai_judge_model,
                messages=[
                    {"role": "system", "content": self.progress_prompt},
                    {"role": "user", "content": json.dumps(progress_data, ensure_ascii=False)}
                ],
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            
            content = response.choices[0].message.content
            # 处理返回内容可能带有 ```json 前缀和 ``` 后缀的情况
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]
            result = json.loads(content.strip())
            
            # 更新游戏进度
            new_percent = result['recalculated_percent']
            game['percent'] = new_percent
            
            return new_percent
            
        except Exception as e:
            raise Exception(f"重新计算进度失败: {str(e)}")
