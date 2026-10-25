from uplift_engine.lineage import git_state, tree_fingerprint


def test_tree_fingerprint_changes_with_content(tmp_path):
    (tmp_path / "src").mkdir()
    f = tmp_path / "src" / "a.py"
    f.write_text("x = 1")
    a = tree_fingerprint(tmp_path, ["src/**/*.py"])
    f.write_text("x = 2")
    assert tree_fingerprint(tmp_path, ["src/**/*.py"]) != a


def test_git_state_outside_a_repository_is_none_not_unknown(tmp_path):
    state = git_state(tmp_path)
    assert state["git_commit"] is None and state["git_dirty"] is None
