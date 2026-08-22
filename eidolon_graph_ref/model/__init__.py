"""图模型：节点类型、端口声明、图定义与连线校验。"""

from .assets import AssetIn, AssetRef
from .graph import GraphDefinition, NodeSpec, Wire, SLOT_DATA, SLOT_TRIGGER, SLOT_SIGNAL
from .node_type import Group, NodeType
from .definition import DefinitionError, GroupSpec, NodeDefinition
from .readiness import ALL, ANY, DATA, TRIGGER
from .ports import APPEND, REPLACE, DataIn, DataOut, SignalIn, SignalOut, TriggerIn
from .validate import ValidationError, ValidationResult, ensure_valid, validate

__all__ = [
    "AssetIn",
    "AssetRef",
    "GraphDefinition",
    "NodeSpec",
    "Wire",
    "SLOT_DATA",
    "SLOT_TRIGGER",
    "SLOT_SIGNAL",
    "Group",
    "NodeType",
    "DefinitionError",
    "GroupSpec",
    "NodeDefinition",
    "ALL",
    "ANY",
    "DATA",
    "TRIGGER",
    "APPEND",
    "REPLACE",
    "DataIn",
    "DataOut",
    "SignalIn",
    "SignalOut",
    "TriggerIn",
    "ValidationError",
    "ValidationResult",
    "ensure_valid",
    "validate",
]
