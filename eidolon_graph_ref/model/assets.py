"""资产声明与引用模型(资源平面)。

依据:graph-assets.md §2-3, §7-8

- **编辑与运行分离**:编辑期图定义只保存 AssetRef(纯身份引用),不持有任何
  活对象;运行期构建时才解析成能力对象。
- AssetIn:NodeType 对 Capability 的依赖声明("需要什么")
- AssetRef:GraphDefinition 中的纯身份引用("使用哪个")
- 运行实例解析 AssetRef → Capability("实际是什么")

三个层次:
  NodeType         asset_in = AssetIn("llm", LLMCapability)
  GraphDefinition  bind_asset(node="writer", slot="llm", asset_id="llm-42")
  GraphInstance    resolve("llm-42") → LLMCapability → ctx.assets["llm"]
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AssetIn:
    """资产输入声明 = 节点需要某个 Capability(与 data_in 等并列的声明维度)。

    **声明即必须**(2026-08-20 裁定,替代原"可选 → None"语义):声明的槽位
    在构建期必须绑定且解析成功,否则 BuildReport error——资产是资源而非
    数据,缺席是结构缺陷,不存在"槽位为 None"的运行形态。需要降级时由
    资产系统提供 Null 资产(真实 Capability),节点代码永不需要 None 分支。

    - name: 槽位名。ctx.assets 的键集合由声明决定
    - type: Capability 接口(类或 runtime_checkable Protocol)。声明类型就是
      能力接口——不含 close() 等管理操作(§2-5);None = 不做类型检查
    """

    name: str
    type: type | None = None  # Capability 接口(类或 runtime_checkable Protocol)


@dataclass(frozen=True)
class AssetRef:
    """图定义中的纯身份引用(编辑期数据,可序列化)。

    仅 asset_id(实例身份),不含创建参数——参数属于资产系统创建时的配置
    (§7 裁定)。相同参数不意味着相同资产(§2-3)。
    """

    asset_id: str
