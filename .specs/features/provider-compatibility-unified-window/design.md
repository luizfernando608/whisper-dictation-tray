# Design e critérios de implementação

Premissas: manter Tk no thread principal do processo de UI, serviço de captura separado e IPC existente como autoridade das configurações. Não usar chaves reais em testes automáticos. A verificação de compatibilidade não equivale a disponibilidade/billing de uma conta sem credenciais.

## Adaptadores (T2)

Catálogo com modelos atuais comprovados nas fontes primárias de `research.md`. Modelos novos de transcrição recebem seu contrato correto; IDs personalizados/legados salvos continuam aceitos. Padrões novos: OpenAI `gpt-transcribe`, Gemini `gemini-3.8-flash`. Gemini moderno usa Interactions com `store=False`; a família dedicada de transcrição não recebe prompt/thinking genéricos. Respostas inválidas disparam erro/fallback, uploads temporários são apagados e cliente fechado. Payloads ficam dentro dos limites reais, inclusive fallback para WAV.

Arquivos: `providers.py`, `transcription.py`, somente defaults de modelos em `settings.py`, limites inferiores de SDKs em `requirements.txt`, testes de contratos. Critério: SDK real com transporte controlado serializa parâmetros corretos para cada família; Groq/local continuam passando; limites/cleanup/falhas verificados. Testes reais de conta ficam explicitamente separados.

## Formulário (T3)

`build_settings_frame(parent, config, on_save, context, on_close=...) -> SettingsFrame`, sem outro Tk, mainloop ou ownership da janela principal. `on_close(config)` após commit bem-sucedido; `on_close(None)` para cancelar. `can_close()` protege preparo de modelos; `set_active(False)` suspende captura de atalhos. O frame é preservado ao trocar página, descartado ao salvar/cancelar e libera timers/bindings próprios. Wrapper standalone continua para testes compatíveis.

`create_settings_context(project_root, config)` extrai callbacks IPC/transacionais atuais sem importar a janela da biblioteca. Arquivos: `settings_window.py`, `settings_ui.py`, testes embutidos. Critério: salvar/cancelar não destroem o root, alterações de chave só chegam ao commit, modelo em preparo não pode ser encerrado por acidente, navegação não captura teclas na biblioteca.

## Janela e roteamento (T4)

Reutilizar `RecordingsWindow` e seu processo `--recordings-ui`. O proprietário da bandeja guarda página solicitada/revisão sob lock. `CAPABILITIES` inclui snapshot `ui_navigation` da mesma instalação. Abrir gravações/configurações incrementa revisão e inicia um único filho se necessário. O filho consulta esse snapshot via IPC e aplica mudanças de página/foco no thread Tk. Não criar outro socket, arquivo de controle, Tk ou processo de configurações. Uma revisão já consumida não rouba foco de novo.

O frame de configurações ocupa a mesma coluna principal da biblioteca; navegação na lateral. A biblioteca, seleção e player continuam vivos na troca de página. Salvar aplica a nova localização de biblioteca e volta às gravações. Rotas CLI antigas permanecem compatíveis; `--settings-ui` passa pelo runner unificado. O formulário standalone é só wrapper legado/testes.

O único item `default=True` da bandeja é “Abrir PACE”. Ditado, reunião, pausa e atalhos continuam explícitos. Fechar a janela não encerra a captura do serviço; a presença de preparo de modelos deve impedir descarte involuntário do frame.

Arquivos: `app.py`, `recordings_window.py`, `main.py` apenas se necessário, testes novos de roteamento/Tk. Critérios: chamadas concorrentes geram um filho; abrir configurações e voltar preserva root/seleção; foco recebe somente novas revisões da própria instalação; clique padrão da bandeja não altera estado de captura; escala/layout utilizável.

## Gates finais

Revisão independente de cada componente e correções antes do candidato. Suíte e smokes apropriados; build 1.5.1 separado; fontes congeladas verificadas; ensaio local em biblioteca isolada. Relatório distingue respostas simuladas de chamadas reais. O release 1.5.0 publicado permanece idêntico.
