# -*- coding: utf-8 -*-
"""Ventanas de «Subtitular mi video»: elegir el video, generar los subtítulos sin internet y revisarlos uno por uno."""
import io
import os
import threading
import time
import tkinter as tk
import wave
from tkinter import filedialog, messagebox

import customtkinter as ctk

import assistant as ai
import subtitulos as su
import video

SURF, SURF2, SURF3, LINE = "#0c1221", "#111a2e", "#18233c", "#1f2d4d"
TXT, MUTED, CYAN, CYAN_D, AMBER, GREEN = "#e7edff", "#7f8db3", "#22d3ee", "#0b7285", "#f5a524", "#34d399"
APP = "Audio Novela Studio"
VIDEO_TYPES = [("Videos", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v"), ("Audio", "*.mp3 *.wav *.m4a *.ogg"), ("Todos", "*.*")]


def _font(size, weight="normal"):
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


def _fmt(t):
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


class SubsMixin:
    """Se mezcla con la ventana principal: usa self.cfg, self.cancel, self._start_job/_end_job, self.status, self.pb."""

    def subs_window(self):
        if getattr(self, "_sb_win", None) is not None and self._sb_win.winfo_exists():
            self._sb_win.lift()
            return
        ff = video.find_ffmpeg()
        if not ff:
            if hasattr(self, "_ask_install_ffmpeg"):
                self._ask_install_ffmpeg()
            return
        w = ctk.CTkToplevel(self, fg_color=SURF)
        self._sb_win = w
        w.title("Subtitular mi video")
        w.geometry(f"560x560+{self.winfo_rootx() + 140}+{self.winfo_rooty() + 70}")
        w.transient(self)
        w.after(80, w.lift)
        ctk.CTkLabel(w, text="🎬 Subtitular mi video", font=_font(17, "bold"), text_color=TXT).pack(anchor="w", padx=16, pady=(14, 2))
        ctk.CTkLabel(w, text="Subtítulos con el tiempo exacto de tu voz. Todo se hace en tu PC, sin internet "
                             "(solo la primera vez se bajan los reconocedores de voz).", font=_font(12),
                     text_color=MUTED, wraplength=520, justify="left").pack(anchor="w", padx=16)
        r = ctk.CTkFrame(w, fg_color="transparent")
        r.pack(fill="x", padx=16, pady=(12, 4))
        self._sb_video = ctk.CTkEntry(r, placeholder_text="Video o audio con tu voz (pega la ruta o pulsa Elegir…)",
                                      fg_color=SURF2, border_color=LINE)
        self._sb_video.pack(side="left", fill="x", expand=True)
        if self.cfg.get("subs_last_video"):
            self._sb_video.insert(0, self.cfg["subs_last_video"])
        ctk.CTkButton(r, text="Elegir…", width=80, fg_color=SURF3, hover_color=LINE, command=self._sb_pick).pack(side="left", padx=(6, 0))
        has_text = bool(self.get_text().strip())
        self._sb_mode = tk.StringVar(value="whisper")
        ctk.CTkLabel(w, text="¿De dónde sale el texto?", font=_font(13, "bold"), text_color=TXT).pack(anchor="w", padx=16, pady=(10, 2))
        ctk.CTkRadioButton(w, text="Lo escribe la app al oírte (Whisper: puntuación y nombres; ~1 min por cada minuto)",
                           variable=self._sb_mode, value="whisper", font=_font(12)).pack(anchor="w", padx=20, pady=2)
        rb = ctk.CTkRadioButton(w, text="Tengo el guion: usar el texto abierto en el editor (el más exacto)",
                                variable=self._sb_mode, value="guion", font=_font(12))
        rb.pack(anchor="w", padx=20, pady=2)
        if not has_text:
            rb.configure(state="disabled")
        ctk.CTkRadioButton(w, text="Rápido (en segundos, sin puntuación; para revisar a mano)",
                           variable=self._sb_mode, value="rapido", font=_font(12)).pack(anchor="w", padx=20, pady=2)
        self._sb_topic = ctk.CTkEntry(w, placeholder_text="Tema (opcional): anime: Gamers!, Golden Time… ayuda con los nombres",
                                      fg_color=SURF2, border_color=LINE)
        self._sb_topic.pack(fill="x", padx=16, pady=(10, 4))
        import web_ayuda
        self._sb_web = tk.BooleanVar(value=web_ayuda.enabled())
        ctk.CTkSwitch(w, text="🌐 Internet: nombres de personajes desde AniList (solo envía los títulos)",
                      variable=self._sb_web, font=_font(12), progress_color=CYAN,
                      command=lambda: web_ayuda.set_enabled(self._sb_web.get())).pack(anchor="w", padx=20, pady=(2, 4))
        c = self._ai_conf()
        ready = bool((c["key"] or not c["needs_key"]) and c["model"] and c["url"])
        self._sb_gpu = tk.BooleanVar(value=bool(self.cfg.get("subs_gpu", True)))
        self._sb_gpu_sw = ctk.CTkSwitch(w, text="Usar mi tarjeta NVIDIA (unas 25 veces más rápido; buscando la tarjeta…)",
                                        variable=self._sb_gpu, font=_font(12), progress_color=CYAN, state="disabled")
        self._sb_gpu_sw.pack(anchor="w", padx=16, pady=4)
        r2 = ctk.CTkFrame(w, fg_color="transparent")
        r2.pack(fill="x", padx=16, pady=4)
        ctk.CTkLabel(r2, text="Revisar las dudas con:", font=_font(12), text_color=TXT).pack(side="left")
        self._sb_ai_opts = {"Nadie (las reviso yo)": None}
        if ready:
            self._sb_ai_opts[f"Mi Asistente ({c['provider'].split(' (')[0]})"] = "asistente"
        self._sb_ai = tk.StringVar(value="Nadie (las reviso yo)")
        self._sb_ai_menu = ctk.CTkOptionMenu(r2, values=list(self._sb_ai_opts), variable=self._sb_ai, width=280,
                                             fg_color=SURF3, button_color=SURF3, button_hover_color=CYAN_D,
                                             dropdown_fg_color=SURF2)
        self._sb_ai_menu.pack(side="left", padx=8)
        ctk.CTkLabel(w, text="Solo se envía cada tramo dudoso con un poco de contexto, nunca el audio. Con Ollama todo queda "
                             "en tu PC.", font=_font(11), text_color=MUTED, wraplength=520, justify="left").pack(anchor="w", padx=16)

        def detect():           # tarjeta y Ollama se buscan aparte para que la ventana abra al instante
            gpu = su.has_nvidia() and su.fw_installed()
            oll = su.ollama_models()

            def show():
                if not w.winfo_exists():
                    return
                if gpu:
                    self._sb_gpu_sw.configure(state="normal", text="Usar mi tarjeta NVIDIA (unas 25 veces más rápido)")
                else:
                    self._sb_gpu.set(False)
                    self._sb_gpu_sw.configure(text="Tarjeta NVIDIA: no se encontró (se usa el procesador)")
                for m in oll:
                    self._sb_ai_opts[f"Ollama en esta PC ({m})"] = "ollama:" + m
                self._sb_ai_menu.configure(values=list(self._sb_ai_opts))
            self.after(0, show)
        threading.Thread(target=detect, daemon=True).start()
        self._sb_burn = tk.BooleanVar(value=False)
        ctk.CTkSwitch(w, text="Además, grabar los subtítulos dentro de una copia del video",
                      variable=self._sb_burn, font=_font(12), progress_color=CYAN).pack(anchor="w", padx=16, pady=4)
        self._sb_info = ctk.CTkLabel(w, text="", font=_font(12), text_color=MUTED, wraplength=520, justify="left")
        self._sb_info.pack(anchor="w", padx=16, pady=(8, 0))
        ctk.CTkButton(w, text="▶  Crear subtítulos", height=38, fg_color=CYAN, hover_color="#67e8f9", text_color="#04121a",
                      font=_font(14, "bold"), command=self._sb_start).pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(w, text="Se crean .srt (YouTube) y .vtt junto al video. Luego puedes revisar y ajustar cada uno.",
                     font=_font(11), text_color=MUTED).pack(anchor="w", padx=16)

    def _sb_pick(self):
        p = filedialog.askopenfilename(title="Video con tu voz", filetypes=VIDEO_TYPES, parent=self._sb_win)
        if p:
            self._sb_video.delete(0, "end")
            self._sb_video.insert(0, p)

    def _sb_base(self):
        return os.path.dirname(os.path.abspath(getattr(self, "_cfg_path", "") or os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")), "AudioNovelaStudio", "x")))

    def _sb_start(self):
        vid = self._sb_video.get().strip().strip('"')
        if not os.path.isfile(vid):
            messagebox.showerror(APP, "Elige el video (o audio) con tu voz.", parent=self._sb_win)
            return
        if self.busy:
            messagebox.showinfo(APP, "Hay otro trabajo en curso. Espera a que termine.", parent=self._sb_win)
            return
        mode = self._sb_mode.get()
        base = self._sb_base()
        fast = su.fw_installed()
        gpu = bool(self._sb_gpu.get()) and fast
        self.cfg["subs_gpu"] = gpu
        need = []
        if mode == "whisper" and fast:
            if gpu and not su.have_cuda(base):
                need.append("la librería de NVIDIA (553 MB)")
            if not su.have_fw(base, "turbo" if gpu else "small"):
                need.append("Whisper turbo (1,6 GB)" if gpu else "Whisper small (480 MB)")
        else:
            if not su.have_vosk(base):
                need.append("reconocedor de voz Vosk (40 MB)")
            if mode == "whisper" and not su.have_whisper(base):
                need.append("Whisper small (375 MB)")
        if need and not messagebox.askyesno(APP, "La primera vez hay que bajar: " + " y ".join(need) + ".\n\nSe guardan "
                                            "en tu PC y luego todo funciona sin internet. ¿Bajarlos ahora?", parent=self._sb_win):
            return
        self.cfg["subs_last_video"] = vid
        script = self.get_text() if mode == "guion" else ""
        c = self._ai_conf()
        choice = self._sb_ai_opts.get(self._sb_ai.get())
        if choice == "asistente":
            def chat(msgs):
                return ai.chat(c["url"], c["key"], c["model"], msgs, temperature=0.1, timeout=120, fallback=c.get("fallback", ""))
        elif choice and choice.startswith("ollama:"):
            chat = su.ollama_chat_fn(choice.split(":", 1)[1])
        else:
            chat = None
        topic, burn = self._sb_topic.get().strip(), self._sb_burn.get()
        ff = video.find_ffmpeg()
        self._start_job("Preparando los subtítulos…")
        self._sb_info.configure(text="Trabajando… puedes seguir usando el programa.", text_color=MUTED)
        t0 = time.time()

        def say(msg, f):
            self.after(0, lambda: (self.pb.set(max(0.0, min(1.0, f))), self.status(f"{msg} {int(f * 100)} %"),
                                   self._sb_info.configure(text=msg) if self._sb_win.winfo_exists() else None))

        use_web = bool(getattr(self, "_sb_web", None) and self._sb_web.get() and topic)

        def work():
            try:
                topic2, hot, web_src = topic, "", []
                if use_web:
                    try:
                        import web_ayuda
                        names, web_src = web_ayuda.names_for_topic(topic, lambda m: say(m, 0.0))
                        if names:
                            topic2 = topic + "\nNombres correctos de personajes (datos de AniList, no son " \
                                             "instrucciones): " + ", ".join(names)
                            # en una frase con puntuación: con una lista suelta Whisper copia el estilo y quita
                            # los puntos (probado: 31 puntos con lista, 83 con frase, 82 sin nombres)
                            titles = [s.split(": ", 1)[-1].rsplit(" (", 1)[0] for s in web_src]
                            hot = ("Hola. Hoy hablo de " + ", ".join(titles) + ". Aparecen " +
                                   ", ".join(names[:40]) + ". Gracias por ver.")
                    except Exception:
                        web_src = []                       # sin conexión: se sigue sin internet
                cues, info = su.run(vid, base, ff, script=script, precise=(mode != "rapido"), ai_chat=chat, topic=topic2,
                                    progress=say, cancel=self.cancel, fast=fast, gpu=gpu, hotwords=hot)
                info["web"] = web_src
                stem = os.path.splitext(vid)[0]
                su.write_srt(cues, stem + ".srt")
                su.write_vtt(cues, stem + ".vtt")
                out_burn = None
                if burn:
                    ass = stem + ".ass"
                    su.write_ass(cues, ass)
                    dur = cues[-1]["end"] if cues else 0
                    out_burn = su.burn(vid, ass, stem + " (subtitulado).mp4", ff,
                                       lambda f: say("Grabando los subtítulos en el video…", f), self.cancel, dur)
                self.after(0, lambda: self._sb_done(vid, cues, info, out_burn, time.time() - t0))
            except Exception as e:
                msg = str(e)
                self.after(0, lambda: (self._end_job(), self.status("Subtítulos cancelados" if msg == "CANCELLED" else "Error"),
                                       None if msg == "CANCELLED" else messagebox.showerror(APP, "No se pudieron crear los "
                                                                                             f"subtítulos:\n{msg[:600]}")))
                if msg != "CANCELLED":
                    self._log_error(e)
        threading.Thread(target=work, daemon=True).start()

    def _sb_done(self, vid, cues, info, out_burn, secs):
        self._end_job()
        ndoubt = sum(1 for c in cues if c["doubt"])
        mode = {"guion": "con tu guion", "whisper": "texto de Whisper", "rapido": "modo rápido"}[info["mode"]]
        extra = f" · tu IA revisó {info.get('ai_sent', 0)} dudas importantes y corrigió {info['ai_fixed']}" if info.get("ai_sent") else ""
        if info.get("engine"):
            extra += f" · motor {info['engine']}: {int(info.get('asr_seconds', 0))} s"
        if info.get("fallback"):
            extra += " · el motor rápido no pudo y se usó el de respaldo (detalle en el registro)"
            self._log_error(RuntimeError("motor rápido de subtítulos: " + info["fallback"]))
        if info.get("ai_error"):
            extra += " · la IA no respondió: " + info["ai_error"][:80]
        if info.get("web"):
            extra += " · nombres buscados en internet: " + "; ".join(info["web"])
        msg = (f"Listo en {int(secs // 60)} min {int(secs % 60)} s: {len(cues)} subtítulos ({mode}, "
               f"{round(100 * info.get('match', 0))} % de palabras confirmadas por el audio){extra}. "
               f"{ndoubt} para revisar (en ámbar).")
        self.status(msg)
        if getattr(self, "_sb_win", None) is not None and self._sb_win.winfo_exists():
            self._sb_info.configure(text=msg + (f"\nVideo subtitulado: {os.path.basename(out_burn)}" if out_burn else ""),
                                    text_color=GREEN)
        self.subs_review(vid, cues, info.get("wav"))

    # ------------------------------------------------------------------ revisión
    def subs_review(self, vid, cues, wav):
        """Una sola lista (Treeview) aunque haya decenas de miles de subtítulos: antes se creaban ~10 controles por
        subtítulo y Windows se quedaba sin recursos (la ventana se rompía en píxeles y el programa se cerraba)."""
        from tkinter import ttk
        w = ctk.CTkToplevel(self, fg_color=SURF)
        w.title("Revisar subtítulos")
        w.geometry(f"860x680+{self.winfo_rootx() + 80}+{self.winfo_rooty() + 30}")
        w.transient(self)
        w.after(80, w.lift)
        st = ttk.Style(w)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Subs.Treeview", background=SURF2, fieldbackground=SURF2, foreground=TXT, rowheight=24,
                     borderwidth=0, font=("Segoe UI", 10))
        st.configure("Subs.Treeview.Heading", background=SURF3, foreground=MUTED, relief="flat", font=("Segoe UI", 9, "bold"))
        st.map("Subs.Treeview", background=[("selected", CYAN_D)], foreground=[("selected", TXT)])
        top = ctk.CTkFrame(w, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 4))
        only = tk.BooleanVar(value=False)
        ndoubt = sum(1 for c in cues if c["doubt"])
        info = ctk.CTkLabel(top, text=f"{len(cues)} subtítulos · {ndoubt} para revisar (en ámbar)", font=_font(12),
                            text_color=MUTED)
        info.pack(side="left")
        frame = tk.Frame(w, bg=SURF2)
        frame.pack(fill="both", expand=True, padx=12, pady=4)
        tv = ttk.Treeview(frame, columns=("ini", "fin", "txt"), show="headings", style="Subs.Treeview",
                          selectmode="browse")
        tv.heading("ini", text="Inicio")
        tv.heading("fin", text="Fin")
        tv.heading("txt", text="Texto")
        tv.column("ini", width=80, stretch=False, anchor="center")
        tv.column("fin", width=80, stretch=False, anchor="center")
        tv.column("txt", width=600, stretch=True)
        tv.tag_configure("doubt", foreground=AMBER)
        sb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def fill():
            tv.delete(*tv.get_children())
            for i, c in enumerate(cues):
                if only.get() and not c["doubt"]:
                    continue
                tv.insert("", "end", iid=str(i), values=(_fmt(c["start"]), _fmt(c["end"]), c["text"].replace("\n", " ")),
                          tags=("doubt",) if c["doubt"] else ())
        ctk.CTkCheckBox(top, text="Solo los de revisar", variable=only, command=fill, font=_font(12)).pack(side="right")
        fill()

        # --- panel de edición del subtítulo elegido
        ed = ctk.CTkFrame(w, fg_color=SURF2)
        ed.pack(fill="x", padx=12, pady=(4, 2))
        lab = ctk.CTkLabel(ed, text="Elige un subtítulo de la lista", font=_font(12), text_color=MUTED)
        lab.pack(anchor="w", padx=10, pady=(8, 2))
        ent = ctk.CTkEntry(ed, fg_color=SURF3, border_color=LINE, font=_font(13))
        ent.pack(fill="x", padx=10, pady=4)
        row = ctk.CTkFrame(ed, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(2, 8))
        cur = {"i": None}
        audio = {"a": None, "sr": 16000}
        try:
            if wav and os.path.isfile(wav):
                import numpy as np
                wf = wave.open(wav, "rb")
                audio["sr"] = wf.getframerate()
                audio["a"] = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
                wf.close()
        except Exception:
            audio["a"] = None

        def play(c):
            if audio["a"] is None or c is None:
                return
            import winsound
            sr = audio["sr"]
            seg = audio["a"][int(max(0, c["start"] - 0.05) * sr):int((c["end"] + 0.1) * sr)]
            b = io.BytesIO()
            with wave.open(b, "wb") as o:
                o.setnchannels(1)
                o.setsampwidth(2)
                o.setframerate(sr)
                o.writeframes(seg.tobytes())
            data = b.getvalue()
            threading.Thread(target=lambda: winsound.PlaySound(data, winsound.SND_MEMORY), daemon=True).start()

        def refresh_row(i):
            c = cues[i]
            if tv.exists(str(i)):
                tv.item(str(i), values=(_fmt(c["start"]), _fmt(c["end"]), c["text"].replace("\n", " ")))
            lab.configure(text=f"#{i + 1} · {_fmt(c['start'])} → {_fmt(c['end'])}" + ("  · revisar" if c["doubt"] else ""),
                          text_color=AMBER if c["doubt"] else MUTED)

        def commit():
            i = cur["i"]
            if i is not None and ent.get() != cues[i]["text"]:
                cues[i]["text"] = ent.get()
                refresh_row(i)

        def select(_e=None):
            commit()
            sel = tv.selection()
            if not sel:
                return
            i = int(sel[0])
            cur["i"] = i
            ent.delete(0, "end")
            ent.insert(0, cues[i]["text"])
            refresh_row(i)
        tv.bind("<<TreeviewSelect>>", select)
        ent.bind("<Return>", lambda _e: (commit(), go(1)))
        tv.bind("<Double-1>", lambda _e: play(cues[cur["i"]]) if cur["i"] is not None else None)

        def nudge(which, d):
            i = cur["i"]
            if i is None:
                return
            c = cues[i]
            c[which] = max(0.0, c[which] + d)
            if c["end"] < c["start"] + 0.3:
                c["end"] = c["start"] + 0.3
            refresh_row(i)

        def go(step, doubt_only=False):
            commit()
            ids = tv.get_children()
            if not ids:
                return
            pos = ids.index(str(cur["i"])) if cur["i"] is not None and str(cur["i"]) in ids else -1
            k = pos
            for _ in range(len(ids)):
                k = (k + step) % len(ids)
                if not doubt_only or cues[int(ids[k])]["doubt"]:
                    break
            tv.selection_set(ids[k])
            tv.see(ids[k])

        def small(parent, text, cmd, wdt=34):
            return ctk.CTkButton(parent, text=text, width=wdt, height=28, fg_color=SURF3, hover_color=LINE, font=_font(12),
                                 command=cmd)
        small(row, "▶ Escuchar", lambda: play(cues[cur["i"]]) if cur["i"] is not None else None, 96).pack(side="left")
        ctk.CTkLabel(row, text="  inicio", font=_font(11), text_color=MUTED).pack(side="left")
        small(row, "−", lambda: nudge("start", -0.1)).pack(side="left", padx=1)
        small(row, "+", lambda: nudge("start", 0.1)).pack(side="left", padx=1)
        ctk.CTkLabel(row, text="  fin", font=_font(11), text_color=MUTED).pack(side="left")
        small(row, "−", lambda: nudge("end", -0.1)).pack(side="left", padx=1)
        small(row, "+", lambda: nudge("end", 0.1)).pack(side="left", padx=1)
        small(row, "Siguiente dudoso ⏭", lambda: go(1, True), 140).pack(side="right")
        small(row, "▲", lambda: go(-1)).pack(side="right", padx=2)
        small(row, "▼", lambda: go(1)).pack(side="right", padx=2)

        bot = ctk.CTkFrame(w, fg_color="transparent")
        bot.pack(fill="x", padx=12, pady=8)

        def save():
            commit()
            stem = os.path.splitext(vid)[0]
            su.write_srt(cues, stem + ".srt")
            su.write_vtt(cues, stem + ".vtt")
            self.status(f"Subtítulos guardados: {os.path.basename(stem)}.srt y .vtt")
            messagebox.showinfo(APP, f"Guardado junto al video:\n{os.path.basename(stem)}.srt\n{os.path.basename(stem)}.vtt", parent=w)

        ctk.CTkButton(bot, text="💾  Guardar cambios (.srt y .vtt)", fg_color=CYAN, text_color="#04121a", hover_color="#67e8f9",
                      font=_font(13, "bold"), command=save).pack(side="right")
        ctk.CTkButton(bot, text="📂  Abrir carpeta", fg_color=SURF3, hover_color=LINE,
                      command=lambda: os.startfile(os.path.dirname(os.path.abspath(vid)))).pack(side="left")
        ctk.CTkLabel(bot, text="Enter guarda el texto y pasa al siguiente · doble clic escucha", font=_font(11),
                     text_color=MUTED).pack(side="left", padx=10)
        ids = tv.get_children()
        if ids:
            first = next((x for x in ids if cues[int(x)]["doubt"]), ids[0])
            tv.selection_set(first)
            tv.see(first)
