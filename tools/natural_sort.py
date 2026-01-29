import re
import sys


def natural_key(s):
    # Split string into list of strings and integers for natural sort
    return [int(text) if text.isdigit() else text for text in re.split(r"(\d+)", s)]


with open(sys.argv[1]) as f:
    lines = [line.rstrip("\n") for line in f if line.strip()]

lines.sort(key=natural_key)

for line in lines:
    print(line)
