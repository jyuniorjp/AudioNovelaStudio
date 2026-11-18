# -*- coding: utf-8 -*-
"""Video MP4 para YouTube a partir del audio final: portada (o fondo oscuro), título y una onda de audio que se
mueve con la voz, para que la imagen no sea totalmente estática (recomendación de las guías de YouTube).
Usa FFmpeg instalado en la PC (winget «Gyan.FFmpeg», en el PATH o en una carpeta «ffmpeg» junto al programa)."""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

W, H, FPS = 1280, 720, 12
FONTS = ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeui.ttf")


def find_ffmpeg():
    here = os.path.dirname(os.path.abspath(sys.executable))
    for p in (os.path.join(here, "ffmpeg", "ffmpeg.exe"), os.path.join(here, "ffmpeg.exe"),
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg", "ffmpeg.exe")):
        if os.path.isfile(p):
            return p
    p = shutil.which("ffmpeg")
    if p:
        return p
    la = os.environ.get("LOCALAPPDATA", "")
    for pat in (os.path.join(la, "Microsoft", "WinGet", "Links", "ffmpeg.exe"),
                os.path.join(la, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "*", "bin", "ffmpeg.exe"),
                r"C:\ffmpeg\bin\ffmpeg.exe", r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"):
        hits = glob.glob(pat)
        if hits:
            return hits[0]
    return ""


def _esc(path):
    """Ruta para usar dentro de un filtro de FFmpeg (C:/... -> C\\:/...)."""
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _dur(ffmpeg, path):
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", r.stderr)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return 0.0


def make_mp4(audio_path, out_path, image=None, title="", color="0x22d3ee", progress=None, cancel=None):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFMPEG_MISSING")
    total = _dur(ffmpeg, audio_path) or 1.0
    font = next((f for f in FONTS if os.path.isfile(f)), "")
    tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    tmp.write((title or "").strip()[:80])
    tmp.close()
    args = [ffmpeg, "-hide_banner", "-y", "-nostdin"]
    if image and os.path.isfile(image):
        args += ["-loop", "1", "-framerate", str(FPS), "-i", image]
        bg = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,"
              f"eq=brightness=-0.08,format=yuv420p[bg]")
    else:
        args += ["-f", "lavfi", "-i", f"color=c=0x0b0f1a:s={W}x{H}:r={FPS}"]
        bg = "[0:v]format=yuv420p[bg]"
    args += ["-i", audio_path]
    f = (bg + f";[1:a]aformat=channel_layouts=mono,showwaves=s={W}x150:mode=cline:rate={FPS}:"
         f"colors={color}@0.85:scale=sqrt[wv];[bg][wv]overlay=0:{H - 190}:format=auto[v1]")
    if title and font:
        f += (f";[v1]drawtext=fontfile='{_esc(font)}':textfile='{_esc(tmp.name)}':fontsize=46:fontcolor=white:"
              f"x=(w-text_w)/2:y=70:box=1:boxcolor=0x000000@0.45:boxborderw=18[v]")
    else:
        f += ";[v1]null[v]"
    args += ["-filter_complex", f, "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast",
             "-tune", "stillimage", "-crf", "26", "-r", str(FPS), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
             "-shortest", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", out_path + ".part.mp4"]
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                         errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    err = []
    import threading
    th = threading.Thread(target=lambda: err.extend(p.stderr.readlines()[-30:]), daemon=True)
    th.start()
    try:
        for line in p.stdout:
            if cancel is not None and cancel.is_set():
                p.kill()
                break
            m = re.match(r"out_time_(?:ms|us)=(\d+)", line)
            if m and progress:
                progress(min(1.0, int(m.group(1)) / 1e6 / total))
        p.wait()
        th.join(2)
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
    part = out_path + ".part.mp4"
    if cancel is not None and cancel.is_set():
        try:
            os.remove(part)
        except OSError:
            pass
        raise RuntimeError("CANCELLED")
    if p.returncode != 0 or not os.path.isfile(part):
        try:
            os.remove(part)
        except OSError:
            pass
        raise RuntimeError("FFmpeg no pudo crear el video:\n" + "".join(err)[-800:])
    os.replace(part, out_path)
    return out_path


# ============================================================================ video de audionovela con fondo animado
BACKGROUNDS = {          # nombre visible -> parámetros del bucle de partículas
    "Estrellas": dict(bg=((4, 6, 18), (18, 12, 52), (40, 30, 110)), n=260, kind="star", col=(220, 230, 255)),
    "Lluvia": dict(bg=((4, 7, 14), (10, 24, 40), (20, 50, 80)), n=220, kind="rain", col=(150, 190, 230)),
    "Brasas": dict(bg=((10, 4, 2), (36, 10, 4), (90, 30, 8)), n=120, kind="ember", col=(255, 150, 60)),
    "Neón": dict(bg=((7, 10, 18), (40, 12, 70), (10, 90, 120)), n=45, kind="orb", col=(120, 220, 255)),
}
LOOP_SECONDS, LOOP_FPS = 12, 24


def _encoders(ffmpeg):
    """NVIDIA (rápido) si la tarjeta lo permite; si no, el procesador."""
    try:
        r = subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=s=256x144:d=0.2", "-c:v", "h264_nvenc",
                            "-f", "null", "-"], capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode == 0:
            return [["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "24"], ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]]
    except Exception:
        pass
    return [["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]]


def _gradient(w, h, c0, c1, c2):
    import numpy as np
    y = np.linspace(0, 1, h)[:, None]
    x = np.linspace(0, 1, w)[None, :]
    a, b, c = (np.array(v, float) for v in (c0, c1, c2))
    t = np.clip(y * 0.8 + 0.2 * x, 0, 1)[..., None]
    img = a * (1 - t) + b * t
    r = np.sqrt((x - 0.5) ** 2 + (y - 0.35) ** 2)[..., None]
    k = np.clip(0.55 - r, 0, 1) * 0.6
    return img * (1 - k) + c * k


def render_loop(name, w, h, cache_dir, ffmpeg, progress=None, cancel=None):
    """Bucle de 12 s que empieza y termina igual (se repite sin corte). Se crea una vez y se guarda."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    os.makedirs(cache_dir, exist_ok=True)
    out = os.path.join(cache_dir, f"{name}_{w}x{h}_v1.mp4")
    if os.path.isfile(out) and os.path.getsize(out) > 10000:
        return out
    p = BACKGROUNDS[name]
    rng = np.random.default_rng(7)
    base = _gradient(w, h, *p["bg"])
    n, kind, col = p["n"], p["kind"], p["col"]
    x0, y0 = rng.random(n) * w, rng.random(n) * h
    size = rng.random(n)
    loops = rng.integers(1, 3, n)
    phase = rng.random(n) * 6.283
    frames = LOOP_SECONDS * LOOP_FPS
    s = w / 1280.0
    for enc in _encoders(ffmpeg):
        part = out + ".part.mp4"
        cmd = [ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(LOOP_FPS),
               "-i", "-", *enc, "-pix_fmt", "yuv420p", part]
        pr = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ok = True
        try:
            for f in range(frames):
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("CANCELLED")
                u = f / frames
                breath = 0.92 + 0.08 * np.sin(6.283 * u)
                layer = Image.new("RGB", (w // 2, h // 2), (0, 0, 0))
                d = ImageDraw.Draw(layer)
                if kind == "star":
                    tw = 0.35 + 0.65 * (0.5 + 0.5 * np.sin(6.283 * loops * u * 2 + phase))
                    xs, ys = (x0 + 18 * s * np.sin(6.283 * u + phase)) / 2, y0 / 2
                    for i in range(n):
                        r = (0.4 + 1.2 * size[i]) * s
                        d.ellipse((xs[i] - r, ys[i] - r, xs[i] + r, ys[i] + r), fill=tuple(int(c * tw[i]) for c in col))
                elif kind == "rain":
                    yy = y0 + loops * h * u * 3
                    ys, xs = yy % h / 2, (x0 - 0.18 * yy) % w / 2
                    for i in range(n):
                        L = (6 + 14 * size[i]) * s
                        v = 0.25 + 0.5 * size[i]
                        d.line((xs[i], ys[i], xs[i] - L * 0.18, ys[i] - L), fill=tuple(int(c * v) for c in col), width=1)
                elif kind == "ember":
                    ys = (y0 - loops * h * u) % h / 2
                    xs = (x0 + 30 * s * np.sin(6.283 * loops * u + phase)) / 2
                    fl = 0.5 + 0.5 * np.sin(6.283 * 3 * u + phase)
                    for i in range(n):
                        r = (0.8 + 2.2 * size[i]) * s
                        v = (0.4 + 0.6 * fl[i]) * (ys[i] / (h / 2)) ** 0.6
                        d.ellipse((xs[i] - r, ys[i] - r, xs[i] + r, ys[i] + r), fill=tuple(int(c * v) for c in col))
                else:
                    xs = (x0 + loops * w * u * 0.25 * np.sign(phase - 3)) % w / 2
                    ys = (y0 + 40 * s * np.sin(6.283 * u + phase)) / 2
                    for i in range(n):
                        r = (6 + 22 * size[i]) * s
                        v = 0.10 + 0.14 * (0.5 + 0.5 * np.sin(6.283 * u * loops[i] + phase[i]))
                        cc = col if i % 3 else (244, 114, 182)
                        d.ellipse((xs[i] - r, ys[i] - r, xs[i] + r, ys[i] + r), fill=tuple(int(c * v) for c in cc))
                glow = layer.filter(ImageFilter.GaussianBlur((2.2 if kind != "orb" else 9) * s)).resize((w, h), Image.BILINEAR)
                sharp = layer.resize((w, h), Image.BILINEAR)
                g = np.asarray(glow, float) * 1.6 + np.asarray(sharp, float) * (0.9 if kind != "orb" else 0.3)
                pr.stdin.write(np.clip(base * breath + g, 0, 255).astype(np.uint8).tobytes())
                if progress and f % 12 == 0:
                    progress(f / frames)
            pr.stdin.close()
            pr.wait()
        except (BrokenPipeError, OSError):
            ok = False
            pr.kill()
        except RuntimeError:
            pr.kill()
            try:
                os.remove(part)
            except OSError:
                pass
            raise
        if ok and pr.returncode == 0 and os.path.isfile(part):
            os.replace(part, out)
            return out
    raise RuntimeError("No se pudo crear el fondo animado.")


def _ass_color(hexcol, alpha=0):
    h = hexcol.lstrip("#")
    if len(h) != 6:
        h = "e7edff"
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _ass_time(s):
    s = max(0.0, s)
    return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}"


def _ass_text(t):
    return t.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(path, w, h, title, marks, chunks, chapter_times, chapter_names, colors, subs=True, names=True):
    """Capa de texto: título al inicio, tarjeta de cada capítulo, subtítulos y el nombre de quien habla en su color."""
    vertical = h > w
    fs = int(h * (0.055 if not vertical else 0.032))
    head = ("[Script Info]\nScriptType: v4.00+\n"
            f"PlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding\n"
            f"Style: Sub,Segoe UI,{fs},&H00FFFFFF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,{max(2, fs // 14)},1,2,"
            f"{int(w * 0.08)},{int(w * 0.08)},{int(h * (0.20 if not vertical else 0.22))},1\n"
            f"Style: Who,Segoe UI,{int(fs * 0.72)},&H00FFFFFF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,"
            f"{max(2, fs // 16)},0,2,{int(w * 0.08)},{int(w * 0.08)},{int(h * (0.20 if not vertical else 0.22)) + int(fs * 2.6)},1\n"
            f"Style: Title,Bahnschrift,{int(fs * 2.0)},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,1,0,1,"
            f"{max(2, fs // 12)},2,5,{int(w * 0.06)},{int(w * 0.06)},0,1\n"
            f"Style: Chap,Bahnschrift,{int(fs * 1.15)},&H00EEDB22,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,1,0,1,"
            f"{max(2, fs // 14)},1,8,{int(w * 0.06)},{int(w * 0.06)},{int(h * 0.08)},1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ev = []
    if title:
        ev.append(f"Dialogue: 2,{_ass_time(0.3)},{_ass_time(5.5)},Title,,0,0,0,,{{\\fad(600,800)}}{_ass_text(title)}")
    for i, t0 in sorted(chapter_times.items()):
        if i < len(chapter_names) and chapter_names[i] and t0 > 1.0 or (i == 0 and not title and chapter_names):
            ev.append(f"Dialogue: 2,{_ass_time(t0)},{_ass_time(t0 + 4.0)},Chap,,0,0,0,,{{\\fad(400,700)}}"
                      f"{_ass_text(chapter_names[i])}")
    if subs:
        for a, b, txt, ci in marks:
            t = _ass_text(txt.strip())
            if not t:
                continue
            ev.append(f"Dialogue: 1,{_ass_time(a)},{_ass_time(max(b, a + 0.4))},Sub,,0,0,0,,{t}")
    if names:
        # el nombre aparece mientras habla ese personaje (no para el narrador)
        run = None
        for a, b, _txt, ci in list(marks) + [(None, None, None, None)]:
            who = chunks[ci].who if ci is not None and 0 <= ci < len(chunks) else None
            if run and (who != run[2] or a is None or a - run[1] > 1.2):
                col = _ass_color(colors.get(run[2], "#e7edff"))
                ev.append(f"Dialogue: 1,{_ass_time(run[0])},{_ass_time(run[1])},Who,,0,0,0,,{{\\c{col}}}{_ass_text(run[2])}")
                run = None
            if who and a is not None:
                run = [a, b, who] if run is None else [run[0], b, who]
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(ev) + "\n")


def _slides(images, total, min_len=4.0):
    """[(t0, ruta)] -> [(t0, duración, ruta)] sin tramos cortos (se unen al anterior); el primero empieza en 0."""
    ims = sorted((max(0.0, float(t)), p) for t, p in (images or []) if p and os.path.isfile(p))
    out = []
    for t, p in ims:
        if t >= total - min_len:
            break
        if out and t - out[-1][0] < min_len:
            continue
        out.append((0.0 if not out else t, p))
    return [(t, (out[k + 1][0] if k + 1 < len(out) else total) - t, p) for k, (t, p) in enumerate(out)]


def make_novel_video(audio_path, out_path, res=None, chunks=None, chapter_names=(), title="", background="Estrellas",
                     image=None, colors=None, subs=True, names=True, wave_=True, vertical=False, cache_dir="",
                     progress=None, cancel=None, images=None):
    """Video de la audionovela: fondo animado (o la portada con zoom lento) + título, capítulos, subtítulos,
    nombre de quien habla y onda de la voz. El audio ya trae la música de fondo mezclada."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFMPEG_MISSING")
    w, h = (720, 1280) if vertical else (1280, 720)
    total = (res.duration if res is not None else 0) or _dur(ffmpeg, audio_path) or 1.0
    say = progress or (lambda *_: None)
    tmpd = tempfile.mkdtemp(prefix="ans_vid_")
    ass = os.path.join(tmpd, "capa.ass")
    build_ass(ass, w, h, title, res.marks if res is not None else [], chunks or [],
              res.chapter_times if res is not None else {}, list(chapter_names), colors or {}, subs, names)
    args = [ffmpeg, "-hide_banner", "-y", "-nostdin"]
    slides = _slides(images, total)
    ai = 1                                   # índice de la entrada de audio
    if slides:
        # una imagen por tramo (p. ej. por capítulo) con zoom/paneo lento y fundido de 1 s entre ellas
        fade = 1.0
        bg, prev = "", ""
        for k, (t0, dur, path) in enumerate(slides):
            last = k == len(slides) - 1
            args += ["-loop", "1", "-framerate", str(LOOP_FPS), "-t", f"{dur + (0.5 if last else fade):.3f}", "-i", path]
            per = LOOP_FPS * (22 + 5 * (k % 3))
            bg += (f"[{k}:v]scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
                   f"zoompan=z='1.09+0.04*sin(on/{per}*6.283+{k})':"
                   f"x='(iw-iw/zoom)/2*(1+0.8*sin(on/{per * 1.3:.0f}*6.283+{k * 2}))':"
                   f"y='(ih-ih/zoom)/2*(1+0.5*cos(on/{per * 1.7:.0f}*6.283+{k}))':"
                   f"d=1:s={w}x{h}:fps={LOOP_FPS},eq=brightness=-0.10,setsar=1,format=yuv420p[s{k}];")
        prev = "s0"
        for k in range(1, len(slides)):
            bg += f"[{prev}][s{k}]xfade=transition=fade:duration={fade}:offset={slides[k][0]:.3f}[x{k}];"
            prev = f"x{k}"
        bg += f"[{prev}]null[bg]"
        ai = len(slides)
    elif image and os.path.isfile(image):
        args += ["-loop", "1", "-framerate", str(LOOP_FPS), "-i", image]
        # zoom muy lento (efecto Ken Burns) que va y vuelve
        bg = (f"[0:v]scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
              f"zoompan=z='1.04+0.04*sin(on/{LOOP_FPS * 20}*6.283)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
              f"d=1:s={w}x{h}:fps={LOOP_FPS},eq=brightness=-0.10,format=yuv420p[bg]")
    else:
        say("Creando el fondo animado (solo la primera vez)…", 0.0)
        loop = render_loop(background if background in BACKGROUNDS else "Estrellas", w, h, cache_dir or tmpd, ffmpeg,
                           lambda f: say("Creando el fondo animado (solo la primera vez)…", f), cancel)
        args += ["-stream_loop", "-1", "-i", loop]
        bg = "[0:v]format=yuv420p[bg]"
    args += ["-i", audio_path]
    f = bg
    if wave_:
        wh_ = int(h * 0.16)
        f += (f";[{ai}:a]aformat=channel_layouts=mono,showwaves=s={w}x{wh_}:mode=cline:rate={LOOP_FPS}:"
              f"colors=0x22d3ee:scale=sqrt:draw=full,format=rgba,colorchannelmixer=aa=0.6[wv];"
              f"[bg][wv]overlay=0:{h - wh_ - int(h * 0.02)}:format=yuv420[b2]")
    else:
        f += ";[bg]null[b2]"
    f += f";[b2]ass='{_esc(ass)}'[v]"
    err = []
    for enc in _encoders(ffmpeg):
        part = out_path + ".part.mp4"
        cmd = args + ["-filter_complex", f, "-map", "[v]", "-map", f"{ai}:a", *enc, "-r", str(LOOP_FPS), "-pix_fmt", "yuv420p",
                      "-c:a", "aac", "-b:a", "192k", "-shortest", "-t", f"{total:.2f}", "-movflags", "+faststart",
                      "-progress", "pipe:1", "-nostats", part]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                             errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        import threading
        th = threading.Thread(target=lambda: err.extend(p.stderr.readlines()[-20:]), daemon=True)
        th.start()
        for line in p.stdout:
            if cancel is not None and cancel.is_set():
                p.kill()
                break
            m = re.match(r"out_time_(?:ms|us)=(\d+)", line)
            if m:
                say("Creando el video…", min(1.0, int(m.group(1)) / 1e6 / total))
        p.wait()
        th.join(2)
        if cancel is not None and cancel.is_set():
            try:
                os.remove(part)
            except OSError:
                pass
            raise RuntimeError("CANCELLED")
        if p.returncode == 0 and os.path.isfile(part):
            os.replace(part, out_path)
            shutil.rmtree(tmpd, ignore_errors=True)
            return out_path
    shutil.rmtree(tmpd, ignore_errors=True)
    raise RuntimeError("FFmpeg no pudo crear el video:\n" + "".join(err)[-800:])
