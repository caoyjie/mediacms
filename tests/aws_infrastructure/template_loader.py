from pathlib import Path
from typing import Any

import yaml


class CloudFormationLoader(yaml.SafeLoader):
    """Load CloudFormation intrinsics without resolving them."""


def _construct_intrinsic(
    loader: CloudFormationLoader, node: yaml.Node
) -> dict[str, Any]:
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    else:
        value = loader.construct_mapping(node)
    return {node.tag.removeprefix("!"): value}


for intrinsic in ("Ref", "Sub", "GetAtt", "If", "Equals", "And", "Or", "Not"):
    CloudFormationLoader.add_constructor(f"!{intrinsic}", _construct_intrinsic)


def load_template(path: str) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as template_file:
        return yaml.load(template_file, Loader=CloudFormationLoader)
