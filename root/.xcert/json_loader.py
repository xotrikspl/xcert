import json
import os


def load_config(config_path=None, config_file="config.json", directory=None):
    if config_path is None:
        if directory is None:
            directory = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(directory, config_file)

    try:
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}\n"
            f"Copy config.json.example to config.json and fill in your details."
        )
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON syntax error in {config_path}: {e}")


def save_config(config, config_path=None, config_file="config.json", directory=None):
    if config_path is None:
        if directory is None:
            directory = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(directory, config_file)

    # Write next to the target and swap atomically, so a crash never leaves half a config.
    tmp_path = f"{config_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")
    if os.path.exists(config_path):
        os.chmod(tmp_path, os.stat(config_path).st_mode & 0o777)
    os.replace(tmp_path, config_path)
