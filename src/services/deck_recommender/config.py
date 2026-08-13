from utils import *

CONFIG = {}
CONFIG_PATH = pjoin(os.path.dirname(os.path.abspath(__file__)), 'config.yaml')
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        CONFIG = yaml.safe_load(f) or {}
else:
    log(f"未找到配置文件 {CONFIG_PATH}，使用默认配置")

HOST = os.getenv('DECK_RECOMMENDER_HOST', str(CONFIG.get('host', '127.0.0.1')))
PORT = int(os.getenv('DECK_RECOMMENDER_PORT', str(CONFIG.get('port', 45557))))
WORKER_NUM = int(os.getenv('DECK_RECOMMENDER_WORKER_NUM', str(CONFIG.get('worker_num', 1))))
DATA_DIR = os.getenv('DECK_RECOMMENDER_DATA_DIR', str(CONFIG.get('data_dir', 'data/pjsk/deckrec')))
USERDATA_CACHE_NUM = int(os.getenv('DECK_RECOMMENDER_USERDATA_CACHE_NUM', str(CONFIG.get('userdata_cache_num', 10))))
DB_PATH = pjoin(DATA_DIR, 'deckrec.json')
