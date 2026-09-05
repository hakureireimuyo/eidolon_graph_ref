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
| `Append[T]` | 参数 | 累积型输入(增量批次,消费排空,裁定 2026-08-23) | `DataIn(cache=APPEND)` |
| `Asset[T]` | 参数 | 资产依赖(节点级声明) | `AssetIn(name, T)` |
| `State[T] = v` | 类属性 | 节点级状态 | `state_defaults` |
| `-> T` | 返回值 | 数据输出(名 = 组名) | `DataOut("{group}")` |
| `-> None` | 返回值 | 无输出事件 | 无 |
| `-> Signal[bool]` | 返回值 | 信号输出 | `SignalOut("{group}")` |

### 2.2 `@group` 装饰器参数

```python
@group(readiness=ANY("a", "b"))     # 缺省 = ALL(数据输入) ∧ ANY(触发器)
@group(defaults={"mode": "truthy"}) # 组 config 缺省(图可覆写,≠ 参数默认值)
@group(outputs=("out1", "out2"))    # 多数据输出声明;端口按裁定 2 限定为
                                    # "fan.out1"/"fan.out2"
@group(outputs=("gt",), signals=("a_gt_b",))
                                    # 同组数据+信号输出:signals= 必须配 outputs=
                                    # (纯信号输出仍走 -> Signal[bool],裁定 12)
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
| `this.count = v` / `+=` | 整值替换,fire 结束全量写回 |
| `this.items.append(x)` / `.extend(xs)` | **生效**(裁定 2026-08-23):工作副本上的原地变异随全量提交写回 |
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
| `@group(outputs=(a, b))` | 声明输出端口数 ≥ 2 → dict 返回协议(裁定 12) |
| `@group(outputs=..., signals=...)` | 数据+信号输出同组声明(dict 键按声明分派) |

**多输出返回协议(裁定 12,2026-08-23)**:声明输出端口数决定 handler 返回
形态——恰 1 个 → 裸值返回(同 `-> T` / `-> Signal[bool]` 单输出裁定 4);
≥ 2 个 → dict 返回,键 = `outputs=`/`signals=` 声明成员名(未限定,由
编译器映射到组限定端口),缺失键 = 该端口本轮无事件,None 值 = 合法载荷
照发;键不在声明集 → 运行期 TypeError → KIND_ERROR;声明 0 个输出端口却
返回载荷 → 同样 KIND_ERROR(「写必须声明」)。`signals=` 声明信号输出
端口,必须配 `outputs=`;`spec.outputs` = 数据+信号端口全集。

### 2.6 tags / doc —— 只读声明函数(描述层)

```python
class Clock(NodeDefinition):
    @staticmethod
    def tags() -> tuple[str, ...]:
        return ("category:source", "tick:tick.trigger")

    @staticmethod
    def doc() -> DocSpec:
        return DocSpec("摘要", sections=(...))
```

基类 `NodeDefinition` 声明这两个函数的默认实现(`()` / `None`),具体节点
以 `@staticmethod` **显式重载**;声明是只读纯函数(零参数、无副作用),编译期
求值一次,结果进入 NodeType 元数据(描述层定位不变:执行路径禁止读取)。
类属性赋值形式编译期 DefinitionError。未重载的节点 = 无 tag / 无说明书
(编辑器侧落 custom 分类与「暂无说明」兜底)。

### 2.7 init / init_defaults / type_name(闭包审计冻结 2026-09-05)

```python
class Boot(NodeDefinition):
    type_name = "bootnode"          # NodeType.name(缺省 = 类名)
    init_defaults = {"seed": 7}     # 构建配置默认(实例 config["init"] 可覆写)

    value: State[int] = 0

    @staticmethod
    def init(ctx):                  # 构建期钩子:InitContext -> dict | None
        return {"value": ctx.config["seed"]}   # delta ⊆ state_defaults 键
