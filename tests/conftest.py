from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "channel_transcripts.py"


def load_channel_transcripts():
    spec = importlib.util.spec_from_file_location("channel_transcripts", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["channel_transcripts"] = module
    spec.loader.exec_module(module)
    return module


ct = load_channel_transcripts()
