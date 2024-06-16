import argparse
import os.path
import subprocess
from termcolor import colored
from json_loader import load_config

config = load_config()
emails = config["general"]["emails"]
domains = config["general"]["domains"]
command = config["general"]["cert_cmd"]


def run_command(cmd, output_file, error_file):
    result = subprocess.run(cmd, shell=True, stdout=open(output_file, 'w'), stderr=open(error_file, 'w'))
    return result.returncode == 0


def cert_active_manual(email, domain):
    output_file = config["general"]["output"]
    error_file = config["general"]["error"]
    run_command(f"{command} --email {email} -d {domain}", output_file, error_file)



def cert_active_auto():
    output_file = config["general"]["output"]
    error_file = config["general"]["error"]

    idx = 0
    while idx < len(emails):
        while idx < len(domains):
            run_command(f'{command} --email {emails[idx]} -d {domains[idx]}', output_file, error_file)
            idx += 1


def main():
    parser = argparse.ArgumentParser(description='Exapmle:')
    parser.add_argument('-a', '--auto', help="Auto cert domain using JSON file")
    parser.add_argument('-e', '--email', help="")
    parser.add_argument('-d', '--domain', help="")

    args = parser.parse_args()
    cert_active_manual(args.email, args.domain)


if __name__ == "__main__":
    main()