```

- `init` 调用形态与内核约定一致:单 `ctx` 参数、无默认值;返回增量字典,
  未知 state 字段 = 构建期错误(内核校验,graph-group-protocol.md §7)。
- 三者此前"碰巧可用"但未文档化;已由
  [tests/test_primitives_as_contract.py](../tests/test_primitives_as_contract.py)
  升格为冻结契约,完整闭包状态见
  [semantic-closure-matrix.md](./semantic-closure-matrix.md)。

## 3. 语义裁定表

| # | 裁定 | 内容 |
|---|---|---|
| 1 | 函数 = Group | 一个 `@group` 函数 = 一个 Group;函数名 = 组名;多个函数天然是多个独立组 |
| 2 | 端口属于 Group | 端口名限定为 `"{group}.{param}"`;同名参数跨组是**不同端口**(声明层作用域规则,内核零改动) |
| 3 | 参数默认值 = DataIn fallback | 输入事件缺席时的取值;**与 @group(defaults) 组配置绝不互转**——fallback 属端口声明,defaults 属图配置面 |
| 4 | 一函数零或一输出 | `-> None` 无输出;`-> T` 唯一输出(名 = 组名);多输出是 `outputs=` 显式扩展,非基础语义 |
| 5 | this = 仅 State 视图(修订 2026-08-23) | 工作副本 + 全量提交:整值赋值与原地变异均生效;未声明字段运行期 AttributeError;State→Data 输出为 ownership 边界(见 §4-3) |
| 6 | Gated 绑定位置参数 | `Gated[int, "gate"]`——`by=` 关键字形式是 Python SyntaxError(PEP 637 未落地,已实测),位置参数为唯一合法形态 |
| 7 | Asset 声明/使用分离 | 签名统一声明;编译器剥离为节点级 `asset_in`;函数体内以**参数值**收到已解析能力对象(构建期注入) |
| 8 | readiness 缺省可视化 | 缺省 = ALL(组内数据输入) ∧ ANY(触发器)——由参数数量直接可视;自定义走 `@group(readiness=...)`,叶端口自动限定 |
| 9 | 错误分两期 | 编译期 DefinitionError(类定义时爆炸:签名/顺序/绑定/声明合法性);运行期 handler 异常 → KIND_ERROR 事件,执行不中断 |
| 10 | 继承闸门沿用 | 具体节点定义间禁止行为继承;共享行为走普通材料类(与 graph-node-protocol.md §2.0 一致) |
| 11 | Config 显式通道 | 函数体读取组配置的唯一通道是 `cfg: Config` 参数(值 = defaults ∪ 图配置);`this` 保持仅 State 视图 |
| 12 | 多输出返回协议(2026-08-23) | 声明输出端口数决定返回形态:恰 1 个 → 裸值;≥ 2 个 → dict(键=outputs=/signals= 声明成员名,编译器映射组限定端口;缺失键=该端口本轮无事件,None=合法载荷照发);`signals=` 声明信号输出端口(必须配 `outputs=`,纯信号走 `-> Signal[bool]`);未知键/0 输出返回载荷 → KIND_ERROR |

## 4. 已知语义事实(原型实测)

1. **全静态组连带触发 → 已修复(内核裁定 16,2026-08-22)**:全部参数带
   默认值且未接线的组曾在任意节点唤醒时连带触发(fallback 组合)。内核
   `group_ready` 现已要求无触发器组至少一个输入 pending 才触发——见
   [graph-group-protocol.md](./graph-group-protocol.md) 裁定 16。
2. **门控信号必须早一个 epoch 到达**(仍成立,属内核调度模型):同一批
   injection 先于上游 firing 投递,数据事件会先于信号事件到达端口(此刻
   电平为初始 None → fallback)。门控信号与数据分属两个 `run()` 调用即可。
3. **Append = 增量批次语义(裁定 2026-08-23)**:端口缓存随组消费排空,
   handler 每次收到的是**自上次消费以来**的新增事件列表;跨消费累积由
   节点 state 负责。镜像缓存到 state(`this.items = list(item)`)是旧语义
   的写法——flush 清空 state 后旧项会在端口缓存里复活(实测复现),现
   已改为内核 `consume` 对 APPEND 端口排空 + Buffer `put` 直接列表方法
   (`this.items.extend(item)`)。
4. **State→Data ownership 边界(裁定 2026-08-23)**:State 持有的对象不得
   以**隐式 alias** 进入 Data Plane——handler 输出与 state 持有对象同一
   引用时,输出侧复制解除 alias(实现于 `_make_wrapper`);Data Plane 内部
   保持零拷贝共享(`test_fanout_shares_payload_reference` 冻结),Data
   payload 进入 Data Plane 即视为不可变值。State 事务 = 工作副本 + 全量
   提交,与 Data Plane 引用共享通过「每轮 fire 重新 deepcopy 工作副本」
   隔离。跨边界存在两种合法形态:
   - **Borrow / snapshot**:对象仍属 State,输出侧 detach(deepcopy);
   - **Move / ownership transfer**:节点显式把对象移出 State
     (`this.items = []; return items`)——ownership 随输出转移给 Data
     Plane,零拷贝释放。
   `Buffer.flush` 即 Move 的示范:积累批次从 State 取走、作为单个数据包
   释放,此后不再属于 State。这是"谁拥有对象、谁能改对象"模型下唯一
   允许的跨边界共享形态(显式转移,非隐式 alias)。

## 5. 验证边界

- `tests/test_dsl_prototype.py`:14 项(IR 同构 / Add / Counter / Gate /
  端口作用域 / Buffer 双组 / 原地变异 no-op / Asset 注入 / 编译期与运行期
  错误行为)
- 内置 10 节点已全部迁移至 DSL(`eidolon_primitives/nodes.py`),测试套件
  翻转完成:63/63 通过
- 闭包审计(2026-09-05):primitives 升格为 DSL 合约测试(exec 前端逐字段
  一致),init/type_name 形态冻结(§2.7);缺口与限制见
  [semantic-closure-matrix.md](./semantic-closure-matrix.md)
