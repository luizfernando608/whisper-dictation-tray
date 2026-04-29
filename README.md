# Local Whisper Dictation

Aplicativo local para Windows que transforma fala em texto usando Whisper na CPU e insere automaticamente a transcricao no campo ativo.

Ele foi feito para substituir o fluxo do `Win + H` por um ditado local, acionado por atalho global e sem depender de API externa.

## Recursos

- Atalho global configuravel.
- Icone na bandeja do sistema.
- Gravacao do microfone local.
- Transcricao local com `faster-whisper`.
- Insercao automatica do texto por colagem ou digitacao.
- Configuracao simples por `config.json`.

## Como Usar

1. Instale Python 3.11.
2. Instale as dependencias:

```powershell
cd C:\Users\MEUCOMPUTADOR\Documents\whisper-dictation-tray
.\scripts\install.ps1
```

3. Inicie o app:

```powershell
.\scripts\run.ps1
```

O icone aparece na bandeja. O atalho padrao e `Ctrl + Shift + H`.

- Pressione uma vez para comecar a gravar.
- Pressione de novo para parar.
- O app transcreve e insere o texto na janela que estiver com foco.

## Configuracao

Edite `config.json`.

Campos principais:

- `hotkey`: atalho global.
- `language`: idioma fixo. Para portugues, use `pt`.
- `model_size`: modelo Whisper local. Padrao: `small`.
- `compute_type`: em CPU, use `int8`.
- `input_device`: nome parcial do microfone ou `null` para o padrao do sistema.
- `insert_mode`: `paste` ou `type`.
- `cpu_threads`: `0` para deixar o runtime decidir.

Depois de editar o arquivo, use `Recarregar configuracao` no menu da bandeja ou reinicie o app.

## Modelos

O app usa `faster-whisper` em CPU:

```json
{
  "model_size": "small",
  "compute_type": "int8",
  "language": "pt"
}
```

Modelos recomendados:

- `base`: mais rapido, menor precisao.
- `small`: melhor equilibrio para ditado local em CPU.
- `medium`: mais preciso, mas mais lento.

Na primeira execucao de um modelo ainda nao baixado, o `faster-whisper` pode fazer download dos pesos do modelo. A transcricao continua sendo executada localmente.

## Selecionar Microfone

Liste os dispositivos:

```powershell
.\.venv\Scripts\python.exe .\main.py --list-devices
```

Copie um trecho do nome exibido para `input_device` em `config.json`.

## Inicializacao no Windows

Para iniciar junto com o Windows, crie um atalho para `scripts\run.ps1` na pasta de inicializacao do usuario:

```powershell
shell:startup
```

Nesta maquina, o atalho aponta para:

```text
C:\Users\MEUCOMPUTADOR\Documents\whisper-dictation-tray\scripts\run.ps1
```

## Estrutura

- `main.py`: entrada do app.
- `src/whisper_dictation/app.py`: orquestracao, tray e fluxo principal.
- `src/whisper_dictation/win32_hotkey.py`: hotkey global nativo no Windows.
- `src/whisper_dictation/audio.py`: captura do microfone.
- `src/whisper_dictation/transcription.py`: integracao com `faster-whisper`.
- `src/whisper_dictation/text_inserter.py`: colagem ou digitacao no app ativo.

## Observacoes

- Nao usa chave de API para transcrever.
- `Win + H` pertence ao Windows; este app usa outro atalho.
- Apps executados como administrador podem bloquear insercao de texto se este app nao estiver no mesmo nivel de permissao.
- `insert_mode = paste` e mais rapido, mas usa temporariamente a area de transferencia.
