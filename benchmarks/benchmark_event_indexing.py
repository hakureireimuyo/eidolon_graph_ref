"""事件索引优化基准（REFACTOR_EVENT_INDEXING 性能验收）。

三个场景（方案 §性能对比）：
- fanout：1 源 → N 目标（旧 O(n²)：每次消费扫描事件全部投递）
- append：单 APPEND 端口累积 K 事件（旧 O(k²)）
- depth：长链 N 层（旧 O(n×depth)）

用法：uv run benchmarks/benchmark_event_indexing.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
sys.stdout.reconfigure(encoding="utf-8")  # Windows GBK 控制台

from eidolon_graph_ref.engine.event import Injection, Kind
from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_DATA, SLOT_TRIGGER

from conftest import make_world


def bench_fanout(num_targets: int, epochs: int = 20):
    g = GraphDefinition(f"fan{num_targets}")
    g.add_node("src", "Source")
    for i in range(num_targets):
        g.add_node(f"s{i}", "Sink")
        g.wire("src", "tick", f"s{i}", "consume.value")
    world = make_world(g)
    start = time.perf_counter()
    for _ in range(epochs):
        world.run([Injection("src", "tick.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])
    elapsed = time.perf_counter() - start
    return elapsed / epochs


def bench_append(event_count: int, epochs: int = 20):
    g = GraphDefinition(f"append{event_count}")
    g.add_node("buf", "Buffer")
    g.add_node("flush", "Source", config={"groups": {"tick": {"step": 1}}})
    g.wire("flush", "tick", "buf", "flush.trigger")
    world = make_world(g)
    injections = [
        Injection("buf", "put.item", SLOT_DATA, Kind.DATA, i) for i in range(event_count)
    ] + [Injection("flush", "tick.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)]
    start = time.perf_counter()
    for _ in range(epochs):
        world.run(list(injections))
    elapsed = time.perf_counter() - start
    return elapsed / epochs


def bench_depth(depth: int, epochs: int = 20):
    g = GraphDefinition(f"depth{depth}")
    g.add_node("src", "Source")
    for i in range(depth):
        g.add_node(f"n{i}", "Source", config={"groups": {"tick": {"step": 0}}})
        if i == 0:
            g.wire("src", "tick", "n0", "tick.trigger")
        else:
            g.wire(f"n{i-1}", "tick", f"n{i}", "tick.trigger")
    world = make_world(g)
    start = time.perf_counter()
    for _ in range(epochs):
        world.run([Injection("src", "tick.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])
    elapsed = time.perf_counter() - start
    return elapsed / epochs


def fmt(seconds: float) -> str:
    if seconds < 0.001:
        return f"{seconds * 1e6:.0f} µs"
    if seconds < 1:
        return f"{seconds * 1e3:.1f} ms"
    return f"{seconds:.2f} s"


def main():
    print("事件索引基准（每 epoch 均值）")
    print("场景 1：高扇出 1 → N（20 epochs）")
    for n in (10, 100, 1000):
        print(f"  fanout N={n:<5} {fmt(bench_fanout(n))}")
    print("场景 2：APPEND 单端口累积 K 事件（20 epochs）")
    for k in (10, 100, 1000):
        print(f"  append K={k:<5} {fmt(bench_append(k))}")
    print("场景 3：长链深度 D（20 epochs）")
    for d in (1, 5, 10, 20):
        print(f"  depth D={d:<5} {fmt(bench_depth(d))}")


if __name__ == "__main__":
    main()
