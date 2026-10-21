import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_MODEL_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="set RUN_MODEL_TESTS=1 to run (downloads local models)")
    for item in items:
        if "models" in item.keywords:
            item.add_marker(skip)
