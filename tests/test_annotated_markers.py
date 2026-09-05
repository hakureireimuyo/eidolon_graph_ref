"""Annotated marker 非法用法断言(docs/annotated-markers.md 的可检验形态)。

冻结 marker 语义边界:合法位置 / 非法用法 / 编译器错误消息。
"""

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eidolon_dsl import DefinitionError, compile_dsl

_HEADER = textwrap.dedent(
    """
    from typing import Annotated
    from eidolon_dsl import (
        NodeDefinition, group, Trigger, Config, Signal, Gated, Append, Asset,
        StateMarker, TriggerMarker, SignalMarker, GatedMarker, AppendMarker, AssetMarker,
    )
    """
)


def _compile(body: str, name: str = "N"):
    return compile_dsl(_HEADER + textwrap.dedent(body), name)


def test_state_marker_as_parameter_rejected():
    """State 只能声明为类字段;作参数 → unknown annotation。"""
    with pytest.raises(DefinitionError, match="unknown annotation"):
        _compile(
            """
            class N(NodeDefinition):
                @group
                def g(x: Annotated[int, StateMarker()]) -> int:
                    return x
            """
        )


def test_gated_marker_signal_must_be_string():
    with pytest.raises(DefinitionError, match="Gated binding must be a string"):
        _compile(
            """
            class N(NodeDefinition):
                @group
                def g(gate: Signal, x: Annotated[int, GatedMarker(7)]) -> int:
                    return x
            """
        )


def test_asset_marker_rejects_default():
    with pytest.raises(DefinitionError, match="takes no default"):
        _compile(
            """
            class N(NodeDefinition):
                @group
                def g(cap: Annotated[object, AssetMarker()] = None) -> int:
                    return 1
            """
        )


def test_old_and_new_forms_compile_identically():
    """旧形式与新 Annotated 形式语义等价——编译产物逐字段一致。"""
    old_form = _compile(
        """
        class N(NodeDefinition):
            @group
            def g(gate: Signal, x: Gated[int, "gate"]) -> int:
                return x
        """
    )
    new_form = _compile(
        """
        class N(NodeDefinition):
            @group
            def g(gate: Annotated[bool, SignalMarker()], x: Annotated[int, GatedMarker("gate")]) -> int:
                return x
        """
    )
    assert [p.name for p in old_form.signal_in] == [p.name for p in new_form.signal_in]
    assert [(p.name, p.signal, p.cache) for p in old_form.data_in] == [
        (p.name, p.signal, p.cache) for p in new_form.data_in
    ]
    assert old_form.group("g").inputs == new_form.group("g").inputs


def test_non_state_class_field_annotation_ignored():
    """类字段只有 State 注解成为 state;其余注解字段被忽略(冻结行为)。"""
    nt = _compile(
        """
        class N(NodeDefinition):
            note: Annotated[int, TriggerMarker()] = 0

            @group
            def g(x: int) -> int:
                return x
        """
    )
    assert nt.state_defaults == {}
    assert [p.name for p in nt.trigger_in] == []


def test_non_signal_marker_on_return_treated_as_data():
    """返回值只识别 SignalMarker;其余 marker 按普通数据注解处理(冻结行为)。"""
    nt = _compile(
        """
        class N(NodeDefinition):
            @group
            def g(x: int) -> Annotated[list, AppendMarker()]:
                return [x]
        """
    )
    assert [p.name for p in nt.data_out] == ["g"]
    assert [p.name for p in nt.signal_out] == []


def test_no_config_marker_exists():
    """Config 通道只经裸 Config 参数表达——Annotated 元数据里无 ConfigMarker。"""
    nt = _compile(
        """
        class N(NodeDefinition):
            @group
            def g(cfg: Config, x: int) -> int:
                return x
        """
    )
    assert nt.group("g").inputs == ("g.x",)
    assert [p.name for p in nt.data_in] == ["g.x"]
