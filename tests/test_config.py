import pytest
import yaml

from conftest import REPO, write_fixture_config
from uplift_engine.config import ProjectRootError, load_config, project_root


def test_repo_config_loads():
    cfg = load_config(root=REPO)
    assert cfg.dataset.as_of.year == 2019 and cfg.root == REPO


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("models__candidates", []),
        ("models__candidates", ["random", "response"]),
        ("models__candidates", ["s_learner", "nope"]),
        ("models__ensemble_top_k", 1),
        ("models__ensemble_top_k", 9),
        ("experiment__n_folds", 1),
        ("economics__margin_per_purchase", -5),
        ("economics__sms_cost", 0),
        ("campaign__holdout_share", 1.0),
    ],
)
def test_invalid_configs_rejected(tmp_path, key, value):
    path = write_fixture_config(tmp_path, {}, **{key: value})
    with pytest.raises(ValueError):
        load_config(path, root=REPO)


def test_unknown_keys_like_removed_tuning_do_not_silently_enable_anything(tmp_path):
    raw = yaml.safe_load((REPO / "configs" / "pipeline.yaml").read_text())
    assert "tuning" not in raw["models"]


def test_project_root_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("UTE_ROOT", str(REPO))
    assert project_root() == REPO
    monkeypatch.setenv("UTE_ROOT", str(tmp_path))
    with pytest.raises(ProjectRootError):
        project_root()
    monkeypatch.delenv("UTE_ROOT")
    assert project_root(start=REPO / "src") == REPO


def test_cli_outside_a_checkout_fails_cleanly(monkeypatch):
    from typer.testing import CliRunner

    import uplift_engine.cli as cli

    def no_root(*args, **kwargs):
        raise ProjectRootError("cannot find the project root")

    monkeypatch.setattr(cli, "load_config", no_root)
    result = CliRunner().invoke(cli.app, ["status"])
    assert result.exit_code == 2 and "project root" in result.output
