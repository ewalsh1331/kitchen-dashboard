"""Check wake-word scores on WAV files: voice/.venv/bin/python voice/wake_test.py a.wav b.wav"""
import sys

import numpy as np
from openwakeword.model import Model

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from assistant import CHUNK, load_wav_16k  # noqa: E402

model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
for path in sys.argv[1:]:
    model.reset()
    audio = np.concatenate([np.zeros(16000, np.float32), load_wav_16k(path), np.zeros(16000, np.float32)])
    pcm = (audio * 32767).astype(np.int16)
    peak = max(max(model.predict(pcm[i:i + CHUNK]).values()) for i in range(0, len(pcm) - CHUNK, CHUNK))
    print(f"{path}: peak score {peak:.2f}")
