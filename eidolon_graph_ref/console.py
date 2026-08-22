"""控制台渲染：把事件传递过程直接打印出来。

验证手段（用户裁定 2026-08-19）：本阶段不需要前端，事件传递过程经控制台
输出观察——事件有身份、记录谁生产谁消费，传播过程一目了然。

目标（《架构验证性重写》）：只看 Graph、输入、输出和 Trace，就能推断
内核正在执行什么。
"""

from __future__ import annotations

from .engine.timeline import Entry, Timeline, KIND_CONSUME, KIND_DELIVER, KIND_ERROR, KIND_FIRE, KIND_QUIESCE


def _short(payload) -> str:
    """载荷的紧凑表示。"""
    text = repr(payload)
    return text if len(text) <= 40 else text[:37] + "..."


def render_epoch(timeline: Timeline, run: int) -> str:
    """渲染一个 epoch 的时间线。"""
    lines = [f"── epoch {run} " + "─" * 60]
    for e in timeline.epoch_entries(run):
        lines.append(_render_entry(e))
    return "\n".join(lines)


def _render_entry(e: Entry) -> str:
    prefix = f"  {e.seq:>3}  {e.kind:<8}"
    if e.kind == KIND_DELIVER:
        src = e.src_node or "host"
        return f"{prefix}ev#{e.event_id:<3} {_short(e.payload):<42} {src}:{e.src_port or '-'} → {e.dst_node}.{e.dst_port}[{e.dst_slot}]"
    if e.kind == KIND_FIRE:
        consumed = ",".join(f"#{c}" for c in e.consumed) or "-"
        produced = ",".join(f"#{p}" for p in e.produced) or "-"
        return f"{prefix}{e.dst_node}.{e.group:<10} consumed [{consumed:<12}] produced [{produced}]"
    if e.kind == KIND_CONSUME:
        consumed = ",".join(f"#{c}" for c in e.consumed)
        msg = f"  ({e.message})" if e.message else ""
        return f"{prefix}{e.dst_node}.{e.dst_port} events [{consumed}]{msg}"
    if e.kind == KIND_ERROR:
        return f"{prefix}{e.dst_node}.{e.group}: {e.message}"
    if e.kind == KIND_QUIESCE:
        return f"{prefix}quiescence (队列排空，静止)"
    return f"{prefix}{e}"


def render_event_archive(timeline: Timeline) -> str:
    """事件档案总览：每个事件的身份、生产、投递与生命周期状态。"""
    lines = ["── event archive " + "─" * 56]
    if not timeline.events:
        lines.append("  (empty)")
        return "\n".join(lines)
    for ev in timeline.events.values():
        payload = _short(ev.payload)
        src = ev.producer or "host"
        dsts = ", ".join(f"{d.node}.{d.port}[{d.slot}]" for d in ev.deliveries) or "—"
        consumed = ",".join(f"#{s}" for s, _, _ in ev.consumed_by) or "—"
        lines.append(
            f"  ev#{ev.id:<3} [{ev.kind.value:<6}] {payload:<42} {src}:{ev.port or '-'}"
        )
        lines.append(f"        → {dsts}")
        lines.append(f"        status={ev.status:<8} consumed_by=[{consumed}]")
    return "\n".join(lines)


def render_state(observable: dict) -> str:
    """节点可观察状态：state 字段与端口状态。"""
    lines = ["── node state " + "─" * 60]
    for nid, view in observable.items():
        st = ", ".join(f"{k}={_short(v)}" for k, v in view["state"].items()) or "—"
        ports = []
        for name, p in view["data_in"].items():
            ports.append(f"{name}: v={_short(p['value'])} pending={p['pending']}")
        for name, p in view["trigger_in"].items():
            ports.append(f"{name}: pending={p['pending']} payload={_short(p['payload'])}")
        for name, p in view.get("signal_in", {}).items():
            ports.append(f"{name}: level={p['level']} pending={p['pending']}")
        lines.append(f"  {nid:<10} [{view['type']}] state: {st}")
        for port in ports:
            lines.append(f"    {port}")
    return "\n".join(lines)
