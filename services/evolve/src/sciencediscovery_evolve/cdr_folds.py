# Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
# Licensed under the Apache License, Version 2.0 (the "License");
# you may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
"""Bounded per-cycle folds; workers never modify population or emit events."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import threading
from typing import Callable


@dataclass
class FoldBatch:
    results: list[tuple[list, dict]] = field(default_factory=list)
    attempts: int = 0
    error: Exception | None = None


def fold_batch(payloads: list[dict], measure: Callable[[dict], dict], workers: int,
               should_stop: Callable[[], bool]) -> FoldBatch:
    outcome = FoldBatch()
    if not payloads:
        return outcome
    count = min(workers, len(payloads))
    cancelled = threading.Event()
    lock = threading.Lock()
    readings: list[dict | None] = [None] * len(payloads)
    next_index = 0

    def worker(slot: int) -> None:
        nonlocal next_index
        while True:
            with lock:
                if cancelled.is_set() or should_stop() or next_index == len(payloads):
                    return
                index = next_index
                next_index += 1
                outcome.attempts += 1
            payload = dict(payloads[index])
            if count > 1:
                payload.update(_device_slot=slot, _batch_stop=cancelled.is_set)
            try:
                reading = measure(payload)
                reading.setdefault("sequence", payload["sequence"])
                readings[index] = reading
                if reading.get("runner_lost"):
                    cancelled.set()
            except Exception as error:
                with lock:
                    if outcome.error is None:
                        outcome.error = error
                cancelled.set()
                return

    if count == 1:
        worker(0)
    else:
        with ThreadPoolExecutor(max_workers=count, thread_name_prefix="cdr-fold") as pool:
            futures = [pool.submit(worker, slot) for slot in range(count)]
            for future in futures:
                future.result()
    outcome.results = [(payload["mutations"], reading) for payload, reading in zip(payloads, readings)
                       if reading is not None]
    return outcome
