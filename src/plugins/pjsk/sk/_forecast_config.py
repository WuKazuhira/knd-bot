from .._config import FORECAST_EVENTS_API_URL, FORECAST_LATEST_API_URL

# SK预测源配置

FORECAST_SOURCES = {
    'local': {
        'name': '本地预测',
        'enabled': True,
        'regions': ['jp', 'cn', 'tw'],
        'update_interval_minutes': 20,
        'error_retry_minutes': 5,
        'start_after_hours': 1,
        'end_before_hours': 0.25,
        'sample_points': 80,
        'ranks': [10, 20, 30, 40, 50, 100, 200, 300, 400, 500, 1000, 2000, 3000, 4000, 5000, 10000, 50000, 100000],
    },
    '33kit': {
        'name': '33Kit预测',
        'enabled': True,
        'regions': ['jp'],
        'url': 'https://sekai-data.3-3.dev/predict.json',
        'update_interval_minutes': 20,
        'error_retry_minutes': 10,
    },
    'moe': {
        'name': 'Moesekai预测',
        'enabled': bool(FORECAST_EVENTS_API_URL and FORECAST_LATEST_API_URL),
        'regions': ['jp', 'cn'],
        'events_url': FORECAST_EVENTS_API_URL,
        'latest_url': FORECAST_LATEST_API_URL,
        'update_interval_minutes': 10,
        'error_retry_minutes': 10,
        'ranks': [50, 100, 200, 300, 400, 500, 1000, 2000, 3000, 4000, 5000, 10000],
    },
    'sekarun': {
        'name': 'SekaRun预测',
        'enabled': True,
        'regions': ['jp', 'tw'],
        'url': 'https://jiiku831.github.io/{region}data/sekarun.js',
        'update_interval_minutes': 10,
        'error_retry_minutes': 10,
        'ranks': [10, 30, 50, 100, 200, 300, 500, 1000, 2000, 3000, 5000, 10000, 50000, 100000],
    },
}

# 实时分数 & 时速显示的排名档位（表格行顺序）
LIVE_RANKS = [
    10, 20, 30, 40, 50, 100,
    200, 300, 400, 500,
    1000, 2000, 3000, 4000, 5000,
    10000,
]

# 预测数据过期时间（小时）
FORECAST_EXPIRE_HOURS = 3

# 活动开始多少小时后开始更新预测
START_FORECAST_HOURS_AFTER_EVENT_START = 2

# 活动结束前多少小时停止更新预测
STOP_FORECAST_HOURS_BEFORE_EVENT_END = 2
