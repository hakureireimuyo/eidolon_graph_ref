# 内核概念设定清单

> 状态:2026-08-22,Group-centric 裁定后定稿
>
> 定位:本清单给出内核每一个概念的本质定义,作为后续**实现的依照**。
> 规范文档 = [graph-node-protocol.md](./graph-node-protocol.md);
> 修正方案与裁定记录 = [graph-group-protocol.md](./graph-group-protocol.md)。

## 一、总纲

- **内核**:事件传播机器。唯一职责:注入 → 传播 → Readiness → 行为 → 新事件 → 静止。不认识任何具体节点类型,不产生"源"概念,不产生执行之外的任何机制。
- **事件**:唯一传播事实。有身份、有载荷语义、有生命周期;一切状态变化皆源于事件。
- **规则**:声明(图定义、节点类型、组)——"世界如何运行"。
- **事实**:事件产生的结果状态(端口状态、节点状态、时间线)——"世界现在是什么"。
- **世界**:图的一次运行实例 = 规则 + 事实。
- **静止**:epoch 内传播队列排空,再无待处理状态变化。

## 二、事件系统

- **Event**:一个有身份的事实:id / run / kind / payload / producer(节点级,宿主注入为 None)/ port。**不携带组信息**。
- **Kind**:DATA(载荷 + 激活)与 SIGNAL(电平)两种载荷语义,不是两套机制。
- **Delivery**:一次投递 = 一条线、一次端口状态更新。Delivery → 唯一目标 Port → 唯一所属 Group(经声明层静态推导)。
- **Injection**:宿主注入的外部事件,与节点产出完全同构,producer = None。
- **事件生命周期**:produced → delivered(扇出多次) → consumed;status = orphan / pending / consumed。
- **时间线**:按序的传播事实(inject / deliver / fire / consume / error / quiesce),因果记录。
- **事件档案**:所有产生过的事件,消费后保留——传播分析/追踪/可视化的底层。
- **消费记录**:consumed_by (seq, node, port);fire 条目记录 consumed / produced 集合。

## 三、端口层

- **端口**:连接 ABI 单位 = 节点对世界的接线面。一种声明,两种运行模式;身份 = (node, port, slot)。
- **DataIn**:数据输入参数。一格缓存(REPLACE / APPEND)+ pending;default 为静态默认;signal 绑定(可选)。
- **DataOut**:数据输出声明。写即投递。
- **TriggerIn**:激活请求入口。Data Event = 载荷 + 激活;Signal Event = 纯激活。
- **SignalIn**:信号输入。两种角色:绑定控制(语义一)或纯数据输入(语义二)。
- **SignalOut**:信号输出声明。电平输出 = Signal Event;与 data_out 自由组合,写必须声明。
- **AssetIn**:能力依赖声明——"需要什么"。
- **静态**:未连接的运行模式——默认属性,条件恒成立,不等待、不消费。
- **动态**:已连接(或曾注入)的运行模式——事件驱动,必须等待事件,初始"尚未收到"。
- **pending**:端口上尚未被当前执行消费的状态变化标记。
- **端口事实**:value / level / has_value / event_driven——消费后保持的当前结果。
- **扇入禁止**:每 (节点, 端口, 槽位) 至多一条线。
- **扇出**:无限——一个事件多条 Delivery,各自独立消费。

## 四、节点层

- **节点**:容器 = 身份 + 端口 + 状态 + 配置 + 资产 + 组 + 标签。**无执行语义**。
- **NodeType**:节点声明(规则):端口清单、状态字段表、构建配置、组、标签、init。
- **NodeSpec**:图中节点实例 = 类型名 + config;状态属运行态,不属图定义。
- **状态**:节点跨轮事实的唯一存储;单写者(执行读深拷贝,提交增量)。
- **配置**:实例级行为参数覆盖;三节(groups / ports / init);加载期可改,运行期只读。
- **init_defaults**:构建配置默认值;仅 init 可见,不参与行为参数。
- **标签(tags)**:描述层分类。执行路径禁止读取;不得成为隐式行为开关。
- **init**:构建期初始化钩子——资产解析后、实例构造前至多一次;返回初始状态增量。

## 五、组层

