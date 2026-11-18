# -*- coding: utf-8 -*-
"""Pestaña «Asistente»: conversar con una IA sobre la historia y aplicar lo que proponga."""

import threading
import tkinter as tk

import customtkinter as ctk

import assistant as ai
import brain

SURF, SURF2, SURF3, LINE = "#0c1221", "#111a2e", "#18233c", "#1f2d4d"
TXT, MUTED, CYAN, CYAN_D, AMBER, GREEN, RED = "#e7edff", "#7f8db3", "#22d3ee", "#0b7285", "#f5a524", "#34d399", "#f87171"
EMOS = {"neutral", "grito", "susurro", "pensamiento", "triste", "alegre", "enojo", "duda", "frio", "miedo", "ternura"}
AGES = {"niño", "joven", "adulto", "anciano"}


def _font(size, weight="normal"):
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


class AssistantMixin:
    # ------------------------------------------------------------ interfaz
    def _tab_assistant(self, t):
        a = self.cfg.setdefault("assistant", {})
        self.v_ai_prov = tk.StringVar(value=a.get("provider") if a.get("provider") in ai.PROVIDERS else "Gemini (Google)")
        self._ai_msgs = []          # conversación (sin el mensaje de sistema)
        self._ai_lines = {}         # L1 -> clave del segmento
        self._ai_pending = []
        self._ai_busy = False

        top = ctk.CTkFrame(t, fg_color="transparent")
        top.pack(fill="x", padx=4, pady=(2, 4))
        ctk.CTkOptionMenu(top, values=list(ai.PROVIDERS), variable=self.v_ai_prov, width=210, height=28,
                          fg_color=SURF3, button_color=SURF3, button_hover_color=CYAN_D, dropdown_fg_color=SURF2,
                          font=_font(12), command=lambda _v: self._ai_load_provider()).pack(side="left")
        self.btn_ai_cfg = ctk.CTkButton(top, text="Configurar", width=96, height=28, fg_color=SURF3, hover_color=LINE,
                                        font=_font(12), command=self._ai_toggle_cfg)
        self.btn_ai_cfg.pack(side="left", padx=6)
        self.lbl_ai_state = ctk.CTkLabel(top, text="", font=_font(11), text_color=MUTED)
        self.lbl_ai_state.pack(side="left")

        cfgf = self.ai_cfg = ctk.CTkFrame(t, fg_color=SURF2, corner_radius=10, border_width=1, border_color=LINE)
        self.lbl_ai_help = ctk.CTkLabel(cfgf, text="", font=_font(11), text_color=MUTED, wraplength=380, justify="left")
        self.lbl_ai_help.pack(anchor="w", padx=10, pady=(8, 4))
        self.e_ai_url = ctk.CTkEntry(cfgf, placeholder_text="https://…/v1", height=28, fg_color=SURF3, border_color=LINE)
        self.e_ai_key = ctk.CTkEntry(cfgf, placeholder_text="Pega aquí tu clave (API key)", show="•", height=28,
                                     fg_color=SURF3, border_color=LINE)
        self.e_ai_key.pack(fill="x", padx=10, pady=3)
        # al pegar o escribir la clave se buscan solos los modelos que esa clave puede usar
        self._ai_key_timer = None
        for ev in ("<KeyRelease>", "<<Paste>>"):
            self.e_ai_key.bind(ev, lambda _e: self._ai_key_changed(), add="+")
        self.e_ai_url.bind("<FocusOut>", lambda _e: self._ai_key_changed(), add="+")
        mrow = ctk.CTkFrame(cfgf, fg_color="transparent")
        mrow.pack(fill="x", padx=10, pady=3)
        self.cb_ai_model = ctk.CTkComboBox(mrow, values=[], height=28, fg_color=SURF3, border_color=LINE,
                                           button_color=SURF3, dropdown_fg_color=SURF2, font=_font(12))
        self.cb_ai_model.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(mrow, text="Modelos", width=80, height=28, fg_color=SURF3, hover_color=LINE, font=_font(12),
                      command=self._ai_models).pack(side="left", padx=(6, 0))
        frow = ctk.CTkFrame(cfgf, fg_color="transparent")
        frow.pack(fill="x", padx=10, pady=3)
        ctk.CTkLabel(frow, text="Respaldo", font=_font(11), text_color=MUTED, width=62).pack(side="left")
        self.cb_ai_fb = ctk.CTkComboBox(frow, values=[], height=28, fg_color=SURF3, border_color=LINE,
                                        button_color=SURF3, dropdown_fg_color=SURF2, font=_font(12))
        self.cb_ai_fb.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(cfgf, text="Si el modelo principal está saturado, la app reintenta y luego usa el de respaldo "
                                "(por ejemplo uno «flash-lite»). Los modelos de voz («tts») no sirven para conversar.",
                     font=_font(10), text_color=MUTED, wraplength=380, justify="left").pack(anchor="w", padx=10)
        brow = ctk.CTkFrame(cfgf, fg_color="transparent")
        brow.pack(fill="x", padx=10, pady=(4, 10))
        ctk.CTkButton(brow, text="Guardar", width=90, height=28, fg_color=CYAN, hover_color="#67e8f9",
                      text_color="#04121a", font=_font(12, "bold"), command=self._ai_save_cfg).pack(side="left")
        ctk.CTkButton(brow, text="Probar", width=80, height=28, fg_color=SURF3, hover_color=LINE, font=_font(12),
                      command=lambda: self._ai_send("Hola, ¿me escuchas? Responde en una línea.")).pack(side="left", padx=6)
        self.v_ai_learn = tk.BooleanVar(value=a.get("learn", True))
        ctk.CTkCheckBox(cfgf, text="Kuon IA aprende de lo que aceptas", variable=self.v_ai_learn, font=_font(11),
                        checkbox_width=16, checkbox_height=16, fg_color=CYAN_D).pack(anchor="w", padx=10, pady=(0, 8))
        self._ai_cfg_open = False

        self.ai_props = ctk.CTkFrame(t, fg_color="#14203a", corner_radius=10, border_width=1, border_color=CYAN_D)

        irow = ctk.CTkFrame(t, fg_color="transparent")
        irow.pack(side="bottom", fill="x", padx=4, pady=(4, 2))
        quick = self._ai_quick = ctk.CTkFrame(t, fg_color="transparent")
        quick.pack(side="bottom", fill="x", padx=4)
        for lab, fn in (("Revisar dudosos", self._ai_review), ("¿Quién dice esta línea?", self._ai_this_line),
                        ("Nueva charla", self._ai_reset)):
            ctk.CTkButton(quick, text=lab, height=26, fg_color=SURF3, hover_color=LINE, font=_font(11),
                          width=10, command=fn).pack(side="left", padx=(0, 4))
        self.ai_in = ctk.CTkTextbox(irow, height=58, fg_color=SURF3, text_color=TXT, font=_font(12), wrap="word",
                                    border_width=1, border_color=LINE, corner_radius=10)
        self.ai_in.pack(side="left", fill="x", expand=True)
        self.ai_in.bind("<Return>", self._ai_enter)
        self.btn_ai_send = ctk.CTkButton(irow, text="Enviar", width=70, height=58, fg_color=CYAN, hover_color="#67e8f9",
                                         text_color="#04121a", font=_font(12, "bold"), command=lambda: self._ai_send())
        self.btn_ai_send.pack(side="left", padx=(6, 0))
        self.ai_log = ctk.CTkTextbox(t, fg_color=SURF2, text_color=TXT, font=_font(12), wrap="word",
                                     border_width=1, border_color=LINE, corner_radius=10)
        self.ai_log.pack(fill="both", expand=True, padx=4, pady=4)
        tb = self.ai_log._textbox
        tb.tag_configure("me", foreground="#a5f3fc", spacing1=8)
        tb.tag_configure("ai", foreground=TXT, spacing1=8, lmargin1=4, lmargin2=4)
        tb.tag_configure("note", foreground=MUTED, spacing1=6)
        tb.tag_configure("err", foreground=RED, spacing1=6)
        self.ai_log.configure(state="disabled")

        self._ai_load_provider()
        self._ai_note("Pregúntame lo que quieras sobre tu historia: quién dice un diálogo, el reparto, cómo "
                      "se pronuncia un nombre o ideas para el capítulo. Si propongo cambios, tú decides si se aplican "
                      "y todo se puede deshacer con ↶.")

    def _ai_toggle_cfg(self):
        self._ai_cfg_open = not self._ai_cfg_open
        if self._ai_cfg_open:
            self.ai_cfg.pack(fill="x", padx=4, pady=4, after=self.btn_ai_cfg.master)
        else:
            self.ai_cfg.pack_forget()

    def _ai_conf(self):
        prov = self.v_ai_prov.get()
        p = ai.PROVIDERS[prov]
        saved = self.cfg.setdefault("assistant", {}).setdefault("providers", {}).get(prov, {})
        return {"provider": prov, "url": saved.get("url") or p["url"], "model": saved.get("model") or p["model"],
                "fallback": saved.get("fallback", ""),
                "key": ai.unprotect(saved.get("key", "")), "needs_key": p["key"], "help": p["help"]}

    def _ai_load_provider(self):
        c = self._ai_conf()
        self.lbl_ai_help.configure(text=c["help"])
        self.e_ai_url.pack_forget()
        if c["provider"].startswith("Otro"):
            self.e_ai_url.pack(fill="x", padx=10, pady=3, before=self.e_ai_key)
        self.e_ai_url.delete(0, "end")
        self.e_ai_url.insert(0, c["url"])
        self.e_ai_key.delete(0, "end")
        self.e_ai_key.insert(0, c["key"])
        self.cb_ai_model.set(c["model"])
        self.cb_ai_fb.set(c.get("fallback", ""))
        ready = (c["key"] or not c["needs_key"]) and c["model"] and c["url"]
        self.lbl_ai_state.configure(text="● listo" if ready else "● falta configurar",
                                    text_color=GREEN if ready else AMBER)
        if not ready and not self._ai_cfg_open:
            self._ai_toggle_cfg()
        self.cfg.setdefault("assistant", {})["provider"] = c["provider"]

    def _ai_save_cfg(self):
        prov = self.v_ai_prov.get()
        d = self.cfg.setdefault("assistant", {}).setdefault("providers", {}).setdefault(prov, {})
        d["key"] = ai.protect(self.e_ai_key.get().strip())
        d["model"] = self.cb_ai_model.get().strip()
        d["fallback"] = self.cb_ai_fb.get().strip()
        if prov.startswith("Otro"):
            d["url"] = self.e_ai_url.get().strip()
        self.cfg["assistant"]["learn"] = bool(self.v_ai_learn.get())
        self._store()
        self._ai_load_provider()
        if self._ai_cfg_open:
            self._ai_toggle_cfg()
        self.status("Asistente guardado. La clave queda cifrada con tu cuenta de Windows.")

    def _ai_key_changed(self):
        if self._ai_key_timer:
            self.after_cancel(self._ai_key_timer)
        k = self.e_ai_key.get().strip()
        if len(k) >= 20 or not self._ai_conf()["needs_key"]:
            self._ai_key_timer = self.after(900, self._ai_models)

    def _ai_models(self):
        c = self._ai_conf()
        key = self.e_ai_key.get().strip() or c["key"]
        url = self.e_ai_url.get().strip() if c["provider"].startswith("Otro") else c["url"]
        self.lbl_ai_state.configure(text="buscando modelos…", text_color=MUTED)

        def work():
            try:
                ms = ai.rank_models(ai.list_models(url, key), url)

                def show():
                    self.cb_ai_model.configure(values=ms[:300])
                    self.cb_ai_fb.configure(values=[""] + ms[:300])
                    if ms and self.cb_ai_model.get().strip() not in ms:
                        self.cb_ai_model.set(ms[0])          # el recomendado: el primero de la lista
                    self.lbl_ai_state.configure(text=f"✓ conectado · {len(ms)} modelos (despliega la lista)", text_color=GREEN)
                self.after(0, show)
            except Exception as e:
                self.after(0, lambda e=e: self.lbl_ai_state.configure(text=str(e)[:60], text_color=RED))
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ registro de la charla
    def _ai_write(self, text, tag):
        self.ai_log.configure(state="normal")
        self.ai_log._textbox.insert("end", text + "\n", tag)
        self.ai_log.configure(state="disabled")
        self.ai_log._textbox.see("end")

    def _ai_note(self, text):
        self._ai_write(text, "note")

    def _ai_reset(self):
        self._ai_msgs.clear()
        self._ai_clear_props()
        self.ai_log.configure(state="normal")
        self.ai_log._textbox.delete("1.0", "end")
        self.ai_log.configure(state="disabled")
        self._ai_note("Charla nueva.")

    def _ai_enter(self, e):
        if e.state & 0x1:       # Mayús+Enter: salto de línea
            return None
        self._ai_send()
        return "break"

    # ------------------------------------------------------------ contexto de la obra
    def _ai_seg_at_cursor(self):
        try:
            off = self._off("insert")
        except Exception:
            return None
        return next((s for s in self.segs if s.kind == "dlg" and s.start <= off <= s.end), None)

    def _ai_context(self, extra_lines=()):
        text = self.get_text()
        self._ai_lines = {}
        out = [f"Obra: {self.project} · documento: {self.doc_key} · {len(text.split())} palabras."]
        names = [n for n in self.chars if n in self.cast] + [n for n in self.cast if n not in self.chars]
        if names:
            out.append("PERSONAJES (nombre · género · edad · líneas):")
            for n in names[:60]:
                c, ch = self.cast[n], self.chars.get(n)
                out.append(f"- {n} · {'mujer' if c.get('gender') == 'F' else 'hombre'} · {c.get('age', 'adulto')}"
                           f" · {ch.lines if ch else 0}")
        segs = list(extra_lines)
        cur = self._ai_seg_at_cursor()
        if cur and cur not in segs:
            segs.insert(0, cur)
        if segs:
            out.append("\nLÍNEAS DE DIÁLOGO (con el texto anterior para dar contexto):")
            for i, s in enumerate(segs, 1):
                lid = f"L{i}"
                self._ai_lines[lid] = s.key
                ctx = text[max(0, s.start - 450):s.start].replace("\n", " ")
                now = s.speaker or "narrador (no está segura)"
                mark = " [donde está el cursor]" if s is cur else ""
                out.append(f"{lid}{mark} · ahora la lee: {now}\n   contexto: …{ctx[-450:]}\n   diálogo: {s.text[:300]}")
        try:
            sel = self.txt.get("sel.first", "sel.last")
            if sel.strip():
                out.append("\nTEXTO SELECCIONADO POR EL USUARIO:\n" + sel[:3000])
        except Exception:
            pass
        return "\n".join(out)

    # ------------------------------------------------------------ enviar
    def _ai_send(self, text=None, lines=(), shown=None):
        if self._ai_busy:
            return
        if text is None:
            text = self.ai_in.get("1.0", "end").strip()
            self.ai_in.delete("1.0", "end")
        if not text:
            return
        self._ai_clear_props()      # las propuestas viejas apuntan a líneas de la consulta anterior
        c = self._ai_conf()
        if c["needs_key"] and not c["key"]:
            self._ai_write("Primero pega tu clave en «Configurar» y pulsa Guardar.", "err")
            if not self._ai_cfg_open:
                self._ai_toggle_cfg()
            return
        self._ai_write("Tú: " + (shown or text), "me")
        ctx = self._ai_context(lines)
        sys_msg = ai.SYSTEM + "\n\nCONTEXTO ACTUAL DE LA APP:\n" + ctx
        self._ai_msgs.append({"role": "user", "content": text})
        msgs = [{"role": "system", "content": sys_msg}] + self._ai_msgs[-16:]
        self._ai_busy = True
        self.btn_ai_send.configure(state="disabled", text="…")
        self.lbl_ai_state.configure(text="pensando…", text_color=AMBER)

        def work():
            try:
                note = lambda m: self.after(0, lambda m=m: self.lbl_ai_state.configure(text=m[:60], text_color=AMBER))
                ans = ai.chat(c["url"], c["key"], c["model"], msgs, fallback=c.get("fallback", ""), on_note=note)
                self.after(0, lambda: self._ai_got(ans))
            except Exception as e:
                self.after(0, lambda e=e: self._ai_fail(e))
        threading.Thread(target=work, daemon=True).start()

    def _ai_done(self):
        self._ai_busy = False
        self.btn_ai_send.configure(state="normal", text="Enviar")
        self.lbl_ai_state.configure(text="● listo", text_color=GREEN)

    def _ai_fail(self, e):
        self._ai_done()
        if self._ai_msgs and self._ai_msgs[-1]["role"] == "user":
            self._ai_msgs.pop()
        extra = ""
        if getattr(e, "code", 0) in (400, 404) and getattr(e, "detail", ""):
            import re as _re
            m = _re.search(r'"message"\s*:\s*"([^"]+)"', e.detail)
            extra = " (" + (m.group(1) if m else e.detail)[:160] + ")"
        if getattr(e, "code", 0) in (429, 503):
            extra = ". Elige un modelo de respaldo en «Configurar» para que la app cambie sola."
        self._ai_write("No pude responder: " + str(e) + extra, "err")
        self.lbl_ai_state.configure(text="● error", text_color=RED)

    def _ai_got(self, ans):
        self._ai_done()
        self._ai_msgs.append({"role": "assistant", "content": ans})
        visible, acts = ai.parse_actions(ans)
        self._ai_write("Kuon: " + (visible or "(propuesta de cambios)"), "ai")
        if acts:
            self._ai_show_props(acts)

    # ------------------------------------------------------------ atajos
    def _ai_this_line(self):
        s = self._ai_seg_at_cursor()
        if not s:
            self._ai_write("Pon el cursor dentro de un diálogo del editor y vuelve a pulsar.", "err")
            return
        self._ai_send("¿Quién dice la línea L1? Explica en una frase por qué y, si la app se equivoca, "
                      "propón el cambio.", lines=[s], shown="¿Quién dice esta línea?")

    def _ai_review(self):
        th = float(self.v_threshold.get()) if hasattr(self, "v_threshold") else 0.55
        doubt = [s for s in self.segs if s.kind == "dlg" and (not s.speaker or s.guess or s.conf < th + 0.1)
                 and s.source not in ("manual", "guion", "inciso")]
        if not doubt:
            self._ai_note("No hay diálogos dudosos en este documento.")
            return
        batch = doubt[:25]
        self._ai_send(f"Revisa estas {len(batch)} líneas dudosas (L1 a L{len(batch)}). Para cada una decide quién la "
                      "dice según el contexto y propón solo los cambios en el bloque de acciones. Si no se puede "
                      "saber, no la incluyas. Al final resume en una línea.", lines=batch,
                      shown=f"Revisar {len(batch)} diálogos dudosos" + (f" (de {len(doubt)})" if len(doubt) > 25 else ""))

    # ------------------------------------------------------------ propuestas
    def _ai_clear_props(self):
        for w in self.ai_props.winfo_children():
            w.destroy()
        self.ai_props.pack_forget()
        self._ai_pending = []

    def _ai_show_props(self, acts):
        self._ai_clear_props()
        for a in acts:
            for f in ("nombre", "con"):
                if a.get(f) and a[f] not in ("narrador",):
                    a[f] = self._canon_name(a[f])
        valid = [a for a in acts if self._ai_valid(a)]
        if not valid:
            self._ai_note("(La propuesta no tenía cambios que se puedan aplicar.)")
            return
        ctk.CTkLabel(self.ai_props, text="Cambios propuestos", font=_font(12, "bold"), text_color=CYAN).pack(
            anchor="w", padx=10, pady=(8, 2))
        box = ctk.CTkScrollableFrame(self.ai_props, fg_color="transparent", height=150) if len(valid) > 5 \
            else ctk.CTkFrame(self.ai_props, fg_color="transparent")
        box.pack(fill="x", padx=6)
        for a in valid:
            v = tk.BooleanVar(value=True)
            ctk.CTkCheckBox(box, text=ai.describe(a), variable=v, font=_font(11), checkbox_width=16,
                            checkbox_height=16, fg_color=CYAN_D).pack(anchor="w", pady=1)
            self._ai_pending.append((a, v))
        row = ctk.CTkFrame(self.ai_props, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(4, 8))
        ctk.CTkButton(row, text="Aplicar", width=90, height=28, fg_color=CYAN, hover_color="#67e8f9",
                      text_color="#04121a", font=_font(12, "bold"), command=self._ai_apply).pack(side="left")
        ctk.CTkButton(row, text="Descartar", width=90, height=28, fg_color=SURF3, hover_color=LINE, font=_font(12),
                      command=self._ai_clear_props).pack(side="left", padx=6)
        self.ai_props.pack(side="bottom", fill="x", padx=4, pady=4, after=self._ai_quick)

    def _ai_valid(self, a):
        k = a.get("accion")
        names = set(self.cast) | set(self.chars)
        if k in ("asignar", "emocion"):
            if a.get("linea") not in self._ai_lines:
                return False
            if k == "emocion":
                return a.get("emocion") in EMOS
            return True
        if k in ("unir",):
            return bool(a.get("nombre")) and bool(a.get("con"))
        if k in ("genero", "edad", "color", "no_es_personaje"):
            nm = self._canon_name(a.get("nombre", ""))
            if not nm or (nm not in names and k != "no_es_personaje"):
                return False
            if k == "edad":
                return a.get("edad") in AGES
            if k == "color":
                return bool(str(a.get("color", "")).startswith("#")) and len(str(a.get("color"))) in (4, 7)
            return True
        if k == "pronunciar":
            return bool(a.get("palabra")) and bool(a.get("como"))
        return False

    def _ai_apply(self):
        chosen = [a for a, v in self._ai_pending if v.get()]
        if not chosen:
            self._ai_clear_props()
            return
        self._checkpoint(f"{len(chosen)} cambios del asistente")
        self._in_batch = True
        learn = bool(self.cfg.get("assistant", {}).get("learn", True))
        byk = {s.key: s for s in self.segs}
        done, learned = 0, []
        try:
            for a in chosen:
                k = a["accion"]
                try:
                    if k == "asignar":
                        seg = byk.get(self._ai_lines.get(a["linea"]))
                        if not seg:
                            continue
                        nm = self._canon_name(a.get("nombre", "")) if a.get("nombre") not in ("", "narrador", None) else ""
                        if nm:
                            self._allow_name(nm)
                        self.overrides[seg.key] = nm
                        if learn:
                            learned += brain.learn_from_correction(self.memory, self.project, self.get_text(),
                                                                   self.segs, seg, nm)
                    elif k == "emocion":
                        key = self._ai_lines.get(a["linea"])
                        if a["emocion"] == "neutral":
                            self.emo_over.pop(key, None)
                        else:
                            self.emo_over[key] = a["emocion"]
                    elif k == "unir":
                        self._merge(self._canon_name(a["nombre"]), self._canon_name(a["con"]), ask=False)
                    elif k == "genero":
                        nm = self._canon_name(a["nombre"])
                        self._toggle_gender(nm, "F" if str(a.get("genero", "")).upper().startswith("F") else "M")
                    elif k == "edad":
                        self._set_age(self._canon_name(a["nombre"]), a["edad"])
                    elif k == "color":
                        self._set_color(self._canon_name(a["nombre"]), a["color"])
                    elif k == "no_es_personaje":
                        self._remove_char(self._canon_name(a["nombre"]), ask=False)
                    elif k == "pronunciar":
                        self.lex.insert("end", f"\n{a['palabra']} = {a['como']}")
                    done += 1
                except Exception as e:
                    self._log_error(e)
        finally:
            self._in_batch = False
        self._save_mem()
        self._update_memory_label()
        self._ai_clear_props()
        self.schedule_analysis(10)
        self._autosave()
        msg = f"Apliqué {done} cambios. Se deshacen todos juntos con ↶."
        if learned:
            msg += " Kuon IA aprendió: " + ", ".join(learned[:6])
        self._ai_note(msg)
        self.status(msg)
