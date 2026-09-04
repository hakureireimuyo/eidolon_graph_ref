"""DSL 编译管道三阶段单元测试（REFACTOR_DSL_COMPILATION）。

提取(extract) → 解释(interpret) → 生成(generate) 每层独立可测：
- 提取层：纯机械，签名 → ParameterDeclaration/ReturnDeclaration
- 解释层：业务规则（角色映射、保留名、序列裁定、返回值语义）
- 生成层：IR 收集（端口表、GroupSpec、handler 包装器）
"""

import inspect

import pytest

from eidolon_dsl import (
    Append,
    Asset,
    Config,
    Gated,
    Signal,
    Trigger,
    _Marker,
    _compile_group,
    _GroupOpts,
    extract_parameters,
    generate_group_spec,
    generate_handler_wrapper,
    generate_ports,
    interpret_parameter,
    interpret_parameters,
    interpret_return,
    ParameterDeclaration,
    ReturnDeclaration,
)
from eidolon_graph_ref.engine.protocol import GroupContext
from eidolon_graph_ref.model.definition import DefinitionError
from eidolon_graph_ref.model.ports import APPEND, REPLACE
from eidolon_graph_ref.model.readiness import DATA


def _where(group="g") -> str:
    return f"Test.{group}"


def _decl(name, index=0, ann=None, default=None, has_default=False):
    return ParameterDeclaration(name, index, inspect.Parameter.POSITIONAL_OR_KEYWORD, ann, default, has_default)


# ---- 阶段 1：提取 -------------------------------------------------------------


def test_extract_parameters_mechanical():
    """提取层：签名 → 原始元数据（名称/位置/kind/标注/默认），零语义判断。"""

    def fn(this, trigger: Trigger, count: int = 1) -> int:
        pass

    params, ret = extract_parameters(fn, _where())
    assert [p.name for p in params] == ["this", "trigger", "count"]
    assert [p.index for p in params] == [0, 1, 2]
    assert params[0].type_hint is None  # this 无标注
    assert params[1].type_hint is Trigger
    assert params[2].type_hint is int
    assert params[2].has_default is True and params[2].default == 1
    assert params[0].has_default is False
    assert ret.annotation is int


def test_extract_parameters_empty_signature_rejected():
    """提取层唯一的结构性错误：空签名。"""

    def fn():
        pass

    with pytest.raises(DefinitionError, match="at least one parameter"):
        extract_parameters(fn, _where())


def test_extract_parameters_forward_ref_fallback():
    """字符串标注回退 __annotations__（get_type_hints 失败不阻断提取）。"""

    def fn(value: "int") -> None:
        pass

    params, _ = extract_parameters(fn, _where())
    assert params[0].type_hint is int or params[0].type_hint == "int"


# ---- 阶段 2：解释 -------------------------------------------------------------


def test_interpret_roles():
    """类型标注 → 语义角色：trigger/config/signal/data/append/gated/asset。"""
    cases = [
        (_decl("go", 0, Trigger), "trigger", "g.go"),
        (_decl("cfg", 0, Config), "config", ""),
        (_decl("s", 0, Signal), "signal", "g.s"),
        (_decl("x", 0, int), "data", "g.x"),
        (_decl("acc", 0, Append[list]), "append", "g.acc"),
        (_decl("v", 0, Gated[int, "gate"]), "gated", "g.v"),
        (_decl("cap", 0, Asset[object]), "asset", "g.cap"),
    ]
    for decl, role, port in cases:
        interp = interpret_parameter(decl, "g", _where())
        assert interp.role == role
        assert interp.port == port


def test_interpret_gated_carries_binding_and_default():
    interp = interpret_parameter(
        _decl("v", 0, _Marker(("gated", int, "gate")), 3, True), "g", _where()
    )
    assert interp.signal_binding == "gate"
    assert interp.default == 3


def test_interpret_gated_binding_must_be_string():
    with pytest.raises(DefinitionError, match="Gated binding must be a string"):
        interpret_parameter(_decl("v", 0, _Marker(("gated", int, 7))), "g", _where())


