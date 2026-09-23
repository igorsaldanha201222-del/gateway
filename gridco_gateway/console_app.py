"""Console do Gateway Grid Co: aplicativo de janela.

Sem porta e sem navegador. A janela e' nativa (WebView2, que ja vem no Windows
11) e recebe os dados por uma ponte Python, nao por HTTP.

Le o banco SQLite do servico em modo somente leitura. Como o banco esta em WAL,
ler nao bloqueia a aquisicao; e sendo ``mode=ro``, esta janela nao tem como
escrever configuracao nem corromper o buffer.

Identidade ISA-101: fundo cinza, estado normal em cinza, cor reservada para
condicao anormal.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_PADRAO = Path(r"C:\ProgramData\GridCo\Gateway")
SERVICO = "GridCoGateway"


def caminhos() -> tuple[Path, Path]:
    config = Path(os.environ.get("GRIDCO_CONFIG", BASE_PADRAO / "config" / "gateway.json"))
    dados = Path(os.environ.get("GRIDCO_DATA_DIR", BASE_PADRAO / "data"))
    return config, dados


def estado_servico() -> str:
    try:
        import win32service
        import win32serviceutil
        codigo = win32serviceutil.QueryServiceStatus(SERVICO)[1]
        return {
            win32service.SERVICE_RUNNING: "RODANDO",
            win32service.SERVICE_STOPPED: "PARADO",
            win32service.SERVICE_START_PENDING: "INICIANDO",
            win32service.SERVICE_STOP_PENDING: "PARANDO",
        }.get(codigo, f"CODIGO {codigo}")
    except Exception:
        return "NAO INSTALADO"


class Ponte:
    """O que o HTML pode pedir. Só leitura, sem nenhum método de escrita."""

    def __init__(self) -> None:
        self.config_path, self.dir_dados = caminhos()
        self.banco = self.dir_dados / "gateway.db"

    def _config(self) -> dict:
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _banco(self) -> dict:
        vazio = {"ok": False, "devices": [], "eventos": [], "fila": 0, "fila_erro": 0}
        if not self.banco.exists():
            return vazio
        try:
            con = sqlite3.connect(f"file:{self.banco.as_posix()}?mode=ro", uri=True, timeout=2.0)
            con.row_factory = sqlite3.Row
        except sqlite3.Error:
            return vazio
        try:
            devices = []
            for r in con.execute("select device_id, topic, quality, sampled_at, payload "
                                 "from telemetry_latest order by device_id"):
                item = dict(r)
                try:
                    item["valores"] = json.loads(item.pop("payload") or "{}")
                except Exception:
                    item["valores"] = {}
                devices.append(item)
            eventos = [dict(r) for r in con.execute(
                "select created_at, level, source, code, message from events "
                "order by id desc limit 120")]
            fila = con.execute("select count(*) from mqtt_queue").fetchone()[0]
            erro = con.execute("select count(*) from mqtt_queue where last_error <> ''").fetchone()[0]
            return {"ok": True, "devices": devices, "eventos": eventos,
                    "fila": fila, "fila_erro": erro}
        except sqlite3.Error:
            return vazio
        finally:
            con.close()

    # ---------- escrita: cadastro de equipamento ----------
    def _catalogo(self):
        import sys

        from .catalog import TemplateCatalog
        candidatos = []
        if os.environ.get("GRIDCO_CATALOG"):
            candidatos.append(Path(os.environ["GRIDCO_CATALOG"]))
        # Empacotado, o catalogo vai junto e e' extraido em _MEIPASS.
        if getattr(sys, "_MEIPASS", None):
            candidatos.append(Path(sys._MEIPASS) / "template_catalog.json")
        candidatos.append(self.config_path.parent / "template_catalog.json")
        # Rodando do repositorio, o catalogo esta em config/ do projeto.
        candidatos.append(Path(__file__).resolve().parents[1] / "config" / "template_catalog.json")
        for c in candidatos:
            if c.is_file():
                return TemplateCatalog(c)
        raise FileNotFoundError(
            "template_catalog.json não encontrado. Procurei em: "
            + ", ".join(str(c) for c in candidatos))

    def catalogo(self) -> dict:
        """Lista os modelos disponíveis, sem expor o mapa de registradores."""
        try:
            resumo = self._catalogo().summary()
        except Exception as exc:
            return {"ok": False, "erro": str(exc), "modelos": []}
        modelos = sorted(resumo.get("entries", []),
                         key=lambda e: (str(e.get("manufacturer")), str(e.get("name"))))
        return {"ok": True, "revisao": resumo.get("catalog_revision"), "modelos": modelos}

    def canais(self) -> list:
        cfg = self._config()
        return [{"id": c.get("id"), "nome": c.get("name"), "transporte": c.get("transport"),
                 "ip": c.get("ip"), "porta": c.get("port"), "serial": c.get("serial_device")}
                for c in (cfg.get("channels") or [])]

    def equipamentos(self) -> list:
        cfg = self._config()
        return [{"id": d.get("id"), "nome": d.get("name"), "tipo": d.get("device_type"),
                 "canal": d.get("channel_id"), "unit_id": d.get("unit_id"),
                 "indice": (d.get("metadata") or {}).get("mqtt_topic_index")}
                for d in (cfg.get("devices") or [])]

    def previa(self, pedido: dict) -> dict:
        """Monta o candidato sem gravar: é o passo 'Revisão' do assistente."""
        try:
            atual = self._config()
            novo = self._catalogo().onboard(atual, pedido)
        except Exception as exc:
            return {"ok": False, "erro": str(exc)}
        topico = next((t.get("topic") for t in (novo.get("topics") or [])
                       if str(t.get("device_id")) == str((pedido.get("device") or {}).get("id"))), "")
        return {
            "ok": True, "topico": topico,
            "blocos": len(novo.get("requests") or []) - len(atual.get("requests") or []),
            "variaveis": len(novo.get("fields") or []) - len(atual.get("fields") or []),
            "canais": len(novo.get("channels") or []) - len(atual.get("channels") or []),
        }

    def cadastrar(self, pedido: dict) -> dict:
        """Grava de verdade: valida, versiona e reinicia o serviço."""
        from .config import ConfigurationManager
        try:
            atual = self._config()
            novo = self._catalogo().onboard(atual, pedido)
            gerenciador = ConfigurationManager(self.config_path, self.dir_dados / "config_versions")
            gerenciador.load()
            resultado = gerenciador.apply(novo, origin="console")
        except PermissionError:
            return {"ok": False, "erro": "Sem permissão para gravar a configuração. "
                                         "Abra o console como administrador."}
        except Exception as exc:
            return {"ok": False, "erro": str(exc)}
        reinicio = self.reiniciar_servico()
        return {"ok": True, "revisao": (resultado.get("configuration") or {}).get("revision"),
                "servico": reinicio}

    def remover(self, device_id: str) -> dict:
        from .config import ConfigurationManager
        try:
            cfg = self._config()
            antes = len(cfg.get("devices") or [])
            cfg["devices"] = [d for d in (cfg.get("devices") or []) if str(d.get("id")) != str(device_id)]
            cfg["topics"] = [t for t in (cfg.get("topics") or []) if str(t.get("device_id")) != str(device_id)]
            if len(cfg["devices"]) == antes:
                return {"ok": False, "erro": "equipamento não encontrado"}
            gerenciador = ConfigurationManager(self.config_path, self.dir_dados / "config_versions")
            gerenciador.load()
            gerenciador.apply(cfg, origin="console")
        except Exception as exc:
            return {"ok": False, "erro": str(exc)}
        return {"ok": True, "servico": self.reiniciar_servico()}

    def aquisicao(self, ligar: bool) -> dict:
        from .config import ConfigurationManager
        try:
            cfg = self._config()
            cfg.setdefault("runtime", {})["enabled"] = bool(ligar)
            gerenciador = ConfigurationManager(self.config_path, self.dir_dados / "config_versions")
            gerenciador.load()
            gerenciador.apply(cfg, origin="console")
        except Exception as exc:
            return {"ok": False, "erro": str(exc)}
        return {"ok": True, "servico": self.reiniciar_servico()}

    # ---------- localizador de equipamentos ----------
    def _job(self):
        if getattr(self, "_finder", None) is None:
            from .device_finder import FinderJob
            self._finder = FinderJob()
        return self._finder

    def localizar(self, alvos: str, units: str) -> dict:
        """Varre a faixa procurando a porta 502 e identifica por assinatura."""
        lista = [a for a in str(alvos or "").replace(";", " ").replace(",", " ").split() if a]
        if not lista:
            return {"erro": "informe um IP ou uma rede, ex.: 192.168.1.0/24"}
        try:
            return self._job().start(lista, str(units or "1-20"))
        except Exception as exc:
            return {"erro": str(exc)}

    def localizar_estado(self) -> dict:
        return self._job().snapshot()

    def localizar_parar(self) -> dict:
        return self._job().cancel()

    # ---------- do achado para o cadastro ----------
    @staticmethod
    def _tokens(texto: str) -> set[str]:
        limpo = "".join(c.lower() if c.isalnum() else " " for c in str(texto))
        return {t for t in limpo.split() if len(t) > 1}

    def sugerir(self, host: str, unit, fabricante: str, modelo: str, tipo: str) -> dict:
        """Traduz um achado do localizador em um cadastro pré-preenchido.

        O localizador identifica por assinatura e devolve fabricante e modelo
        como texto; o catálogo é outra fonte. O casamento é por sobreposição de
        palavras, e por isso devolve uma LISTA ordenada, não uma escolha: quem
        confirma é quem está olhando.
        """
        try:
            modelos = self._catalogo().summary().get("entries", [])
        except Exception as exc:
            return {"ok": False, "erro": str(exc)}

        alvo = self._tokens(f"{fabricante} {modelo}")
        candidatos = []
        for m in modelos:
            seus = self._tokens(f"{m.get('manufacturer')} {m.get('name')} {m.get('model')}")
            if not seus:
                continue
            comuns = len(alvo & seus)
            if not comuns:
                continue
            nota = comuns / len(alvo | seus)
            # Mesmo tipo de equipamento conta: separa um relé de um inversor
            # quando as palavras do fabricante coincidem.
            if tipo and str(m.get("device_type")) == str(tipo):
                nota += 0.25
            candidatos.append({"catalog_id": m.get("catalog_id"),
                               "rotulo": f"{m.get('manufacturer')} · {m.get('name')}",
                               "device_type": m.get("device_type"),
                               "nota": round(nota, 3)})
        candidatos.sort(key=lambda c: -c["nota"])

        cfg = self._config()
        tipo_alvo = (candidatos[0]["device_type"] if candidatos else tipo) or "device"

        # Canal: reaproveita um que já aponte para este IP.
        canal = next((c for c in (cfg.get("channels") or [])
                      if str(c.get("ip")) == str(host)), None)
        sugestao_canal = ({"existente": canal.get("id")} if canal else
                          {"novo": {"id": "modbus-" + str(host).replace(".", "-"),
                                    "ip": str(host), "port": 502}})

        # Índice: o menor número livre para este tipo, nunca a contagem da
        # lista — device removido no meio deixaria buraco e reutilizar o número
        # misturaria histórico no servidor.
        usados = {int((d.get("metadata") or {}).get("mqtt_topic_index", 0) or 0)
                  for d in (cfg.get("devices") or [])
                  if str(d.get("device_type")) == tipo_alvo}
        indice = next(i for i in range(1, 10000) if i not in usados)

        ids = {str(d.get("id")) for d in (cfg.get("devices") or [])}
        base = f"{tipo_alvo}-{indice:02d}"
        novo_id = base
        n = indice
        while novo_id in ids:
            n += 1
            novo_id = f"{tipo_alvo}-{n:02d}"

        return {"ok": True, "candidatos": candidatos[:8], "canal": sugestao_canal,
                "device": {"id": novo_id, "name": f"{fabricante} {modelo}".strip() or novo_id,
                           "unit_id": unit, "index": indice},
                "tipo": tipo_alvo}

    # ---------- manutenção de templates já instalados ----------
    @staticmethod
    def _resumo_mudancas(antes_f: list, depois_f: list, antes_r: list, depois_r: list) -> dict:
        chaves_a = {str(f.get("json_key")) for f in antes_f}
        chaves_d = {str(f.get("json_key")) for f in depois_f}

        def assinatura(f):
            return (f.get("request_id"), f.get("register_offset"), f.get("bit_offset"),
                    f.get("data_type"), f.get("gain"), f.get("offset"),
                    f.get("word_order"), f.get("unit"), f.get("source_type"))

        por_chave_a = {str(f.get("json_key")): assinatura(f) for f in antes_f}
        alteradas = sorted(k for k in (chaves_a & chaves_d)
                           if por_chave_a[k] != next(assinatura(f) for f in depois_f
                                                     if str(f.get("json_key")) == k))
        blocos_a = {(r.get("function_code"), r.get("address"), r.get("quantity")) for r in antes_r}
        blocos_d = {(r.get("function_code"), r.get("address"), r.get("quantity")) for r in depois_r}
        return {
            "adicionadas": sorted(chaves_d - chaves_a),
            "removidas": sorted(chaves_a - chaves_d),
            "alteradas": alteradas,
            "blocos_mudaram": blocos_a != blocos_d,
            "blocos_antes": len(antes_r), "blocos_depois": len(depois_r),
        }

    def templates(self) -> dict:
        """Compara cada modelo instalado nesta usina com o do catálogo."""
        from .catalog import digest_da_config, digest_da_entrada
        cfg = self._config()
        try:
            cat = self._catalogo()
        except Exception as exc:
            return {"ok": False, "erro": str(exc), "itens": []}

        por_template: dict[str, list] = {}
        for d in (cfg.get("devices") or []):
            por_template.setdefault(str(d.get("template_id")), []).append(d)

        itens = []
        for tpl in (cfg.get("templates") or []):
            tid = str(tpl.get("id"))
            devices = por_template.get(tid, [])
            meta = (devices[0].get("metadata") or {}) if devices else {}
            catalog_id = str(meta.get("catalog_id") or "")
            local = digest_da_config(cfg, tid)
            gravado = meta.get("template_digest")

            entrada = None
            if catalog_id:
                try:
                    entrada = cat.detail(catalog_id)
                except Exception:
                    entrada = None

            if entrada is None:
                estado, mud = "sem_catalogo", {}
            else:
                do_catalogo = digest_da_entrada(entrada)
                if local == do_catalogo:
                    estado, mud = "atualizado", {}
                elif gravado and local != gravado:
                    # A copia daqui nao e' mais a que foi instalada: alguem
                    # editou. Substituir descartaria essa edicao em silencio.
                    estado = "editado_aqui"
                    mud = self._resumo_mudancas(
                        [f for f in (cfg.get("fields") or []) if str(f.get("template_id")) == tid],
                        entrada["fields"],
                        [r for r in (cfg.get("requests") or []) if str(r.get("template_id")) == tid],
                        entrada["requests"])
                else:
                    estado = "desatualizado"
                    mud = self._resumo_mudancas(
                        [f for f in (cfg.get("fields") or []) if str(f.get("template_id")) == tid],
                        entrada["fields"],
                        [r for r in (cfg.get("requests") or []) if str(r.get("template_id")) == tid],
                        entrada["requests"])
                if not gravado and estado != "atualizado":
                    # Cadastrado por uma versao que ainda nao gravava o digest:
                    # da' para ver que difere, nao da' para saber de que lado.
                    estado = "difere_origem_incerta"

            itens.append({
                "template_id": tid, "nome": tpl.get("name") or tid,
                "catalog_id": catalog_id, "estado": estado,
                "devices": [str(d.get("id")) for d in devices],
                "mudancas": mud,
            })
        return {"ok": True, "itens": itens}

    def atualizar_template(self, template_id: str) -> dict:
        """Substitui o modelo instalado pelo do catálogo, versionando antes."""
        from .config import ConfigurationManager
        from .catalog import digest_da_entrada
        try:
            cfg = self._config()
            tid = str(template_id)
            devices = [d for d in (cfg.get("devices") or []) if str(d.get("template_id")) == tid]
            if not devices:
                return {"ok": False, "erro": "nenhum equipamento usa este modelo"}
            catalog_id = str((devices[0].get("metadata") or {}).get("catalog_id") or "")
            entrada = self._catalogo().detail(catalog_id)

            # Troca template, blocos e variaveis por inteiro. Os devices so'
            # apontam para template_id, entao continuam validos.
            cfg["templates"] = [t for t in (cfg.get("templates") or []) if str(t.get("id")) != tid]
            cfg["requests"] = [r for r in (cfg.get("requests") or []) if str(r.get("template_id")) != tid]
            cfg["fields"] = [f for f in (cfg.get("fields") or []) if str(f.get("template_id")) != tid]
            cfg["templates"].append(entrada["template"])
            cfg["requests"].extend(entrada["requests"])
            cfg["fields"].extend(entrada["fields"])

            novo_digest = digest_da_entrada(entrada)
            for d in cfg["devices"]:
                if str(d.get("template_id")) == tid:
                    d.setdefault("metadata", {})
                    d["metadata"]["catalog_sha256"] = entrada.get("semantic_sha256")
                    d["metadata"]["template_digest"] = novo_digest

            gerenciador = ConfigurationManager(self.config_path, self.dir_dados / "config_versions")
            gerenciador.load()
            resultado = gerenciador.apply(cfg, origin="console:template")
        except Exception as exc:
            return {"ok": False, "erro": str(exc)}
        return {"ok": True, "revisao": (resultado.get("configuration") or {}).get("revision"),
                "devices": len(devices), "servico": self.reiniciar_servico()}

    def reiniciar_servico(self) -> str:
        try:
            import win32serviceutil
            win32serviceutil.RestartService(SERVICO)
            return "reiniciado"
        except Exception as exc:
            return f"não reiniciou: {exc}"

    # chamado pelo HTML
    def dados(self) -> dict:
        cfg = self._config()
        planta = cfg.get("plant") if isinstance(cfg.get("plant"), dict) else {}
        runtime = cfg.get("runtime") if isinstance(cfg.get("runtime"), dict) else {}
        d = self._banco()
        from .versao import longa as versao_longa
        d.update({
            "planta": (planta or {}).get("name") or (planta or {}).get("id") or "sem planta",
            "configuracao": f"{cfg.get('configuration_id', '—')} · rev {cfg.get('revision', '?')}",
            "versao_app": versao_longa(),
            "servico": estado_servico(),
            "aquisicao": "LIGADA" if (runtime or {}).get("enabled") else "PARADA",
            "canais": len(cfg.get("channels") or []),
            "cadastrados": len(cfg.get("devices") or []),
            "hora": datetime.now().strftime("%H:%M:%S"),
            "caminho_banco": str(self.banco),
            "caminho_config": str(self.config_path),
        })
        return d


PAGINA = r"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<title>Gateway Grid Co</title><style>
:root{--ground:#c3c3c3;--surface:#d2d2d2;--surface2:#cbcbcb;--surface3:#bcbcbc;
--banner:#a9a9a9;--line:#8e8e8e;--hard:#5c5c5c;--ink:#161616;--ink2:#414141;--ink3:#6b6b6b;
--field:#e4e4e4;--p1:#b51414;--p1bg:#e8c9c9;--p2:#c98200;--p2bg:#ecdcbc;--act:#1f4e79;
--ui:"Segoe UI",Arial,sans-serif;--mono:Consolas,"Courier New",monospace}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--ground);color:var(--ink);font:13px/1.45 var(--ui);
display:flex;flex-direction:column;overflow:hidden;user-select:none}
.banner{background:var(--banner);border-bottom:1px solid var(--hard);display:flex;
justify-content:space-between;align-items:stretch;flex:none}
.bid{display:flex;align-items:center;gap:11px;padding:8px 14px}
.mark{width:28px;height:28px;border:1px solid var(--hard);display:grid;place-items:center;
font:700 12px var(--mono);background:var(--surface2)}
.bid strong{display:block;font-size:12.5px;letter-spacing:.06em}
.bid small{display:block;color:var(--ink2);font:10.5px var(--mono)}
.meta{display:flex}
.cell{padding:8px 14px;border-left:1px solid var(--line);min-width:96px}
.cell span{display:block;font-size:9px;letter-spacing:.11em;text-transform:uppercase;color:var(--ink2)}
.cell strong{display:block;font:600 15px var(--mono);font-variant-numeric:tabular-nums}
.cell.alarme{background:var(--p1bg)}.cell.alarme strong{color:var(--p1)}
.cell.aviso{background:var(--p2bg)}.cell.aviso strong{color:#7a4f00}
nav{display:flex;background:var(--surface2);border-bottom:1px solid var(--hard);flex:none}
nav button{border:0;border-right:1px solid var(--line);background:transparent;color:var(--ink);
font:600 12px var(--ui);padding:9px 18px;cursor:pointer}
nav button:hover{background:var(--surface)}
nav button[aria-current]{background:var(--surface);box-shadow:inset 0 -3px var(--act)}
main{flex:1;overflow:auto;padding:12px}
.painel{background:var(--surface);border:1px solid var(--hard);margin-bottom:12px}
.ph{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:9px 12px;
border-bottom:1px solid var(--line);background:var(--surface2)}
.ph h2{margin:0;font-size:13px;font-weight:600}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line)}
th{background:var(--surface2);font:600 9.5px var(--ui);letter-spacing:.1em;text-transform:uppercase;
color:var(--ink2);border-bottom:1px solid var(--hard);position:sticky;top:0}
td{font:11.5px var(--mono);font-variant-numeric:tabular-nums}
tbody tr:nth-child(even){background:var(--surface2)}
tr.ruim td{background:var(--p1bg);color:#5e0c0c}
tr.aviso td{background:var(--p2bg);color:#5e3d00}
.st{display:inline-flex;align-items:center;gap:5px;font:600 10.5px var(--mono);padding:2px 6px;
border:1px solid var(--hard);background:var(--surface2)}
.st::before{content:"";width:7px;height:7px;background:var(--ink3);border:1px solid var(--ink3)}
.st.bad{border-color:var(--p1);background:var(--p1bg);color:#5e0c0c}
.st.bad::before{background:var(--p1);border-color:var(--p1);transform:rotate(45deg)}
.vazio{padding:22px 14px;color:var(--ink2);font-size:12.5px;margin:0}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px;padding:10px}
.card{border:1px solid var(--hard);background:var(--surface)}
.card h3{margin:0;padding:7px 10px;font:600 12px var(--mono);background:var(--surface2);
border-bottom:1px solid var(--hard);display:flex;justify-content:space-between;gap:8px}
.card.ruim{border-color:var(--p1)}.card.ruim h3{background:var(--p1bg);border-bottom-color:var(--p1)}
.card dl{display:grid;grid-template-columns:1fr auto;margin:0}
.card dt,.card dd{margin:0;padding:4px 10px;font:11px var(--mono);border-bottom:1px solid var(--line)}
.card dt{color:var(--ink2)}.card dd{text-align:right}
footer{flex:none;background:var(--surface2);border-top:1px solid var(--hard);padding:5px 12px;
font:10.5px var(--mono);color:var(--ink3);display:flex;justify-content:space-between;gap:12px}
.form{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px;padding:12px}
.col{display:flex;flex-direction:column;gap:10px;min-width:0}
.dupla{display:grid;grid-template-columns:1fr 1fr;gap:10px}
label{display:grid;gap:4px;font:11px var(--ui);color:var(--ink2)}
input,select{width:100%;border:1px solid var(--hard);background:var(--field);color:var(--ink);
padding:6px 7px;font:12px var(--mono);border-radius:0}
select[size]{padding:0}select[size] option{padding:4px 7px}
input:focus,select:focus{outline:2px solid var(--act);outline-offset:-1px}
.det{border:1px solid var(--line);background:var(--surface2);padding:10px;font:11px var(--mono);
color:var(--ink2);min-height:90px}
.det b{color:var(--ink);font-weight:600}
.acoes{display:flex;gap:8px;justify-content:flex-end}
.btn{border:1px solid var(--hard);background:var(--surface2);color:var(--ink);padding:6px 12px;
font:600 12px var(--ui);cursor:pointer}
.btn:hover{background:var(--field)}
.btn.act{background:var(--act);border-color:var(--act);color:#fff}
.btn:disabled{color:var(--ink3);background:var(--surface3);cursor:not-allowed}
.msg{border:1px solid var(--hard);border-left:4px solid var(--act);background:var(--surface2);
padding:10px;font:11.5px var(--mono);white-space:pre-wrap}
.msg.erro{border-left-color:var(--p1);background:var(--p1bg);color:#5e0c0c}
.aviso{margin:0 12px;padding:8px 10px;border:1px solid var(--line);background:var(--surface2);
font-size:11.5px;color:var(--ink2)}
.prog{margin:10px 12px;font:11.5px var(--mono);color:var(--ink2)}
.conf{display:inline-flex;align-items:center;gap:6px}
.conf i{display:block;height:8px;border:1px solid var(--hard);background:var(--ink3);min-width:2px}
.conf.baixa i{background:var(--p2);border-color:var(--p2)}
.conf.nenhuma i{background:transparent}
@media(max-width:900px){.form{grid-template-columns:1fr}}
</style></head><body>
<div class="banner">
  <div class="bid"><div class="mark">GC</div><div>
    <strong id="planta">—</strong><small id="cfg">—</small></div></div>
  <div class="meta">
    <div class="cell" id="c-svc"><span>Serviço</span><strong id="svc">—</strong></div>
    <div class="cell" id="c-aq"><span>Aquisição</span><strong id="aq">—</strong></div>
    <div class="cell" id="c-dev"><span>Equipamentos</span><strong id="dev">—</strong></div>
    <div class="cell" id="c-fila"><span>Fila</span><strong id="fila">—</strong></div>
    <div class="cell"><span>Atualizado</span><strong id="hora">—</strong></div>
  </div>
</div>
<nav>
  <button data-aba="equip" aria-current="page">Equipamentos</button>
  <button data-aba="valores">Valores</button>
  <button data-aba="cadastro">Cadastro</button>
  <button data-aba="localizador">Localizador</button>
  <button data-aba="eventos">Eventos</button>
</nav>
<main>
  <section id="aba-equip"><article class="painel">
    <div class="ph"><h2>Estado dos equipamentos</h2><span class="st" id="resumo">—</span></div>
    <div id="tab-equip"></div></article></section>
  <section id="aba-valores" hidden><div class="cards" id="cards"></div></section>

  <section id="aba-cadastro" hidden>
    <article class="painel">
      <div class="ph"><h2>Inserir equipamento pelo catálogo</h2><span class="st" id="cat-rev">—</span></div>
      <div class="form">
        <div class="col">
          <label>Modelo
            <input id="f-busca" placeholder="filtrar: Huawei, Solis, Schneider…" autocomplete="off">
            <select id="f-modelo" size="10"></select></label>
          <div id="f-detalhe" class="det">Selecione um modelo.</div>
        </div>
        <div class="col">
          <div class="dupla">
            <label>Nome<input id="f-nome" placeholder="Inversor 01"></label>
            <label>ID<input id="f-id" placeholder="inversor-01"></label>
            <label>Unit ID<input id="f-unit" type="number" min="0" max="255" value="1"></label>
            <label>Índice no tópico<input id="f-indice" type="number" min="1" max="9999" value="1"></label>
          </div>
          <label>Canal
            <select id="f-canal"></select></label>
          <div id="f-novocanal" hidden class="dupla">
            <label>ID do canal<input id="f-cid" value="modbus-tcp-01"></label>
            <label>IP<input id="f-cip" placeholder="192.168.1.11"></label>
            <label>Porta<input id="f-cporta" type="number" value="502"></label>
          </div>
          <div class="acoes">
            <button class="btn" id="b-revisar">Revisar</button>
            <button class="btn act" id="b-cadastrar" disabled>Cadastrar e aplicar</button>
          </div>
          <div id="f-resultado"></div>
        </div>
      </div>
    </article>
    <article class="painel">
      <div class="ph"><h2>Cadastrados</h2>
        <span><button class="btn" id="b-aquis">—</button></span></div>
      <div id="tab-cad"></div>
    </article>
  </section>
  <section id="aba-localizador" hidden>
    <article class="painel">
      <div class="ph"><h2>Localizar equipamentos na rede</h2><span class="st" id="loc-estado">PARADO</span></div>
      <div class="form" style="grid-template-columns:2fr 1.2fr auto auto;align-items:end">
        <label>IPs ou redes<input id="loc-alvos" value="192.168.1.0/24"></label>
        <label>Unit IDs<input id="loc-units" value="1-20,247,255"></label>
        <button class="btn act" id="loc-ir">Procurar</button>
        <button class="btn" id="loc-parar" disabled>Parar</button>
      </div>
      <p class="aviso">Somente leitura: nada é escrito nos equipamentos. A identificação
        compara a resposta com 23 assinaturas conhecidas da frota.</p>
      <p class="prog" id="loc-prog"></p>
      <div id="loc-tab"></div>
    </article>
  </section>

  <section id="aba-eventos" hidden><article class="painel">
    <div class="ph"><h2>Eventos do serviço</h2></div><div id="tab-ev"></div></article></section>
</main>
<footer><span id="rodape">—</span><span id="origem">—</span></footer>
<script>
// Sem devtools na janela empacotada, um erro de script deixaria a tela parada
// em "—" sem dizer nada. Entao todo erro vai para o rodape.
window.onerror=function(msg,arq,lin){
  var r=document.getElementById("rodape");
  if(r) r.textContent="ERRO NA TELA: "+msg+"  (linha "+lin+")";
  return false;
};
var esc=function(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){
return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];});};
var ABAS=["equip","valores","cadastro","localizador","eventos"];
document.querySelectorAll("nav button").forEach(function(b){
  b.onclick=function(){
    document.querySelectorAll("nav button").forEach(function(x){x.removeAttribute("aria-current");});
    b.setAttribute("aria-current","page");
    ABAS.forEach(function(a){
      document.getElementById("aba-"+a).hidden=(a!==b.dataset.aba);});
    if(b.dataset.aba==="cadastro") carregarCadastro();};});

/* ---------------- cadastro ---------------- */
var MODELOS=[], escolhido=null;

async function carregarCadastro(){
  if(!MODELOS.length){
    var c=await window.pywebview.api.catalogo();
    if(!c.ok){ document.getElementById("f-detalhe").innerHTML="<b>Catálogo indisponível:</b> "+esc(c.erro); return; }
    MODELOS=c.modelos||[];
    document.getElementById("cat-rev").textContent=MODELOS.length+" MODELOS · REV "+esc(c.revisao);
    listarModelos();
  }
  var canais=await window.pywebview.api.canais();
  var sel=document.getElementById("f-canal");
  sel.innerHTML=canais.map(function(x){
    return '<option value="'+esc(x.id)+'">'+esc(x.id)+" · "+esc(x.ip||x.serial||"")+
           (x.porta?":"+esc(x.porta):"")+"</option>";}).join("")+
    '<option value="__novo">＋ criar novo canal…</option>';
  sel.onchange=function(){document.getElementById("f-novocanal").hidden=(sel.value!=="__novo");};
  sel.onchange();
  var eq=await window.pywebview.api.equipamentos();
  document.getElementById("tab-cad").innerHTML=eq.length?
    '<table><thead><tr><th>ID</th><th>Tipo</th><th>Canal</th><th>Unit</th><th>Índice</th><th></th></tr></thead><tbody>'+
    eq.map(function(x){return "<tr><td>"+esc(x.id)+"</td><td>"+esc(x.tipo)+"</td><td>"+esc(x.canal)+
      "</td><td>"+esc(x.unit_id)+"</td><td>"+esc(x.indice)+
      '</td><td><button class="btn" data-remover="'+esc(x.id)+'">remover</button></td></tr>';}).join("")+
    "</tbody></table>":'<p class="vazio">Nenhum equipamento cadastrado.</p>';
  document.querySelectorAll("[data-remover]").forEach(function(b){
    b.onclick=async function(){
      b.disabled=true;
      var r=await window.pywebview.api.remover(b.dataset.remover);
      mostrar(r.ok?("Removido. Serviço "+r.servico):("Falhou: "+r.erro), !r.ok);
      carregarCadastro();};});
}

function listarModelos(){
  var q=(document.getElementById("f-busca").value||"").toLowerCase();
  var lista=MODELOS.filter(function(m){
    return !q||((m.manufacturer+" "+m.name+" "+m.device_type).toLowerCase().indexOf(q)>=0);});
  document.getElementById("f-modelo").innerHTML=lista.map(function(m){
    return '<option value="'+esc(m.catalog_id)+'">'+esc(m.manufacturer)+" · "+esc(m.name)+"</option>";}).join("");
  if(lista.length){document.getElementById("f-modelo").value=lista[0].catalog_id; detalhar();}
}
function detalhar(){
  var id=document.getElementById("f-modelo").value;
  escolhido=MODELOS.filter(function(m){return m.catalog_id===id;})[0]||null;
  var d=document.getElementById("f-detalhe");
  if(!escolhido){d.textContent="Selecione um modelo.";return;}
  d.innerHTML="<b>"+esc(escolhido.manufacturer)+" "+esc(escolhido.name)+"</b><br>"+
    "tipo: "+esc(escolhido.device_type)+"<br>blocos: "+esc(escolhido.request_count)+
    " · variáveis: "+esc(escolhido.field_count)+"<br>"+esc(escolhido.description||"");
  document.getElementById("b-cadastrar").disabled=true;
}
document.getElementById("f-busca").oninput=listarModelos;
document.getElementById("f-modelo").onchange=detalhar;

function pedido(){
  var canal=document.getElementById("f-canal").value;
  var p={catalog_id:escolhido?escolhido.catalog_id:"",
    device:{id:document.getElementById("f-id").value.trim(),
      name:document.getElementById("f-nome").value.trim(),
      unit_id:Number(document.getElementById("f-unit").value),
      index:Number(document.getElementById("f-indice").value),
      channel_id:(canal==="__novo"?document.getElementById("f-cid").value.trim():canal)}};
  if(canal==="__novo"){p.channel={id:document.getElementById("f-cid").value.trim(),
    transport:"tcp",ip:document.getElementById("f-cip").value.trim(),
    port:Number(document.getElementById("f-cporta").value)};}
  return p;
}
function mostrar(texto,erro){
  document.getElementById("f-resultado").innerHTML='<div class="msg'+(erro?" erro":"")+'">'+esc(texto)+"</div>";
}
document.getElementById("b-revisar").onclick=async function(){
  var r=await window.pywebview.api.previa(pedido());
  if(!r.ok){ mostrar(r.erro,true); document.getElementById("b-cadastrar").disabled=true; return; }
  mostrar("Tópico:  "+r.topico+"\n+"+r.blocos+" blocos   +"+r.variaveis+" variáveis   +"+r.canais+" canal(is)"+
          "\n\nNada foi gravado ainda.");
  document.getElementById("b-cadastrar").disabled=false;
};
document.getElementById("b-cadastrar").onclick=async function(){
  this.disabled=true;
  var r=await window.pywebview.api.cadastrar(pedido());
  mostrar(r.ok?("Cadastrado. Revisão "+r.revisao+". Serviço "+r.servico):("Falhou: "+r.erro), !r.ok);
  if(r.ok) carregarCadastro();
};

function marcar(id,cls){var el=document.getElementById(id);
  el.className="cell"+(cls?" "+cls:"");}

function pintar(d){
  document.getElementById("planta").textContent=d.planta;
  document.getElementById("cfg").textContent=d.configuracao;
  document.getElementById("svc").textContent=d.servico;
  marcar("c-svc", d.servico==="RODANDO"?"":"alarme");
  document.getElementById("aq").textContent=d.aquisicao;
  marcar("c-aq","");
  var devs=d.devices||[];
  var mudos=devs.filter(function(x){return Number(x.quality)!==192;});
  document.getElementById("dev").textContent=devs.length?(mudos.length?mudos.length+"/"+devs.length:devs.length):"0";
  marcar("c-dev", mudos.length?"alarme":"");
  document.getElementById("fila").textContent=d.fila;
  marcar("c-fila", d.fila_erro?"alarme":(d.fila?"aviso":""));
  document.getElementById("hora").textContent=d.hora;

  var r=document.getElementById("resumo");
  r.textContent=devs.length?(mudos.length?mudos.length+" SEM COMUNICAÇÃO":devs.length+" NORMAIS"):"NENHUM";
  r.className="st"+(mudos.length?" bad":"");

  document.getElementById("tab-equip").innerHTML=devs.length?
    '<table><thead><tr><th>Device</th><th>Qualidade</th><th>Tópico</th><th>Última amostra</th></tr></thead><tbody>'+
    devs.map(function(x){var ok=Number(x.quality)===192;
      return '<tr class="'+(ok?"":"ruim")+'"><td>'+esc(x.device_id)+"</td><td>"+esc(x.quality)+
      (ok?" normal":" sem comunicação")+"</td><td>"+esc(x.topic)+"</td><td>"+
      esc(String(x.sampled_at||"").slice(0,19))+"</td></tr>";}).join("")+"</tbody></table>"
    :'<p class="vazio">Nenhum equipamento publicou telemetria ainda. Com a configuração genérica instalada — '+
      esc(d.canais)+' canal(is) e '+esc(d.cadastrados)+' device(s) cadastrados — não há o que ler.</p>';

  document.getElementById("cards").innerHTML=devs.length?devs.map(function(x){
    var ok=Number(x.quality)===192, v=x.valores||{};
    return '<article class="card'+(ok?"":" ruim")+'"><h3><span>'+esc(x.device_id)+"</span><span>"+
      esc(x.quality)+"</span></h3><dl>"+Object.keys(v).slice(0,40).map(function(k){
      return "<dt>"+esc(k)+"</dt><dd>"+esc(v[k])+"</dd>";}).join("")+"</dl></article>";}).join("")
    :'<p class="vazio">Sem valores.</p>';

  var ev=d.eventos||[];
  document.getElementById("tab-ev").innerHTML=ev.length?
    '<table><thead><tr><th>Quando</th><th>Nível</th><th>Origem</th><th>Código</th><th>Mensagem</th></tr></thead><tbody>'+
    ev.map(function(x){var c=x.level==="ERROR"?"ruim":(x.level==="WARNING"?"aviso":"");
      return '<tr class="'+c+'"><td>'+esc(String(x.created_at||"").slice(0,19))+"</td><td>"+esc(x.level)+
      "</td><td>"+esc(x.source)+"</td><td>"+esc(x.code)+"</td><td>"+esc(x.message)+"</td></tr>";}).join("")+
    "</tbody></table>":'<p class="vazio">Sem eventos registrados.</p>';

  var ba=document.getElementById("b-aquis"), ligada=(d.aquisicao==="LIGADA");
  ba.textContent=ligada?"Parar aquisição":"Ligar aquisição";
  ba.className="btn"+(ligada?"":" act");
  ba.onclick=async function(){
    ba.disabled=true;
    var r=await window.pywebview.api.aquisicao(!ligada);
    if(!r.ok) mostrar("Falhou: "+r.erro,true);
    ba.disabled=false;};

  document.getElementById("rodape").textContent=d.ok?"":"Banco do serviço indisponível.";
  document.getElementById("origem").textContent=d.caminho_banco;
}

/* ---------------- localizador ---------------- */
var locTimer=null;
function veredito(s){
  if(s<0.5) return {t:"NÃO IDENTIFICADO", c:"bad", f:"nenhuma"};
  if(s<0.8) return {t:"POSSÍVEL", c:"warn", f:"baixa"};
  return {t:"IDENTIFICADO", c:"", f:""};
}
function pintarLoc(d){
  var rodando=d.running===true;
  document.getElementById("loc-ir").disabled=rodando;
  document.getElementById("loc-parar").disabled=!rodando;
  var e=document.getElementById("loc-estado");
  e.textContent=rodando?"PROCURANDO":(d.finished_at?(d.cancelled?"INTERROMPIDO":"CONCLUÍDO"):"PARADO");
  e.className="st"+(d.cancelled?" warn":"");

  if(d.erro||d.error){
    document.getElementById("loc-prog").textContent="Erro: "+(d.erro||d.error);
  }else if(d.started_at){
    var abertos=(d.open_hosts||[]).length;
    document.getElementById("loc-prog").textContent = rodando
      ? (d.phase==="portas"||d.phase==="ports"
          ? "Procurando a porta 502 em "+(d.hosts_total||0)+" endereço(s)…"
          : (d.hosts_done||0)+" de "+abertos+" host(s) analisados"+(d.current?" · agora em "+d.current:""))
      : abertos+" host(s) com a porta 502 aberta";
  }

  var linhas=[];
  (d.reports||[]).forEach(function(rel){
    (rel.units||[]).forEach(function(u){
      var melhor=(u.matches||[])[0];
      var v=veredito(melhor?melhor.score:0);
      linhas.push('<tr><td>'+esc(rel.host)+"</td><td>"+esc(u.unit)+(rel.unit_agnostic?" (qualquer)":"")+
        '</td><td><span class="st '+v.c+'">'+v.t+"</span></td><td>"+
        esc(melhor&&melhor.score>=0.5?(melhor.manufacturer+" "+melhor.model):"—")+"</td><td>"+
        esc(melhor&&melhor.score>=0.5?melhor.device_type:"—")+'</td><td><span class="conf '+v.f+'">'+
        '<i style="width:'+Math.round((melhor?melhor.score:0)*44)+'px"></i>'+
        (melhor?Math.round(melhor.score*100)+"%":"—")+"</span></td><td>"+
        esc(melhor?melhor.evidence:"")+"</td></tr>");});});
  document.getElementById("loc-tab").innerHTML=linhas.length?
    '<table><thead><tr><th>IP</th><th>Unit</th><th>Resultado</th><th>Equipamento</th><th>Tipo</th>'+
    '<th>Confiança</th><th>Evidência</th></tr></thead><tbody>'+linhas.join("")+"</tbody></table>"
    :(d.finished_at&&!rodando?'<p class="vazio">Nenhum Unit ID respondeu nos endereços informados.</p>':"");

  clearTimeout(locTimer);
  if(rodando) locTimer=setTimeout(async function(){
    pintarLoc(await window.pywebview.api.localizar_estado());},2000);
}
document.getElementById("loc-ir").onclick=async function(){
  document.getElementById("loc-ir").disabled=true;
  pintarLoc(await window.pywebview.api.localizar(
    document.getElementById("loc-alvos").value,
    document.getElementById("loc-units").value));
};
document.getElementById("loc-parar").onclick=async function(){
  pintarLoc(await window.pywebview.api.localizar_parar());
};

async function ciclo(){
  try{ pintar(await window.pywebview.api.dados()); }
  catch(e){
    document.getElementById("svc").textContent="ERRO";
    document.getElementById("rodape").textContent="Falha ao ler o gateway: "+(e&&e.message?e.message:e);
  }
  setTimeout(ciclo,5000);
}
// A ponte pode ficar pronta ANTES deste script rodar, e ai o evento
// pywebviewready ja passou. Por isso: ouve o evento e tambem verifica sozinho.
function iniciar(){
  if(window.pywebview&&window.pywebview.api&&window.pywebview.api.dados){ ciclo(); return; }
  setTimeout(iniciar,150);
}
window.addEventListener("pywebviewready", iniciar);
iniciar();
</script></body></html>
"""


def main() -> int:
    import tempfile

    import webview

    ponte = Ponte()

    # Carrega por arquivo, nao por html=. O NavigateToString do WebView2 falha
    # calado em algumas maquinas e a janela fica branca, sem erro nenhum.
    pasta = Path(tempfile.gettempdir()) / "gridco-console"
    pasta.mkdir(parents=True, exist_ok=True)
    pagina = pasta / "console.html"
    pagina.write_text(PAGINA, encoding="utf-8")

    webview.create_window(
        "Gateway Grid Co", url=pagina.as_uri(), js_api=ponte,
        width=1240, height=800, min_size=(900, 560),
        background_color="#c3c3c3",
    )
    # GRIDCO_CONSOLE_DEBUG=1 abre o devtools: e' como se ve um erro de script
    # numa maquina onde a tela nao monta.
    depurar = os.environ.get("GRIDCO_CONSOLE_DEBUG", "").strip() in {"1", "true", "sim"}
    webview.start(gui="edgechromium", debug=depurar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
