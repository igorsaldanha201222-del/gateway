# O que o gateway manda para o servidor

Escrito a partir do código, não de memória. As referências apontam o arquivo e
a linha onde cada coisa acontece.

O servidor precisa de duas peças: um **broker MQTT** e um **consumidor** que
assina os tópicos e grava no banco. Hoje a porta 8883 está aberta mas não há
serviço escutando, então nada disso existe ainda.

---

## 1. O broker

Qualquer broker MQTT 3.1.1 serve — Mosquitto, EMQX, HiveMQ, VerneMQ. O cliente
é escrito à mão em `gridco_gateway/mqtt.py`, fala 3.1.1 (nível de protocolo 4),
QoS 0 e 1, usuário e senha, Will, TLS.

O que ele exige do broker:

| Item | Valor |
|---|---|
| Porta | 8883 |
| TLS | mínimo 1.2 |
| Certificado do servidor | assinado pela CA da Grid Co (`config/ca-gridco.crt`), com `app.gridco.com.br` no subjectAltName |
| Autenticação | certificado de cliente (mTLS) |
| QoS | 1 nos dois sentidos |

O gateway confia **só** na nossa CA. Um certificado Let's Encrypt no broker
seria recusado — teria de trocar `mqtt.tls.ca_file` para `""` em cada usina,
para usar o depósito do Windows.

`deploy/broker/` tem a configuração pronta: `mosquitto.conf` com os dois
listeners, `acl.txt`, e os scripts que geram os certificados.

### Autenticação

`require_certificate true` e `use_identity_as_username true`. O CN do
certificado do cliente vira o nome de usuário, e a ACL trabalha em cima dele.
Não há lista de senhas para manter.

Hoje a frota usa **um certificado só**, CN `gateway`. A ACL correspondente:

```
user gateway
topic write dev/read/UFV/#
topic read  dev/write/UFV/#
```

O consumidor do servidor usa outro certificado, CN `servidor`, com as
permissões invertidas. Nenhum usuário faz os dois — é o que impede uma usina
comprometida de publicar comando para as outras.

> **Cuidado ao conferir ACL:** o Mosquitto aceita assinatura com curinga em
> tópico proibido e devolve SUBACK positivo. Ele filtra na **entrega**, não no
> SUBSCRIBE. "Consegui assinar" não é furo. O que prova é publicar de um lado e
> ver se chega do outro.

---

## 2. Tópicos

Padrão em `gridco_gateway/engine.py:295`:

```
dev/read/UFV/{usina}/{tipo}/{indice}
```

- `{usina}` — o identificador definido no console, aba Usina. Minúsculas,
  números e `_`.
- `{tipo}` — `inverter`, `relay`, `tracker`, `stringbox`, `weather_station`…
- `{indice}` — começa em 1.

Exemplos reais:

```
dev/read/UFV/acopiara/inverter/1
dev/read/UFV/acopiara/inverter/2
dev/read/UFV/acopiara/weather_station/1
dev/read/UFV/acopiara/gateway/status
```

Os quatro tópicos fixos de cada usina:

| Tópico | Direção | Retido |
|---|---|---|
| `dev/read/UFV/{usina}/gateway/status` | gateway → servidor | sim |
| `dev/write/UFV/{usina}/+/+` | servidor → gateway | não |
| `dev/write/UFV/{usina}/feedback` | gateway → servidor | não |
| `dev/write/UFV/{usina}/gateway/configuration/v3/set` | servidor → gateway | não |

O consumidor assina `dev/read/UFV/#` e resolve usina, tipo e índice pelo
próprio tópico.

---

## 3. Telemetria — o formato

Um JSON por equipamento, a cada ciclo de publicação (padrão **60 s**), QoS 1,
sem retain. Montado em `gridco_gateway/decoder.py:348`.

```json
{
  "active_power": 201.9,
  "dc_voltage": 612.4,
  "daily_energy": 1843.2,
  "device_type": "inverter",
  "timestamp": "2026-09-23T10:31:00-03:00",
  "communication_fault": 192
}
```

Três campos aparecem **sempre**, em todo payload:

- **`device_type`** — o tipo, ou o rótulo do equipamento quando o template
  define um (`"Inversor1"`, `"Tcu1"`). O tipo técnico está no tópico de
  qualquer jeito.
- **`timestamp`** — ISO 8601 com fuso, hora local da usina
  (`America/Sao_Paulo`), segundos inteiros.
- **`communication_fault`** — qualidade OPC: **192** = bom, **28** = ruim.

