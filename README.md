# eidolon_graph_ref — Kernel Semantics 可执行参考实现

最小内核。**唯一规范来源**：`../eidolon-graph/docs/` 下的收敛设计文档
（以《ChatGPT-架构验证性重写-20260819-1140.md》为范围依据）。
本包不读取、不依赖旧内核的任何代码——旧内核只是待迁移的对照物。

## 定位与目标

验证新计算模型是否自洽成立，而非功能完整：

> **提出语义 → 最小实现 → 构造反例 → 验证语义 → 冻结 Kernel → 对照旧实现 → 迁移**

验收标准（《架构验证性重写》）：

> 可以不用看 Kernel 的实现，只通过 Graph、输入、输出和 Trace，就能够推断
> Kernel 正在执行什么。

验证手段：语义测试（`uv run pytest`）+ 控制台直接输出事件传递过程
（`uv run python examples/validation_chain.py`）。本阶段**没有前端、没有后端服务**。

## 核心语义（每一条都有文档出处，测试 docstring 注明章节）

- **Event 是唯一传播事实**：Data/Signal 是载荷语义，不是两套机制。
  事件有身份、生命周期（produced → delivered → consumed）、记录谁生产谁消费；
  被消费后的事件暂时保留在事件档案中——传播分析/追踪/未来可视化的底层基础。
  事件彼此独立（无同因果组绑定）。
- **端口 = 声明 × 运行模式**：一个端口一种声明；静态（未连接 = 默认属性，
  条件恒成立，不参与触发不消费）/动态（已连接或收到注入 = 外部事件驱动，
  初始态「尚未收到事件」）。连接状态由内核吸收进 Readiness 计算，节点不写
  `if connected`。
- **端口状态**：Data = value + pending（Replace 覆盖 / Append 累积）；
  Signal = level + pending（level 消费后保持；同电平重复 S1→S1 是两次独立资格）；
  Trigger = pending（Data Event = 载荷 + 激活；Signal Event = 纯激活）。
- **资格（qualification）**：Signal 是输入资格，不是门。端口级资格槽：
  Readiness = `Data.pending AND Qual.pending AND level==HIGH`（D1/S1 配对）；
  节点级 enable：持续电平门控（Readiness 只看 level==HIGH）。
  **LOW 不拒数据不清缓存**：数据照常缓存，仅不参与执行。
- **Readiness 与 Activation 分离**：Dirty ≠ Execute——Data/Signal 在调度层面
  完全对称，只改变状态并唤醒节点；组策略（ON_ALL_DATA_READY / ON_ANY_DATA /
  ON_TRIGGER / ON_DATA_AND_TRIGGER）把 pending 聚合为 Readiness。
- **执行**：epoch = run(events)；注入序 + 源节点声明序播种 → worklist 脏传播
  （投递即唤醒、深度优先、队列遍历非递归）→ 每组每轮至多一次（NodeTurn 预算）
  → 反馈环跨轮迭代 → 队列排空即静止。异常 = 不产出 + 错误条目 + pending 保留重试。
- **输出侧无隐式信号**：不写即不投递；没有 Event = 没有事实发生；数据节点
  永不写信号（声明违规报错）；信号节点（声明 SignalOut）显式产生 Signal Event。
- **确定性**：同一图、同一输入序列 → 同一时间线、同一状态。

## 关键裁定记录（实现时与文档逐行推导对齐）

1. **enable 的 pending 语义**：端口级资格槽 = pending 配对消费（D1/S1 案例）；
   enable（节点级）若同样配对消费，章节门控（持续 HIGH 期间持续执行）不成立，
   因此 enable 取持续电平语义（pending 仅触发重估，访问时消费）。
2. **静态端口 + 资格槽 = 受控默认参数**：LOW 时组内全部数据端口都无资格则
   组不执行（SignalToData 的放行语义）；静态端口无 pending 可配对，资格取
   持续电平。组若因其他端口执行，LOW 端口的 effective = 默认属性。
