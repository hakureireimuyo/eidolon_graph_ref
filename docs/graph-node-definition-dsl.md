# 节点定义语言 v2(函数签名即组协议)

> 状态:已裁定(2026-08-22,原型验证 14/14 通过)
>
> 文档角色:**本文件冻结 DSL 的语义,不冻结实现**。原型实现为
> [eidolon_dsl.py](../eidolon_dsl.py),验证为
> [tests/test_dsl_prototype.py](../tests/test_dsl_prototype.py);
> 内核 ABI 以 [graph-node-protocol.md](./graph-node-protocol.md) 为准。
>
> 相关:定义语言现状 [graph-node-protocol.md](./graph-node-protocol.md) §2.0;
> 本 DSL 是其函数式前端——编译产物仍为 NodeType IR,内核零改动、零感知。

## 1. 定位与总原则

### 1.1 定位

`GroupSpec + handler="name"` 形式把同一件事拆成四处声明(组名、输入、
输出、handler 名),且全部是裸字符串。v2 让**函数签名成为组协议的主要
载体**,装饰器只声明无法从签名推导的信息:

```python
class Counter(NodeDefinition):
    count: State[int] = 0

    @group
    def tick(this, trigger: Trigger, step: int = 1) -> int:
        this.count += step
        return this.count
```

编译器读出:组 `tick`、触发器 `tick.trigger`、数据输入 `tick.step`
(fallback 1)、数据输出 `tick`、状态字段 `count`。

### 1.2 总原则:借用 Python 语法,不继承 Python 语义

DSL 是嵌入 Python 的一门小型语言,Python 只承载语法与编译工具。因此:

- `@group` 函数**不是可调用的 Python 方法**,编译后由内核经 wrapper 调用;
- `def add(a: int, b: int = 0)` 中 `= 0` 是 **DataIn fallback 声明**,
  不是 Python 调用可选参数——`add(1)` 这种 Python 调用行为没有意义;
- `this` 不是 Python 实例,`self` 被编译期拒绝;
- 端口参数不知道、也不在乎事件来自哪里——连接关系完全属于 Graph。

```text
Node Definition 定义"我能做什么"    Graph 定义"谁和谁发生关系"
Runtime 定义"什么时候发生"          Event 定义"这一次发生了什么"
```

### 1.3 语言分层

```text
Python 函数签名(语法承载)
        ↓  DSL 编译器(metaclass,类创建时)
     GroupSpec(中间表示构造)
        ↓  现有编译管线(NodeDefinitionMeta._compile → _type_errors)
     NodeType(内核 ABI)
        ↓
     GraphInstance / Executor(内核,零改动)
```

NodeType 的身份是**语义 IR**:DSL 的编译目标、内核的唯一输入、编辑器与
运行时共享的序列化契约——运行时执行的是语义,不是语法;任何存在于 DSL
与 NodeType 之间的东西必须具有独立的语义职责(graph-node-protocol.md §1.0)。

## 2. 语法规范

### 2.1 注解词汇表

| 注解 | 位置 | 语义 | 编译产物 |
|---|---|---|---|
| `T`(普通类型) | 参数 | 组数据输入 | `DataIn("{group}.{name}")` |
| `Trigger` | 参数 | 组激活条件 | `TriggerIn` + 组 triggers |
| `Config` | 参数 | 组配置访问(合并 `@group(defaults)` + 图配置) | 无端口,参数值 = `ctx.config` |
| `Signal` | 参数 | 信号依赖(未绑定时可作数据输入) | `SignalIn` |
| `Gated[T, "gate"]` | 参数 | 数据有效性由 gate 信号参与解释 | `DataIn(signal="...")` |
| `Append[T]` | 参数 | 累积型输入 | `DataIn(cache=APPEND)` |
| `Asset[T]` | 参数 | 资产依赖(节点级声明) | `AssetIn(name, T)` |
| `State[T] = v` | 类属性 | 节点级状态 | `state_defaults` |
| `-> T` | 返回值 | 数据输出(名 = 组名) | `DataOut("{group}")` |
| `-> None` | 返回值 | 无输出事件 | 无 |
| `-> Signal[bool]` | 返回值 | 信号输出 | `SignalOut("{group}")` |

### 2.2 `@group` 装饰器参数

```python
@group(readiness=ANY("a", "b"))     # 缺省 = ALL(数据输入) ∧ ANY(触发器)
@group(defaults={"mode": "truthy"}) # 组 config 缺省(图可覆写,≠ 参数默认值)
@group(outputs=("out1", "out2"))    # 多输出扩展(必须配 tuple 返回);端口按裁定 2
                                    # 限定为 "fan.out1"/"fan.out2"
@group(trigger="pass")              # 触发器端口名与参数名解耦(撞关键字等)
```

