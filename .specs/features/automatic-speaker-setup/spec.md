# Preparo automático de identificação de falantes

Incidente: atualização 1.5.1 concluída em outro PC, gravação com texto disponível e status **Falha**, sem falantes. O erro exato não foi fornecido. Código reproduz duas lacunas em cache vazio: Nemotron exige prepared.json e ASR local exige marcador antes de qualquer preparo automático. O preparo exige Vulkan mesmo quando CPU seria suficiente. Não atribuir a causa remota a hardware sem evidência.

- R1: processamento Nemotron prepara/repara automaticamente pesos e runtime oficiais quando necessários, no worker existente, sem bloquear captura/UI e sem conta/token.
- R2: CPU íntegra continua utilizável se preparo/driver Vulkan opcional falhar; respeitar requisitos reais do runtime oficial. Não prometer suporte a CPUs sem AVX2.
- R3: manter hashes/tamanhos/ZIP oficiais, tentativas limitadas, temporários únicos, lock entre preparos, cancelamento e cache atômico. Cache existente válido não deve baixar novamente.
- R4: preservar áudio, transcrição/checkpoints e reprocessamento da mesma gravação. Mostrar etapa real de preparo/erro na biblioteca. ASR local e alternativas de timestamps devem funcionar em cache vazio; nunca inventar IDs para esconder ausência de falantes.
- R5: validar download/preparo/inferência reais com cache/HF_HOME vazios e áudio público, mais cache offline, falha/cancelamento, regressão e revisão independente. Os testes anteriores copiavam modelos preparados e não cobriam esse primeiro uso.

Aplicar correção pequena aos módulos atuais. Community-1 avançado continua com preparo explícito e condições próprias. A publicação será uma nova versão 1.5.2 após build/revisão/validação, preservando releases e dados existentes.