def test_interpret_asset_rejects_default():
    with pytest.raises(DefinitionError, match="takes no default"):
        interpret_parameter(_decl("cap", 0, Asset[object], 1, True), "g", _where())


def test_interpret_unknown_marker_rejected():
    with pytest.raises(DefinitionError, match="unknown annotation"):
        interpret_parameter(_decl("x", 0, _Marker(("mystery",))), "g", _where())


def test_interpret_this_position_and_annotation_rules():
    assert interpret_parameter(_decl("this", 0), "g", _where()).role == "this"
    with pytest.raises(DefinitionError, match="'this' must be the first parameter"):
        interpret_parameter(_decl("this", 1), "g", _where())
    with pytest.raises(DefinitionError, match="'this' takes no annotation"):
        interpret_parameter(_decl("this", 0, int), "g", _where())


def test_interpret_self_rejected():
    with pytest.raises(DefinitionError, match="instance receiver"):
        interpret_parameter(_decl("self", 0), "g", _where())


def test_interpret_sequence_rules():
    """序列裁定：特殊参数先于数据、特殊无默认、必填先于带默认。"""
    good = (
        _decl("this", 0),
        _decl("go", 1, Trigger),
        _decl("x", 2, int),
        _decl("y", 3, int, 1, True),
    )
    assert interpret_parameters(good, "g", _where())  # 不抛

    with pytest.raises(DefinitionError, match="must precede data inputs"):
        interpret_parameters((_decl("x", 0, int), _decl("go", 1, Trigger)), "g", _where())
    with pytest.raises(DefinitionError, match="takes no default"):
        interpret_parameters((_decl("go", 0, Trigger, True, True),), "g", _where())
    with pytest.raises(DefinitionError, match="must precede defaulted inputs"):
        interpret_parameters(
            (_decl("y", 0, int, 1, True), _decl("x", 1, int)), "g", _where()
        )


def test_interpret_return_kinds():
    """返回值语义：None / data / signal（Signal[bool] marker）。"""
    none_ret = interpret_return(ReturnDeclaration(None), _GroupOpts(), "g", _where())
    assert none_ret.out_kind is None and none_ret.data_names == () and none_ret.signal_names == ()

    data_ret = interpret_return(ReturnDeclaration(int), _GroupOpts(), "g", _where())
    assert data_ret.out_kind == "data"
    assert data_ret.data_names == ("g",) and data_ret.signal_names == ()

    signal_ret = interpret_return(ReturnDeclaration(_Marker(("signal_out", bool))), _GroupOpts(), "g", _where())
    assert signal_ret.out_kind == "signal"
    assert signal_ret.signal_names == ("g",) and signal_ret.data_names == ()


def test_interpret_return_outputs_and_signals_options():
    """outputs=/signals=：组限定端口名 + dict 协议未限定键；互斥与配套规则。"""
    ret = interpret_return(
        ReturnDeclaration(int),
        _GroupOpts(outputs=("a", "b"), signals=("s1",)),
        "g",
        _where(),
    )
    assert ret.data_names == ("g.a", "g.b")
    assert ret.signal_names == ("g.s1",)
    assert ret.data_keys == ("a", "b")
    assert ret.signal_keys == ("s1",)

    with pytest.raises(DefinitionError, match="signals= requires outputs="):
        interpret_return(ReturnDeclaration(int), _GroupOpts(signals=("s1",)), "g", _where())
    with pytest.raises(DefinitionError, match="outputs= requires a data return annotation"):
        interpret_return(
            ReturnDeclaration(_Marker(("signal_out", bool))),
            _GroupOpts(outputs=("a",)),
            "g",
            _where(),
        )


# ---- 阶段 3：生成 -------------------------------------------------------------


