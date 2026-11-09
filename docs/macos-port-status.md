# Porte para macOS (Apple Silicon): estado em 2026-11-09

Branch local `macos-local`, por cima do PR #715 ("Add initial macOS build support"). O jogo roda nativo em arm64,
com o renderizador WebGPU (`gpu/render`) sobre Metal via `wgpu-native`. Testado num MacBook com Apple M5, 16 GB,
macOS 27.2, Xcode 27.2.

## Como compilar e rodar

```sh
bash scripts/macos/deps.sh        # Python, SDL2, dav1d + FFmpeg, wgpu-native em build-macos/deps (sem Homebrew)
scripts/macos/build-game.sh --nup /caminho/do/jogo.nup --out game --language pt-BR
cd game && ./hookshot
```

A primeira compilação leva uns 10 minutos (lift ~1 min, 413 arquivos C em paralelo). A pasta `game/` (o jogo
montado, ~25 GB) e `build-macos/` ficam fora do repositório (`.gitignore`). Qualidade: `game/hookshot_env.txt`
(`HOS_QUALITY=alta|media|baixa|minima`); teclas fn+F8 (FSR), fn+F9 (nitidez), fn+F10 (ajustar/esticar), F11 (tela cheia).

## O que funciona

- Build completo nativo arm64; o jogo abre, toca áudio, reconhece controle (PS4 testado) e grava saves.
- Tela de título com o vídeo de fundo e o menu em português.
- Abertura (vídeo + legendas em português) a ~50–59 fps.
- A gameplay carrega (HUD, corações, textos), mas a cena 3D sai branca: ver "Onde paramos".

## O que foi ajustado

### Build e dependências
- `scripts/macos/deps.sh`: cmake, meson, ninja e pkg-config num venv quando o Mac não os tem (sem Homebrew);
  dav1d compilado e FFmpeg com `libdav1d` (o decodificador AV1 nativo do FFmpeg só usa hardware: vídeos pretos).
- `nx2/zbic.py`: o sysroot do SDK passado ao clang chamado pelo caminho completo (`string.h` não encontrado).
- `nx2/build.py`: `-lc++` no link do macOS; `libwgpu_native.dylib` copiado para a pasta do jogo.
- `nx2/make_game.py`: copia `scripts/macos/hookshot_env.txt` para a pasta do jogo quando ela não tem um.
- `scripts/macos/build-game.sh`: roda o builder deste checkout com as dependências de `build-macos/deps`.

### Runtime (`host/`)
- `posix/win32_posix.c`:
  - fibras: a página de guarda colocada depois do `makecontext` (o do macOS zera a pilha inteira) e do tamanho de página real (16 KB);
  - `InitializeCriticalSection` e `SleepConditionVariable*` também no macOS (faltavam no link);
  - **esperas por endereço com `os_sync_wait_on_address`** (macOS 14.4+). Os "baldes" anteriores eram 64 variáveis de condição compartilhadas, e `WakeConditionVariable` acordava a thread errada (acordadas perdidas). A abertura foi de 13–16 para 50–59 fps. `HOS_WAIT_BUCKETS=1` volta ao método antigo;
  - QoS `USER_INTERACTIVE` nas threads do jogo (`HOS_QOS`).
- `posix/display_sdl.c`: sem a dica `wayland,x11` no Mac (a janela não abria); teclas F7–F10 ligadas a `rx_present_hotkey`.
- `nn_video.c`: o núcleo virtual é liberado enquanto o dav1d decodifica; quadros YUV420 guardados só como NV12 (sem
  converter para RGBA a cada quadro); `HOS_VIDEO_TIMES=1` mede o decodificador.
- `hos_main.c`: presets `HOS_QUALITY` (alta, media, baixa, minima) aplicados depois de ler `hookshot_env.txt`.
- `rx_backend.c`: sem o menu Insert (só existe no Vulkan), `rx_option` lê `HOS_DYNRES`, `HOS_RES_SCALE` e
  `HOS_REALTIME_STEP` do ambiente, e a resolução fixa passa a funcionar no Mac.

