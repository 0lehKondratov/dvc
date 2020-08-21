from collections import defaultdict

from ._common import *  # noqa, pylint: disable=wildcard-import
from ._json import *  # noqa, pylint: disable=wildcard-import
from ._toml import *  # noqa, pylint: disable=wildcard-import
from ._yaml import *  # noqa, pylint: disable=wildcard-import

LOADERS = defaultdict(lambda: load_yaml)  # noqa: F405
LOADERS.update({".toml": load_toml, ".json": load_json})  # noqa: F405

MODIFIERS = defaultdict(lambda: modify_yaml)  # noqa: F405
MODIFIERS.update({".toml": modify_toml, ".json": modify_json})  # noqa: F405
