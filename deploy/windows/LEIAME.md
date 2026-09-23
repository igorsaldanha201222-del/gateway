# Gateway Grid Co no Windows

Dois executáveis. Nenhum depende de Python no PC da usina.

```
dist\gridco-gateway.exe   11,0 MB   serviço, headless, sobe no boot
dist\gridco-console.exe   16,9 MB   janela do operador, clica e abre
```

## Scripts

```
build.ps1        gera os dois .exe                (máquina de desenvolvimento)
instalar.ps1     instala o serviço                (PC da usina, como admin)
atualizar.ps1    troca o binário por um release   (PC da usina, como admin)
desinstalar.ps1  remove, preservando os dados     (PC da usina, como admin)
```

`build.ps1` só empacota o serviço. O console se gera com:

```powershell
py -3 -m PyInstaller deploy\windows\gridco-console.spec --noconfirm --clean --distpath dist
```

## Onde cada coisa mora

Essa separação é o que permite atualizar o binário sem atropelar a usina.

| Caminho | O que é | Atualização mexe? |
|---|---|---|
| repositório | código-fonte | — |
| `C:\Program Files\Grid Co\Gateway` | só o binário | **sim, troca** |
| `C:\ProgramData\GridCo\Gateway\config` | config **da usina** | não |
| `C:\ProgramData\GridCo\Gateway\data` | banco, fila, logs | não |

`instalar.ps1` nunca sobrescreve um `gateway.json` que já exista.

## O serviço

Aparece em `services.msc` como **Gateway Grid Co** (nome interno `GridCoGateway`),
inicialização **Automática**, conta **LocalSystem**.

O handshake com o Service Control Manager está em `gridco_gateway/winservice.py`.
Sem ele o SCM derrubaria o processo em 30 s por achar que travou — é por isso que
`sc.exe create` apontando para um `.exe` comum não funciona.

Recuperação: o SCM reinicia em 60 s nas três primeiras falhas, zerando a
contagem a cada 24 h.

```powershell
Get-Service GridCoGateway
Restart-Service GridCoGateway
Get-Content "C:\ProgramData\GridCo\Gateway\data\logs\gateway.log" -Wait -Tail 20
```

O log rotaciona em 5 MB, guardando 5 arquivos.

**Dois processos é o normal.** O bootloader do PyInstaller lança um filho; pai e
filho, não duplicata. Por isso os scripts encerram por **nome**, nunca por PID —
matar só o pai deixa o filho segurando o banco.

## O console

`gridco-console.exe` abre com dois cliques. Janela nativa (WebView2, que já vem
no Windows 11), sem porta e sem navegador.

Lê o banco do serviço em `mode=ro`. Como o banco está em WAL, ler não bloqueia a
aquisição.

Abas:

- **Equipamentos** — estado e qualidade de cada device
- **Valores** — o payload publicado, por equipamento
- **Cadastro** — insere equipamento pelo catálogo de 58 modelos, liga/desliga a
  aquisição, remove device
- **Localizador** — varre a rede e identifica por assinatura
- **Eventos** — o log do serviço

**Pede UAC ao abrir.** Gravar em `ProgramData` e reiniciar o serviço exigem
elevação; melhor um prompt na abertura que um erro depois do operador preencher
tudo.

O catálogo de 58 modelos vai dentro do `.exe`.

### Localizador

Varre a porta 502 na faixa e compara cada Unit ID com **23 assinaturas** escritas
à mão, uma por família. Cada uma lê registradores específicos e confere se o
valor faz sentido fisicamente — não consulta o catálogo.

Abaixo de 50 % não identificado · até 80 % possível · acima disso identificado.

**Somente leitura: nada é escrito nos equipamentos.**

## Ligar a aquisição é decisão separada

- **o serviço sobe sempre** — conecta no MQTT e escuta comandos;
- **a aquisição Modbus só começa se `runtime.enabled` for `true`.**

O padrão é `false` de propósito: instalação nova não sai lendo equipamento
sozinha. Para ligar:

- no console, aba Cadastro, botão **Ligar aquisição**; ou
- `.\instalar.ps1 -IniciarAquisicao`; ou
- remotamente, publicando `{"action":"start"}` em
  `dev/write/UFV/<planta>/gateway/command`.

## Credenciais MQTT

