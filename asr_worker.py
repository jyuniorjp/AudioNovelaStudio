# -*- coding: utf-8 -*-
"""Reconocimiento de voz en un PROCESO APARTE (el mismo programa con «--asr trabajo.json»).

Así, si la tarjeta gráfica, un modelo o una librería nativa fallan, solo se cae este proceso y la app sigue abierta
(la app usa entonces el motor de respaldo). Además evita mezclar la librería onnxruntime de faster-whisper con la de
la voz sin internet (sherpa-onnx), que tienen el mismo nombre de archivo.

Entrada (JSON): wav, model_dir, device ("cuda"|"cpu"), compute ("float16"|"int8"), cuda_dir, out.
Salida: el avance (0..1) en «progress» (archivo de texto) y el resultado en «out» (JSON).
(El programa compilado no tiene consola: por eso no se usa la salida estándar.)
"""
import json
import os
import sys
import time
import wave


def _say(job, f):
    try:
        with open(job["progress"], "w", encoding="utf-8") as fh:
            fh.write(f"{f:.3f}")
    except (OSError, KeyError):
        pass


def main(job_path):
    job = json.load(open(job_path, encoding="utf-8"))
    cuda_dir = job.get("cuda_dir") or ""
    if job.get("device") == "cuda" and os.path.isdir(cuda_dir):
        os.add_dll_directory(cuda_dir)
        os.environ["PATH"] = cuda_dir + os.pathsep + os.environ.get("PATH", "")
    import numpy as np
    from faster_whisper import WhisperModel
    t0 = time.time()
    model = WhisperModel(job["model_dir"], device=job.get("device", "cpu"), compute_type=job.get("compute", "int8"),
                         cpu_threads=max(2, (os.cpu_count() or 4) - 2))
    wf = wave.open(job["wav"], "rb")
    audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    wf.close()
    total = max(1.0, len(audio) / 16000)
    segs, info = model.transcribe(audio, language="es", word_timestamps=True, vad_filter=True, beam_size=5,
                                  condition_on_previous_text=False,      # evita que en audios largos repita frases
                                  hotwords=job.get("hotwords") or None)  # nombres propios (si hay internet)
    words = []
    for s in segs:
        for w in (s.words or []):
            t = w.word.strip()
            if t:
                words.append({"word": t, "start": float(w.start), "end": float(w.end), "conf": float(w.probability)})
        _say(job, min(0.999, s.end / total))
    json.dump({"words": words, "seconds": time.time() - t0, "device": job.get("device")},
              open(job["out"] + ".part", "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(job["out"] + ".part", job["out"])
    _say(job, 1.0)


if __name__ == "__main__":
    main(sys.argv[1])
