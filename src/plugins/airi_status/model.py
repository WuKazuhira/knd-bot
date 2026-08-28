# Adapted from AiriCore plugins/airi_status (MIT License)
from dataclasses import dataclass
from functools import lru_cache

import psutil

from utils.sysinfo import disk_usage

try:
    import cpuinfo
except Exception:  # pragma: no cover - optional dependency fallback
    cpuinfo = None

# CPU 占用率的采样窗口。这段时间是纯 sleep，直接计入出图耗时，
# 0.3s 足够拿到有代表性的读数。
CPU_SAMPLE_INTERVAL = 0.3


@lru_cache(maxsize=1)
def _cpu_brand() -> str:
    """CPU 型号不会变，但 py-cpuinfo 自己不缓存，实测每次调用要 1.9 秒。"""
    if cpuinfo is None:
        return ""
    try:
        return cpuinfo.get_cpu_info().get("brand_raw") or ""
    except Exception:
        return ""


@dataclass
class CPUInfo:
    core: int
    logical_core: int
    usage: float
    freq: float
    brand: str

    @classmethod
    def get_cpu_info(cls):
        physical = psutil.cpu_count(logical=False) or 0
        logical = psutil.cpu_count(logical=True) or physical or 1
        usage = round(psutil.cpu_percent(interval=CPU_SAMPLE_INTERVAL), 1)
        freq_obj = psutil.cpu_freq()
        freq = round((freq_obj.current if freq_obj else 0) / 1000, 2)
        return CPUInfo(
            core=physical or logical,
            logical_core=logical,
            usage=usage,
            freq=freq,
            brand=_cpu_brand(),
        )


@dataclass
class RAMInfo:
    total: float
    usage: float

    @classmethod
    def get_ram_info(cls):
        mem = psutil.virtual_memory()
        return RAMInfo(total=round(mem.total / (1024**3), 2), usage=round(mem.used / (1024**3), 2))


@dataclass
class SwapMemory:
    total: float
    usage: float

    @classmethod
    def get_swap_info(cls):
        mem = psutil.swap_memory()
        return SwapMemory(total=round(mem.total / (1024**3), 2), usage=round(mem.used / (1024**3), 2))


@dataclass
class DiskInfo:
    total: float
    usage: float

    @classmethod
    def get_disk_info(cls):
        disk = disk_usage()
        return DiskInfo(total=round(disk.total / (1024**3), 2), usage=round(disk.used / (1024**3), 2))


def get_status_info() -> tuple[CPUInfo, RAMInfo, SwapMemory, DiskInfo]:
    return CPUInfo.get_cpu_info(), RAMInfo.get_ram_info(), SwapMemory.get_swap_info(), DiskInfo.get_disk_info()
