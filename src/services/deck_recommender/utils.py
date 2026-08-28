import asyncio
import faulthandler
import json
import os
import shutil
import time
import traceback
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from glob import glob
from os.path import join as pjoin
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import yaml
import zstandard

faulthandler.enable()


def write_file(file_path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    tmp_path = file_path + '.tmp'
    with open(tmp_path, 'wb') as file:
        file.write(data)
    os.replace(tmp_path, file_path)

def load_json(file_path: str, default=None) -> dict:
    if not os.path.exists(file_path):
        if default is not None:
            return default
        raise FileNotFoundError(f"File not found: {file_path}")
    with open(file_path, 'rb') as file:
        return json.loads(file.read())

def dump_json(data: dict, file_path: str, indent: bool = True) -> None:
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    tmp_path = file_path + '.tmp'
    with open(tmp_path, 'wb') as file:
        buffer = json.dumps(data, ensure_ascii=False, indent=2 if indent else None).encode('utf-8')
        file.write(buffer)
    os.replace(tmp_path, file_path)

def loads_json(s) -> dict:
    return json.loads(s)

def dumps_json(data: dict, indent: bool = True) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2 if indent else None)

async def aload_json(path: str) -> Dict[str, Any]:
    return await asyncio.to_thread(load_json, path)

async def asave_json(data: Dict[str, Any], path: str):
    return await asyncio.to_thread(dump_json, data, path)

def get_exc_desc(e: Exception) -> str:
    et = type(e).__name__
    e = str(e)
    if et in ['AssertionError', 'HTTPException', 'Exception']:
        return e
    if et and e:
        return f"{et}: {e}"
    return et or e

def log(*args, **kwargs):
    time_str = datetime.now().strftime("[%Y-%m-%d %H:%M:%S] [INFO]")
    print(time_str, *args, **kwargs, flush=True)

def error(*args, print_trace: bool = True, **kwargs):
    time_str = datetime.now().strftime("[%Y-%m-%d %H:%M:%S] [ERROR]")
    print(time_str, *args, **kwargs, flush=True)
    if print_trace:
        print(traceback.format_exc(), flush=True)

def create_parent_folder(path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path

def compress_zstd(b: bytes):
    cctx = zstandard.ZstdCompressor()
    return cctx.compress(b)

def decompress_zstd(b: bytes):
    dctx = zstandard.ZstdDecompressor()
    return dctx.decompress(b, max_output_size=100*1024*1024)

def remove_file(file_path: str) -> None:
    try:
        os.remove(file_path)
    except FileNotFoundError:
        pass

def remove_folder(folder_path: str) -> None:
    try:
        shutil.rmtree(folder_path)
    except FileNotFoundError:
        pass
