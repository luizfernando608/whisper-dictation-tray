# Validação em andamento — PACE 1.5.0

Não é aprovação de publicação. O padrão local sem token foi confirmado pelo usuário.
O pacote final está sendo reconstruído após a última correção de recuperação de cache.

## Evidências

- 108 testes passaram com `PACE_RUN_MODEL_SMOKE=1` e `PYTHONPATH=src` em 2026-10-14.
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
- Nemotron treinado: CPU e Intel Vulkan produziram 25 turnos/3 IDs na AMI pública
  de 60 s. DER com collar de 0,25 s: 13,574%; a quarta voz com duas intervenções
  breves não foi separada. Não representa acurácia universal nem valida português.
- Pipeline real local completo com Whisper tiny e Nemotron: `ready`, 18 segmentos,
  25 turnos e exportações TXT/SRT, sem credenciais externas.
- CAPI com um estado por gravação e leituras de 5 s: duas horas de silêncio em
  217,64 s, pico real do processo 207.106.048 bytes (197,5 MiB), nenhum turno falso.
- AMI repetida por 30 min: 770 turnos/3 IDs, sem trocas de rótulos detectadas nos
  pontos verificados, inclusive após compactação. Erro com IDs fixos vs primeiro
  minuto entre 1,38–3,13%; ensaio artificial de continuidade, não qualidade de reunião.
  Detalhes/licenças: [relatório Nemotron](nemotron-validation.md).

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
- Instância antiga aceitando comando sem executar: versão/pasta/capacidades
  verificadas antes de encaminhar comandos e importações.
- Importação concorrente com troca de pasta: enfileiramento no serviço principal,
  sob os mesmos locks da configuração, com metadados completos no INSERT.
- Exceção de disco/SQLite encerrando o listener IPC: handler protegido, erro
  retornado e pedidos seguintes continuam funcionando; lote parcial fica explícito.
- Cache Nemotron com JSON válido mas formato inválido bloqueando reparo: estrutura
  validada antes de uso, erro reparável pelo preparo explícito.
- CLI nativa carregando áudio inteiro: substituída pela CAPI stateful por blocos;
  pico de RAM caiu de 2,17 GiB para 197,5 MiB no ensaio de duas horas.
- Falha de GPU impedindo teste das seguintes: isolamento de falha por candidato.
- Launcher Python subestimando RAM e deixando descendente após cancelamento:
  medição no processo que hospeda DLL e encerramento da árvore própria.
- Leitura de progresso impedindo replace no Windows: progresso advisory não
  interrompe inferência/preparo; resultado final é persistido separadamente.

## Verificações ainda necessárias

- Candidato final reconstruído e revisão independente correspondente.
- Qualidade treinada Community-1, português e cinco ou mais falantes substanciais.
  Community-1 exige aceite/token e permanece opcional. Nemotron treinado foi
  verificado apenas nos cenários públicos e sintéticos descritos acima.
- Captura prolongada com hardware real, suspensão e desconexão físicas.
- Atualização real de instalação anterior: instalador gerado, mas não executado
  sobre a instalação do usuário. Os testes do helper usam processos/filesystems
  controlados e verificam integridade, espera, flags, recuperação e preservação.
- GPUs AMD/NVIDIA não disponíveis nesta máquina. Não declarar esses dispositivos
  como testados apenas porque um backend os suporta em princípio.
