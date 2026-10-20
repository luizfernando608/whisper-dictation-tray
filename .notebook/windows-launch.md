# Abertura no Windows

`main.py:main()` sem argumentos inicia apenas a bandeja; se a mesma instalação já estiver aberta, sai sem abrir janela. Atalhos interativos precisam de `--recordings` ou `--settings`. `installer.iss:[Icons]` e `[Run]` usam `--recordings` nos atalhos principais e na abertura após instalar; `[Icons]` mantém a inicialização automática sem argumentos.

O canal IPC é único entre instalações. Uma instalação antiga iniciada pelo Windows impede a prévia de assumir os atalhos globais; a prévia detecta o conflito e pede para fechar a antiga. No computador de desenvolvimento, atalhos de menu Iniciar/configurações/gravações e inicialização foram alinhados à prévia existente em 2026-10-20, com cópia dos atalhos anteriores em dados locais de backup. A versão instalada antiga foi encerrada via IPC e a biblioteca da prévia foi aberta pelo atalho principal para verificar o resultado. Configuração e gravações não foram migradas.
