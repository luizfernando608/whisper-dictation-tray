# Abrir PACE pela pesquisa do Windows

Data: 2026-10-20

Problema: o atalho principal e a inicialização automática apontavam à instalação antiga; o principal não tinha argumentos e abria somente a bandeja. A versão atual estava em uma prévia separada.

Correção local: preservar atalhos anteriores em backup, alinhar atalhos interativos e inicialização à prévia atual, encerrar a instalação antiga via IPC e abrir a biblioteca pelo atalho principal. Preservar configurações, áudio e transcrições.

Correção permanente no instalador: atalhos principais do menu Iniciar e desktop e abertura após instalação passam `--recordings`; inicialização automática continua somente na bandeja.

Verificado: destino existe e recebe `--recordings`; execução do próprio arquivo `.lnk` abriu `PACE — Gravações`; IPC confirmou a pasta da prévia e versão 1.5.0; instalação antiga deixou de rodar. `git diff --check` passou. Instalador não recompilado neste ajuste local.
