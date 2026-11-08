import pytest

from histos import operations
from histos.cli import build_parser


@pytest.fixture(autouse=True)
def _isolated_histos_home(tmp_path_factory, monkeypatch):
    """Every test records its vaults in a throwaway HISTOS_HOME, never in the real ~/.histos.
    It's an env var, so the subprocesses the slow tests start inherit it too."""
    monkeypatch.setenv("HISTOS_HOME", str(tmp_path_factory.mktemp("histos-home")))


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
