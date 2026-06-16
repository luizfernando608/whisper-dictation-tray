# Whisper Dictation Tray

Aplicativo para Windows que transforma fala em texto usando Groq Whisper (nuvem) ou `faster-whisper` (local) e insere automaticamente a transcrição no campo ativo.

Ele substitui o fluxo do `Win + H` por um ditado acionado por atalho global altamente configurável.

## Pré-requisitos

1. **Python 3.10 ou superior**: Recomenda-se 3.11.
2. **FFmpeg**: Necessário para o processamento de áudio (especialmente para o modo local).
   - Instale via [winget](https://github.com/microsoft/winget-cli): `winget install ffmpeg`
   - Ou via [Scoop](https://scoop.sh/): `scoop install ffmpeg`

## Instalação

1. Clone ou baixe este repositório.
2. Abra o terminal (PowerShell) na pasta do projeto e rode o script de instalação:

```powershell
.\scripts\install.ps1
```

3. Crie um arquivo `.env` na raiz do projeto (ou renomeie o `.env.example`) com sua chave da Groq:

```text
GROQ_API_KEY=gsk_sua_chave_aqui
```

*Nota: Se você não fornecer uma chave, o app usará automaticamente o fallback local.*

## Como Usar

1. Inicie o app:

```powershell
.\scripts\run.ps1
```

O ícone aparecerá na bandeja do sistema (tray). O atalho padrão é `Ctrl + Shift + H`.

- **Pressione uma vez**: Inicia a gravação (um círculo vermelho aparece no topo da tela).
- **Pressione de novo**: Para a gravação.
- O app transcreve o áudio, insere o texto na janela ativa e copia para a área de transferência.

## Configuração

Edite o arquivo `config.json` para ajustar o comportamento:

- `hotkey`: Atalho global (ex: `Ctrl+Shift+H`, `Alt+H`).
- `language`: Idioma (use `pt` para português).
- `transcription_provider`: `groq` (rápido, precisa de internet) ou `local` (privado, roda no seu PC).
- `insert_mode`: `paste` (mais rápido, via Ctrl+V) ou `type` (simula digitação).

Após editar, clique com o botão direito no ícone da bandeja e selecione **"Recarregar configuração"**.

## Inicialização com o Windows

O script de instalação (`install.ps1`) já configura automaticamente um atalho na pasta de inicialização do Windows (`shell:startup`). Portanto, o aplicativo iniciará sozinho sempre que você ligar o computador.

Se quiser desativar isso, basta abrir a pasta de inicialização (pressione `Win + R`, digite `shell:startup` e aperte Enter) e excluir o atalho do WhisperDictation.

## Estrutura do Projeto

- `main.py`: Ponto de entrada.
- `src/whisper_dictation/`: Código fonte principal.
- `logs/`: Logs de execução para diagnóstico.
- `config.json`: Ajustes do usuário.
