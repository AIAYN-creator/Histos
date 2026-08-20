import pytest

from histos import operations


@pytest.fixture
def initialized_vault(tmp_path):
    """A freshly init'd vault, for tests that don't care about init_vault itself."""
    operations.init_vault(tmp_path)
    return tmp_path
