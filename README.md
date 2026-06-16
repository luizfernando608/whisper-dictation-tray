# Whisper Dictation Tray

Aplicativo para Windows que transforma fala em texto usando Groq Whisper por padrao e insere automaticamente a transcricao no campo ativo.

Ele foi feito para substituir o fluxo do `Win + H` por um ditado acionado por atalho global. Se a API da Groq ou a internet falhar, o app usa `faster-whisper` local como fallback.

## Recursos

- Atalho global configuravel.
- Icone na bandeja do sistema.
- Gravacao do microfone local.
- Transcricao primaria com Groq `whisper-large-v3`.
- Fallback local com `faster-whisper`.
- Insercao automatica do texto por colagem ou digitacao.
- Copia automatica da transcricao final para a area de transferencia.
- Configuracao simples por `config.json`.

## Como Usar

1. Instale Python 3.11.
2. Instale as dependencias:

```powershell
cd C:\Users\MEUCOMPUTADOR\Documents\whisper-dictation-tray
.\scripts\install.ps1
```

3. Crie um `.env` na raiz do projeto com sua chave da Groq:

```text
GROQ_API_KEY=sua_chave_aqui
```

4. Inicie o app:

```powershell
.\scripts\run.ps1
```

O icone aparece na bandeja. O atalho padrao e `Ctrl + Shift + H`.

- Pressione uma vez para comecar a gravar.
- Pressione de novo para parar.
- O app transcreve, insere o texto na janela que estiver com foco e deixa a transcricao no clipboard.

## Configuracao

Edite `config.json`.

Campos principais:

- `hotkey`: atalho global.
- `language`: idioma fixo. Para portugues, use `pt`.
- `transcription_provider`: `groq` ou `local`. Padrao: `groq`.
- `groq_model`: modelo da Groq. Padrao: `whisper-large-v3`.
- `groq_api_key_env`: nome da variavel de ambiente com a chave. Padrao: `GROQ_API_KEY`.
- `model_size`: modelo Whisper local usado no fallback. Padrao: `small`.
- `compute_type`: em CPU local, use `int8`.
- `input_device`: nome parcial do microfone ou `null` para o padrao do sistema.
- `insert_mode`: `paste` ou `type`.
- `cpu_threads`: `0` para deixar o runtime decidir.

Depois de editar o arquivo, use `Recarregar configuracao` no menu da bandeja ou reinicie o app.

## Provedor de Transcricao

O app usa Groq primeiro:

```json
{
  "transcription_provider": "groq",
  "groq_model": "whisper-large-v3",
  "groq_api_key_env": "GROQ_API_KEY",
  "language": "pt"
}
```

Se a Groq falhar, ele cai automaticamente para `faster-whisper` em CPU:

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

Na primeira execucao de um modelo local ainda nao baixado, o `faster-whisper` pode fazer download dos pesos do modelo. O fallback continua sendo executado localmente.

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
- `src/whisper_dictation/transcription.py`: integracao com Groq e fallback `faster-whisper`.
- `src/whisper_dictation/text_inserter.py`: colagem ou digitacao no app ativo.

## Observacoes

- A chave da Groq deve ficar no `.env`, que nao deve ser versionado.
- A transcricao primaria envia audio para a API da Groq; o fallback local nao envia audio para API externa.
- `Win + H` pertence ao Windows; este app usa outro atalho.
- Apps executados como administrador podem bloquear insercao de texto se este app nao estiver no mesmo nivel de permissao.
- `insert_mode = paste` e mais rapido, mas usa temporariamente a area de transferencia.
