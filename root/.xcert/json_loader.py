import os
import json

def load_config(config_file="config.json", directory=None):
    if directory is None:
        directory = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(directory, config_file)
    try:
        with open(config_path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}\n"
            f"Copy config.json.example to config.json and fill in your details."
        )
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON syntax error in {config_path}: {e}")

def save_config(config, config_file="config.json", directory=None):
    if directory is None:
        directory = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(directory, config_file)
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)