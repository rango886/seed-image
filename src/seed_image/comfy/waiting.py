"""等待任务到达终态。

只依赖一个 `fetch(job_id) -> job dict` 承诺，因此可以脱离 HTTP 单测。
超时不代表任务失败：把最后一次快照原样交回上层，由上层决定是保留还是取消。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

PENDING = "pending"
IN_PROGRESS = "in_progress"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED})

Fetch = Callable[[str], Mapping]
Report = Callable[[str], None]


@dataclass(frozen=True)
class WaitOutcome:
    """等待结束时的全貌：谁到了终态，谁还在跑。"""

    finished: Mapping[str, Mapping] = field(default_factory=dict)
    unfinished: Mapping[str, Mapping] = field(default_factory=dict)

    @property
    def timed_out(self) -> bool:
        return bool(self.unfinished)


def wait_for_jobs(
    fetch: Fetch,
    job_ids: Sequence[str],
    *,
    timeout: float,
    poll_interval: float = 1.0,
    report: Report | None = None,
) -> WaitOutcome:
    """轮询直到所有 job 到终态或超时。轮询是权威状态，不依赖任何推送。"""
    deadline = time.monotonic() + timeout
    waiting = list(job_ids)
    finished: dict[str, Mapping] = {}
    latest: dict[str, Mapping] = {}
    announced: str | None = None
    while waiting:
        for job_id in list(waiting):
            job = fetch(job_id)
            latest[job_id] = job
            if str(job.get("status")) in TERMINAL:
                finished[job_id] = job
                waiting.remove(job_id)
        if not waiting:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if report:
            # 只在状态真的变了时说话：轮询自身不是信息，刷屏会淹没真正的诊断。
            line = _progress_line(waiting, latest)
            if line != announced:
                report(line)
                announced = line
        time.sleep(min(poll_interval, remaining))
    return WaitOutcome(finished, {job_id: latest.get(job_id, {}) for job_id in waiting})


def _progress_line(waiting: Sequence[str], latest: Mapping[str, Mapping]) -> str:
    parts = [f"{job_id[:8]}={latest.get(job_id, {}).get('status', '?')}" for job_id in waiting]
    return f"waiting {len(waiting)} job(s): {', '.join(parts)}"
