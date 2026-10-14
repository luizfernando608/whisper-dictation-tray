# Validação em andamento — PACE 1.5.0

Não é aprovação de publicação. Build final e escolha do motor padrão pendentes.

## Evidências

- 89 testes passaram com `PACE_RUN_MODEL_SMOKE=1` e `PYTHONPATH=src` em 2026-10-14.
- `pip check`: sem dependências quebradas. ORT CPU no ambiente principal; DirectML
  fica em wheel isolada e só é importado pelo subprocesso específico.
- PyInstaller e Inno Setup geraram um primeiro candidato. O código recebeu ajustes
  posteriores de revisão: é necessário reconstruir o candidato final.
- Teste do executável empacotado: CPU ONNX, OpenVINO CPU, GPU Intel OpenVINO e
  DirectML passaram equivalência numérica com PyanNet de dimensões reais e pesos
  sintéticos (erro máximo 4,77e-7). Isso NÃO comprova qualidade de falantes treinados.
- Capture/storage/importação: 18 testes; enumeração WASAPI real. Stress sintético
  com duas fontes por 2 horas: aproximadamente 43 MB de pico de memória.
- Player RF64 e seek comparados amostra a amostra. Nenhum áudio pessoal foi usado.
- Interfaces Tk renderizadas com biblioteca sintética e inspecionadas: filtros,
  seleção, cancelamento/retry, nomes de falantes, preparação e exportação.

## Revisões independentes realizadas

`audit_updates` implementou T3 e revisou T1/T2. `capture_storage` implementou T1 e
revisou T3 e integração/UI/configurações. `inspect_diarizer` implementou T2. O agente
raiz implementou a integração e corrigiu os achados. Revisão independente do pacote
final ainda pendente.

Achados reproduzidos e corrigidos/revalidados:

- Quebras artificiais entre blocos na reamostragem: resampler contínuo.
- Silêncio artificial após suspensão: duração encerra no último instante seguro.
- Confirmação de atualização antes do tray pronto: confirmação após inicialização.
- Ditado iniciado em corrida com reinício: proteção dentro do lock de estado.
- Flag de update pronto mantida depois de download novo falhar: estado corrigido.
- Cache acumulando cópias do runtime: limpeza limitada com backup recuperável.
- Filtro mantendo seleção/exportação do item oculto: seleção e player limpos.
- Credenciais alteradas em Save inválido/rejeitado: coleta pura e commit no tray,
  com validação, locks e recuperação de credenciais/configuração.
- Troca de pasta inválida interrompendo a fila: preparação antes de substituir;
  novo serviço fica suspenso até confirmação do commit.
- Falha silenciosa de exclusão de credencial: estado do cofre conferido após escrita.
- Fallback local em endpoint IPC ocupado: só conexão recusada permite Save offline.
- Preparação/worker órfãos: Windows Job Object supervisiona apenas os descendentes
  criados pelo PACE; teste com pai/neto reais e processo independente passou.
- Cache de modelo inválido impossível de reparar: preparo valida/renova os arquivos.
- Backend sem prazo de inferência: processos isolados com timeout; watchdog do job
  retoma em CPU uma vez e preserva os checkpoints em falha persistente.
- Associação de palavras com custo quadrático: acumulação por segmento.
- Botões de exportação fora da janela: área de texto com altura mínima adequada.

## Verificações ainda necessárias

- Candidato final reconstruído e revisão independente correspondente.
- Identidades globais, sobreposição e qualidade com pesos treinados/vozes anotadas.
  Community-1 exige aceite/token; alternativa pública NVIDIA está em protótipo.
- Captura prolongada com hardware real, suspensão e desconexão físicas.
- Atualização real de instalação anterior: instalador gerado, mas não executado
  sobre a instalação do usuário. Os testes do helper usam processos/filesystems
  controlados e verificam integridade, espera, flags, recuperação e preservação.
- GPUs AMD/NVIDIA não disponíveis nesta máquina. Não declarar esses dispositivos
  como testados apenas porque um backend os suporta em princípio.
