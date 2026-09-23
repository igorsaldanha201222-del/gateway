# Gateway Grid Co — estado da construção

> Handoff. Este arquivo é o ponto de partida de quem continuar o projeto.
> Última atualização: 22/09/2026

## O que é

Dois aplicativos, para **200 usinas**.

```
200 PCs ──MQTT/TLS──> app.gridco.com.br:8883 ──> raw_* ──> dbt ──> Grafana
 (app 1)                     (app 2)             (já existe)
```

**App 1 — borda.** Roda num PC em cada usina. Lê Modbus TCP pelos templates,
monta o payload, guarda em buffer SQLite e publica MQTT. Sem interface.

**App 2 — servidor.** Ainda **não começou**. Recebe em `app.gridco.com.br:8883`
e grava nas tabelas `raw_inverter`, `raw_relay`, `raw_tracker`... no formato
`valor@YYYYMMDDHHmmss`. A partir daí o pipeline dbt existente processa sem
mudança nenhuma.

## Origem

Derivado de `00_VERSAO_IOT2050_V3` (gateway AIOTI para Siemens IOT2050).
O original continua intacto; nada aqui importa dele em tempo de execução.

## App 1 — o que já está feito

### Núcleo copiado e enxugado

```
FICARAM (10)   config  catalog  decoder  payload_policy  modbus
               mqtt  storage  engine  __main__  __init__

SAÍRAM (9+)    webserver  auth  device_finder  network  network_helper
               connectivity_helper  acopiara_value_policy
               thermal_relay_policy  plant_topic_policy  +  web/
```

O corte foi limpo porque `engine.py` nunca importou a interface — só
`config`, `decoder`, `modbus`, `mqtt`, `storage`. Todo o acoplamento com a tela
estava no `__main__.py`.

`payload_policy` ficou porque o `decoder` depende dele (é genérico, não
específico de planta).

### Marca removida

60 referências a `aioti` / `iot2050` trocadas, **zero restantes** (conferido).
Inclui identificadores acoplados que tinham de mudar nos dois lados:

| Antes | Depois |
|---|---|
| `aioti.iot2050.template-catalog` | `gridco.gateway.template-catalog` |
| `AIOTI_V7_COMPAT` | `GRIDCO_V1` |
| `AIOTI_MQTT_USERNAME` / `_PASSWORD` | `GRIDCO_MQTT_USERNAME` / `_PASSWORD` |
| `AIOTI_CONFIG` / `AIOTI_DATA_DIR` | `GRIDCO_CONFIG` / `GRIDCO_DATA_DIR` |

Os imports internos são relativos, então renomear a pasta do pacote não quebrou
nada.

### `__main__.py` headless

Sem servidor web. Sobe o engine e dorme até SIGINT/SIGTERM, com
`stopping.wait(1.0)` em laço para continuar interrompível no Windows.

### Config base

`config/gateway.json` aponta para `app.gridco.com.br:8883` com TLS ligado.
`runtime.enabled` continua `false` de propósito — instalação nova não começa a
adquirir sozinha.

### Estado verificado

```
30 testes   →  OK
--validate  →  0 erro(s), 0 aviso(s)
```

```powershell
$env:PYTHONPATH = "D:\Fluxos usinas\gridco-gateway"
py -3 -m unittest discover -s tests
py -3 -m gridco_gateway --config config\gateway.json --validate
```

Usar `py -3`; o `python` no PATH é o stub da Microsoft Store e falha.

## Template do P3U30 — feito

Entrada `catalog-schneider-p3u30` adicionada ao `template_catalog.json`
(57 → 58 entradas, `catalog_revision` 4, backup em `.bak`).

Fonte: mapa conferido da UFV Acopiara, 22/09/2026, contra
`mapa_modbus_p3u30.xlsx` (797 linhas).

```
6 requests · 27 campos · 27 publicadas   (bate com o contrato relay de 27 chaves)

bloco 01  2000  50 reg   medidas, disjuntor, local/remoto
bloco 02  3000  56 reg   lido, não publicado
bloco 03  3401  14 reg   lido, não publicado
bloco 04  6000  38 reg   bits de START e TRIP
bloco 05  6249  12 reg   tensões RMS, não publicado
bloco 06  2100   5 reg   buffer do último evento  ← event_code
```

Regra de endereçamento confirmada: **`PDU = índice 4x − 400001`**.

Decisões tomadas ao montar:

