# -*- coding: utf-8 -*-
"""
¿Con qué trabaja esta PC? Detecta si hay una tarjeta gráfica DEDICADA (NVIDIA, AMD Radeon o Intel Arc)
y decide dónde se hace el trabajo pesado:
  · NVIDIA            → «cuda»     (voz sin internet, subtítulos con Whisper, quitar fondos, imágenes)
  · AMD / Intel Arc   → «directml» (lo que lo admite: modelos ONNX); lo demás va al procesador
  · solo integrada    → «cpu»
Se puede forzar con la variable ANS_DEVICE=cpu|gpu o con "device" en config.json ("auto" por defecto).
Uso: python hardware.py   (muestra lo detectado)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading

CFG_FILE = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "AudioNovelaStudio", "config.json")
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_cache = {}
_lock = threading.Lock()

# integradas o virtuales: no cuentan como tarjeta dedicada
_NOT_DEDICATED = re.compile(r"UHD|HD Graphics|Iris|Microsoft Basic|Remote|Virtual|Parsec|Meta|Citrix|"
                            r"^AMD Radeon\(TM\) Graphics$|Radeon\(TM\) Graphics$|Radeon Graphics$|Vega \d+ Graphics|"
                            r"Radeon\(TM\) \d{3}M\b|^Intel\(R\) Arc\(TM\) Graphics$", re.I)


def _run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=_NOWIN)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _nvidia():
    exe = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    if not os.path.isfile(exe):
        return []
    out = _run([exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])
    gpus = []
    for line in out.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) >= 3:
            try:
                gpus.append({"name": p[0], "vendor": "NVIDIA", "vram_gb": round(float(p[1]) / 1024, 1),
                             "driver": p[2], "dedicated": True})
            except ValueError:
                pass
    return gpus


def _vram_registry():
    """Memoria real de cada tarjeta (Win32 corta en 4 GB). {nombre: GB}"""
    out = {}
    try:
        import winreg
        base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
            for i in range(64):
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                try:
                    with winreg.OpenKey(k, sub) as s:
                        name = winreg.QueryValueEx(s, "DriverDesc")[0]
                        mem = winreg.QueryValueEx(s, "HardwareInformation.qwMemorySize")[0]
                        out[name] = round(int(mem) / 1024 ** 3, 1)
                except OSError:
                    pass
    except Exception:
        pass
    return out


def _windows_gpus():
    if sys.platform != "win32":
        return []
    out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterCompatibility,AdapterRAM,"
                "PNPDeviceID | ConvertTo-Json -Compress"], timeout=20)
    try:
        data = json.loads(out) if out.strip() else []
    except ValueError:
        data = []
    if isinstance(data, dict):
        data = [data]
    reg = _vram_registry()
    gpus = []
    for d in data:
        name = (d.get("Name") or "").strip()
        pnp = (d.get("PNPDeviceID") or "").upper()
        vendor = ("NVIDIA" if "VEN_10DE" in pnp or "NVIDIA" in name.upper() else
                  "AMD" if "VEN_1002" in pnp or re.search(r"AMD|Radeon", name, re.I) else
                  "Intel" if "VEN_8086" in pnp or "Intel" in name else (d.get("AdapterCompatibility") or "?"))
        vram = reg.get(name) or round((d.get("AdapterRAM") or 0) / 1024 ** 3, 1)
        dedicated = vendor in ("NVIDIA", "AMD", "Intel") and not _NOT_DEDICATED.search(name)
        if vendor == "Intel":
            dedicated = dedicated and bool(re.search(r"Arc.*\b[AB]\d{3}", name, re.I))
        gpus.append({"name": name, "vendor": vendor, "vram_gb": vram, "driver": "", "dedicated": dedicated})
    return gpus


def _forced():
    v = (os.environ.get("ANS_DEVICE") or "").strip().lower()
    if not v:
        try:
            with open(CFG_FILE, encoding="utf-8") as f:
                v = str(json.load(f).get("device", "")).lower()
        except Exception:
            v = ""
    return v if v in ("cpu", "gpu") else "auto"


def info(refresh=False):
    """{device: gpu|cpu, backend: cuda|directml|cpu, name, vendor, vram_gb, forced, gpus:[...]}"""
    with _lock:
        if _cache and not refresh:
            return dict(_cache)
        nv = _nvidia()
        others = [g for g in _windows_gpus() if g["vendor"] != "NVIDIA" or not nv]
        gpus = nv + others
        ded = [g for g in gpus if g["dedicated"]]
        best = max(ded, key=lambda g: (g["vendor"] == "NVIDIA", g["vram_gb"])) if ded else None
        forced = _forced()
        use = best is not None and forced != "cpu"
        backend = ("cuda" if best["vendor"] == "NVIDIA" else "directml") if use else "cpu"
        _cache.clear()
        _cache.update(device="gpu" if use else "cpu", backend=backend, name=best["name"] if use else "",
                      vendor=best["vendor"] if use else "", vram_gb=best["vram_gb"] if use else 0.0,
                      forced=forced, gpus=gpus, cpu_threads=os.cpu_count() or 2)
        return dict(_cache)


def use_cuda():
    """¿Mandar a la tarjeta lo que solo funciona con NVIDIA (Whisper, ComfyUI)?"""
    return info()["backend"] == "cuda"


def onnx_provider():
    """Proveedor para sherpa-onnx: cuda | directml | cpu (si la librería no lo trae, ella misma vuelve a cpu)."""
    return info()["backend"]


def ort_providers(available):
    """Orden de proveedores para onnxruntime según lo que traiga la librería instalada."""
    want = {"cuda": ["CUDAExecutionProvider", "DmlExecutionProvider"],
            "directml": ["DmlExecutionProvider"], "cpu": []}[info()["backend"]]
    return [p for p in want if p in available] + ["CPUExecutionProvider"]


def summary():
    i = info()
    if i["device"] == "gpu":
        how = "NVIDIA CUDA" if i["backend"] == "cuda" else "DirectML"
        return f"Tarjeta gráfica: {i['name']} ({i['vram_gb']} GB) · el trabajo pesado va a la tarjeta ({how})"
    ded = [g for g in i["gpus"] if g["dedicated"]]
    if ded and i["forced"] == "cpu":
        return f"Procesador (elegido a mano; hay {ded[0]['name']})"
    return f"Procesador ({i['cpu_threads']} hilos) · no se encontró tarjeta gráfica dedicada"


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(summary())
    print(json.dumps(info(), ensure_ascii=False, indent=1))
