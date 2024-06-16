import os
import json


def load_config(config_file="config.json", directory=None):
    if directory is None:
        directory = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(directory, config_file)

    with open(config_path) as f:
        config = json.load(f)

    return config