# eidolon_graph_ref — Kernel Semantics 可执行参考实现

> **状态：Semantic Baseline（语义基准），已冻结(2026-08-20)**

最小内核。**唯一规范来源**：`../eidolon-graph/docs/` 下的收敛设计文档
（以《ChatGPT-架构验证性重写-20260819-1140.md》为范围依据）。
本包不读取、不依赖旧内核的任何代码——旧内核只是待迁移的对照物。

## 定位与目标

已完成"提出语义 → 最小实现 → 构造反例 → 验证语义"的循环，进入冻结阶段。
本包的职责从**探索设计**转变为**大内核的 Oracle**：

```text
Small Kernel（本包）
      │  semantic reference
      ▼
Large Kernel
      └── behavior must be equivalent
```

大内核实现任何机制时，对照本包的裁定文档与语义测试验证行为等价，而不是
从大内核内部重新推导"当初想让它怎么工作"。**冻结意味着：大内核开发中
出现的新问题不得随意修改本包语义**——任何变更必须走"裁定修订 → 文档
更新 → 测试翻转"的完整流程。

验收标准（《架构验证性重写》）：

> 可以不用看 Kernel 的实现，只通过 Graph、输入、输出和 Trace，就能够推断
> Kernel 正在执行什么。

验证手段：语义测试（`uv run pytest`）+ 控制台直接输出事件传递过程
（`uv run python examples/validation_chain.py`）。本阶段**没有前端、没有后端服务**。

## 稳定性状态(2026-08-20)

| 层级 | 状态 | 依据 |
|---|---|---|
| 执行语义(事件/传播/Readiness/信号) | **已稳定** | 既有语义测试,不再重新设计 |
| 协议与所有权(Node/Asset/AssetSystem/Runtime 边界) | **已稳定** | docs/graph-assets.md + graph-asset-protocols.md + 边界测试 |
| 跨平面抽象(Value/Capability 分离) | **已稳定** | 值域裁定 + 12 行越权矩阵 + 入口探针 |
| 节点协议(内核 ↔ 外部节点 ABI,含 init 钩子) | **已稳定** | docs/graph-node-protocol.md + ABI 测试 |
| Snapshot/Replay、真实 AssetSystem | 实现缺口 | 按已裁定规格实现,非语义问题 |
| 并发语义 | 未定义 | 不构成当前内核的不稳定 |
| 大规模压力/性能 | 未验证 | 属工程验证,不属于语义裁定 |

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
- **资产层(资源平面)**：见 `docs/graph-assets.md` 与 `docs/graph-asset-protocols.md`。
  节点是编排者，资产是能力实现者，资产系统是唯一工厂与所有者；
  声明即必须（构建期解析，无 None 槽位）；降级经 Null 资产；使用面
  （Capability）与管理面分离；**Value/Capability 分属不同语义类别**——
  State/Data/Event 载荷的值域 = Value，三入口以可复制性探针校验（零拷贝
  保持）；共享资产调用顺序不构成 Runtime 语义。

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
8. **声明即必须**（graph-assets.md §7）：资产槽位构建期必须绑定并解析成功，
   不存在 None 槽位；`GraphInstance.build()` 是唯一正式构建入口，裸构造
   在构造器层面被拒绝。
9. **State/Data/Event 载荷的值域 = Value**（2026-08-20）：Capability 不得
   进入任何传播/状态平面；状态提交、数据产出、宿主注入、构建期 config
   四入口以 deepcopy 探针校验（只校验不复制，零拷贝保持）。
10. **共享资产调用顺序不构成 Runtime 语义**（2026-08-20）：内核不承诺
    声明序；顺序敏感性与可共享性由资产系统与编排负责。

## 范围

**包含**：图模型、端口/资格语义、事件传播执行引擎、时间线+事件档案、
连线校验（kind 匹配 + 扇入禁止）、10 个验证原语、资产层（AssetIn/AssetRef/
bind_asset/构建期解析/Capability 注入，假 AssetSystem 驱动）、节点协议
ABI（Activation/Event 执行契约 + init 构建期钩子 + 外部节点 ABI 测试，
docs/graph-node-protocol.md）、平面边界攻击测试套件、语义测试套件、
控制台渲染。

**明确排除**（ChatGPT 文档）：LLM、真实 Asset 系统（资产管理器）、
持久化/快照（Snapshot/Replay 机制）、脚本节点、前后端服务、死等拓扑诊断、
熔断器、纯信号源同轮再触发机制（最小原语集中无纯信号源）、RNG（无消费者）。

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
uv run pytest                # 113 个语义 + 边界测试
uv run python examples/validation_chain.py   # 控制台观察事件传播
uv run python examples/external_node.py      # 外部节点包端到端(节点协议 ABI)
```

## 结构

```
eidolon_graph_ref/           # 内核核心包（零第三方依赖）
├── model/                   # ports / node_type / graph / validate
├── engine/                  # event / port_state / protocol / timeline / executor / instance
├── primitives/              # 10 个验证原语
└── console.py               # 控制台渲染（时间线 + 事件档案 + 节点状态）
docs/                        # 裁定文档(graph-assets / graph-asset-protocols / graph-node-protocol)
examples/                    # validation_chain.py 组合链演示;external_node.py 外部节点包演示
tests/                       # 语义测试（建模正确性）
```
