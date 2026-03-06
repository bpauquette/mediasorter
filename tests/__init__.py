import os
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MEDIASORTER_DATA_DIR", str((Path(__file__).resolve().parent / ".test-data")))