O instalador **não pede nem grava senha**. O `mqtt.py` busca, nesta ordem:

1. variáveis de ambiente `GRIDCO_MQTT_USERNAME` e `GRIDCO_MQTT_PASSWORD`;
2. arquivos em `CREDENTIALS_DIRECTORY`.

O serviço roda como LocalSystem, então as variáveis precisam ser **de máquina**:

```powershell
[Environment]::SetEnvironmentVariable("GRIDCO_MQTT_USERNAME","<usuario>","Machine")
```

Depois `Restart-Service GridCoGateway`. Variável de máquina é legível por
qualquer administrador local — considere isso ao decidir quem tem acesso ao PC.

## Atualizar pelo GitHub

`.github\workflows\build-windows.yml` compila ao empurrar uma tag `v*`, roda os
testes, valida a config, confere os binários e anexa ao release os dois `.exe`
com seus `.sha256`.

### À mão

```powershell
.\atualizar.ps1 -Repo "<org>/<repo>"
.\atualizar.ps1 -Repo "<org>/<repo>" -Rollback   # volta o binário anterior
.\atualizar.ps1 -Repo "<org>/<repo>" -Canal teste  # aceita pré-release
```

### Automática — é o modo para uma frota

Ligada na instalação, informando o repositório:

```powershell
.\instalar.ps1 -Repo "<org>/<repo>"
```

Isso registra a tarefa **GridCo Gateway - atualizacao**: diária às 03:00, como
SYSTEM. A partir daí, `git push` de uma tag aqui atualiza a frota sozinha.

Quatro proteções, porque com 200 usinas um binário ruim publicado derrubaria
todas de uma vez:

| | |
|---|---|
| **SHA-256** | conferido antes de trocar; não bate, não troca |
| **Desfaz sozinho** | se o serviço não ficar de pé por 15 s seguidos em até 90 s, o binário anterior volta e o serviço sobe de novo |
| **Versão bloqueada** | a que falhou é anotada em `data\versao-recusada.txt` e não é tentada de novo, para não virar laço de quebrar-e-desfazer |
| **Dispersão** | cada PC sorteia até 2 h de atraso, para 200 não baterem no GitHub no mesmo minuto |

O canal padrão é **estável**: `/releases/latest` já exclui rascunho e
pré-release. Marque um release como *pre-release* no GitHub para ele chegar só
a quem instalou com `-Canal teste` — é como se testa numa usina antes da frota.

Sem rede, o script registra aviso e sai com código 0: tenta na próxima janela.

Log em `C:\ProgramData\GridCo\Gateway\data\logs\atualizacao.log`.

Repositório privado precisa de um token de leitura na variável de **máquina**
`GRIDCO_GITHUB_TOKEN` — a tarefa roda como SYSTEM e não enxerga variável de
usuário.

### O que a atualização NÃO faz

Troca só o binário. **Configuração da usina, fila e histórico ficam intactos.**

Isso inclui os templates já cadastrados: quando um equipamento é cadastrado, os
blocos e variáveis são **copiados** do catálogo para o `gateway.json` daquela
usina. Binário novo traz catálogo novo, mas não mexe em quem já está lá. Para
propagar a correção, use a aba **Templates** do console, que compara os dois
lados e mostra o que muda antes de aplicar.

## Detalhes do empacotamento que não são óbvios

- **`tzdata` vai dentro.** O Windows não tem base de fusos do sistema e o
  `decoder.py` usa `zoneinfo("America/Sao_Paulo")` para fechar a energia diária.
  Sem ele a data local sai errada — e erro de data não estoura, entrega número
  plausível.
- **UPX desligado.** Binário comprimido é barrado por antivírus com frequência, e
  um gateway que não sobe no boot custa mais que 4 MB.
- **Serviço em modo console, console em modo janela.** O serviço mantém
  `console=True` porque o SCM o roda sem sessão interativa de qualquer forma, e
  o modo *windowed* reproduz a armadilha do `pythonw`. O console é
  `console=False`, porque tem janela própria.
- **`app_root()` respeita `sys.frozen`.** Empacotado, `__file__` aponta para o
  diretório temporário de extração, que muda a cada boot.
- **WebView2** já vem no Windows 11. Em Windows 10 antigo pode faltar o runtime.
