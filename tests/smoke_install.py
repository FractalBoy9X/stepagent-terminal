"""Install the built wheel in a fresh venv and exercise it outside the checkout."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import venv


def main():
    root = Path(__file__).resolve().parents[1]
    wheels = list((root / "dist").glob("*.whl"))
    assert len(wheels) == 1, "Build one wheel with python -m build first"
    with tempfile.TemporaryDirectory(prefix="stepagent installed ") as directory:
        work = Path(directory)
        environment = work / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        cli = scripts / ("stepagent-terminal.exe" if os.name == "nt" else "stepagent-terminal")
        env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
        env.update(CODEX_HOME=str(work / "unused"), CODEX_SESSIONS_DIR=str(work),
                   PYTHONIOENCODING="ascii", PIP_DISABLE_PIP_VERSION_CHECK="1")

        def run(*args):
            return subprocess.run([str(arg) for arg in args], cwd=work, env=env,
                                  capture_output=True, encoding="utf-8", check=True, timeout=120)

        run(python, "-m", "pip", "install", wheels[0])
        run(cli, "--help")
        run(cli, "--version")
        log = work / "rollout 世界.jsonl"
        log.write_text(json.dumps({"type": "event_msg", "payload": {
            "type": "user_message", "message": "Zażółć 世界"}}, ensure_ascii=False) + "\n", encoding="utf-8")
        doc = json.loads(run(cli, "--snapshot", "--file", log.name).stdout)
        assert doc["raw_records"] == 1
        assert "Zażółć 世界" in doc["interactions"][0]["summary"]
        run(python, "-m", "stepagent.demo", "--delay", "0", "--turns", "0")
        print("Installed wheel: CLI, UTF-8 snapshot and packaged demo passed outside checkout.")


if __name__ == "__main__":
    main()
