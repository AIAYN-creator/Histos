import pytest

from histos import operations
from histos.cli import build_parser


@pytest.fixture
def initialized_vault(tmp_path):
    """A freshly init'd vault, for tests that don't care about init_vault itself."""
    operations.init_vault(tmp_path)
    return tmp_path


def run(monkeypatch, vault, *argv):
    """Runs a histos command in `vault` exactly as the CLI would, without a subprocess."""
    monkeypatch.chdir(vault)
    args = build_parser().parse_args(list(argv))
    return args.func(args)