def test_generate_ports_tables():
    """端口表：data/signal/trigger/asset 各归其位，inputs 含未绑定信号。"""

    def fn(this, go: Trigger, s: Signal, x: int = 2, acc: Append[int] = None) -> None:
        pass

    where = _where()
    decls, ret = extract_parameters(fn, where)
    params = interpret_parameters(decls, "g", where)
    outputs = interpret_return(ret, _GroupOpts(), "g", where)
    ports = generate_ports(params, outputs, "g", _GroupOpts(), where)

    assert [p.name for p in ports.trigger_in] == ["g.go"]
    assert [p.name for p in ports.signal_in] == ["g.s"]
    assert [p.name for p in ports.data_in] == ["g.x", "g.acc"]
    assert ports.data_in[0].default == 2 and ports.data_in[0].cache == REPLACE
    assert ports.data_in[1].cache == APPEND
    assert ports.asset_in == ()
    assert ports.inputs == ("g.s", "g.x", "g.acc")  # 未绑定信号按数据输入（参数序）
    assert ports.triggers == ("g.go",)
    assert ports.data_out == () and ports.signal_out == ()


def test_generate_ports_gated_binding_rules():
    """Gated 绑定：目标必须是 Signal 参数；同一信号 1:1 门控。"""
    where = _where()

    def ok_fn(gate: Signal, v: Gated[int, "gate"]) -> int:
        pass

    decls, ret = extract_parameters(ok_fn, where)
    params = interpret_parameters(decls, "g", where)
    outputs = interpret_return(ret, _GroupOpts(), "g", where)
    ports = generate_ports(params, outputs, "g", _GroupOpts(), where)
    assert ports.data_in[0].signal == "g.gate"
    assert ports.inputs == ("g.v",)  # 已绑定信号不再是数据输入

    def bad_fn(v: Gated[int, "nope"]) -> int:
        pass

    decls, ret = extract_parameters(bad_fn, where)
    params = interpret_parameters(decls, "g", where)
    outputs = interpret_return(ret, _GroupOpts(), "g", where)
    with pytest.raises(DefinitionError, match="must reference a Signal parameter"):
        generate_ports(params, outputs, "g", _GroupOpts(), where)

    def double_fn(gate: Signal, v1: Gated[int, "gate"], v2: Gated[int, "gate"]) -> int:
        pass

    decls, ret = extract_parameters(double_fn, where)
    params = interpret_parameters(decls, "g", where)
    outputs = interpret_return(ret, _GroupOpts(), "g", where)
    with pytest.raises(DefinitionError, match="already gates"):
        generate_ports(params, outputs, "g", _GroupOpts(), where)


def test_generate_ports_trigger_option_exclusive():
    """trigger= 与 Trigger 参数互斥；trigger= 追加组限定 TriggerIn。"""
    where = _where()

    def fn(x: int) -> None:
        pass

    decls, ret = extract_parameters(fn, where)
    params = interpret_parameters(decls, "g", where)
    outputs = interpret_return(ret, _GroupOpts(), "g", where)
    ports = generate_ports(params, outputs, "g", _GroupOpts(trigger="go"), where)
    assert ports.triggers == ("g.go",)
    assert [p.name for p in ports.trigger_in] == ["g.go"]

    def clash_fn(go: Trigger) -> None:
        pass

    decls, ret = extract_parameters(clash_fn, where)
    params = interpret_parameters(decls, "g", where)
    outputs = interpret_return(ret, _GroupOpts(), "g", where)
    with pytest.raises(DefinitionError, match="mutually exclusive"):
        generate_ports(params, outputs, "g", _GroupOpts(trigger="go"), where)


def test_generate_group_spec_readiness_qualification():
    """GroupSpec：readiness 叶端口组限定（DATA("a") → DATA("g.a")）。"""
    where = _where()

    def fn(x: int) -> None:
        pass

    decls, ret = extract_parameters(fn, where)
    params = interpret_parameters(decls, "g", where)
    outputs = interpret_return(ret, _GroupOpts(), "g", where)
    ports = generate_ports(params, outputs, "g", _GroupOpts(readiness=DATA("x")), where)
    spec = generate_group_spec("g", ports, _GroupOpts(readiness=DATA("x")))
    assert spec.name == "g"
    assert spec.readiness.port == "g.x"  # 已组限定
    assert spec.outputs == ()


