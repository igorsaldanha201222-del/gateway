# Ida a campo — roteiro curto

Primeira instalação de verdade. Nada aqui foi testado numa usina ainda, então o
objetivo é descobrir o que quebra, não sair com tudo publicando.

## Levar

Um pendrive com:

```
gridco-gateway.exe        ← dist\
gridco-console.exe        ← dist\
instalar.ps1              ← deploy\windows\
desinstalar.ps1           ← deploy\windows\
gateway.json              ← config\  (a base genérica)
```

Sem internet no PC da usina não tem problema: nada disso baixa nada.

## Instalar

PowerShell **como administrador**, na pasta do pendrive:

```powershell
.\instalar.ps1 -Exe .\gridco-gateway.exe -Config .\gateway.json
```

Não passe `-IniciarAquisicao`. A aquisição se liga depois, pelo console, com os
equipamentos já cadastrados.

Conferir:

```powershell
Get-Service GridCoGateway      # Running / Automatic
```

## Ordem do teste

1. **Localizador primeiro.** Abra o console, aba Localizador, informe a faixa da
   usina. Isso não escreve nada e já diz o que existe na rede.
2. **Cadastre um equipamento só.** O que o localizador identificou com maior
   confiança. Índice no tópico começa em 1.
3. **Ligue a aquisição** e veja se o device sai de "sem comunicação".
4. Se funcionar, cadastre o resto.

## O que anotar

Pontos onde eu não sei o que vai acontecer:

- [ ] O antivírus da usina deixou os `.exe` rodarem?
- [ ] O localizador achou os equipamentos? Quais ficaram "não identificado"?
- [ ] A evidência que ele mostrou bate com o equipamento real?
- [ ] O botão "Cadastrar e aplicar" gravou? A revisão subiu de 1 para 2?
- [ ] O serviço reiniciou sozinho depois do cadastro?
- [ ] Depois de ligar a aquisição, a qualidade do device virou 192?
- [ ] O WebView2 existe nesse PC? (se o console não abrir, é a primeira suspeita)

## Se der errado

Log do serviço:

```powershell
Get-Content "C:\ProgramData\GridCo\Gateway\data\logs\gateway.log" -Tail 40
```

Erro na tela do console aparece no rodapé da janela.

Voltar tudo, preservando os dados:

```powershell
.\desinstalar.ps1
```

## O que já se sabe que NÃO vai funcionar

- **MQTT não conecta.** Falta credencial; ele vai tentar e falhar em laço. É
  esperado e não impede a aquisição nem o buffer.
- **Nada chega no servidor.** O app 2 não existe. A telemetria fica na fila
  SQLite local — que é justamente o que o console mostra.
