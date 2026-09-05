"""Readiness 与 Gated 语义审计(语义闭包,2026-09-05)。

把 graph-group-protocol.md §5/§6 与裁定 14/15/16 的语义在 **DSL 编译层**
冻结为可执行断言:

- Gated = 数据来源选择(源选择),不是门控执行、不是 validity 判定
- SignalIn↔DataIn 严格 1:1;DSL 仅允许**同组**绑定
- 显式 readiness 引用端口 ⊆ 组 inputs ∪ triggers
- 缺省 readiness 由内核推导,DSL 不物化

运行期模式规则(LOW=静态不等待/HIGH=动态必须等待)由既有语义测试冻结,
本文件只锁编译层判定。缺口与裁定见 docs/readiness-gated-spec.md。
"""

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eidolon_dsl import DefinitionError, compile_dsl
from eidolon_graph_ref.model.node_type import NodeType

_HEADER = textwrap.dedent(
    """
    from typing import Annotated
    from eidolon_dsl import NodeDefinition, group, Trigger, Signal, Gated, Config, DATA, TRIGGER, ALL, ANY
    """
)


def _compile(body: str, name: str = "N") -> NodeType:
    return compile_dsl(_HEADER + textwrap.dedent(body), name)


# ---- Gated:审计问题 ①②③④(binding 存在性 / 同组性 / 1:1)----


def test_gated_binding_must_reference_a_signal_parameter():
    """binding 指向数据参数(非 Signal)时编译期拒绝。"""
    with pytest.raises(DefinitionError, match="must reference a Signal parameter"):
        _compile(
            """
            class N(NodeDefinition):
                @group
                def g(x: int, y: Gated[int, "x"]) -> int:
                    return y
            """
        )


def test_gated_binding_to_unknown_name_rejected():
    """binding 指向不存在的参数时编译期拒绝。"""
    with pytest.raises(DefinitionError, match="must reference a Signal parameter"):
        _compile(
            """
            class N(NodeDefinition):
                @group
                def g(y: Gated[int, "nope"]) -> int:
                    return y
            """
        )


def test_gated_cross_group_binding_rejected():
    """IR 层面允许 DataIn.signal 引用节点级任意 SignalIn,但 DSL 冻结为
    仅同组绑定——跨组(无论限定与否)一律编译期拒绝(矩阵 R1)。"""
    for binding in ('"gate"', '"a.gate"'):
        with pytest.raises(DefinitionError, match="must reference a Signal parameter"):
            _compile(
                f"""
                class N(NodeDefinition):
                    @group
                    def a(gate: Signal) -> int:
                        return 1

                    @group
                    def b(x: Gated[int, {binding}]) -> int:
                        return x
                """
            )


def test_one_signal_binds_at_most_one_data():
    """裁定 15:SignalIn↔DataIn 严格一对一——同一信号门控两个数据输入
    即编译期拒绝。"""
    with pytest.raises(DefinitionError, match="already gates"):
        _compile(
            """
            class N(NodeDefinition):
                @group
                def g(gate: Signal, x: Gated[int, "gate"], y: Gated[int, "gate"]) -> int:
                    return x + y
            """
        )


def test_unbound_signal_is_plain_data_input():
    """裁定 15/§14-1:未绑定 SignalIn 按数据处理——进入组 inputs。"""
    nt = _compile(
        """
        class N(NodeDefinition):
            @group
            def g(level: Signal) -> bool:
                return level
        """
    )
    assert [p.name for p in nt.signal_in] == ["g.level"]
    assert nt.group("g").inputs == ("g.level",)


def test_gated_port_enters_group_inputs_with_signal_binding():
    """Gated 数据端口:进入组 inputs,DataIn.signal 绑定同组 SignalIn。"""
    nt = _compile(
        """
        class N(NodeDefinition):
            @group
            def g(gate: Signal, x: Gated[int, "gate"]) -> int:
                return x
        """
    )
    assert nt.group("g").inputs == ("g.x",)
    assert nt.port("g.x").signal == "g.gate"


# ---- Readiness:显式谓词引用边界 ----


