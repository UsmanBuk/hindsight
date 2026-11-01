#!/usr/bin/env bash
# Idempotent installer: Python env, offline models, `hindsight` on PATH, Super+R shortcut.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

uv sync -q

M="$HERE/models"; mkdir -p "$M"
B=https://github.com/k2-fsa/sherpa-onnx/releases/download
fetch_tar() { [ -d "$M/$1" ] || curl -sSLf "$B/$2/$1.tar.bz2" | tar xj -C "$M"; }
fetch()     { [ -f "$M/$1" ] || curl -sSLf -o "$M/$1" "$B/$2/$1"; }
fetch_tar sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8 asr-models
fetch_tar sherpa-onnx-pyannote-segmentation-3-0 speaker-segmentation-models
fetch silero_vad.onnx asr-models
fetch 3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx speaker-recongition-models

mkdir -p "$HOME/.local/bin"
ln -sf "$HERE/bin/hindsight" "$HOME/.local/bin/hindsight"

# GNOME custom shortcut: Super+R -> hindsight toggle
KB=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/hindsight/
SCHEMA=org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$KB
gsettings set "$SCHEMA" name 'Hindsight: toggle meeting recording'
gsettings set "$SCHEMA" command "$HOME/.local/bin/hindsight toggle"
gsettings set "$SCHEMA" binding '<Super>r'
LIST=$(gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings)
if [[ "$LIST" != *"$KB"* ]]; then
  if [[ "$LIST" == "@as []" || "$LIST" == "[]" ]]; then NEW="['$KB']"; else NEW="${LIST%]}, '$KB']"; fi
  gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$NEW"
fi
echo "Installed. Press Super+R (or run 'hindsight toggle') to start/stop recording."
