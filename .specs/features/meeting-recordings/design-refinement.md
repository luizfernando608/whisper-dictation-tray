# Revisão visual da biblioteca

Solicitação de 2026-10-14: a biblioteca deve parecer um produto premium,
minimalista e profissional, com menos ambiguidade na hierarquia das ações.

## Decisões

- Manter o tema escuro e a identidade PACE. Superfícies neutras, divisórias sutis,
  uma cor primária e tipografia com níveis claros. Não adicionar decoração ou
  métricas sem utilidade para a tarefa.
- Biblioteca lateral com busca e cartões de título/data/duração/estado. Área
  principal dedicada ao áudio e à transcrição da gravação selecionada.
- Nova gravação é a ação primária; Importar é secundária. Configurações ficam
  no rodapé da biblioteca. Pausa/retomada só aparecem durante a captura.
- Título apresentado como texto, player compacto e transcrição organizada por
  turnos de falante. Renomear permanece disponível no menu.
- Copiar e Exportar perto da transcrição; TXT/SRT/JSON e WAV agrupados em Exportar.
  Busca, nomes de falantes, reprocessamento, cancelamento, detalhes e exclusão no
  menu de ações. Busca também por Ctrl+F. Nenhuma capacidade anterior removida.
- Estados de biblioteca vazia, filtro sem resultados, processamento, falha,
  gravação e pausa. Mensagens longas resumidas; diagnóstico integral em Detalhes.
- Títulos limitados visualmente a duas linhas, preservando o valor integral.
  Foco e posição da lista preservados durante atualizações. Menus acompanham a
  paleta e permitem Tab, setas, Enter/Espaço e Escape, pulando ações desabilitadas.

## Execução e verificação

1. Reestruturar a biblioteca em `recordings_window.py`, preservando storage, IPC,
   proteção de originais e bloqueio de reprodução durante captura.
2. Verificar widgets reais, seleção/filtro/importação, menus, teclado, estado de
   captura e layout mínimo/DPI; adaptar o ensaio visual sintético em
   `scripts/preview_recordings.py` para capturar exclusivamente a janela própria.
3. Revisão independente, correções e nova verificação; gerar a prévia portátil
   para avaliação local. Não publicar nem substituir a instalação principal.

A presença de CTkTextbox revelou um problema no encerramento compartilhado:
`close_window` deve cancelar timers pelo Tcl sem apagar o comando registrado por
um widget filho, pois o próprio filho o remove ao ser destruído. Menus são
reutilizados e ocultados para permitir o término das animações agendadas do CTk.

Achados independentes corrigidos: título longo escondendo controles; foco perdido
ao reconstruir cartões; timers de popovers destruídos; Tab em ação desabilitada;
restauração do foco ao fechar com Escape. Evidências finais registradas na
validação da feature.