def test_generate_handler_wrapper_single_output_and_state():
    """包装器：参组织、单输出裸值、this 状态全量写回。"""

    def fn(this, x: int) -> int:
        this.count = x
        return this.count

    where = _where()
    decls, ret = extract_parameters(fn, where)
    params = interpret_parameters(decls, "g", where)
    outputs = interpret_return(ret, _GroupOpts(), "g", where)
    handler = generate_handler_wrapper(fn, params, outputs)

    ctx = GroupContext("g", {"g.x": 5}, {"count": 0}, {})
    out = handler(ctx)
    assert out.data_out == {"g": 5}
    assert out.state == {"count": 5}


def test_generate_handler_wrapper_dict_protocol():
    """多输出 dict 协议：键 = 声明成员名；缺失键 = 该端口无事件；未知键拒绝。"""

    def fn(a: int, b: int) -> dict:
        return {"out1": a, "out2": None}

    where = _where()
    opts = _GroupOpts(outputs=("out1", "out2"))
    decls, ret = extract_parameters(fn, where)
    params = interpret_parameters(decls, "g", where)
    outputs = interpret_return(ret, opts, "g", where)
    handler = generate_handler_wrapper(fn, params, outputs)

    out = handler(GroupContext("g", {"g.a": 1, "g.b": 2}, {}, {}))
    assert out.data_out == {"g.out1": 1, "g.out2": None}  # None 值合法载荷照发

    def partial(a: int, b: int) -> dict:
        return {"out1": a}

    decls, ret = extract_parameters(partial, where)
    params = interpret_parameters(decls, "g", where)
    outputs = interpret_return(ret, opts, "g", where)
    handler = generate_handler_wrapper(partial, params, outputs)
    out = handler(GroupContext("g", {"g.a": 1, "g.b": 2}, {}, {}))
    assert out.data_out == {"g.out1": 1}  # 缺失键 = 该端口本轮无事件

    def unknown(a: int, b: int) -> dict:
        return {"typo": a}

    decls, ret = extract_parameters(unknown, where)
    params = interpret_parameters(decls, "g", where)
    outputs = interpret_return(ret, opts, "g", where)
    handler = generate_handler_wrapper(unknown, params, outputs)
    with pytest.raises(TypeError, match="undeclared output"):
        handler(GroupContext("g", {"g.a": 1, "g.b": 2}, {}, {}))


def test_generate_handler_wrapper_state_ownership_boundary():
    """State→Data ownership 边界：输出与 state 对象同引用时输出侧复制。"""

    def fn(this, x: int) -> object:
        this.buf = []
        return this.buf

    where = _where()
    decls, ret = extract_parameters(fn, where)
    params = interpret_parameters(decls, "g", where)
    outputs = interpret_return(ret, _GroupOpts(), "g", where)
    handler = generate_handler_wrapper(fn, params, outputs)

    out = handler(GroupContext("g", {"g.x": 1}, {"buf": None}, {}))
    assert out.data_out["g"] is not out.state["buf"]  # 输出侧解除 alias
    assert out.data_out["g"] == []


def test_compile_group_stages_agree():
    """三阶段管道与公开编译路径一致：端口表 / spec / 包装器行为。"""

    def fn(this, go: Trigger, x: int = 1) -> int:
        this.n += x
        return this.n

    spec, wrapper, ports = _compile_group("Test", fn, _GroupOpts())
    assert spec.name == "fn"
    assert [p.name for p in ports.data_in] == ["fn.x"]
    assert [p.name for p in ports.trigger_in] == ["fn.go"]
    assert spec.inputs == ("fn.x",) and spec.triggers == ("fn.go",)
    out = wrapper(GroupContext("fn", {"fn.go": None, "fn.x": 2}, {"n": 10}, {}))
    assert out.data_out == {"fn": 12} and out.state == {"n": 12}
