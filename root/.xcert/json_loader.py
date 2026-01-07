import os
import json

def load_config(config_file="config.json", directory=None):
    if directory is None:
        directory = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(directory, config_file)
    with open(config_path, encoding='utf-8') as f:
        return json.load(f)

def save_config(config, config_file="config.json", directory=None):
    if directory is None:
        directory = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(directory, config_file)
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)