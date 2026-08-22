from __future__ import annotations
from collections import deque
from copy import deepcopy
from .event import Delivery, Event, Injection, Kind
from .node_semantics import NodeSemantics
from .protocol import GroupContext, GroupOutput
from .timeline import Entry, KIND_DELIVER, KIND_ERROR, KIND_FIRE, KIND_QUIESCE
class Executor:
 def __init__(self,types): self.types=types
 def run(self,inst,injections):
  inst.run_no+=1; queue=deque(); turns=set()
  for inj in injections: self._inject(inst,inj,queue)
  while queue: self._visit(inst,queue.popleft(),queue,turns)
  inst.timeline.record(Entry(run=inst.run_no,kind=KIND_QUIESCE))
 def _inject(self,inst,inj,queue):
  try: deepcopy(inj.payload)
  except Exception: raise ValueError("injection carries non-copyable payload")
  e=Event(inst.timeline.new_event_id(),inst.run_no,inj.kind,inj.payload,None,inj.port); inst.timeline.archive(e); self._deliver(inst,e,inj.node,inj.port,inj.slot,queue)
 def _visit(self,inst,nid,queue,turns):
  nt=self.types[inst.definition.nodes[nid].type]
  NodeSemantics.settle_control_signals(inst,nid)
  for g in nt.groups:
   if (nid,g.name) not in turns and NodeSemantics.group_ready(inst,nid,g): turns.add((nid,g.name)); self._fire(inst,nid,g,queue)
 def _fire(self,inst,nid,g,queue):
  nt=self.types[inst.definition.nodes[nid].type]; data=NodeSemantics.handler_arguments(inst,nid,g)
  ctx=GroupContext(g.name,data,deepcopy(inst.node_states[nid]),{**g.defaults,**inst.configs[nid]["groups"].get(g.name,{})},dict(inst.assets.get(nid,{})))
  try: out=g.handler(ctx) or GroupOutput()
  except Exception as e:
   inst.timeline.record(Entry(run=inst.run_no,kind=KIND_ERROR,dst_node=nid,group=g.name,message=f"{type(e).__name__}: {e}")); return
  consumed=NodeSemantics.consume_group(inst,nid,g)
  unknown=set(out.state)-set(nt.state_defaults)
  if unknown: inst.timeline.record(Entry(run=inst.run_no,kind=KIND_ERROR,dst_node=nid,group=g.name,message=f"handler wrote undeclared state fields: {sorted(unknown)}"))
  for k,v in out.state.items():
   if k not in unknown:
    try: deepcopy(v); inst.node_states[nid][k]=v
    except Exception: inst.timeline.record(Entry(run=inst.run_no,kind=KIND_ERROR,dst_node=nid,group=g.name,message=f"non-copyable state {k!r}"))
  fire=inst.timeline.record(Entry(run=inst.run_no,kind=KIND_FIRE,dst_node=nid,group=g.name,consumed=tuple(consumed))); produced=[]
  self._emit(inst,nid,g,out.data_out,Kind.DATA,queue,produced); self._emit(inst,nid,g,out.signal_out,Kind.SIGNAL,queue,produced); fire.produced=tuple(produced)
 def _emit(self,inst,nid,g,items,kind,queue,produced):
  nt=self.types[inst.definition.nodes[nid].type]; permitted=set(g.outputs); declared={p.name for p in (nt.data_out if kind is Kind.DATA else nt.signal_out)}
  for port,value in items.items():
   if port not in permitted or port not in declared:
    inst.timeline.record(Entry(run=inst.run_no,kind=KIND_ERROR,dst_node=nid,group=g.name,message=f"output {port!r} is not authorized by group")); continue
   try:
    if kind is Kind.DATA: deepcopy(value)
   except Exception: inst.timeline.record(Entry(run=inst.run_no,kind=KIND_ERROR,dst_node=nid,message=f"non-copyable output {port!r}")); continue
   e=Event(inst.timeline.new_event_id(),inst.run_no,kind,bool(value) if kind is Kind.SIGNAL else value,nid,port); inst.timeline.archive(e); produced.append(e.id)
   for w in inst.out_index.get((nid,port),[]): self._deliver(inst,e,w.dst_node,w.dst_port,w.dst_slot,queue)
 def _deliver(self,inst,e,nid,port,slot,queue):
  d=Delivery(e.id,nid,port,slot,inst.timeline.next_seq); e.deliveries.append(d); inst.timeline.record(Entry(run=e.run,kind=KIND_DELIVER,event_id=e.id,payload=e.payload,src_node=e.producer,src_port=e.port,dst_node=nid,dst_port=port,dst_slot=slot))
  NodeSemantics.receive(inst,e,nid,port,slot)
  queue.appendleft(nid)
