# Validação do candidato 1.5.1

## Escopo

1.5.0 publicada e verificada online antes deste trabalho. O candidato novo corrige adaptadores de ditado Gemini/OpenAI, atualiza sugestões de modelos e centraliza biblioteca/configurações; o clique principal da bandeja abre a UI. Não modifica o arquivo da 1.5.0 publicado. O usuário escolheu explicitamente testes sem chaves Gemini/OpenAI: contratos com SDK real/HTTP em memória, sem chamadas reais nesses provedores.

## Implementação e revisão

Pesquisa em fontes oficiais e assinaturas locais registrada em `research.md`. T2: 27 casos de contratos, formatos, idiomas, respostas inválidas, payloads/encoder, upload/delete e recursos; regressão Groq inclui reutilização do cliente em chamadas consecutivas. T3: 11 testes de frame embutido, credenciais transacionais, preparo, cleanup e layout em 100/125/150%. T4: 9 testes de roteamento/Tk, abertura concorrente, seleção/player/rascunho e ação padrão sem captura. Caso combinado usa formulário real para capturar/salvar Ctrl+F.

A revisão independente encontrou dois P2, ambos corrigidos: campo Gemini `errors` e conflito do binding específico Ctrl+F do root com a captura de atalhos. Revisor reexecutou seus casos e as suítes focadas; sem achados pendentes. A política de reuniões com provedor de timestamps permanece separada dos adaptadores de ditado.

Suíte completa antes da restauração pontual de reutilização Groq: 225 executados, 224 aprovados e um skip por privilégio de symlink. Os quatro smokes ONNX/OpenVINO CPU/OpenVINO Intel GPU/DirectML passaram. A suíte final será registrada após o build.

Verificação visual das duas páginas na mesma janela, com dados sintéticos e sem chaves, em `temp/unified-ui-review-final/recordings.png` e `settings.png`. Capturas foram feitas pelo HWND da própria janela, sem incluir o desktop. Layout de configurações permanece dentro do painel nos tamanhos/escalas testados.

## Pacote e validação local

Em andamento: gerar instalador/EXE 1.5.1 em caminho separado da 1.5.0 validada, comparar fontes congeladas, instalar e exercitar navegação por IPC com a UI única. Preservar gravações, configuração e credenciais existentes. Não declarar teste de API real Gemini/OpenAI nem publicação da 1.5.1 antes de concluídos esses passos.
