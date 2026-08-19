"""图模型：节点类型、端口声明、图定义与连线校验。"""

from .graph import GraphDefinition, NodeSpec, Wire, SLOT_DATA, SLOT_QUAL, SLOT_TRIGGER, SLOT_SIGNAL
from .node_type import InputGroup, NodeType, Policy
from .ports import APPEND, REPLACE, DataIn, DataOut, SignalIn, SignalOut, TriggerIn
from .validate import ValidationError, ValidationResult, ensure_valid, validate

__all__ = [
    "GraphDefinition",
    "NodeSpec",
    "Wire",
    "SLOT_DATA",
    "SLOT_QUAL",
    "SLOT_TRIGGER",
    "SLOT_SIGNAL",
    "InputGroup",
    "NodeType",
    "Policy",
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
