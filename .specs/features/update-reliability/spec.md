# Confiabilidade da atualização — incidente de 21/10/2026

Pedido: reproduzir a falha de instalação relatada em outro computador e executar um ciclo de correção/revisão/testes isolados. O texto exato do erro e a versão instalada foram solicitados; não assumir que já foram confirmados.

- R1: conferir o instalador público real e reproduzir o caminho das versões antigas, incluindo EOF prematuro e colisão no arquivo temporário. Distinguir mecanismo reproduzido de causa confirmada no outro computador.
- R2: o cliente atual deve repetir downloads transitórios com limite, verificar tamanho/hash e nunca executar conteúdo parcial, HTML ou corrompido. Falhas não devem fechar o aplicativo nem destruir dados.
- R3: o aplicativo só pode encerrar para atualização após confirmação verificável de prontidão do auxiliar. Falha no bootstrap mantém o aplicativo disponível.
- R4: ensaios reais com o pacote público e com o candidato corrigido, em caminhos separados, incluindo espaço/Unicode/caminho longo, falha de instalação e falha de confirmação. Preservar a prévia, biblioteca, credenciais, atalhos e registro do host.
- R5: revisão independente das correções e novos ensaios após achados. Registrar os limites: não há VM/Windows Sandbox disponível; pastas e dados isolados no mesmo Windows não equivalem a outros sistemas operacionais ou hardwares.

O público continua em 1.5.0. Não substituir assets publicados durante a investigação. A versão 1.5.1 ainda é candidato local. Código novo não altera o downloader que já está em execução nas versões 1.3/1.4; qualquer recuperação do primeiro salto deve tratar isso explicitamente.
