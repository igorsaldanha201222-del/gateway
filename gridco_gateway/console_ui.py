"""Janela do console — tkinter puro, biblioteca padrão.

Sem WebView2, sem HTML, sem pythonnet. O que a janela desenha é o que o Windows
desenha: um widget nativo por linha. Foi uma troca deliberada depois de a versão
em WebView2 renderizar sem estilo em máquina de campo, sem erro nenhum.

Identidade ISA-101: fundo cinza, estado normal em cinza, cor só para condição
anormal — losango vermelho P1, triângulo âmbar P2, quadrado cinza normal.
"""

from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .console_app import Ponte


def recurso(nome: str) -> Path | None:
    """Acha um arquivo de marca, empacotado ou rodando do repositório."""
    locais = []
    if getattr(sys, "_MEIPASS", None):
        locais.append(Path(sys._MEIPASS) / nome)
    projeto = Path(__file__).resolve().parents[1]
    locais.append(projeto / "deploy" / "windows" / nome)
    for p in locais:
        if p.is_file():
            return p
    return None

# --- paleta ISA-101 --------------------------------------------------------
GROUND, SURF, SURF2, SURF3 = "#c3c3c3", "#d2d2d2", "#cbcbcb", "#bcbcbc"
BANNER, LINE, HARD = "#a9a9a9", "#8e8e8e", "#5c5c5c"
INK, INK2, INK3 = "#161616", "#414141", "#6b6b6b"
P1, P1BG, P2, P2BG = "#b51414", "#e8c9c9", "#c98200", "#ecdcbc"
ACT, FIELD = "#1f4e79", "#e4e4e4"

UI = ("Segoe UI", 9)
UI_B = ("Segoe UI", 9, "bold")
UI_P = ("Segoe UI", 7)
MONO = ("Consolas", 9)
MONO_G = ("Consolas", 13, "bold")
PERIODO_MS = 5000


