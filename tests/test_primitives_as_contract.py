"""Primitives 作为 DSL 合约测试（语义闭包自举验证,2026-09-05）。

把 eidolon_primitives/nodes.py 的**源码**经统一入口 ``compile_dsl`` 编译——
与外部节点包走完全相同的 exec 前端路径——断言编译产物 NodeType 的
语义字段与预期契约一致。预期契约以 graph-group-protocol.md §11 表格
为权威来源。

若 DSL 无法表达某个 primitive 的语义,本文件立即失败,缺口记入
docs/semantic-closure-matrix.md。
"""

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eidolon_dsl import compile_dsl
from eidolon_graph_ref.model.ports import APPEND, REPLACE
from eidolon_primitives import PRIMITIVES

_PRIMITIVES_SOURCE = (
    Path(__file__).resolve().parent.parent / "eidolon_primitives" / "nodes.py"
).read_text(encoding="utf-8")

PRIMITIVE_NAMES = (
    "Source",
    "Constant",
    "Sink",
    "Probe",
    "Buffer",
    "Join",
    "Split",
    "Latch",
    "DataToSignal",
    "SignalToData",
)


def compile_primitive(name):
    return compile_dsl(_PRIMITIVES_SOURCE, name)


def _project(nt):
    """确定性语义投影:只比较语义字段(handler 函数体 / 源码位置除外)。"""
    return {
        "name": nt.name,
        "data_in": [(p.name, p.cache, p.signal, p.default) for p in nt.data_in],
        "trigger_in": [p.name for p in nt.trigger_in],
        "signal_in": [p.name for p in nt.signal_in],
        "data_out": [p.name for p in nt.data_out],
        "signal_out": [p.name for p in nt.signal_out],
        "asset_in": [(p.name, p.type) for p in nt.asset_in],
        "state_defaults": dict(nt.state_defaults),
        "init_defaults": dict(nt.init_defaults),
        "groups": [
            (g.name, tuple(g.inputs), tuple(g.triggers), tuple(g.outputs),
             dict(g.defaults), None if g.readiness is None else repr(g.readiness))
            for g in nt.groups
        ],
        "tags": tuple(nt.tags),
        "has_init": nt.init is not None,
        "doc": None if nt.doc is None else nt.doc.summary,
    }


# 预期契约(graph-group-protocol.md §11 表格 + nodes.py 源码)。
# DataIn 四元组 = (name, cache, signal, default);缓存缺省 REPLACE。
_EXPECTED = {
    "Source": {
        "name": "Source",
        "data_in": [],
        "trigger_in": ["tick.trigger"],
        "signal_in": [],
        "data_out": ["tick"],
        "signal_out": [],
        "asset_in": [],
        "state_defaults": {"count": 0},
        "init_defaults": {},
        "groups": [("tick", (), ("tick.trigger",), ("tick",), {"step": 1}, None)],
        "tags": (),
        "has_init": False,
        "doc": None,
    },
    "Constant": {
        "name": "Constant",
        "data_in": [],
        "trigger_in": ["tick.trigger"],
        "signal_in": [],
        "data_out": ["tick"],
        "signal_out": [],
        "asset_in": [],
        "state_defaults": {},
        "init_defaults": {},
        "groups": [("tick", (), ("tick.trigger",), ("tick",), {"value": 0}, None)],
        "tags": (),
        "has_init": False,
        "doc": None,
    },
    "Sink": {
        "name": "Sink",
        "data_in": [("consume.value", REPLACE, None, None)],
        "trigger_in": [],
        "signal_in": [],
        "data_out": [],
        "signal_out": [],
        "asset_in": [],
        "state_defaults": {"last": None},
        "init_defaults": {},
        "groups": [("consume", ("consume.value",), (), (), {}, None)],
        "tags": (),
        "has_init": False,
        "doc": None,
    },
    "Probe": {
        "name": "Probe",
        "data_in": [("observe.value", REPLACE, None, None)],
        "trigger_in": [],
        "signal_in": [],
        "data_out": [],
        "signal_out": [],
        "asset_in": [],
        "state_defaults": {"log": []},
        "init_defaults": {},
        "groups": [("observe", ("observe.value",), (), (), {}, None)],
        "tags": (),
        "has_init": False,
        "doc": None,
    },
    "Buffer": {
        "name": "Buffer",
        "data_in": [("put.item", APPEND, None, None)],
        "trigger_in": ["flush.trigger"],
        "signal_in": [],
        "data_out": ["flush"],
        "signal_out": [],
        "asset_in": [],
        "state_defaults": {"items": []},
        "init_defaults": {},
        "groups": [
            ("put", ("put.item",), (), (), {}, None),
            ("flush", (), ("flush.trigger",), ("flush",), {}, None),
        ],
        "tags": (),
        "has_init": False,
        "doc": None,
    },
    "Join": {
        "name": "Join",
        "data_in": [("join.a", REPLACE, None, None), ("join.b", REPLACE, None, None)],
        "trigger_in": [],
        "signal_in": [],
        "data_out": ["join"],
        "signal_out": [],
        "asset_in": [],
        "state_defaults": {},
        "init_defaults": {},
        "groups": [("join", ("join.a", "join.b"), (), ("join",), {}, None)],
        "tags": (),
        "has_init": False,
        "doc": None,
    },
    "Split": {
        "name": "Split",
        "data_in": [("fan.value", REPLACE, None, None)],
        "trigger_in": [],
        "signal_in": [],
        "data_out": ["fan.out1", "fan.out2"],
        "signal_out": [],
        "asset_in": [],
        "state_defaults": {},
        "init_defaults": {},
        "groups": [("fan", ("fan.value",), (), ("fan.out1", "fan.out2"), {}, None)],
        "tags": (),
        "has_init": False,
        "doc": None,
    },
    "Latch": {
        "name": "Latch",
        "data_in": [("release.data", REPLACE, "release.gate", None)],
        "trigger_in": ["release.trigger"],
        "signal_in": ["release.gate"],
        "data_out": ["release"],
        "signal_out": [],
        "asset_in": [],
        "state_defaults": {},
        "init_defaults": {},
        "groups": [("release", ("release.data",), ("release.trigger",), ("release",), {}, None)],
        "tags": (),
        "has_init": False,
        "doc": None,
    },
    "DataToSignal": {
        "name": "DataToSignal",
        "data_in": [("convert.data", REPLACE, None, None)],
        "trigger_in": [],
        "signal_in": [],
        "data_out": [],
        "signal_out": ["convert"],
        "asset_in": [],
        "state_defaults": {},
        "init_defaults": {},
        "groups": [("convert", ("convert.data",), (), ("convert",), {"mode": "truthy", "threshold": 0}, None)],
        "tags": (),
        "has_init": False,
        "doc": None,
    },
    "SignalToData": {
        "name": "SignalToData",
        "data_in": [("pass_value.x", REPLACE, "pass_value.gate", None)],
        "trigger_in": ["pass_value.pass"],
        "signal_in": ["pass_value.gate"],
        "data_out": ["pass_value"],
        "signal_out": [],
        "asset_in": [],
        "state_defaults": {},
        "init_defaults": {},
        "groups": [("pass_value", ("pass_value.x",), ("pass_value.pass",), ("pass_value",), {}, None)],
        "tags": (),
        "has_init": False,
        "doc": None,
    },
}


