from forge.docker_runner import generate_python_dockerfile


def test_generated_python_dockerfile_installs_pytest() -> None:
    dockerfile = generate_python_dockerfile()

    assert "python -m pip install pytest" in dockerfile