### Renderizador WebGPU/Metal (`gpu/render/`)
- Buffers de vértice com strides inteiros: o Metal conta `tamanho / stride` elementos e lia o último vértice como
  zero (o logo do título cortado na diagonal).
- **Push constants do compute NVN2 no c0** (como `gpu/vk/vk_bank.cpp`): sem elas, as tabelas ficavam 64 bytes
  adiantadas e o passo YUV→RGB dos vídeos nunca rodava (vídeos pretos, só som).
- **Base vertex, base instance e draw ID no c0** do vertex (0x640/0x644/0x648, como `GetReplaceConstBuffer` do Vulkan):
  o texto da interface acha os glifos pelo base instance (todo o texto sumia).
- **Substituto das redes neurais com HMMA** (`nvn_upscale_stub`), só para programas com HMMA (`nvn_cs_matrix`):
  - vídeo: a entrada RGB ampliada linearmente para a saída;
  - gameplay (TAAU): a cor da cena em RGB9E5 empacotada numa imagem R32UI é decodificada no shader e elevada ao
    quadrado, como `taau_upscale.comp` do Vulkan.
- **Samplers compartilhados a partir do 16º** (`RX_SHARE_SAMPLERS`, também no emissor WGSL): o Metal permite 16 por
  estágio, e com 17–18 o wgpu descartava o lote inteiro de comandos do frame.
- Diagnóstico nos frame dumps: índices do desenho, fatores de blend, imagens e texturas de cada dispatch, chave do
  programa. Chaves: `RX_NO_UPSCALE_STUB`, `RX_NO_CS_PUSH`.

### Tradutor de shaders
- `gpu/third_party/suyu/.../frontend/ampere/lower_to_maxwell.h` agora é a versão do eden (`gpu/vk/eden`, a do backend
  Vulkan), muito mais completa nas instruções do Switch 2. A versão intermediária, com as instruções portadas à mão
  (0x8a5, 0x4a4, 0x899, 0x825, TXQ), ficou só localmente em `build-macos/lower_to_maxwell.suyu-ported.h`.
- Emissor WGSL: atômicos de imagem emulados como leitura, operação e escrita (o passe de exposição usa).

## Onde paramos

1. **Gameplay 3D branca.** A cena HDR é desenhada (luminância média ~22), mas a imagem final satura em 1,0.
   Com `RX_SYNC_DISPATCH_WRITES=1` (copiar de volta para a memória do jogo o que os shaders de compute escrevem, a
   cada frame) aparecem formas e degradê, a ~4 fps. O Vulkan teve a mesma "parede branca" e só saiu dela com correções
   específicas do Zelda que o renderizador WebGPU ainda não tem:
   - shaders que leem dados por ponteiros de 64 bits: ainda falham `3720e3ab9f56fbfa`, `ba00b8c5a7b43504`,
     `c7f6c62cc98485ac` e `e7d75810de840595` ("WGSL global memory (64-bit addresses) is not implemented"; no Vulkan
     é a tabela de ponteiros em 0x880 de `vk_bank.cpp`);
   - grades de dispatch indireto da folhagem (`08b413e`);
   - a sincronização dos resultados da GPU que a CPU lê (exposição automática), sem custo por frame.
2. As redes com HMMA (vídeo, TAAU e outras) não são traduzidas; os substitutos dão uma imagem um pouco mais suave.
3. Escritas de CPU em texturas `Depth32Float` são proibidas no WebGPU: cerca de 2 lotes descartados por execução.
4. Uma textura "não filtrável" pode receber um sampler compartilhado filtrável (1–2 pipelines inválidas).
5. Cosmético: caminhos no estilo Windows no log de saves e no nome do cache (`rxcache\pipelines.bin`).
6. Fps da gameplay: 20–29 com a cena ainda errada; não dá para avaliar antes de corrigir a imagem.

Alternativa: o NX2Apple (baseado no nxnative) lista o Ocarina of Time de Switch 2 rodando no Mac via NVN sobre Metal.