class Console(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.ponte = Ponte()
        self.title("Gateway Grid Co")
        self.configure(bg=GROUND)
        self.geometry("1260x860")
        self.minsize(980, 700)
        # Sem a peninha do Tk: o ícone é a marca.
        ico = recurso("gridco.ico")
        if ico:
            try:
                self.iconbitmap(default=str(ico))
            except tk.TclError:
                pass
        self._modelos: list[dict] = []
        self._estilo()
        self._faixa()
        self._abas()
        self._rodape()
        self._aquecer()
        self.after(200, self._ciclo)

    def _aquecer(self) -> None:
        """Carrega em segundo plano o que custa caro na primeira chamada."""
        def trabalho():
            try:
                from . import device_finder  # noqa: F401
            except Exception:
                pass
        threading.Thread(target=trabalho, name="aquecer", daemon=True).start()

    # ---------------- aparência ----------------
    def _estilo(self) -> None:
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=SURF, foreground=INK, font=UI)
        s.configure("TNotebook", background=GROUND, borderwidth=0, tabmargins=(0, 0, 0, 0))
        s.configure("TNotebook.Tab", background=SURF2, foreground=INK, padding=(16, 7),
                    font=UI, borderwidth=1)
        s.map("TNotebook.Tab", background=[("selected", SURF)], font=[("selected", UI_B)])
        s.configure("TFrame", background=SURF)
        s.configure("TLabel", background=SURF, foreground=INK)
        s.configure("TLabelframe", background=SURF, bordercolor=HARD)
        s.configure("TLabelframe.Label", background=SURF, foreground=INK2, font=UI_B)
        s.configure("TEntry", fieldbackground=FIELD, bordercolor=HARD, insertcolor=INK)
        s.configure("TCombobox", fieldbackground=FIELD, background=SURF2, bordercolor=HARD)
        s.configure("Treeview", background=SURF, fieldbackground=SURF, foreground=INK,
                    rowheight=23, font=MONO, borderwidth=0)
        s.configure("Treeview.Heading", background=SURF2, foreground=INK2,
                    font=("Segoe UI", 8, "bold"), relief="flat", padding=(6, 5))
        s.map("Treeview", background=[("selected", "#a8b6c2")])
        s.configure("TButton", background=SURF2, foreground=INK, font=UI_B,
                    borderwidth=1, focusthickness=0, padding=(12, 5))
        s.map("TButton", background=[("active", FIELD), ("disabled", SURF3)],
              foreground=[("disabled", INK3)])
        s.configure("Acao.TButton", background=ACT, foreground="#ffffff")
        s.map("Acao.TButton", background=[("active", "#2a628f"), ("disabled", SURF3)])

    def _cel(self, pai: tk.Widget, rotulo: str) -> tk.Label:
        q = tk.Frame(pai, bg=BANNER, padx=13, pady=6,
                     highlightbackground=LINE, highlightthickness=1)
        q.pack(side="left", fill="y")
        tk.Label(q, text=rotulo.upper(), bg=BANNER, fg=INK2, font=UI_P).pack(anchor="w")
        v = tk.Label(q, text="—", bg=BANNER, fg=INK, font=MONO_G)
        v.pack(anchor="w")
        v._quadro = q  # para pintar o fundo quando virar alarme
        return v

    def _faixa(self) -> None:
        f = tk.Frame(self, bg=BANNER)
        f.pack(fill="x")
        esq = tk.Frame(f, bg=BANNER, padx=13, pady=7)
        esq.pack(side="left")
        png = recurso("gridco-logo.png")
        if png:
            # Guardado no self: PhotoImage coletado pelo GC some da tela.
            self._logo = tk.PhotoImage(file=str(png))
            tk.Label(esq, image=self._logo, bg=BANNER, bd=0).pack(side="left", padx=(0, 14))
        else:
            tk.Label(esq, text="GRID CO", bg=BANNER, fg=INK,
                     font=("Segoe UI", 11, "bold")).pack(side="left", padx=(0, 14))
        t = tk.Frame(esq, bg=BANNER)
        t.pack(side="left")
        self.lb_planta = tk.Label(t, text="—", bg=BANNER, fg=INK, font=("Segoe UI", 11, "bold"))
        self.lb_planta.pack(anchor="w")
        self.lb_cfg = tk.Label(t, text="—", bg=BANNER, fg=INK2, font=("Consolas", 8))
        self.lb_cfg.pack(anchor="w")

        d = tk.Frame(f, bg=BANNER)
        d.pack(side="right")
        self.v_svc = self._cel(d, "Serviço")
        self.v_aq = self._cel(d, "Aquisição")
        self.v_dev = self._cel(d, "Equipamentos")
        self.v_fila = self._cel(d, "Fila")
        self.v_hora = self._cel(d, "Atualizado")
        tk.Frame(self, bg=HARD, height=1).pack(fill="x")

    def _tabela(self, pai, colunas, larguras, elastica=-1, altura=None, expandir=True):
        q = tk.Frame(pai, bg=SURF, highlightbackground=HARD, highlightthickness=1)
        q.pack(fill="both", expand=expandir, padx=10, pady=10)
        tv = ttk.Treeview(q, columns=colunas, show="headings", selectmode="browse",
                          **({"height": altura} if altura else {}))
        for i, (c, w) in enumerate(zip(colunas, larguras)):
            tv.heading(c, text=c.upper())
            tv.column(c, width=w, anchor="w", stretch=(i == elastica))
        sb = ttk.Scrollbar(q, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        tv.tag_configure("ruim", background=P1BG, foreground="#5e0c0c")
        tv.tag_configure("aviso", background=P2BG, foreground="#5e3d00")
        return tv

    # ---------------- abas ----------------
    def _abas(self) -> None:
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        a = ttk.Frame(nb); nb.add(a, text="Equipamentos")
        self.tv_dev = self._tabela(a, ("device", "estado", "qualidade", "tópico", "última amostra"),
                                   (190, 150, 80, 380, 165), elastica=3)

        b = ttk.Frame(nb); nb.add(b, text="Valores")
        self.tv_val = self._tabela(b, ("device", "variável", "valor"), (200, 300, 260), elastica=2)

        c = ttk.Frame(nb); nb.add(c, text="Cadastro")
        self._aba_cadastro(c)

        d = ttk.Frame(nb); nb.add(d, text="Localizador")
        self._aba_localizador(d)

        e = ttk.Frame(nb); nb.add(e, text="Eventos")
        self.tv_ev = self._tabela(e, ("quando", "nível", "origem", "código", "mensagem"),
                                  (150, 80, 110, 170, 420), elastica=4)

        # self._nb ANTES do bind: cada nb.add dispara <<NotebookTabChanged>>,
        # e o callback usaria um atributo que ainda nao existe.
        self._nb = nb
        nb.bind("<<NotebookTabChanged>>", self._troca_aba)
        # GRIDCO_ABA=2 abre direto no Cadastro - serve para conferir a tela.
        try:
            nb.select(int(os.environ.get("GRIDCO_ABA", "0")))
        except (ValueError, tk.TclError):
            nb.select(0)

    def _troca_aba(self, _evt=None) -> None:
        if self._nb.tab(self._nb.select(), "text") == "Cadastro" and not self._modelos:
            self._carregar_catalogo()

    # ---------------- cadastro ----------------
    def _aba_cadastro(self, pai) -> None:
        # A lista de cadastrados e' empacotada PRIMEIRO, presa embaixo. No Tk
        # quem vem antes reserva o espaco; deixando para o fim, o quadro de
        # cima com expand=True a reduz ao cabecalho quando a janela aperta.
        self._rodape_cadastro(pai)
        topo = ttk.Frame(pai); topo.pack(fill="both", expand=True, padx=10, pady=10)
        esq = ttk.Labelframe(topo, text=" Modelo do catálogo ", padding=10)
        esq.pack(side="left", fill="both", expand=True, padx=(0, 6))
        dir_ = ttk.Labelframe(topo, text=" Equipamento ", padding=10)
        dir_.pack(side="left", fill="both", expand=True, padx=(6, 0))

        ttk.Label(esq, text="Filtrar").pack(anchor="w")
        self.e_busca = ttk.Entry(esq)
        self.e_busca.pack(fill="x", pady=(2, 8))
        self.e_busca.bind("<KeyRelease>", lambda _e: self._listar_modelos())

        self.lst = tk.Listbox(esq, height=11, bg=FIELD, fg=INK, font=MONO,
                              highlightbackground=HARD, selectbackground=ACT,
                              selectforeground="#ffffff", activestyle="none")
        self.lst.pack(fill="both", expand=True)
        self.lst.bind("<<ListboxSelect>>", lambda _e: self._detalhar())

        self.lb_det = tk.Label(esq, text="Selecione um modelo.", bg=SURF2, fg=INK2,
                               font=("Consolas", 8), justify="left", anchor="nw",
                               wraplength=330, padx=8, pady=8,
                               highlightbackground=LINE, highlightthickness=1)
        self.lb_det.pack(fill="x", pady=(8, 0))

        self.campos: dict[str, ttk.Entry] = {}
        for rot, chave, valor in (("Nome", "nome", ""), ("ID único", "id", ""),
                                  ("Unit ID Modbus", "unit", "1"), ("Índice no tópico", "indice", "1")):
            ttk.Label(dir_, text=rot).pack(anchor="w")
            e = ttk.Entry(dir_)
            e.insert(0, valor)
            e.pack(fill="x", pady=(2, 7))
            self.campos[chave] = e

        ttk.Label(dir_, text="Canal").pack(anchor="w")
        self.cb_canal = ttk.Combobox(dir_, state="readonly", values=[])
        self.cb_canal.pack(fill="x", pady=(2, 7))
        self.cb_canal.bind("<<ComboboxSelected>>", lambda _e: self._alterna_canal())

        # Lado a lado: empilhados, os tres campos empurravam o resto para fora
        # do quadro em janela pequena.
        self.qd_novo = ttk.Frame(dir_)
        for col, (rot, chave, valor, peso) in enumerate((
                ("ID do canal", "cid", "modbus-tcp-01", 3),
                ("IP", "cip", "", 3),
                ("Porta", "cporta", "502", 1))):
            ttk.Label(self.qd_novo, text=rot).grid(row=0, column=col, sticky="w",
                                                   padx=(0, 6))
            e = ttk.Entry(self.qd_novo, width=8)
            e.insert(0, valor)
            e.grid(row=1, column=col, sticky="we", padx=(0, 6), pady=(2, 7))
            self.qd_novo.columnconfigure(col, weight=peso)
            self.campos[chave] = e

        bar = ttk.Frame(dir_); bar.pack(fill="x", pady=(6, 0))
        self.b_rev = ttk.Button(bar, text="Revisar", command=self._revisar)
        self.b_rev.pack(side="left")
        self.b_cad = ttk.Button(bar, text="Cadastrar e aplicar", style="Acao.TButton",
                                command=self._cadastrar, state="disabled")
        self.b_cad.pack(side="left", padx=6)

        # altura fixa em linhas: com expand=True esta caixa empurrava os campos
        # de canal para fora do quadro quando a janela era pequena.
        self.lb_res = tk.Label(dir_, text="", bg=SURF2, fg=INK2, font=("Consolas", 8),
                               justify="left", anchor="nw", wraplength=330, padx=8, pady=8,
                               height=7, highlightbackground=LINE, highlightthickness=1)
        self.lb_res.pack(fill="x", pady=(8, 0))

    def _rodape_cadastro(self, pai) -> None:
        baixo = ttk.Labelframe(pai, text=" Cadastrados ", padding=6)
        baixo.pack(side="bottom", fill="x", padx=10, pady=(0, 10))
        acoes = ttk.Frame(baixo); acoes.pack(fill="x")
        self.b_aq = ttk.Button(acoes, text="—", command=self._alternar_aquisicao)
        self.b_aq.pack(side="left")
        ttk.Button(acoes, text="Remover selecionado", command=self._remover).pack(side="left", padx=6)
        self.tv_cad = self._tabela(baixo, ("id", "tipo", "canal", "unit", "índice"),
                                   (220, 140, 170, 70, 70), elastica=2,
                                   altura=6, expandir=False)

    def _carregar_catalogo(self) -> None:
        r = self.ponte.catalogo()
        if not r.get("ok"):
            self.lb_det.config(text="Catálogo indisponível:\n" + str(r.get("erro")))
            return
        self._modelos = r["modelos"]
        self._listar_modelos()

    def _listar_modelos(self) -> None:
        q = self.e_busca.get().strip().lower()
        self._filtrados = [m for m in self._modelos if not q or q in
                           f"{m.get('manufacturer')} {m.get('name')} {m.get('device_type')}".lower()]
        self.lst.delete(0, "end")
        for m in self._filtrados:
            self.lst.insert("end", f"{m.get('manufacturer')} · {m.get('name')}")
        if self._filtrados:
            self.lst.selection_set(0)
            self._detalhar()

    def _modelo(self) -> dict | None:
        sel = self.lst.curselection()
        if not sel or not getattr(self, "_filtrados", None):
            return None
        return self._filtrados[sel[0]]

    def _detalhar(self) -> None:
        m = self._modelo()
        if not m:
            return
        self.lb_det.config(text=(
            f"{m.get('manufacturer')} {m.get('name')}\n"
            f"{m.get('catalog_id')}\n\n"
            f"tipo      {m.get('device_type')}\n"
            f"blocos    {m.get('request_count')}\n"
            f"variáveis {m.get('field_count')}\n\n"
            f"{m.get('description') or ''}"))
        self.b_cad.config(state="disabled")

    def _alterna_canal(self) -> None:
        if self.cb_canal.get().startswith("+"):
            self.qd_novo.pack(fill="x", before=self.lb_res)
        else:
            self.qd_novo.pack_forget()

    def _pedido(self) -> dict:
        canal = self.cb_canal.get()
        novo = canal.startswith("+")
        cid = self.campos["cid"].get().strip() if novo else canal.split(" ")[0]
        p = {"catalog_id": (self._modelo() or {}).get("catalog_id", ""),
             "device": {"id": self.campos["id"].get().strip(),
                        "name": self.campos["nome"].get().strip(),
                        "unit_id": self.campos["unit"].get().strip(),
                        "index": self.campos["indice"].get().strip(),
                        "channel_id": cid}}
        if novo:
            p["channel"] = {"id": cid, "transport": "tcp",
                            "ip": self.campos["cip"].get().strip(),
                            "port": self.campos["cporta"].get().strip()}
        return p

    def _revisar(self) -> None:
        r = self.ponte.previa(self._pedido())
        if not r.get("ok"):
            self.lb_res.config(text="Não dá para cadastrar:\n\n" + str(r.get("erro")),
                               bg=P1BG, fg="#5e0c0c")
            self.b_cad.config(state="disabled")
            return
        self.lb_res.config(text=(f"Tópico:\n{r['topico']}\n\n"
                                 f"+{r['blocos']} blocos\n+{r['variaveis']} variáveis\n"
                                 f"+{r['canais']} canal(is)\n\nNada foi gravado ainda."),
                           bg=SURF2, fg=INK2)
        self.b_cad.config(state="normal")

    def _cadastrar(self) -> None:
        self.b_cad.config(state="disabled")
        r = self.ponte.cadastrar(self._pedido())
        if r.get("ok"):
            self.lb_res.config(text=f"Cadastrado.\nRevisão {r.get('revisao')}.\n"
                                    f"Serviço {r.get('servico')}.", bg=SURF2, fg=INK)
        else:
            self.lb_res.config(text="Falhou:\n\n" + str(r.get("erro")), bg=P1BG, fg="#5e0c0c")
        self._atualizar_cadastro()

    def _remover(self) -> None:
        sel = self.tv_cad.selection()
        if not sel:
            messagebox.showinfo("Remover", "Selecione um equipamento na lista.")
            return
        did = self.tv_cad.item(sel[0], "values")[0]
        if not messagebox.askyesno("Remover", f"Remover '{did}' da configuração?"):
            return
        r = self.ponte.remover(did)
        if not r.get("ok"):
            messagebox.showerror("Remover", str(r.get("erro")))
        self._atualizar_cadastro()

    def _alternar_aquisicao(self) -> None:
        ligada = self.v_aq.cget("text") == "LIGADA"
        r = self.ponte.aquisicao(not ligada)
        if not r.get("ok"):
            messagebox.showerror("Aquisição", str(r.get("erro")))

    def _atualizar_cadastro(self) -> None:
        canais = self.ponte.canais()
        vals = [f"{c['id']}  ({c.get('ip') or c.get('serial') or ''}"
                f"{':' + str(c['porta']) if c.get('porta') else ''})" for c in canais]
        vals.append("+ criar novo canal")
        atual = self.cb_canal.get()
        self.cb_canal["values"] = vals
        if atual in vals:
            self.cb_canal.set(atual)
        else:
            self.cb_canal.set(vals[0])
        self._alterna_canal()

        self.tv_cad.delete(*self.tv_cad.get_children())
        for d in self.ponte.equipamentos():
            self.tv_cad.insert("", "end", values=(d["id"], d["tipo"], d["canal"],
                                                  d["unit_id"], d["indice"]))

    # ---------------- localizador ----------------
    def _aba_localizador(self, pai) -> None:
        topo = ttk.Labelframe(pai, text=" Varredura da rede — somente leitura ", padding=10)
        topo.pack(fill="x", padx=10, pady=10)
        linha = ttk.Frame(topo); linha.pack(fill="x")

        ttk.Label(linha, text="IPs ou redes").grid(row=0, column=0, sticky="w")
        self.e_alvos = ttk.Entry(linha, width=34)
        self.e_alvos.insert(0, "192.168.1.0/24")
        self.e_alvos.grid(row=1, column=0, sticky="we", padx=(0, 8))

        ttk.Label(linha, text="Unit IDs").grid(row=0, column=1, sticky="w")
        self.e_units = ttk.Entry(linha, width=24)
        self.e_units.insert(0, "1-20,247,255")
        self.e_units.grid(row=1, column=1, sticky="we", padx=(0, 8))

        self.b_loc = ttk.Button(linha, text="Procurar", style="Acao.TButton", command=self._localizar)
        self.b_loc.grid(row=1, column=2)
        self.b_loc_parar = ttk.Button(linha, text="Parar", command=self._localizar_parar, state="disabled")
        self.b_loc_parar.grid(row=1, column=3, padx=6)
        linha.columnconfigure(0, weight=2)
        linha.columnconfigure(1, weight=1)

        self.lb_loc = tk.Label(topo, text="Compara cada Unit ID que responde com 23 assinaturas "
                                          "conhecidas. Nada é escrito nos equipamentos.",
                               bg=SURF, fg=INK2, font=("Segoe UI", 8), anchor="w", justify="left")
        self.lb_loc.pack(fill="x", pady=(10, 0))

        acoes = ttk.Frame(pai)
        acoes.pack(fill="x", padx=10, pady=(6, 0))
        self.b_usar = ttk.Button(acoes, text="Cadastrar o selecionado →",
                                 style="Acao.TButton", command=self._usar_achado,
                                 state="disabled")
        self.b_usar.pack(side="left")
        ttk.Label(acoes, text="ou dê duplo clique na linha").pack(side="left", padx=10)

        self.tv_loc = self._tabela(pai, ("ip", "unit", "resultado", "equipamento", "tipo",
                                         "confiança", "evidência"),
                                   (110, 60, 130, 190, 100, 80, 420), elastica=6)
        self.tv_loc.bind("<Double-1>", lambda _e: self._usar_achado())
        self.tv_loc.bind("<<TreeviewSelect>>",
                         lambda _e: self.b_usar.config(state="normal"))

    def _localizar(self) -> None:
        # Em thread: a primeira chamada carrega o device_finder, e num .exe
        # onefile isso e' descompressao de modulo - chegou a segurar 1,7 s a
        # interface. Widget so' e' tocado de volta na thread principal.
        self.b_loc.config(state="disabled")
        self.b_loc_parar.config(state="normal")
        self.lb_loc.config(text="Iniciando a varredura…", fg=INK2)
        alvos, units = self.e_alvos.get(), self.e_units.get()

        def trabalho():
            r = self.ponte.localizar(alvos, units)
            self.after(0, lambda: self._localizar_iniciou(r))

        threading.Thread(target=trabalho, name="iniciar-busca", daemon=True).start()

    def _localizar_iniciou(self, r: dict) -> None:
        if r.get("erro"):
            self.lb_loc.config(text="Erro: " + str(r["erro"]), fg=P1)
            self.b_loc.config(state="normal")
            self.b_loc_parar.config(state="disabled")
            return
        self._localizar_estado()

    def _localizar_parar(self) -> None:
        self.ponte.localizar_parar()

    def _usar_achado(self) -> None:
        """Leva a linha escolhida para a aba Cadastro, já preenchida."""
        sel = self.tv_loc.selection()
        if not sel:
            return
        ip, unit, _res, equipamento, tipo = self.tv_loc.item(sel[0], "values")[:5]
        unit = str(unit).split(" ")[0]          # "1 (qualquer)" -> "1"
        if equipamento == "—":
            equipamento, tipo = "", ""
        fabricante, _, modelo = equipamento.partition(" ")

        s = self.ponte.sugerir(ip, unit, fabricante, modelo, tipo)
        if not s.get("ok"):
            messagebox.showerror("Cadastrar", str(s.get("erro")))
            return

        self._nb.select(2)                       # aba Cadastro
        if not self._modelos:
            self._carregar_catalogo()

        cands = s.get("candidatos") or []
        if cands:
            # Filtra pelo fabricante e seleciona o melhor palpite na lista.
            self.e_busca.delete(0, "end")
            self.e_busca.insert(0, fabricante)
            self._listar_modelos()
            alvo = cands[0]["catalog_id"]
            for i, m in enumerate(getattr(self, "_filtrados", [])):
                if m.get("catalog_id") == alvo:
                    self.lst.selection_clear(0, "end")
                    self.lst.selection_set(i)
                    self.lst.see(i)
                    self._detalhar()
                    break

        d = s["device"]
        for chave, valor in (("nome", d["name"]), ("id", d["id"]),
                             ("unit", d["unit_id"]), ("indice", d["index"])):
            self.campos[chave].delete(0, "end")
            self.campos[chave].insert(0, str(valor))

        canal = s["canal"]
        if canal.get("existente"):
            for v in self.cb_canal["values"]:
                if v.startswith(canal["existente"]):
                    self.cb_canal.set(v)
                    break
        else:
            self.cb_canal.set("+ criar novo canal")
            novo = canal["novo"]
            for chave, valor in (("cid", novo["id"]), ("cip", novo["ip"]), ("cporta", novo["port"])):
                self.campos[chave].delete(0, "end")
                self.campos[chave].insert(0, str(valor))
        self._alterna_canal()

        aviso = ""
        if not cands:
            aviso = ("O localizador não identificou o modelo. Escolha na lista à "
                     "esquerda antes de revisar.")
        elif cands[0]["nota"] < 0.4:
            aviso = (f"Palpite fraco ({round(cands[0]['nota'] * 100)}%). Confira o modelo "
                     f"antes de revisar — casar errado gera ponto plausível e errado.")
        elif len(cands) > 1 and cands[0]["nota"] - cands[1]["nota"] < 0.03:
            # Empate real, como Huawei 20 x 28 strings: a assinatura nao separa,
            # e a diferenca esta no bloco de strings.
            aviso = (f"Empate entre dois modelos:\n· {cands[0]['rotulo']}\n· {cands[1]['rotulo']}\n"
                     f"A assinatura não distingue os dois. Escolha na lista.")
        self.lb_res.config(text=aviso or
                           f"Vindo do localizador: {ip} unit {unit}.\nConfira e clique em Revisar.",
                           bg=P2BG if aviso else SURF2,
                           fg="#5e3d00" if aviso else INK2)
        self.b_cad.config(state="disabled")

    def _localizar_estado(self) -> None:
        d = self.ponte.localizar_estado()
        rodando = d.get("running") is True
        self.b_loc.config(state="disabled" if rodando else "normal")
        self.b_loc_parar.config(state="normal" if rodando else "disabled")

        abertos = len(d.get("open_hosts") or [])
        if d.get("error"):
            txt, cor = "Busca interrompida: " + str(d["error"]), P1
        elif rodando:
            fase = d.get("phase")
            txt = (f"Procurando a porta 502 em {d.get('hosts_total', 0)} endereço(s)…"
                   if fase in ("portas", "ports")
                   else f"{d.get('hosts_done', 0)} de {abertos} host(s) analisados"
                        + (f" · agora em {d['current']}" if d.get("current") else ""))
            cor = INK2
        elif d.get("finished_at"):
            total = sum(len(r.get("units") or []) for r in (d.get("reports") or []))
            txt = f"{abertos} host(s) com a porta 502 aberta · {total} Unit ID(s) respondendo"
            cor = INK2
        else:
            txt, cor = "Parado.", INK2
        self.lb_loc.config(text=txt, fg=cor)

        self.tv_loc.delete(*self.tv_loc.get_children())
        for rel in (d.get("reports") or []):
            for u in (rel.get("units") or []):
                m = (u.get("matches") or [None])[0]
                score = (m or {}).get("score", 0)
                if score < 0.5:
                    rotulo, tag = "NÃO IDENTIFICADO", "ruim"
                elif score < 0.8:
                    rotulo, tag = "POSSÍVEL", "aviso"
                else:
                    rotulo, tag = "IDENTIFICADO", ""
                self.tv_loc.insert("", "end", tags=(tag,), values=(
                    rel.get("host"),
                    f"{u.get('unit')}{' (qualquer)' if rel.get('unit_agnostic') else ''}",
                    rotulo,
                    f"{m['manufacturer']} {m['model']}" if m and score >= 0.5 else "—",
                    m.get("device_type") if m and score >= 0.5 else "—",
                    f"{round(score * 100)}%" if m else "—",
                    (m or {}).get("evidence", "")))

        if rodando:
            self.after(2000, self._localizar_estado)

    # ---------------- rodapé e ciclo ----------------
    def _rodape(self) -> None:
        f = tk.Frame(self, bg=SURF2, highlightbackground=HARD, highlightthickness=1)
        f.pack(fill="x", side="bottom")
        self.lb_rod = tk.Label(f, text="", bg=SURF2, fg=INK3, font=("Consolas", 8), anchor="w")
        self.lb_rod.pack(side="left", padx=10, pady=3)
        ttk.Button(f, text="Atualizar", command=self._ciclo_agora).pack(side="right", padx=8, pady=3)

    def _pinta_cel(self, lb: tk.Label, texto: str, alarme: str = "") -> None:
        fundo = {"p1": P1BG, "p2": P2BG}.get(alarme, BANNER)
        frente = {"p1": P1, "p2": "#7a4f00"}.get(alarme, INK)
        lb.config(text=texto, fg=frente, bg=fundo)
        lb._quadro.config(bg=fundo)
        for filho in lb._quadro.winfo_children():
            filho.config(bg=fundo)

    def _ciclo_agora(self) -> None:
        self._ciclo(uma_vez=True)

    def _ciclo(self, uma_vez: bool = False) -> None:
        try:
            d = self.ponte.dados()
        except Exception as exc:
            self.lb_rod.config(text=f"Falha ao ler o gateway: {exc}")
            if not uma_vez:
                self.after(PERIODO_MS, self._ciclo)
            return

        self.lb_planta.config(text=d["planta"])
        self.lb_cfg.config(text=d["configuracao"])
        self._pinta_cel(self.v_svc, d["servico"], "" if d["servico"] == "RODANDO" else "p1")
        self._pinta_cel(self.v_aq, d["aquisicao"])

        devs = d.get("devices") or []
        mudos = [x for x in devs if int(x.get("quality") or 0) != 192]
        self._pinta_cel(self.v_dev,
                        f"{len(mudos)}/{len(devs)}" if mudos else str(len(devs)),
                        "p1" if mudos else "")
        self._pinta_cel(self.v_fila, str(d.get("fila", 0)),
                        "p1" if d.get("fila_erro") else ("p2" if d.get("fila") else ""))
        self._pinta_cel(self.v_hora, d["hora"])

        self.tv_dev.delete(*self.tv_dev.get_children())
        for x in devs:
            q = int(x.get("quality") or 0)
            ok = q == 192
            self.tv_dev.insert("", "end", tags=("" if ok else "ruim",), values=(
                x["device_id"], "normal" if ok else "SEM COMUNICAÇÃO", q,
                x["topic"], str(x.get("sampled_at") or "")[:19]))

        self.tv_val.delete(*self.tv_val.get_children())
        for x in devs:
            for chave, valor in list((x.get("valores") or {}).items())[:120]:
                self.tv_val.insert("", "end", values=(x["device_id"], chave, valor))

        self.tv_ev.delete(*self.tv_ev.get_children())
        for x in (d.get("eventos") or []):
            nivel = str(x.get("level") or "")
            tag = "ruim" if nivel == "ERROR" else ("aviso" if nivel == "WARNING" else "")
            self.tv_ev.insert("", "end", tags=(tag,), values=(
                str(x.get("created_at") or "")[:19], nivel, x.get("source"),
                x.get("code"), x.get("message")))

        self.b_aq.config(text="Parar aquisição" if d["aquisicao"] == "LIGADA" else "Ligar aquisição")
        if not devs and not self.tv_cad.get_children():
            self._atualizar_cadastro()

        aviso = "" if d.get("ok") else "Banco do serviço indisponível.  "
        if not devs:
            aviso += f"Nenhum equipamento publicou telemetria: {d['canais']} canal(is) e " \
                     f"{d['cadastrados']} device(s) na configuração.  "
        self.lb_rod.config(text=aviso + d["caminho_banco"])

        if not uma_vez:
            self.after(PERIODO_MS, self._ciclo)


def main() -> int:
    Console().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
