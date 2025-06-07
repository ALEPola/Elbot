import pathlib
import sys

# Ensure repo root is on sys.path so tests can import the local package
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