def test_contract_table_covers_all_primitive_definitions():
    """防漂移:本表与包导出的 PRIMITIVES 注册表必须一一对应。"""
    assert set(PRIMITIVES) == set(PRIMITIVE_NAMES)
    assert set(_EXPECTED) == set(PRIMITIVE_NAMES)


@pytest.mark.parametrize("name", PRIMITIVE_NAMES)
def test_primitive_compiles_to_expected_contract(name):
    """每个内置节点经 exec 前端编译后,语义投影与预期契约完全一致。"""
    nt = compile_primitive(name)
    assert _project(nt) == _EXPECTED[name]


# ---- 闭包冻结:IR 字段在 DSL 中的可表达性(未文档化能力的回归锁定)----
# 这些形态此前"碰巧可用"但无文档、无测试;本组测试把它们升格为冻结契约,
# 对应 docs/semantic-closure-matrix.md 发现 R4。

_INIT_SRC = textwrap.dedent(
    """
    from typing import Annotated
    from eidolon_dsl import NodeDefinition, group, StateMarker, DocSpec

    class Boot(NodeDefinition):
        type_name = "bootnode"
        init_defaults = {"seed": 7}

        value: Annotated[int, StateMarker()] = 0

        @staticmethod
        def init(ctx):
            return {"value": ctx.config["seed"]}

        @staticmethod
        def tags():
            return ("category:demo",)

        @staticmethod
        def doc():
            return DocSpec("boot 节点", sections=())

        @group
        def tick(this, x: int) -> int:
            this.value = x
            return this.value
    """
)


def test_init_hook_type_name_and_metadata_expressible():
    """init / init_defaults / type_name / tags / doc 均可经 DSL 表达并进入 IR。"""
    nt = compile_dsl(_INIT_SRC, "Boot")
    assert nt.name == "bootnode"
    assert nt.init_defaults == {"seed": 7}
    assert nt.init is not None and callable(nt.init)
    assert nt.tags == ("category:demo",)
    assert nt.doc is not None and nt.doc.summary == "boot 节点"
    assert nt.state_defaults == {"value": 0}


def test_init_signature_is_ctx_only():
    """init 的调用形态与内核约定一致:单 ctx 参数。"""
    import inspect

    nt = compile_dsl(_INIT_SRC, "Boot")
    params = tuple(inspect.signature(nt.init).parameters.values())
    assert len(params) == 1 and params[0].default is inspect.Parameter.empty