3. **「LOW 不产生有效组合」的精确含义**（文档 §4 序列 A/B 都得到 D1,D3 的
   必要条件）：资格 LOW 时到达的数据，值照常缓存、**其自身 pending 即刻消费**
   （不与后续 HIGH 配对）；此前 HIGH 期间到达的陈旧 pending 保留。
4. **宿主注入目标 = 事件驱动**：已连接数据线或曾收到注入的端口参与触发
   （图的入口点与连线同为外部事件驱动）。
5. **连线槽位自动推断**：slot 缺省时按端口声明推断（TriggerIn→trigger、
   SignalIn→signal、DataIn→data）；SignalOut→已声明资格槽的 DataIn 二义，
   必须显式指定。
6. **静态端口的数据条件真空为真**（node-protocol.md §4）：全静态端口的组
   在触发到达时执行（受控默认参数/静态默认值经 effective 兜底）。
7. **运行时不允许编辑**：图定义先于运行，拓扑不可变（连接/断开的缓存语义
   仅作静态定义：连接不继承静态缓存、断开丢弃缓存回默认属性）。

## 范围

**包含**：图模型、端口/资格语义、事件传播执行引擎、时间线+事件档案、
连线校验（kind 匹配 + 扇入禁止）、10 个验证原语、语义测试套件、控制台渲染。

**明确排除**（ChatGPT 文档）：LLM、Asset 系统、持久化/快照、脚本节点、
前后端服务、死等拓扑诊断、熔断器、纯信号源同轮再触发机制（最小原语集中
无纯信号源）、RNG（无消费者）。

## 验证原语（10 个类型）

| 原语 | 验证目标 |
|------|---------|
| Source / Constant | 源节点每 epoch 播种（宿主节奏 = 反复 run） |
| Sink / Probe | 传播终点吸收 / 显式状态可观察点 |
| Buffer | Append 累积 + 显式 TriggerIn：数据暂存但不产生执行事件 |
| Join | 多输入同步汇合（ON_ALL_DATA_READY + 资格槽） |
| Split | 多输出发射（事件独立，一次执行两个事件） |
| Latch | 受控释放（ON_DATA_AND_TRIGGER + 资格槽 = D1/S1 配对形态） |
| DataToSignal | 数据 → 信号显式转换（控制流构造） |
| SignalToData | 信号 → 数据 = 受控输入（信号扇出到资格槽+触发端口，两重语义分别消费） |

## 节点实现约定（ABI 的一部分）

内核**零复制投递**（文档定案：运行时零强制，数据包载荷就是 Python 对象）。
由此产生一条所有节点实现必须遵守的约定：

1. **ctx.data_in 中的值视为只读，禁止原地修改**。扇出场景下同一个载荷对象
   被多个下游共享——任何分支的原地修改（如 `list.append`）会被其他分支看到，
   形成隐藏通道、引入到达顺序相关、破坏确定性。
2. **需要保存输入时自行拷贝**（写入 state / 累积到列表等）。拷贝深度由节点
   决定，内核不代劳。
3. **产出构造新对象**，不要把输入对象原样发出后又在别处修改它。
4. ctx.state 是深拷贝（修改安全）；但**写入 state 的值成为世界事实**，若直接
   存 data_in 的对象引用，同样受上述约定约束（该对象此后由世界持有，上游
   不得再修改）。

扇出共享引用这一内核事实由测试 `test_fanout_shares_payload_reference`
锁定——迁移旧节点时若发现原地修改输入的习惯，应视为需要修掉的语义债。

## 运行

```bash
cd kernel/eidolon_graph_ref
uv sync                      # 安装 dev 依赖（pytest）
uv run pytest                # 59 个语义测试
uv run python examples/validation_chain.py   # 控制台观察事件传播
```

## 结构

```
eidolon_graph_ref/           # 内核核心包（零第三方依赖）
├── model/                   # ports / node_type / graph / validate
├── engine/                  # event / port_state / protocol / timeline / executor / instance
├── primitives/              # 10 个验证原语
└── console.py               # 控制台渲染（时间线 + 事件档案 + 节点状态）
examples/validation_chain.py # 组合链演示：Source→Buffer→Join→Split→Latch→DataToSignal→SignalToData→Sink
tests/                       # 语义测试（建模正确性）
```