- **组(Group)**:调用契约 = 一次可触发行为的基本接口 = **执行单位**。= inputs + triggers + readiness + handler + outputs。
- **端口分区**:每个输入端口在声明层**唯一归属一个 Group**;pending 属端口,组局部性由结构保证。
- **handler**:组绑定的行为实现,handler(ctx) -> GroupOutput | None。可跨组共享;禁止依赖 ctx.group 分发。
- **outputs**:组的输出授权集合——handler 只能写本组 outputs。
- **defaults**:组行为参数的默认值(定义层)。
- **组间数据**:经节点状态传递;执行只读本组输入。
- **空组**:无 inputs / triggers = 构建错误,显式 readiness 不豁免(裁定 17)。

## 六、Readiness

- **Readiness**:组被调用的资格判定——把 pending 聚合为真值的谓词。
- **谓词**:ALL / ANY 组合子 + DATA / TRIGGER 叶;可嵌套。
- **ALL**:全部满足;空集 = True。
- **ANY**:任一满足;空集 = False。
- **DATA(port)**:动态端口 pending;静态端口真空为真(对未绑定 SignalIn 输入同样适用)。
- **TRIGGER(port)**:触发 pending。
- **默认推导**:ALL(DATA(inputs)) ∧ [triggers 非空 → ANY(TRIGGER)]。默认 = 数据齐集自动处理;声明 triggers = 给默认策略加门。
- **机制与行为分离**:组触发机制(Readiness)与组行为(handler)是两个层面;机制可被显式 readiness 重载。
- **信号不进谓词**。

## 七、信号系统

- **语义一(绑定控制)**:DataIn.signal 绑定的信号控制该端口执行时从哪个来源取值(动态缓存 vs 静态默认);严格一对一;不参与执行时机。
- **语义二(纯数据输入)**:未绑定 SignalIn 是事件携带的数据——组按数据聚合处理,handler 读 level。
- **激活因子**:signal_active = 无绑定/未连接 → True;绑定且已连接 → level == HIGH(未激活 = 默认数据有效)。
- **模式规则**:动态 ⇔ (连接数据线 或 曾注入) ∧ signal_active;否则静态。
- **effective**:fire 时解析的参数值——动态 = 缓存值,静态 = config.ports → default。
- **信号事件消费**:pending 访问时消费(仅触发重估),level 保持;电平翻转即刻改变后续解析,无门控语义。

## 八、资源层

- **资产**:外部运行时资源;由资产系统创建和拥有;节点成员环境——**节点级共享**,不在默认值覆盖链上。
- **AssetRef**:图定义中的纯身份引用(asset_id),不含创建参数。
- **AssetResolver**:宿主提供的解析函数——ref → capability。
- **Capability**:节点获得的受限使用接口(不含管理面)。
- **声明即必须**:声明的槽位构建期必须绑定且解析成功;无 None 槽位。
- **资产隔离**:不产生事件、不参与 Readiness、不进入状态/传播平面。

## 九、执行

- **epoch(run)**:注入 → 脏传播 → 静止;run([]) = 立即静止(无播种)。
- **唤醒**:投递后目标节点入队。
- **脏传播**:投递唤醒,深度优先,队列遍历非递归;Dirty ≠ Execute。
- **NodeTurn 预算**:(节点, 组) 每 epoch 至多一次;反馈环跨轮迭代。
- **fire**:谓词满足 → 模式判定 + effective 解析 → handler → 消费 → 状态提交 → 输出校验投递。
- **消费**:fire 后清除本组端口 pending;value / level 保持。
- **零拷贝**:值域探针只校验不复制;扇出共享载荷引用——输入视为只读,产出构造新对象。
- **值域**:State / Data / Event 载荷 = Value(可复制);Capability 禁止入内。
- **BuildReport**:构建结果一次性收集全部错误;失败则不存在实例。
- **错误分层**:构建期(BuildReport error)/ 执行期(KIND_ERROR + 无输出 + pending 保留,下 epoch 重试)。

## 十、连线

- **Wire**:一次投递的静态路径 (src_node, src_port, dst_node, dst_port, dst_slot)。
- **槽位**:data / trigger / signal。
- **kind 矩阵**:DataOut→DataIn(参数绑定)、DataOut→TriggerIn(载荷 + 激活)、SignalOut→SignalIn(绑定/输入)、SignalOut→TriggerIn(激活);交叉连线非法。

## 十一、宿主

- **宿主**:图与内核的驱动者——类型全集注册(types 字典)、资产系统、注入节奏。内核 registry-agnostic,不区分事件来源与实现来源。
