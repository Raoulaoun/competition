import pytest
from pathlib import Path
from scoring.config import load_config


@pytest.fixture(scope="session")
def config():
    weights_path = Path(__file__).parent / "fixtures" / "weights.json"
    return load_config(weights_path)
