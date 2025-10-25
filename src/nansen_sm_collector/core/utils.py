from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Iterator, Sequence, TypeVar


T = TypeVar("T")


def utc_now() -> datetime:
    """回傳目前 UTC 時間。"""

    return datetime.now(tz=timezone.utc)


def chunked(sequence: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    """將序列切分為固定大小區塊。"""

    if size <= 0:
        raise ValueError("size 必須為正整數")
    for index in range(0, len(sequence), size):
        yield sequence[index : index + size]


def ensure_non_empty(iterable: Iterable[T], message: str) -> Iterable[T]:
    """確認可疊代物件不為空。"""

    iterator = iter(iterable)
    try:
        first = next(iterator)
    except StopIteration as exc:
        raise ValueError(message) from exc
    yield first
    for item in iterator:
        yield item
