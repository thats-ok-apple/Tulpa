"""Disposable local ASR subprocess; models must already exist on disk."""
import json
import os
from pathlib import Path
import sys


def main():
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    from faster_whisper import WhisperModel
    model, audio, output = map(Path, sys.argv[1:])
    if not model.is_dir() or not audio.is_file():
        raise SystemExit(2)
    engine = WhisperModel(str(model), device='cpu', compute_type='int8', local_files_only=True, cpu_threads=4)
    segments, _ = engine.transcribe(str(audio), beam_size=5, vad_filter=True, condition_on_previous_text=False)
    rows = []
    for segment in segments:
        if len(rows)>=2000:
            break
        rows.append(dict(start=segment.start, end=segment.end, content=segment.text[:2000]))
    output.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
