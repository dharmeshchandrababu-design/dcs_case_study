import sys
from pathlib import Path

# make the pipeline scripts (extract.py, load.py, label.py) importable from the tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