### 2.3 `this` —— 受限状态视图

```python
class Counter(NodeDefinition):
    count: State[int] = 0

    @group
    def tick(this, trigger: Trigger, step: int = 1) -> int:
        this.count += step    # 读快照 + 记整值 delta
        return this.count
```

| 用法 | 行为 |
|---|---|
| `this.count` | 读当前状态快照 |
| `this.count = v` / `+=` | 记整值 delta,fire 结束写回(内核 deepcopy 校验) |
| `this.items.append(x)` | **no-op**:变异作用于代理私有拷贝,delta 为空 |
| `this.nope` | AttributeError → KIND_ERROR(运行期事件化) |
| `self` 作参数 | 编译期 DefinitionError |

`this` 不暴露 assets / config / graph / node / connections——节点行为只消费
事件、修改自身状态、产生事件,不观察图拓扑。

### 2.4 参数顺序

```
this → special(Trigger / Signal / Config / Asset) → required data → defaulted data
```

编译期强制(借用 Python 自身"默认值参数在后"的语法约束并延伸)。

### 2.5 返回值映射

| 声明 | 运行期行为 |
|---|---|
| `-> None`(或无 return 注解) | 无输出事件;`return None` 与无 return 等价 |
| `-> T` | 数据输出事件,端口名 = 组名 |
| `-> Signal[bool]` | 信号输出事件 |
| `@group(outputs=(a, b))` + tuple 返回 | 位置一一对应 `return[0]→a`;数量不符编译期报错 |

## 3. 语义裁定表

| # | 裁定 | 内容 |
|---|---|---|
| 1 | 函数 = Group | 一个 `@group` 函数 = 一个 Group;函数名 = 组名;多个函数天然是多个独立组 |
| 2 | 端口属于 Group | 端口名限定为 `"{group}.{param}"`;同名参数跨组是**不同端口**(声明层作用域规则,内核零改动) |
| 3 | 参数默认值 = DataIn fallback | 输入事件缺席时的取值;**与 @group(defaults) 组配置绝不互转**——fallback 属端口声明,defaults 属图配置面 |
| 4 | 一函数零或一输出 | `-> None` 无输出;`-> T` 唯一输出(名 = 组名);多输出是 `outputs=` 显式扩展,非基础语义 |
| 5 | this = 仅 State 视图 | 整值替换写入;原地变异 no-op;未声明字段运行期 AttributeError |
| 6 | Gated 绑定位置参数 | `Gated[int, "gate"]`——`by=` 关键字形式是 Python SyntaxError(PEP 637 未落地,已实测),位置参数为唯一合法形态 |
| 7 | Asset 声明/使用分离 | 签名统一声明;编译器剥离为节点级 `asset_in`;函数体内以**参数值**收到已解析能力对象(构建期注入) |
| 8 | readiness 缺省可视化 | 缺省 = ALL(组内数据输入) ∧ ANY(触发器)——由参数数量直接可视;自定义走 `@group(readiness=...)`,叶端口自动限定 |
| 9 | 错误分两期 | 编译期 DefinitionError(类定义时爆炸:签名/顺序/绑定/声明合法性);运行期 handler 异常 → KIND_ERROR 事件,执行不中断 |
| 10 | 继承闸门沿用 | 具体节点定义间禁止行为继承;共享行为走普通材料类(与 graph-node-protocol.md §2.0 一致) |
| 11 | Config 显式通道 | 函数体读取组配置的唯一通道是 `cfg: Config` 参数(值 = defaults ∪ 图配置);`this` 保持仅 State 视图 |

## 4. 已知语义事实(原型实测)

1. **全静态组连带触发 → 已修复(内核裁定 16,2026-08-22)**:全部参数带
   默认值且未接线的组曾在任意节点唤醒时连带触发(fallback 组合)。内核
   `group_ready` 现已要求无触发器组至少一个输入 pending 才触发——见
   [graph-group-protocol.md](./graph-group-protocol.md) 裁定 16。
2. **门控信号必须早一个 epoch 到达**(仍成立,属内核调度模型):同一批
   injection 先于上游 firing 投递,数据事件会先于信号事件到达端口(此刻
   电平为初始 None → fallback)。门控信号与数据分属两个 `run()` 调用即可。

## 5. 验证边界

- `tests/test_dsl_prototype.py`:14 项(IR 同构 / Add / Counter / Gate /
  端口作用域 / Buffer 双组 / 原地变异 no-op / Asset 注入 / 编译期与运行期
  错误行为)
- 内置 10 节点已全部迁移至 DSL(`eidolon_primitives/nodes.py`),测试套件
  翻转完成:63/63 通过
- 待办:`Append` 端到端用例(内核已有,Buffer 覆盖);`init` 钩子的 DSL 形态
