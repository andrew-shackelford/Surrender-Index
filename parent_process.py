import subprocess
import argparse

def main():
    parser = argparse.ArgumentParser(description="Start the Surrender Index process.")
    parser.add_argument('--disableTweeting', action='store_true', dest='disableTweeting')
    args = parser.parse_args()
    argument = ""
    if args.disableTweeting:
        argument = "--disableTweeting"

    while True:
        subprocess.call(["python", "surrender_index_bot.py", argument])

if __name__ == "__main__":
    main()