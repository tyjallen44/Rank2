"""Cloud Run Jobs runner: sharding and task sizing."""
from perception.jobs import shard


def test_shard_splits_pending_work_evenly_and_completely():
    items = list(range(23))
    parts = [shard(items, i, 5) for i in range(5)]
    assert sorted(x for p in parts for x in p) == items
    assert [len(p) for p in parts] == [5, 5, 5, 4, 4]
    assert shard(items, 0, 1) == items and shard([], 2, 4) == []


def test_task_count_scales_with_the_batch():
    import server
    assert server._task_count(1) == 1 and server._task_count(15) == 1 and server._task_count(16) == 2
    assert server._task_count(160) == 10 and server._task_count(500) == 10       # capped at 10 parallel tasks
    assert server._jobs_enabled() is False                                       # no RUN_JOBS_NAME locally → thread pool