- **Unidade das potências corrigida** para `kW`/`kvar`/`kVA`. O template REV16
  publicava `W`/`var`/`VA` com ganho 1 — rótulo mil vezes menor que o valor.
- **`status_relay` marcado `a_confirmar_em_campo`.** A planilha diz
  Open=0/Close=1, mas uma nota de campo de outra usina viu `2041` lendo 1 com o
  disjuntor aberto.
- **`flag_51GS` fixo em 0** com nota explícita: o P3U30 não tem falta à terra
  sensível. Zero ali é ausência de função, não ausência de falta.
- **`flag_50`/`flag_51_1` e `flag_50N`/`flag_51N` compartilham bit** — o relé
  junta 50 e 51 por estágio. Não é erro de mapa.

## Pendências do app 1

Em ordem sugerida:

1. **`{index}` no pattern de tópico.** O contrato real é
   `dev/read/UFV/Acopiara/inverter/1` — índice numérico, planta com
   maiúscula. O pattern atual gera `.../acopiara/inverter/inv-01`, errado nas
   duas pontas. Era por isso que existia `plant_topic_policy.py` com 215 linhas
   de código por planta. Placeholders a adicionar em
   `engine.py::_telemetry_topic` (linhas 295-303): `{index}` vindo de
   `device.metadata.mqtt_topic_index`, `{plant}` de `plant.metadata.topic_slug`.
   **Índice explícito, não derivado da ordem** — inserir um inversor no meio
   renumeraria os tópicos dos seguintes e quebraria o histórico.

2. **Gerador de config por planta.** Hoje o único caminho é
   `migrate_v2_config.py`, que migra de uma config V2 do CODESYS — usina nova
   não tem de onde migrar, e sobra montar 700 KB de JSON na mão. O
   `catalog.py::onboard()` (linha 174) já expande um template do catálogo em
   device + fields + requests + tópico. Falta o CLI que lê um arquivo pequeno
   (planta, canais, devices com `template` e `unit_id`) e chama `onboard()` por
   device.

3. **`client_id` único por planta.** Hoje é fixo no JSON. Com 200 iguais o
   broker derruba um ao outro.

4. **Ethernet-only de verdade.** `modbus.py` ainda tem o caminho RTU e o
   `config.py` ainda valida `/dev/serial/by-id`. Funciona, mas é código morto.

5. ~~**Instalador desatendido.**~~ ✅ **Feito em 22/09/2026 — é Windows.**
   `deploy/windows/` tem build, instalação, atualização e desinstalação.
   `dist/gridco-gateway.exe` são 10,5 MB e rodam sem Python no PC da usina;
   conferido: processo vivo com 7,7 MB de RSS, SQLite em WAL e log rotativo.
   Sobe no boot por **tarefa agendada como SYSTEM**, não por serviço — o
   executável não fala o protocolo do Service Control Manager, e o SCM derruba
   um `.exe` comum que não responde ao handshake em 30 s.
   Ver `deploy/windows/LEIAME.md`.

   Três coisas ficaram decididas junto:
   - **Ligar o processo ≠ ligar a aquisição.** `runtime.enabled` continua
     `false`; o instalador só mexe nisso com `-IniciarAquisicao` explícito.
   - **`tzdata` vai dentro do `.exe`.** O Windows não tem base de fusos do
     sistema e o `decoder.py` usa `zoneinfo("America/Sao_Paulo")` para fechar a
     energia diária. Sem isso a data local sai errada — e erro de data não
     estoura, entrega número plausível.
   - **Config e dados ficam em `C:\ProgramData\GridCo\Gateway`**, fora do
     diretório do programa. Atualizar troca só o binário.

   Atualização pelo GitHub montada mas **não ligada**: `atualizar.ps1` consome
   release com `.exe` + `.sha256`, e `.github/workflows/build-windows.yml`
   produz os dois ao empurrar uma tag `v*`. Falta o repositório existir — ver
   *O que falta para o GitHub funcionar* no fim deste arquivo.

