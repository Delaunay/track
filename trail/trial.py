import json
from typing import *

from argparse import Namespace
from collections import defaultdict

from dataclasses import dataclass
from dataclasses import field

from trail.serialization import to_json


@dataclass
class Trial:
    # Stores metrics that are

    system_metrics: Dict[str, any] = field(default_factory=dict)
    graph_definition: str = None
    args: Namespace = None

    name: str = None
    version: str = None
    trial_uid: str = None
    code: str = None
    source_file: str = None

    values: Dict[str, any] = field(default_factory=dict)
    metrics: Dict[any, Dict[any, any]] = field(default_factory=lambda: defaultdict(dict))

    def to_json(self, short=False):
        return {
            'name': self.name,
            'version': self.version,
            'arguments': to_json(vars(self.args), short),
            'metrics': to_json(self.metrics, short),
            'values': to_json(self.values, short),
            'system_metrics': to_json(self.system_metrics, short)
        }
