"""验证 Ctrl+C 能否立刻结束一条阻塞中的 seed-image 命令。

必须与被测进程处在同一个控制台（CREATE_NEW_PROCESS_GROUP 的子进程会直接忽略
CTRL_C_EVENT，那样测不出任何东西），所以本进程先把 SIGINT 变成空操作，
再向整个控制台组发一次 Ctrl+C。
"""

from __future__ import annotations

import ctypes
import signal
import subprocess
import sys
import time
import uuid

import httpx

SERVER = "http://127.0.0.1:8199"
CTRL_C_EVENT = 0

signal.signal(signal.SIGINT, lambda *_: None)  # 别把自己也打死

job_id = str(uuid.uuid4())
httpx.post(  # steps == -1 让 fake_comfy 永远返回 in_progress
    f"{SERVER}/api/prompt",
    json={"prompt_id": job_id, "prompt": {"7": {"inputs": {"steps": -1}}}},
)

proc = subprocess.Popen(
    [sys.executable, "-m", "seed_image", "comfyui", "job", "wait", job_id, "--timeout", "120"]
)
time.sleep(3)
print("--- sending CTRL_C_EVENT to console group ---", flush=True)
ctypes.windll.kernel32.GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0)
try:
    print(f"--- exited with {proc.wait(timeout=10)} ---")
except subprocess.TimeoutExpired:
    print("--- STILL RUNNING after 10s ---")
    proc.kill()