def test_readiness_referencing_other_group_port_rejected():
    """显式 readiness 引用端口 ⊆ 本组 inputs∪triggers(NodeType 不变式)。"""
    with pytest.raises(DefinitionError, match="references non-group port"):
        _compile(
            """
            class N(NodeDefinition):
                @group
                def a(x: int) -> int:
                    return x

                @group(readiness=DATA("a.x"))
                def b(y: int) -> int:
                    return y
            """
        )


def test_readiness_referencing_unknown_port_rejected():
    with pytest.raises(DefinitionError, match="references non-group port"):
        _compile(
            """
            class N(NodeDefinition):
                @group(readiness=DATA("zzz"))
                def g(x: int) -> int:
                    return x
            """
        )


def test_readiness_custom_predicate_rejected_by_dsl():
    """IR 的 Readiness 是开放 Protocol,但 DSL 的组限定器只认识
    DATA/TRIGGER/ALL/ANY——自定义谓词编译期拒绝(矩阵 R2)。"""
    with pytest.raises(DefinitionError, match="unsupported readiness predicate"):
        _compile(
            """
            class _Custom:
                def evaluate(self, data, trigger):
                    return True

                def explain(self, data, trigger):
                    return "custom"

                def requires_port_pending(self, port):
                    return False

                def referenced_ports(self):
                    return set()

            class N(NodeDefinition):
                @group(readiness=_Custom())
                def g(x: int) -> int:
                    return x
            """,
        )


def test_readiness_builtin_qualified_to_group_ports():
    """内置谓词叶端口自动组限定:DATA("x") → DATA("g.x")。"""
    nt = _compile(
        """
        class N(NodeDefinition):
            @group(readiness=ALL(DATA("x"), TRIGGER("go")))
            def g(go: Trigger, x: int) -> int:
                return x
        """
    )
    pred = nt.group("g").readiness
    assert pred.referenced_ports() == {"g.x", "g.go"}


def test_default_readiness_not_materialized_by_dsl():
    """缺省 readiness 由内核推导(ALL(data) ∧ ANY(triggers)),
    DSL 不物化——组 readiness 保持 None(裁定 11/16 语义不变)。"""
    nt = _compile(
        """
        class N(NodeDefinition):
            @group
            def g(go: Trigger, x: int) -> int:
                return x
        """
    )
    assert nt.group("g").readiness is None


# ---- Annotated 形式的冻结 ----


def test_signalmarker_return_annotation_produces_signal_out():
    """-> Annotated[bool, SignalMarker()] = 信号输出(新 Annotated 形式)。"""
    nt = _compile(
        """
        from eidolon_dsl import SignalMarker
        class N(NodeDefinition):
            @group
            def g(x: int) -> Annotated[bool, SignalMarker()]:
                return x > 0
        """
    )
    assert [p.name for p in nt.signal_out] == ["g"]
    assert [p.name for p in nt.data_out] == []


def test_signal_inner_type_not_validated():
    """Signal 内型为文档性注解,编译器不校验 bool——电平在运行期恒为
    bool(矩阵 R5,冻结当前行为)。"""
    nt = _compile(
        """
        from eidolon_dsl import SignalMarker
        class N(NodeDefinition):
            @group
            def g(level: Annotated[int, SignalMarker()]) -> int:
                return 0
        """
    )
    assert [p.name for p in nt.signal_in] == ["g.level"]


# ---- 语义缺口候选(矩阵 R6,冻结当前行为,待内核裁定)----


def test_readiness_only_group_with_all_compiles():
    """@group(readiness=ALL()) 可构造"永真组"(无输入/无触发器/永真谓词,
    仅 cfg 参数)。当前 DSL 接受;与裁定 9/16 的"永远 ready 契约禁止借壳"
    精神存在张力,待内核裁定后收紧或明确合法化——冻结当前行为。"""
    nt = _compile(
        """
        class N(NodeDefinition):
            @group(readiness=ALL())
            def g(cfg: Config) -> int:
                return 1
        """
    )
    assert nt.group("g").readiness is not None
    assert nt.group("g").readiness.referenced_ports() == set()
    assert nt.group("g").inputs == () and nt.group("g").triggers == ()
