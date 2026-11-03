# Conclusão e verificação pós-publicação — 30/10/2026

## Código e pacote

O PR #1 publicou a funcionalidade na branch `codex/meeting-recordings`, não na
`main`. O PR #2 reúne a ancestralidade da tag `v1.6.0` e o último commit de
`feat/agent-access` (`79889f3`), mais os ajustes e registros deste fechamento.
O código do aplicativo continua idêntico ao pacote 1.6.0 publicado; as alterações
adicionais são do laboratório e da documentação. Não republicar binários diferentes
sob a mesma tag nem publicar o instalador 1.6.1 usado exclusivamente pelo laboratório.

## Verificações novas

- `scripts/test.ps1`: 327 testes, nenhum erro, 2 testes opcionais ignorados;
  compilação Python aprovada. Nova execução após os ajustes do laboratório.
- Scripts PowerShell alterados analisados pelo parser nativo, sem erros.
- Atualizador extraído da instalação pública 1.5.2 detectou a 1.6.0 no GitHub real.
- O próprio PACE instalado baixou o instalador público completo: 409.089.206 bytes,
  SHA-256 `f85d648a9ced1356b7654e3d5a6c0810aa53204d5808220f3cb4cb9307d7e2f6`.
  Nenhum instalador foi semeado no cache.
- A preparação do primeiro auxiliar foi interrompida durante a investigação da
  demora, antes de executar um instalador. O pedido seguinte reutilizou o download
  público validado. O auxiliar real da 1.5.2 concluiu a instalação, registrou
  `status=updated` e recebeu a confirmação do PACE 1.6.0 reaberto.
- Registro Windows e versão da instância confirmados em 1.6.0; hashes de
  `Pace.exe` e `pace-cli.exe` iguais aos do build publicado.
- Após atualizar, 290 arquivos originais verificados por hash e 3 registros da
  biblioteca preservados; SQLite íntegro; chave configurada continua disponível.
- Conexão real ao Claude Code, Claude Desktop e Codex: os três conectados,
  backups privados das três configurações e comparação sem alterações nos
  conteúdos alheios ao PACE. Cursor não instalado.
- MCP congelado: inicialização com BOM UTF-8, cinco ferramentas, `ping`,
  `pace_status`, `transcribe_audio` e `get_transcription` aprovados.
- Áudio OGG sintético de 37,8 s e vídeo MP4 processados pelo aplicativo instalado
  com provedor local, sem enviar áudio para serviços de transcrição. Resultados
  com texto, identificação de falante e timestamps; TXT/JSON/SRT conferidos.
  Mesmo arquivo/opções reaproveita o resultado. O worker terminou mesmo após o
  cliente MCP fechar. Isso verifica a integração, não a precisão em outros áudios.
- Trabalhos sintéticos removidos ao terminar, mantendo os resultados exportados
  como evidência privada; a biblioteca voltou aos três registros originais,
  idênticos e íntegros. Preparação de auxiliar abandonada nesta investigação
  removida; backup da atualização concluída mantido.

## Caminhos locais e virtualização MSIX

A primeira transcrição real expôs redirecionamento da biblioteca e do cache de
modelos pelo processo empacotado do Codex: a raiz lógica em AppData era diferente
do caminho físico retornado para seus filhos. As proteções de diretórios do PACE
recusaram o novo trabalho e a validação do cache Nemotron.

A instalação de desenvolvimento passou a usar `%USERPROFILE%\Documents\PACEData`,
com `recordings` e `models` explícitos. A cópia verificou por hash os 288 arquivos
originais da biblioteca e os 171 arquivos do cache de modelos (294.849.201 bytes).
Os diretórios anteriores foram mantidos. Somente `meeting_storage_dir` e
`diarization_model_dir` mudaram nas configurações; os três registros originais
continuaram idênticos. Os testes OGG/MP4 e MCP passaram após essa normalização.
Essa correção é da configuração deste computador, sem alteração dos binários.

## Windows Sandbox: resultado e limite

O laboratório público foi executado novamente mantendo as proteções padrão.
A API do GitHub respondeu, mas o Controle de Aplicativos bloqueou o instalador
1.5.2 sem assinatura antes de iniciar o teste de atualização. O log Code Integrity
registrou os eventos 3077/3033. Portanto, esse ensaio **não passou** e não comprova
instalação/atualização em um Windows limpo com essa política ativa.

O laboratório agora preserva as políticas, guarda log de instalação e diagnóstico
de DNS, limita a espera e interrompe verificações dependentes após uma falha.
O runner valida a pasta temporária antes da limpeza recursiva. Nenhuma proteção
do Windows foi desativada nesta execução.

O resultado anterior de 16/16 com GitHub simulado permanece uma evidência distinta.
A hipótese antiga sobre certificados TLS continua sem confirmação. O salto pelo
GitHub real foi confirmado neste computador, com as condições descritas acima.
Os instaladores e executáveis continuam sem assinatura Authenticode; suportar
políticas que exigem assinatura depende de uma etapa posterior de assinatura,
com identidade/certificado do responsável. Não declarar compatibilidade universal
com Windows limpo, Windows 10 ou CPU sem AVX2 a partir destes testes.

## Evidências

Relatórios privados em `Documents/pace-finalization-20261030`: atualização,
preservação da biblioteca, configurações, MCP, exportações dos áudios sintéticos
e saída dos testes. Evidência da Sandbox em
`Output/lab/final-public-20261030` (ignorada pelo Git). Backups de configurações
e gravações reais não são anexados ao repositório nem ao release.
