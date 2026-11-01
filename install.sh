#!/usr/bin/env bash
# Idempotent installer: Python env, offline models, `hindsight` on PATH, optional GNOME shortcut.
#
# Environment:
#   HINDSIGHT_MODELS_DIR  where to download models        (default: <repo>/models)
#   HINDSIGHT_BIN_DIR     where to symlink `hindsight`    (default: ~/.local/bin)
#   HINDSIGHT_SHORTCUT    GNOME accelerator for toggle    (default: <Super>r; set to "none" to skip)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

for dep in uv ffmpeg ffprobe pactl xdpyinfo curl tar; do
  command -v "$dep" >/dev/null || { echo "error: '$dep' is required but not installed" >&2; exit 1; }
done
command -v notify-send >/dev/null || echo "warning: notify-send not found; desktop notifications disabled" >&2

uv sync -q

M="${HINDSIGHT_MODELS_DIR:-$HERE/models}"
mkdir -p "$M"
B=https://github.com/k2-fsa/sherpa-onnx/releases/download
fetch_tar() { [ -d "$M/$1" ] || { echo "downloading $1"; curl -sSLf "$B/$2/$1.tar.bz2" | tar xj -C "$M"; }; }
fetch() { [ -f "$M/$1" ] || { echo "downloading $1"; curl -sSLf -o "$M/$1.part" "$B/$2/$1" && mv "$M/$1.part" "$M/$1"; }; }
fetch_tar sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8 asr-models
fetch_tar sherpa-onnx-pyannote-segmentation-3-0 speaker-segmentation-models
fetch silero_vad.onnx asr-models
fetch 3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx speaker-recongition-models

BIN="${HINDSIGHT_BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$BIN"
ln -sf "$HERE/bin/hindsight" "$BIN/hindsight"
echo "linked $BIN/hindsight"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "note: $BIN is not on your PATH" ;; esac

SHORTCUT="${HINDSIGHT_SHORTCUT:-<Super>r}"
if [[ "$SHORTCUT" == "none" ]]; then
  echo "skipping keyboard shortcut (HINDSIGHT_SHORTCUT=none)"
elif ! command -v gsettings >/dev/null || ! gsettings list-schemas | grep -qx org.gnome.settings-daemon.plugins.media-keys; then
  echo "GNOME not detected; bind '$BIN/hindsight toggle' to a key in your desktop's settings"
else
  KEY=org.gnome.settings-daemon.plugins.media-keys
  KB=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/hindsight/
  SCHEMA=$KEY.custom-keybinding:$KB
  gsettings set "$SCHEMA" name 'Hindsight: toggle meeting recording'
  gsettings set "$SCHEMA" command "$BIN/hindsight toggle"
  gsettings set "$SCHEMA" binding "$SHORTCUT"
  LIST=$(gsettings get $KEY custom-keybindings)
  if [[ "$LIST" != *"$KB"* ]]; then
    if [[ "$LIST" == "@as []" || "$LIST" == "[]" ]]; then NEW="['$KB']"; else NEW="${LIST%]}, '$KB']"; fi
    gsettings set $KEY custom-keybindings "$NEW"
  fi
  echo "GNOME shortcut $SHORTCUT -> hindsight toggle"
fi

echo "Installed. Run 'hindsight toggle' (or press the shortcut) to start/stop recording."
