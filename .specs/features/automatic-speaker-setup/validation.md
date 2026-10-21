# Validação do preparo automático — 21/10/2026

## Estado

Correção de produção no commit `d83465f`, versão 1.5.2. Revisão independente aprovada para código/pipeline/UI e gates da fonte. Pacote congelado e instalação ainda em validação; a release pública continua 1.5.1 até concluir os gates.

## Incidente e reprodução

Usuário informou atualização concluída em outro PC, texto disponível sem falantes e confirmou status **Falha**. Isso significa processamento parcial, não diarização concluída. Sem erro exato/log desse computador, não atribuir causa definitivamente ao cache ou hardware.

No código 1.5.1, `nemotron_diarization.diarize()` chamava `_prepared()` e falhava em cache vazio sem preparar. `prepare_models()`/`_prepared()` exigiam CPU e Vulkan completos, mesmo para CPU. O ASR local exigia marcador anterior ao primeiro uso. A biblioteca ocultava `row.stage` e mostrava somente status genérico; testes antigos de instalação/pipeline copiavam modelos já preparados. Essas lacunas foram reproduzidas com testes vermelhos.

## Correção e revisão

Nemotron prepara/repara automaticamente no worker. CPU é obrigatório; Vulkan opcional mantém validação e fallback CPU. Downloads têm três tentativas limitadas, tamanho/SHA oficiais, arquivos temporários únicos, publicação atômica, cancelamento e lock OS entre processos. Cache válido evita download; preparo manual pode adicionar Vulkan a um cache só CPU. Alterações de manifest não autorizam DLLs/pesos fora dos checksums oficiais.

ASR local/fallback prepara Whisper somente quando um chunk realmente exige inferência, mantendo prioridade dos checkpoints existentes. Cache ASR incompleto/marcador inválido é reparado, modelos públicos usam `use_auth_token=False`, e Whisper é carregado com `local_files_only=True` após preparo. Community-1 avançado continua com fluxo explícito. Ausência inconsistente de turnos na inferência GPU provoca retry CPU; não se inventam identidades.

A biblioteca exibe etapa real e progresso, com erro prioritário. Retry explícito mantém áudio/transcrição parcial/checkpoints e passa a exibir Na fila. Revisão independente encontrou e corrigiu cache CPU que não adicionava Vulkan no preparo manual, e marcador ASR com path inválido. Revisão final sem achados pendentes.

## Testes

- Suíte completa: 276 casos, 275 aprovados, um skip de privilégio symlink Windows, 139,094 s (`temp/auto-speakers-20261021/full-tests.log`). Quatro backends sintéticos ONNX CPU/OpenVINO CPU/Intel GPU/DirectML passaram, erro máximo 4,77e-7.
- Ajuste final do marcador ASR inválido e checagem de completude foi seguido por suites focadas: 26 Nemotron e 33 processamento, aprovadas também pelo revisor. 24 casos Tk de biblioteca aprovados, incluindo etapa, erro prioritário, detalhes e preservação no retry.
- Lock OS testado com threads e com dois processos reais em diretório temporário: filho aguardou, foi cancelado sem adquirir; outro filho adquiriu após liberação do pai. Sem modelos/rede/GUI/cache pessoal nesse gate ad hoc.

## Gate real da fonte

`scripts/validate_fresh_speaker_setup.py` exige output inexistente, cache de modelos e HF_HOME/HF_HUB_CACHE inicialmente ausentes e fixture pública com SHA fixo. Chama o worker normal sem preparo manual ou cópia de modelos; limpa variáveis de tokens/chaves do ambiente do processo e não faz transcrição em provedor remoto. Não abre microfones nem usa gravações privadas.

Fixture: [AMI publicado pela NVIDIA](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/v0.2.0/test_files/diar/README.md), 60 s, PCM16 mono, SHA256 `f00f92e53115a4a6724aec0ddaf9c675df6d5fabd3a489b43fffe56ba52a3ae9`. Fonte da atribuição/licença mantida no README oficial.

`C:/Users/ziull/Documents/pace-speaker-fresh-source-v2-20261021/report.json`: aprovado. Primeiro uso com downloads reais: 33,547 s, 18 segmentos, 25 turnos, três falantes, backend nemotron-cpu; nenhum diretório Vulkan. Segunda gravação/pasta independente, sem checkpoints anteriores, HF offline e proxy HTTP(S) de processo apontando a loopback indisponível: 15,266 s, mesmos counts, cache ASR/modelos sob o próprio output. Não equivale a firewall global ou validação em outro hardware.

A primeira execução do validador havia terminado o processamento com texto/turnos, mas falhou numa asserção do próprio harness que procurava `available_backends` em vez de `runtimes`. Seu relatório continua false. O campo foi corrigido e todo o teste foi repetido com um novo output/cache/HF vazios (V2), sem reclassificar a tentativa anterior.

## Gate congelado

PyInstaller concluído: os code objects dos 32 módulos do pacote e main conferem com a fonte; EXE SHA256 `2af16b5adb5790aabf12741fd4fb7dca67776ad1ecdd460d295a1f7066055274`. Relatório `Output/v1.5.2/source-verification.json`. Smokes reais do EXE em ONNX CPU, OpenVINO CPU, Intel GPU e DirectML aprovados, erro máximo 4,77e-7 (`temp/auto-speakers-20261021/frozen-smoke.log`).

`C:/Users/ziull/Documents/pace-speaker-fresh-frozen-20261021/Perfil João/report.json`: aprovado em caminho com espaços/acentos. Primeiro uso com cache de modelos/HF vazio e download real: 45,109 s, 18 segmentos, 25 turnos, três falantes, CPU. Segunda gravação independente com backend solicitado **auto**, cache somente CPU/sem Vulkan e ambiente offline por proxy/HF: 14,796 s, mesmos counts. Sem preparo manual, modelos semeados ou checkpoints da primeira gravação. Confirma disponibilidade/integridade nesse hardware; não mede DER ou precisão das identidades.

## Limites e compatibilidade

O CPU oficial Windows do [runtime NVIDIA v0.2.0](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/v0.2.0/.github/workflows/release.yml) usa x86-64-v3/AVX2. Não requer placa NVIDIA; não se garante compatibilidade com CPUs sem AVX2. O ZIP oficial CPU contém DLLs MSVC requeridas, verificado pelo revisor diretamente no artefato com SHA oficial; não há evidência de que instalar redistributable seja a solução do PC remoto.

Downloads Nemotron próprios cooperam com cancelamento por chunk. O download_model de faster-whisper é síncrono e não oferece callback de cancelamento por transferência: há checks antes/depois e o supervisor existente encerra o worker/descendentes após cancelamento em até cerca de dez segundos. Áudio/checkpoints permanecem para retomar. Nenhuma gravação privada foi enviada a serviço externo.