O resto das chaves vem do template do equipamento e **varia por modelo**. O
consumidor não deve assumir chave nenhuma além das três acima.

### Equipamento mudo

Quando o gateway não conseguiu ler nada, ele publica assim mesmo
(`gridco_gateway/engine.py:217`):

```json
{"timestamp": "2026-09-23T10:31:00-03:00", "communication_fault": 28}
```

Isso é de propósito. Silêncio no barramento seria indistinguível de gateway
desligado; a mensagem com qualidade 28 diz "estou vivo, o equipamento é que
não responde". O servidor tem de tratar esse caso — gravar a falha, não
descartar a mensagem.

### Buffer

Se o broker cair, o gateway **não perde dado**: ele enfileira em SQLite local
(até 100.000 mensagens, 30 dias) e reenvia quando voltar, em lotes de 25 a cada
250 ms.

Consequência para o servidor: **as mensagens podem chegar fora de ordem e
muito atrasadas**, com `timestamp` de horas atrás. O banco tem de gravar pelo
`timestamp` do payload, nunca pela hora de chegada, e aguentar reinserção do
mesmo ponto sem duplicar. Uma chave única por `(usina, tipo, indice, timestamp)`
resolve.

---

## 4. Status do gateway

A cada 60 s, em `dev/read/UFV/{usina}/gateway/status`, QoS 1, **retido**
(`gridco_gateway/engine.py:469`):

```json
{
  "version": 3,
  "service": "online",
  "acquisition": "running",
  "uptime_seconds": 84213,
  "configuration_id": "gridco-v1-inicial",
  "configuration_revision": 7,
  "configuration_sha256": "…",
  "mqtt": {"connected": true, "state": "…"},
  "buffer": {"pending": 0, "failed": 0},
  "timestamp": "2026-09-23T13:31:00+00:00"
}
```

Atenção: aqui o `timestamp` é **UTC**, diferente do da telemetria, que é local.

### Quando o gateway cai

O Will (LWT) está armado na conexão (`gridco_gateway/mqtt.py:166`): se o
gateway sumir sem se despedir, o próprio broker publica no mesmo tópico, QoS 1
e retido:

```json
{"v": 3, "online": false}
```

É por aí que o servidor sabe que uma usina caiu, sem ficar contando ausência de
mensagem. Como é retido, um consumidor que conecta depois recebe o último
estado de cada usina na hora em que assina.

---

## 5. Comandos

O servidor publica em `dev/write/UFV/{usina}/…` e o gateway responde em
`dev/write/UFV/{usina}/feedback`.

Comando de aquisição:

```json
{"request_id": "abc123", "cmd": "gateway", "action": "start"}
```

`action` é `start` ou `stop`. A resposta
(`gridco_gateway/engine.py:449`):

```json
{"v": 3, "request_id": "abc123", "status": "success", "message": "Gateway start"}
```

Se o `request_id` não vier, o gateway sorteia um — mas aí o servidor não tem
como casar resposta com pedido. Mande sempre.

Limite de 8 MiB por comando.

### Configuração remota

`dev/write/UFV/{usina}/gateway/configuration/v3/set` aceita uma configuração
inteira, mas **vem desligada**: exige `runtime.allow_remote_configuration` e
o SHA-256 da configuração no próprio comando, conferido antes de aplicar
(`gridco_gateway/engine.py:341`).

---

## 6. O consumidor

Não existe ainda. O que ele precisa fazer:

1. Conectar por mTLS com o certificado CN `servidor`.
2. Assinar `dev/read/UFV/#` com QoS 1.
3. Para cada mensagem: extrair usina/tipo/índice do tópico, ler `timestamp` e
   `communication_fault` do payload, gravar o resto como pontos.
4. Gravar pelo `timestamp` do payload, com chave única que absorva reenvio.
5. Tratar `communication_fault: 28` como falha registrada, não como lixo.
6. Manter o último status de cada usina, e marcar como caída quando chegar
   `{"v":3,"online":false}`.

**Não confirme antes de gravar.** Em QoS 1 o PUBACK é o que faz o gateway
apagar a mensagem do buffer. Se o consumidor confirmar e depois falhar ao
gravar, o dado se perde e não há de onde pedir de volta.

---

## 7. O que falta hoje

- Broker no ar na 8883 de `app.gridco.com.br`.
- Certificado do broker instalado (já emitido, em `deploy/broker/`).
- Consumidor.
- **A 1883 está aberta para a internet e aceita conexão anônima.** Qualquer um
  lê toda a telemetria e publica em `dev/write/…`, que é o canal de comando.
  Isso é independente de tudo acima.
