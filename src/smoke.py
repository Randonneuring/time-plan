"""Simple smoke tests (not a real test suite)."""

import extract_gpx_times
import argparse
import pathlib
import sys

parser = argparse.ArgumentParser()
parser.add_argument("gpx", help="Path to GPX file", type=pathlib.Path)
parser.add_argument("landmarks", help="Path to CSV file with landmarks", type=pathlib.Path)
parser.add_argument("output", help="Path to output CSV file", type=pathlib.Path,
                    nargs="?", default=sys.stdout)

sample_gpx_path = "../data/day_1.gpx"
entries = extract_gpx_times.load_landmarks(sample_gpx_path)
print(entries)
