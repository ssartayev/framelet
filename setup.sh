#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

mkdir -p runtime/{bin,models,wan/bin}
if [[ ! -x runtime/venv/bin/python ]]; then
  python3 -m venv runtime/venv
fi
runtime/venv/bin/python -m pip install -r requirements-stories.txt imageio-ffmpeg
ffmpeg_path=$(runtime/venv/bin/python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')
ln -sf "$ffmpeg_path" runtime/bin/ffmpeg
if [[ ${1:-} == --stories-only ]]; then
  exit 0
fi
runtime/venv/bin/python -m pip install mlx-lm
runtime/venv/bin/python make_backgrounds.py

if [[ ! -x runtime/wan/bin/sd-cli ]]; then
  release=master-1081-e1ca5e8
  archive=sd-master-e1ca5e8-bin-Darwin-macOS-26.7.2-arm64.zip
  curl --fail --location --retry 4 --continue-at - \
    "https://github.com/leejet/stable-diffusion.cpp/releases/download/$release/$archive" \
    --output runtime/wan/sd.zip
  unzip -oq runtime/wan/sd.zip -d runtime/wan/bin
fi

hf=runtime/venv/bin/hf
export HF_HUB_DISABLE_XET=1
export HF_HUB_DOWNLOAD_TIMEOUT=90
if [[ ! -s runtime/models/writer/model.safetensors ]]; then
  "$hf" download mlx-community/Qwen3-1.7B-4bit --local-dir runtime/models/writer
fi
download_model() {
  local target=$1 digest=$2 url=$3
  if [[ -f "$target" ]] && printf '%s  %s\n' "$digest" "$target" | shasum -a 256 -c --status; then
    return
  fi
  curl --fail --location --retry 4 --continue-at - "$url" --output "$target.part"
  printf '%s  %s\n' "$digest" "$target.part" | shasum -a 256 -c
  mv -f "$target.part" "$target"
}
download_model runtime/wan/wan.gguf 50f74e4597a214796ef9a6c8f19ae63b0046fe8b611b48d210d366a675fedcac \
  https://huggingface.co/samuelchristlie/Wan2.1-T2V-1.3B-GGUF/resolve/main/Wan2.1-T2V-1.3B-Q4_0.gguf
download_model runtime/wan/t5.gguf f64f8d6dc4d8a24276df69d0ccea789aae686f7417950a41e6568c30cb478a5c \
  https://huggingface.co/city96/umt5-xxl-encoder-gguf/resolve/main/umt5-xxl-encoder-Q3_K_S.gguf
download_model runtime/wan/tae.safetensors 04766eac0221b5390b985ae3fdcca652cbb4b1e8b82b28ea7ff89dfad1b1a93f \
  https://raw.githubusercontent.com/madebyollin/taehv/0ad83bb8fdc48e9e94138704e939d500a3b43660/safetensors/taew2_1.safetensors
