from pathlib import Path

from forge.docker_runner import (
    CONTAINER_RUN_FLAGS,
    _without_repo_dockerignore,
    generate_python_dockerfile,
)


def test_generated_python_dockerfile_installs_pytest() -> None:
    dockerfile = generate_python_dockerfile()

    assert "python -m pip install pytest" in dockerfile


def test_run_flags_isolate_network_and_privileges() -> None:
    assert "--network" in CONTAINER_RUN_FLAGS
    assert CONTAINER_RUN_FLAGS[CONTAINER_RUN_FLAGS.index("--network") + 1] == "none"
    assert "no-new-privileges" in CONTAINER_RUN_FLAGS


def test_without_repo_dockerignore_hides_then_restores(tmp_path: Path) -> None:
    dockerignore = tmp_path / ".dockerignore"
    dockerignore.write_text("tests/\n", encoding="utf-8")

    with _without_repo_dockerignore(tmp_path):
        assert not dockerignore.exists()
        assert (tmp_path / ".dockerignore.forge-disabled").exists()

    assert dockerignore.read_text(encoding="utf-8") == "tests/\n"
    assert not (tmp_path / ".dockerignore.forge-disabled").exists()


def test_without_repo_dockerignore_is_noop_when_absent(tmp_path: Path) -> None:
    with _without_repo_dockerignore(tmp_path):
        assert not (tmp_path / ".dockerignore.forge-disabled").exists()
