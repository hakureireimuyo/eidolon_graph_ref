"""执行引擎：epoch = run(events)。

依据：graph-execution-model.md §4 + graph-port-capability-composition.md §3.5

```
宿主注入输入事件(按注入序入队) → 源节点按声明序播种
    → worklist 脏传播(投递即唤醒,深度优先,队列遍历非递归)
    → 访问时满足 Readiness 即执行、产出即时投递
    → 每组每轮至多一次(NodeTurn 预算),反馈环跨轮迭代
    → 队列排空即静止(quiescence)
```

关键裁定：
- Dirty ≠ Execute：Data/Signal 在调度层面完全对称，都不拥有"触发权"，
  只改变端口状态并唤醒节点；Readiness 决定是否执行
- 端口级资格槽：Readiness 需 Data.pending AND Qual.pending AND level==HIGH
  （D1/S1 配对）；LOW 的 pending 在访问时自我消费为控制状态更新(不产生有效组合)
- enable(节点级资格)是持续电平门控：Readiness 只看 level==HIGH；
  pending 仅触发重估，访问时消费
- 事件独立，无同因果组绑定；消费记录经 pending_events → fire.consumed
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from typing import TYPE_CHECKING

from ..model.graph import SLOT_DATA, SLOT_QUAL, SLOT_SIGNAL, SLOT_TRIGGER
from ..model.node_type import InputGroup, NodeType, Policy
from .event import Delivery, Event, Injection, Kind
from .protocol import TickContext, TickOutput
from .timeline import Entry, KIND_CONSUME, KIND_DELIVER, KIND_ERROR, KIND_FIRE, KIND_QUIESCE

if TYPE_CHECKING:
    from .instance import GraphInstance

# 源节点自走执行的合成组：无输入、无触发 → 真空为真，每 epoch 至多执行一次
SOURCE_GROUP = InputGroup(name="step", inputs=(), policy=Policy.ON_ALL_DATA_READY)


class Executor:
    def __init__(self, types: dict[str, NodeType]):
        self.types = types

    # ================================================================== run
    def run(self, inst: "GraphInstance", injections: list[Injection]) -> None:
        """推进一个 epoch：注入 → 播种 → 脏传播 → 静止。"""
        inst.run_no += 1
        run = inst.run_no
        tl = inst.timeline
        queue: deque[str] = deque()

        # 1. 注入目标按注入序入队（队尾，保持注入序）
        for inj in injections:
            self._inject(inst, inj, queue)

        # 2. 源节点按声明序播种（队尾）
        for nid in inst.definition.node_order():
            if self.types[inst.definition.nodes[nid].type].is_source:
                queue.append(nid)

        # 3. worklist 脏传播：深度优先（投递唤醒 appendleft 插队）
        turns: set[tuple[str, str]] = set()  # NodeTurn 预算：(节点, 组) 每轮至多一次
        while queue:
            nid = queue.popleft()
            self._visit(inst, nid, queue, turns)

        tl.record(Entry(run=run, kind=KIND_QUIESCE))

    # ================================================================== 注入
    def _inject(self, inst: "GraphInstance", inj: Injection, queue: deque[str]) -> None:
        """宿主注入 = 事件产生(producer=None) + 单次投递。与节点产出完全同构。"""
        tl = inst.timeline
        # 值域探针（宿主入口同样执行）：不可复制载荷 = 宿主编程错误，fail fast。
        # 只校验不复制：注入载荷以原对象进入数据平面（零拷贝）
        try:
            deepcopy(inj.payload)
        except Exception:
            raise ValueError(
                f"injection into {inj.node}.{inj.port} carries non-copyable payload: {type(inj.payload).__name__}"
            )
        ev = Event(
            id=tl.new_event_id(),
            run=inst.run_no,
            kind=inj.kind,
            payload=inj.payload,
            producer=None,
            port=inj.port,
        )
        tl.archive(ev)
        self._deliver(inst, ev, inj.node, inj.port, inj.slot, queue)

    # ================================================================== 访问
    def _visit(self, inst: "GraphInstance", nid: str, queue: deque[str], turns: set[tuple[str, str]]) -> None:
        tl = inst.timeline
        ntype = self.types[inst.definition.nodes[nid].type]

        # enable 通知消费（持续电平语义：pending 仅触发重估，访问即消费）
        for pname, es in inst.enable_states[nid].items():
            if es.pending:
                eids = tuple(es.pending_events)
                self._mark_consumed(inst, eids, nid, pname)
                es.pending = False
                es.pending_events = []
                tl.record(Entry(run=inst.run_no, kind=KIND_CONSUME, dst_node=nid, dst_port=pname, consumed=eids))

        if not self._enabled(inst, nid):
            return  # 门控 inactive：整节点不执行 → 无任何输出事件；数据照常接收缓存

        # 端口级资格槽：LOW 的 pending 自我消费为控制状态更新（LOW 不产生有效组合）
        for pname, qs in inst.qual_states[nid].items():
            if qs.pending and qs.level is not True:
                eids = tuple(qs.pending_events)
                self._mark_consumed(inst, eids, nid, pname)
                qs.pending = False
                qs.pending_events = []
                tl.record(
                    Entry(
                        run=inst.run_no,
                        kind=KIND_CONSUME,
                        dst_node=nid,
                        dst_port=pname,
                        consumed=eids,
                        message="qualification LOW: self-consume as control-state update",
                    )
                )

        # 按组声明序检查各组，每组至多执行一次；前组产出回连时可被后序组当轮可见。
        # 源节点 = 无输入组的节点：每 epoch 播种执行一次（合成组 step，真空为真）。
        groups = (SOURCE_GROUP,) if ntype.is_source else ntype.groups
        for group in groups:
            if (nid, group.name) in turns:
                continue
            if self._group_ready(inst, nid, group):
                turns.add((nid, group.name))
                self._fire(inst, nid, group, queue)

    # ================================================================== Readiness
    def _enabled(self, inst: "GraphInstance", nid: str) -> bool:
        es = inst.enable_states[nid]
        if not es:  # 未连接 = 条件恒成立（结构属性）
            return True
        return next(iter(es.values())).level is True

    def _group_ready(self, inst: "GraphInstance", nid: str, group) -> bool:
        # 事件驱动端口 = 已连接数据线，或曾收到注入（宿主注入目标与连线同为外部事件驱动）。
        # 纯静态端口（从未连接、从未注入）不参与触发、不消费。
        dyn = [p for p in group.inputs if inst.data_states[nid][p].event_driven]
        stat = [p for p in group.inputs if not inst.data_states[nid][p].event_driven]
        if group.policy is Policy.ON_ALL_DATA_READY:
            data_ok = all(self._data_port_ready(inst, nid, p) for p in dyn)
        elif group.policy is Policy.ON_ANY_DATA:
            data_ok = any(self._data_port_ready(inst, nid, p) for p in dyn)
        elif group.policy is Policy.ON_TRIGGER:
            data_ok = True  # 数据条件真空为真
        else:  # ON_DATA_AND_TRIGGER：数据齐 pending（无动态端口 = 真空为真）
            data_ok = all(self._data_port_ready(inst, nid, p) for p in dyn)
        # 静态端口 + 已连接资格槽 = 受控默认参数：LOW = 不具备参与资格 →
        # 组内全部数据端口都无资格时组不执行（无事实发生，SignalToData 的放行语义）；
        # 静态端口无 pending 可配对，取持续电平（level==HIGH 即可）
        if group.policy is not Policy.ON_ANY_DATA:
            data_ok = data_ok and all(self._static_qual_ok(inst, nid, p) for p in stat)
        if group.policy in (Policy.ON_TRIGGER, Policy.ON_DATA_AND_TRIGGER):
            trig_ok = any(inst.trigger_states[nid][t].pending for t in group.triggers)
        else:
            trig_ok = True
        return data_ok and trig_ok

    def _static_qual_ok(self, inst: "GraphInstance", nid: str, port: str) -> bool:
        """静态端口的资格：未连接资格槽恒成立；已连接 = 持续电平 HIGH。"""
        qs = inst.qual_states[nid].get(port)
        return qs is None or qs.level is True

    def _data_port_ready(self, inst: "GraphInstance", nid: str, port: str) -> bool:
        """端口 Readiness：pending AND 资格(已连接资格槽: pending AND level==HIGH；未连接恒成立)。"""
        ds = inst.data_states[nid][port]
        if not ds.pending:
            return False
        qs = inst.qual_states[nid].get(port)
        if qs is None:
            return True
        return qs.pending and qs.level is True

    # ================================================================== 执行
    def _fire(self, inst: "GraphInstance", nid: str, group, queue: deque[str]) -> None:
        ntype = self.types[inst.definition.nodes[nid].type]
        tl = inst.timeline

        data_in: dict[str, object] = {}
        for p in group.inputs:
            data_in[p] = self._effective(inst, nid, p)
        for t in group.triggers:
            ts = inst.trigger_states[nid][t]
            if ts.has_payload:
                data_in[t] = ts.payload

        ctx = TickContext(
            group=group.name,
            data_in=data_in,
            state=deepcopy(inst.node_states[nid]),
            config=inst.configs[nid],
            assets=dict(inst.assets.get(nid, {})),  # 浅拷贝：能力对象共享，tick 插入不影响节点 store
        )
        try:
            out = ntype.tick(ctx)
            if out is None:
                out = TickOutput()
        except Exception as exc:  # 节点异常：不产出任何输出 + 错误事件；pending 不消费(等待下次唤醒重试)
            msg = f"{type(exc).__name__}: {exc}"
            inst.log.append(f"[{inst.run_no}] {nid}.{group.name} {msg}")
            tl.record(Entry(run=inst.run_no, kind=KIND_ERROR, dst_node=nid, group=group.name, message=msg))
            return

        # 消费本轮 pending（执行后重新等待；缓存值保持）
        consumed = self._consume(inst, nid, group)

        # 状态提交（值域 = Value：可复制/可序列化，Capability 不得进入状态平面，
        # 2026-08-20 裁定。deepcopy 即校验：失败 → KIND_ERROR + 拒绝提交该字段）
        if out.state:
            unknown = set(out.state) - set(ntype.state_defaults)
            if unknown:
                msg = f"tick wrote undeclared state fields: {sorted(unknown)}"
                inst.log.append(f"[{inst.run_no}] {nid}.{group.name} {msg}")
                tl.record(Entry(run=inst.run_no, kind=KIND_ERROR, dst_node=nid, group=group.name, message=msg))
                out.state = {k: v for k, v in out.state.items() if k not in unknown}
            invalid: list[str] = []
            for field, value in out.state.items():
                try:
                    deepcopy(value)  # 值域探针：仅校验可复制性，传输保持零拷贝
                except Exception:
                    invalid.append(field)
            if invalid:
                msg = f"tick wrote non-copyable values to state fields: {sorted(invalid)}"
                inst.log.append(f"[{inst.run_no}] {nid}.{group.name} {msg}")
                tl.record(Entry(run=inst.run_no, kind=KIND_ERROR, dst_node=nid, group=group.name, message=msg))
            inst.node_states[nid].update({k: v for k, v in out.state.items() if k not in invalid})

        fire_entry = tl.record(
            Entry(
                run=inst.run_no,
                kind=KIND_FIRE,
                dst_node=nid,
                group=group.name,
                consumed=tuple(consumed),
            )
        )

        # 产出并即时投递
        produced: list[int] = []
        self._emit_data(inst, nid, out, queue, produced)
        self._emit_signal(inst, nid, out, queue, produced)
        fire_entry.produced = tuple(produced)

    def _effective(self, inst: "GraphInstance", nid: str, port: str):
        """有效值：资格 HIGH → cached(静态=配置默认/默认属性；动态=缓存值)；
        LOW 或资格未到 → 默认属性。"""
        ntype = self.types[inst.definition.nodes[nid].type]
        decl = ntype.port(port)
        ds = inst.data_states[nid][port]
        qs = inst.qual_states[nid].get(port)
        if qs is not None and qs.level is not True:
            return decl.default
        return ds.value if ds.has_value else decl.default

    def _consume(self, inst: "GraphInstance", nid: str, group) -> list[int]:
        """组执行后消费本轮 pending（value/level 保持）。返回被消费的事件 id。"""
        consumed: list[int] = []
        for p in group.inputs:
            ds = inst.data_states[nid][p]
            if ds.pending:
                eids = tuple(ds.pending_events)
                consumed.extend(eids)
                ds.pending_events = []
                ds.pending = False
                self._mark_consumed(inst, eids, nid, p)
            qs = inst.qual_states[nid].get(p)
            if qs is not None and qs.pending:
                eids = tuple(qs.pending_events)
                consumed.extend(eids)
                qs.pending_events = []
                qs.pending = False
                self._mark_consumed(inst, eids, nid, p)
        for t in group.triggers:
            ts = inst.trigger_states[nid][t]
            if ts.pending:
                eids = tuple(ts.pending_events)
                consumed.extend(eids)
                ts.pending_events = []
                ts.pending = False
                ts.has_payload = False
                ts.payload = None
                self._mark_consumed(inst, eids, nid, t)
        return consumed

    def _mark_consumed(self, inst: "GraphInstance", event_ids, node: str, port: str) -> None:
        """更新事件档案：该端口上未消费投递的 consumed_seq + 消费记录。"""
        seq = inst.timeline.next_seq  # 紧随其后的 fire/consume 条目的 seq
        for eid in event_ids:
            ev = inst.timeline.events.get(eid)
            if ev is None:
                continue
            for d in ev.deliveries:
                if d.node == node and d.port == port and d.consumed_seq is None:
                    d.consumed_seq = seq
            ev.consumed_by.append((seq, node, port))

    # ================================================================== 产出
    def _emit_data(self, inst: "GraphInstance", nid: str, out: TickOutput, queue: deque[str], produced: list[int]) -> None:
        ntype = self.types[inst.definition.nodes[nid].type]
        declared = {p.name for p in ntype.data_out}
        for port, value in out.data_out.items():
            if port not in declared:
                inst.timeline.record(
                    Entry(run=inst.run_no, kind=KIND_ERROR, dst_node=nid, message=f"undeclared data output {port!r}")
                )
                continue
            # 值域探针（Value/Capability 分类，2026-08-20 裁定）：不可复制 → 拒绝产出。
            # 只校验不复制：数据平面保持零拷贝（扇出共享载荷引用是锁定内核事实）
            try:
                deepcopy(value)
            except Exception:
                inst.timeline.record(
                    Entry(
                        run=inst.run_no,
                        kind=KIND_ERROR,
                        dst_node=nid,
                        message=f"data output {port!r} carries non-copyable value",
                    )
                )
                continue
            ev = Event(
                id=inst.timeline.new_event_id(), run=inst.run_no, kind=Kind.DATA, payload=value, producer=nid, port=port
            )
            inst.timeline.archive(ev)
            produced.append(ev.id)
            for wire in inst.out_index.get((nid, port), ()):
                self._deliver(inst, ev, wire.dst_node, wire.dst_port, wire.dst_slot, queue)

    def _emit_signal(self, inst: "GraphInstance", nid: str, out: TickOutput, queue: deque[str], produced: list[int]) -> None:
        ntype = self.types[inst.definition.nodes[nid].type]
        if not out.signal_out:
            return
        # 写必须声明(2026-08-21 修订,废除"数据节点永不写信号"类别):
        # data_out / signal_out 任意组合声明,产出未声明端口 = KIND_ERROR。
        declared = {p.name for p in ntype.signal_out}
        for port, level in out.signal_out.items():
            if port not in declared:
                inst.timeline.record(
                    Entry(run=inst.run_no, kind=KIND_ERROR, dst_node=nid, message=f"undeclared signal output {port!r}")
                )
                continue
            ev = Event(
                id=inst.timeline.new_event_id(),
                run=inst.run_no,
                kind=Kind.SIGNAL,
                payload=bool(level),
                producer=nid,
                port=port,
            )
            inst.timeline.archive(ev)
            produced.append(ev.id)
            for wire in inst.out_index.get((nid, port), ()):
                self._deliver(inst, ev, wire.dst_node, wire.dst_port, wire.dst_slot, queue)

    # ================================================================== 投递
    def _deliver(self, inst: "GraphInstance", ev: Event, dst_node: str, dst_port: str, dst_slot: str, queue: deque[str]) -> None:
        """投递 = 一次下游端口状态更新 + 唤醒（深度优先，插队即时结算）。

        Delivery 是投递动作的事实：先于端口状态更新创建、先于"到达即消费"
        的记录入档，保证时间线因果序（deliver → consume）与 Delivery 生命
        周期一致——LOW 自消费路径也必须能标记到本次投递。
        """
        delivery = Delivery(
            event_id=ev.id, node=dst_node, port=dst_port, slot=dst_slot, seq=inst.timeline.next_seq
        )
        ev.deliveries.append(delivery)
        inst.timeline.record(
            Entry(
                run=ev.run,
                kind=KIND_DELIVER,
                event_id=ev.id,
                payload=ev.payload,
                src_node=ev.producer,
                src_port=ev.port,
                dst_node=dst_node,
                dst_port=dst_port,
                dst_slot=dst_slot,
            )
        )
        if dst_slot == SLOT_DATA:
            ds = inst.data_states[dst_node][dst_port]
            ds.receive(ev)
            # LOW 不产生有效组合（文档 §4 序列 B）：资格为 LOW 时到达的数据
            # 照常缓存（不变量 5），但其自身的 pending 即刻消费——不与后续
            # Signal 形成配对。注意只消费本次投递的事件，此前 HIGH 期间到达的
            # 陈旧 pending 保留（序列 A 推导：D2 pending 保留、值被 D3 覆盖）。
            qs = inst.qual_states[dst_node].get(dst_port)
            if qs is not None and qs.level is False and ev.id in ds.pending_events:
                ds.pending_events.remove(ev.id)
                if not ds.pending_events:
                    ds.pending = False
                self._mark_consumed(inst, (ev.id,), dst_node, dst_port)
                inst.timeline.record(
                    Entry(
                        run=ev.run,
                        kind=KIND_CONSUME,
                        dst_node=dst_node,
                        dst_port=dst_port,
                        consumed=(ev.id,),
                        message="data delivered while qualification LOW: no valid combination",
                    )
                )
        elif dst_slot == SLOT_QUAL:
            inst.qual_states[dst_node][dst_port].receive(ev)
        elif dst_slot == SLOT_TRIGGER:
            inst.trigger_states[dst_node][dst_port].receive(ev)
        elif dst_slot == SLOT_SIGNAL:
            inst.enable_states[dst_node][dst_port].receive(ev)
        else:
            raise ValueError(f"unknown slot {dst_slot!r}")
        queue.appendleft(dst_node)
