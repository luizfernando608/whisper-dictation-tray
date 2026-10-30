# Validação de instalação no computador de desenvolvimento — 2026-10-20

Autorização: instalação e atualização reais neste computador, em ambiente isolado, com diarização imperfeita aceita para uso pessoal. Sem publicação externa neste passo.

- [x] Gerar instalador do código atual e confirmar correspondência do executável às fontes e hashes.
- [x] Preservar atalhos, registro do instalador e dados de uso; usar pastas de teste separadas e restaurar o estado ao concluir.
- [x] Executar instalação limpa real; validar registro, atalhos, UI, configurações e inicialização.
- [x] Atualizar binários reais da versão 1.4.0 e testar o helper de reinício com o instalador real; conferir confirmação, preservação e recuperação em falha.
- [x] Exercitar captura física microfone/loopback, pausa/retomada, importação, transcrição/diarização, player e exportação no aplicativo instalado.
- [x] Concluir revisão independente, corrigir achados, registrar evidências e disponibilizar instalador validado para o uso pessoal autorizado.

Verificação automatizada final com smokes habilitados: 179 testes executados, 178 aprovados e um skip por privilégio de criação de symlink. Instalação limpa, upgrade, rollback, captura física e worker do pacote final passaram. Evidências e limites do ensaio em `install-validation-2026-10-20.md`.
