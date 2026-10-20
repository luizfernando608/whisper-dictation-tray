# Compatibilidade de provedores e janela única — candidato 1.5.1

Solicitação: depois de finalizar a publicação 1.5.0, corrigir uso dos modelos atuais de Gemini/OpenAI, revisar os provedores disponíveis, centralizar configurações e biblioteca, e abrir o programa pelo clique principal da bandeja.

A 1.5.0 foi publicada e sua descoberta/download reais pela 1.4 foram validados. Esta é uma nova entrega; preservar esse release e os dados de uso. A próxima publicação fica separada da validação do novo candidato.

## Requisitos

- R1: oferecer modelos de transcrição/entrada de áudio confirmados por documentação oficial atual, com parâmetros/endpoint próprios de cada família. Não prometer suporte a modelos de imagem/TTS/live pelo fluxo de arquivos.
- R2: preservar IDs personalizados válidos e configurações existentes; manter Groq/local funcionais. Respostas vazias/bloqueadas não devem parecer uma transcrição bem-sucedida. Tratar erros sem expor chaves.
- R3: documentar/testar contratos de Gemini e OpenAI; validar chamadas reais somente com credenciais disponíveis, áudio público curto e resultados explicitamente separados de mocks. Chaves nunca no chat, logs ou repositório.
- R4: biblioteca e configurações na mesma janela principal, navegação interna e uma única instância dessa UI pelas entradas normais. Atalhos existentes e IPC continuam abrindo a página correta.
- R5: salvar/cancelar configurações sem encerrar a biblioteca; preservar verificações de fila/reunião/preparo, credenciais e aplicação de alterações. Alternar páginas preserva seleção e estado útil da biblioteca.
- R6: clique principal da bandeja abre/foca a janela do PACE, sem iniciar/encerrar captura. Ditado/reunião seguem disponíveis por atalhos e menu explícito.
- R7: implementação → testes relevantes → revisão independente → correção/revisão; verificar UI em tamanhos/escalas reais e gerar candidato local testado antes de uma nova publicação.

## Premissas e limites

Reutilizar CustomTkinter, biblioteca e formulário existentes. A UI permanece em processo dedicado com o Tk no thread principal. Sem redesenho de toda a aplicação, troca de framework ou nova dependência de conta para diarização. Neste início só Groq tem chave cadastrada; teste real Gemini/OpenAI depende de configuração segura pelo usuário, já solicitada.
