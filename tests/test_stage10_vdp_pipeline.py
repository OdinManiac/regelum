import os
import sys
from pathlib import Path

import pytest

os.environ["RG_DISABLE_FILE_LOGS"] = "1"
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipes.van_der_pol_hybrid import build_pipeline


def test_vdp_pipeline_converges_amplitude():
    pipe, sink = build_pipeline(dt=0.01, target=0.0)
    assert pipe.compile()

    # Прогоняем достаточно тиков, чтобы система сошлась к целевому x≈0
    pipe.run(ticks=500, dt=0.01)

    assert sink.last is not None
    x_val = sink.last["x"]
    assert x_val == pytest.approx(0.0, rel=0.0, abs=0.05)