6. **Deduplicar o catálogo.** São 58 entradas com duplicação por usina:
   3× o mesmo P3U30, 4× a mesma Longmax StringBox, 8 templates TCU 01..08 que
   deviam ser 1 template + 8 devices, e 3 entradas com nome idêntico
   (Electron EP4 TH104) com mapas diferentes.

   Conferido contra os mapas de campo validados (skill `mapa-de-rede-usinas`):
   **zero divergência de endereço** em Pextron URP6000, Sungrow SG, Growatt
   TL3-X, Growatt 33 KTL3-S e Solis 5G. O endereçamento está são; o problema é
   de arrumação.

   Mas **não é merge cego**. Três causas distintas:

   | Causa | Exemplo | Ação |
   |---|---|---|
   | Nomenclatura | `current_ch_1` vs `current_ch_01` | normalizar e fundir |
   | Escala de instalação | P3U30 `voltage_ab` ganho 1 (Acopiara) vs 0,1 (Pedra Branca) | 1 template + **override no device** |
   | Mapa errado vs certo | EP4 TH104, offsets todos zero numa versão | validar em campo, não arbitrar |

   As 3 entradas antigas do P3U30 **foram mantidas de propósito** — apagar
   perde o ganho 0,1 de Pedra Branca, que é legítimo. Remover só depois que o
   override por device existir.

## App 2 — ainda não começou

Especificação acordada:

- Recebe MQTT/TLS em `app.gridco.com.br:8883`
- Grava em `raw_inverter`, `raw_relay`, etc., formato Grid Co
  `valor@YYYYMMDDHHmmss`
- `json_data` é `jsonb`: chave nova entra sem migração
- Credencial por dispositivo para os 200 gateways

Em aberto: se o 8883 é um broker já existente (Mosquitto/EMQX) com o app 2
sendo só o consumidor, ou se o app 2 inclui subir o broker.

## O que falta para o GitHub funcionar

O `.git` na raiz de `D:\Fluxos usinas` existe mas está **vazio**: só a pasta
`info`, sem `config`, sem `HEAD`, sem objetos, sem commit nenhum. E o `git` não
está instalado nesta máquina — não está no PATH nem nos caminhos padrão.

Então hoje não há de onde puxar nem para onde empurrar. Para fechar o ciclo:

1. instalar o Git;
2. `git init` de verdade na raiz e primeiro commit — o `.gitignore` já está
   escrito e tira `dist/`, `build/`, `data/`, `*.db` e `*.rar` do caminho;
3. criar o repositório no GitHub e apontar o remoto;
4. empurrar uma tag `v1.0.0` — o workflow compila, testa, valida e publica o
   release com `.exe` e `.sha256`;
5. no PC da usina: `.\atualizar.ps1 -Repo "<org>/<repo>"`.

Decidir antes de subir: **o repositório é público ou privado?** Se for privado,
cada PC precisa de um token de leitura em `GRIDCO_GITHUB_TOKEN`, e aí vale
pensar em quem gerencia esse token nas 200 usinas. Criar repositório e empurrar
código para fora é decisão sua — não fiz nada disso.

## Fora do escopo deste projeto

**Resolvido em 22/09/2026.** A skill `mapa-de-rede-usinas` dizia que o Schneider
P3 tinha `Leitura de campo: NENHUMA`. Atualizado na fonte (`docs/modbus/`) e
regerado — a cópia da skill não foi tocada à mão.

Ao conferir, apareceram duas coisas que o mapa da Acopiara desmente:

- **O deslocamento não é `−1` uniforme.** É `−1` em geral, mas as bandeirolas
  `60xx` ficam **`−2`** a partir do Earth-Fault, porque o grupo vazio
  *Current Stages 3* da planilha da Schneider não ocupa registrador. O template
  `catalog-schneider-p3u30` já usa os offsets de campo — confere bit a bit.
- **Aparecida do Taboado é este mesmo mapa.** O arquivo do P3 tinha um alerta
  dizendo que o `DS_RELE_MEDIA1` era de outro relé; o alerta era falso, criado
  pela comparação contra a coluna deslocada. Contra os offsets reais fecham os
  nove bits e as dez medições. Isso reforça as decisões tomadas ao montar o
  template: `flag_50`/`flag_51_1` e `flag_50N`/`flag_51N` compartilham bit
  mesmo, e `flag_51GS` fixo em 0 está certo.

Uma divergência nova ficou em aberto, registrada em `docs/modbus/`: o rótulo
`51_2`. O template o coloca em `6026 · 3` (`I>>` Trip) e Aparecida em
`6037 · 1`, que é direcional de potência — ANSI **32**, não 51. As duas
leituras são defensáveis; não mexi no template.

Também não está no repositório o `mapa_modbus_p3u30.xlsx` de 797 linhas que o
template cita como fonte — só o `Rele_media_P3U30_organizado.xlsx`, de 200
pontos. Campos como `st_local_remote` (2047) vêm de lá e não dá para conferir.
