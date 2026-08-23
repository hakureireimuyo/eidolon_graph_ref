"""图模型:语义 IR(NodeType/Group)、端口声明、声明编译(DSL 前端)、图定义与连线校验。"""

from .assets import AssetIn, AssetRef
from .graph import GraphDefinition, NodeSpec, Wire, SLOT_DATA, SLOT_TRIGGER, SLOT_SIGNAL
from .node_type import DocSection, DocSpec, Group, NodeType
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
    "DocSection",
    "DocSpec",
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
