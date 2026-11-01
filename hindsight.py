"""Hindsight: local, offline meeting recorder.

Usage: hindsight start | stop | toggle | status | process <dir> | cleanup

Configure with HINDSIGHT_* environment variables (see README.md).
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

ROOT = Path(__file__).resolve().parent


def _env(name: str, default: str) -> str:
    """HINDSIGHT_<name> from the environment, or the default when unset/empty."""
    return os.environ.get(f"HINDSIGHT_{name}") or default


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback)


# Configuration: every setting can be overridden with a HINDSIGHT_* environment variable.
MODELS = Path(_env("MODELS_DIR", str(ROOT / "models"))).expanduser()
MEETINGS = Path(_env("MEETINGS_DIR", _env("MEETINGS", str(Path.home() / "Meetings")))).expanduser()
STATE_DIR = Path(_env("STATE_DIR", str(_xdg("XDG_STATE_HOME", ".local/state") / "hindsight"))).expanduser()
STATE_FILE = STATE_DIR / "current.json"
LOCK_FILE = STATE_DIR / "lock"
LOG_FILE = STATE_DIR / "hindsight.log"

OLLAMA_URL = _env("OLLAMA_URL", "http://127.0.0.1:11434/v1").rstrip("/")
OLLAMA_MODEL = _env("OLLAMA_MODEL", "gemma4:e4b")
MIC_SOURCE = _env("MIC", "")              # PulseAudio/PipeWire source; empty = system default
SYSTEM_SOURCE = _env("SYSTEM_AUDIO", "")  # monitor source; empty = default sink's .monitor
VIDEO_ENCODER = _env("VIDEO_ENCODER", "auto")  # auto | h264_nvenc | libx264
RETENTION_DAYS = int(_env("RETENTION_DAYS", "30"))  # 0 disables video cleanup
MAX_W = int(_env("MAX_WIDTH", "1920"))
MAX_H = int(_env("MAX_HEIGHT", "1080"))
FPS = int(_env("FPS", "15"))
DIARIZATION_THRESHOLD = float(_env("DIARIZATION_THRESHOLD", "0.75"))
ASR_THREADS = int(_env("ASR_THREADS", str(min(12, os.cpu_count() or 4))))
SR = 16000


# ---------------------------------------------------------------- utilities

def log(msg: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    with LOG_FILE.open("a") as f:
        f.write(line + "\n")
    if sys.stderr.isatty():
        print(line, file=sys.stderr)


def notify(title: str, body: str = "", urgency: str = "normal") -> None:
    try:
        subprocess.run(
            ["notify-send", "-a", "Hindsight", "-u", urgency, "-i", "media-record", title, body],
            timeout=5, check=False,
        )
    except Exception as e:  # notifications are best-effort
        log(f"notify failed: {e}")


def run(cmd: list[str], **kw) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout.strip()


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers kill(0); treat it as gone.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


def load_state() -> dict | None:
    try:
        st = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return None
    if not pid_alive(st["pid"]):
        return {**st, "dead": True}
    return st


class Lock:
    def __enter__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.f = LOCK_FILE.open("w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


# ---------------------------------------------------------------- recording

def screen_size(display: str) -> tuple[int, int]:
    out = run(["xdpyinfo", "-display", display])
    m = re.search(r"dimensions:\s+(\d+)x(\d+)", out)
    return int(m.group(1)), int(m.group(2))


def scaled_size(w: int, h: int) -> tuple[int, int]:
    s = min(1.0, MAX_W / w, MAX_H / h)
    return int(w * s) // 2 * 2, int(h * s) // 2 * 2


_nvenc_ok: bool | None = None


def nvenc_available() -> bool:
    global _nvenc_ok
    if _nvenc_ok is None:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
             "color=black:s=256x256:d=0.2", "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, timeout=20,
        )
        _nvenc_ok = r.returncode == 0
    return _nvenc_ok


def video_codec_args() -> list[str]:
    if VIDEO_ENCODER == "h264_nvenc" or (VIDEO_ENCODER == "auto" and nvenc_available()):
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr",
                "-cq", "32", "-b:v", "0", "-maxrate", "2M", "-bufsize", "4M", "-g", str(FPS * 10)]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-g", str(FPS * 10)]


def cmd_start() -> int:
    with Lock():
        st = load_state()
        if st and not st.get("dead"):
            notify("Hindsight is already recording", st["dir"])
            return 1
        if st and st.get("dead"):
            log(f"stale state found (ffmpeg {st['pid']} gone); processing {st['dir']}")
            STATE_FILE.unlink(missing_ok=True)
            spawn_process(Path(st["dir"]))

        cleanup_old_videos()

        display = os.environ.get("DISPLAY") or ":0"
        w, h = screen_size(display)
        ow, oh = scaled_size(w, h)
        mic = MIC_SOURCE or run(["pactl", "get-default-source"])
        monitor = SYSTEM_SOURCE or run(["pactl", "get-default-sink"]) + ".monitor"

        now = dt.datetime.now()
        mdir = MEETINGS / f"{now:%Y-%m-%d_%H%M}_recording"
        n = 2
        while mdir.exists():
            mdir = MEETINGS / f"{now:%Y-%m-%d_%H%M}_recording-{n}"
            n += 1
        mdir.mkdir(parents=True)

        tq = ["-thread_queue_size", "4096"]
        cmd = [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning",
            *tq, "-f", "x11grab", "-framerate", str(FPS), "-video_size", f"{w}x{h}",
            "-draw_mouse", "1", "-i", f"{display}+0,0",
            *tq, "-f", "pulse", "-name", "hindsight-mic", "-i", mic,
            *tq, "-f", "pulse", "-name", "hindsight-system", "-i", monitor,
            "-map", "0:v", "-map", "1:a", "-map", "2:a",
            "-vf", f"scale={ow}:{oh}:flags=bicubic,format=yuv420p",
            *video_codec_args(),
            "-c:a", "aac", "-b:a", "64k", "-ac", "1", "-ar", "48000",
            "-metadata:s:a:0", "title=Me (mic)", "-metadata:s:a:1", "title=Others (system)",
            "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=eng",
            str(mdir / "recording.mkv"),
        ]
        errlog = (mdir / "ffmpeg.log").open("w")
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=errlog, stderr=errlog,
                                start_new_session=True)
        time.sleep(1.5)
        if proc.poll() is not None:
            err = (mdir / "ffmpeg.log").read_text()[-600:]
            log(f"ffmpeg failed to start: {err}")
            notify("Hindsight failed to start recording", err[-200:], "critical")
            return 1

        STATE_FILE.write_text(json.dumps({
            "pid": proc.pid, "dir": str(mdir), "started": now.isoformat(),
            "mic": mic, "monitor": monitor,
        }))
        log(f"recording started pid={proc.pid} dir={mdir} {w}x{h}->{ow}x{oh} "
            f"encoder={video_codec_args()[1]} mic={mic} monitor={monitor}")
        notify("● Recording started", f"Mic: {mic}\nSystem: {monitor}")
        return 0


def cmd_stop() -> int:
    with Lock():
        st = load_state()
        if not st:
            notify("Hindsight is not recording")
            return 1
        pid, mdir = st["pid"], Path(st["dir"])
        if not st.get("dead"):
            os.kill(pid, signal.SIGINT)  # ffmpeg finalises the file on SIGINT
            for _ in range(300):
                if not pid_alive(pid):
                    break
                time.sleep(0.1)
            else:
                log(f"ffmpeg {pid} did not exit after 30s; killing")
                os.kill(pid, signal.SIGKILL)
        STATE_FILE.unlink(missing_ok=True)
        mins = (dt.datetime.now() - dt.datetime.fromisoformat(st["started"])).total_seconds() / 60
        log(f"recording stopped dir={mdir} ({mins:.1f} min)")
        notify("■ Recording stopped", f"{mins:.0f} min. Transcribing and summarising…")
        spawn_process(mdir)
        cleanup_old_videos()
        return 0


def cmd_toggle() -> int:
    st = load_state()
    return cmd_stop() if st and not st.get("dead") else cmd_start()


def cmd_status() -> int:
    st = load_state()
    if st and not st.get("dead"):
        print(f"recording since {st['started']} -> {st['dir']}")
        return 0
    print("idle")
    return 1


def spawn_process(mdir: Path) -> None:
    """Run post-processing detached so the hotkey returns immediately."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    out = LOG_FILE.open("a")
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "process", str(mdir)],
                     stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)


def cleanup_old_videos() -> None:
    if RETENTION_DAYS <= 0 or not MEETINGS.exists():
        return
    cutoff = time.time() - RETENTION_DAYS * 86400
    active = (load_state() or {}).get("dir")
    for d in MEETINGS.iterdir():
        if not d.is_dir() or str(d) == active:
            continue
        for name in ("recording.mp4", "recording.mkv"):
            f = d / name
            if f.exists() and f.stat().st_mtime < cutoff:
                f.unlink()
                log(f"deleted old video {f}")


# ---------------------------------------------------------------- transcription

def extract_track(video: Path, index: int, dest: Path) -> np.ndarray:
    import numpy as np
    import soundfile as sf
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
                    "-map", f"0:a:{index}", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le",
                    str(dest)], check=True)
    audio, sr = sf.read(dest, dtype="float32")
    assert sr == SR
    return np.asarray(audio, dtype=np.float32)


def vad_segments(audio) -> list[tuple[float, float, np.ndarray]]:
    import sherpa_onnx
    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = str(MODELS / "silero_vad.onnx")
    cfg.silero_vad.threshold = 0.5
    cfg.silero_vad.min_silence_duration = 0.6
    cfg.silero_vad.min_speech_duration = 0.25
    cfg.silero_vad.max_speech_duration = 25
    cfg.sample_rate = SR
    win = cfg.silero_vad.window_size
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=60)
    segs = []

    def drain():
        while not vad.empty():
            s = vad.front
            segs.append((s.start / SR, (s.start + len(s.samples)) / SR, s.samples))
            vad.pop()

    for i in range(0, len(audio), win):
        chunk = audio[i:i + win]
        if len(chunk) < win:
            break
        vad.accept_waveform(chunk)
        drain()
    vad.flush()
    drain()
    return segs


_recognizer = None


def recognizer():
    global _recognizer
    if _recognizer is None:
        import sherpa_onnx
        d = MODELS / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
        _recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(d / "encoder.int8.onnx"), decoder=str(d / "decoder.int8.onnx"),
            joiner=str(d / "joiner.int8.onnx"), tokens=str(d / "tokens.txt"),
            model_type="nemo_transducer", num_threads=ASR_THREADS,
        )
    return _recognizer


def transcribe(segs, speaker) -> list[dict]:
    rec = recognizer()
    out = []
    for i in range(0, len(segs), 8):
        batch = segs[i:i + 8]
        streams = []
        for _, _, samples in batch:
            s = rec.create_stream()
            s.accept_waveform(SR, samples)
            streams.append(s)
        rec.decode_streams(streams)
        for (start, end, _), s in zip(batch, streams, strict=True):
            text = s.result.text.strip()
            if text:
                out.append({"start": start, "end": end, "speaker": speaker, "text": text})
    return out


def diarize(audio) -> list[tuple[float, float, int]]:
    import sherpa_onnx
    cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(MODELS / "sherpa-onnx-pyannote-segmentation-3-0/model.onnx")),
            num_threads=4),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(MODELS / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"),
            num_threads=4),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=DIARIZATION_THRESHOLD),
        min_duration_on=0.3, min_duration_off=0.5,
    )
    if not cfg.validate():
        raise RuntimeError("invalid diarization config")
    sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)
    res = sd.process(audio).sort_by_start_time()
    return [(r.start, r.end, r.speaker) for r in res]


def label_others(utts: list[dict], turns: list[tuple[float, float, int]]) -> None:
    speakers = sorted({t[2] for t in turns})
    if len(speakers) < 2:
        return  # one remote voice: plain "Others"
    order = {}
    for t in turns:  # number speakers by first appearance
        order.setdefault(t[2], len(order) + 1)
    for u in utts:
        overlap: dict[int, float] = {}
        for s, e, spk in turns:
            o = min(e, u["end"]) - max(s, u["start"])
            if o > 0:
                overlap[spk] = overlap.get(spk, 0) + o
        if overlap:
            u["speaker"] = f"Others {order[max(overlap, key=overlap.get)]}"


def merge_turns(utts: list[dict], gap: float = 2.0) -> list[dict]:
    merged: list[dict] = []
    for u in sorted(utts, key=lambda u: u["start"]):
        if merged and merged[-1]["speaker"] == u["speaker"] and u["start"] - merged[-1]["end"] < gap:
            merged[-1]["text"] += " " + u["text"]
            merged[-1]["end"] = u["end"]
        else:
            merged.append(dict(u))
    return merged


def hms(t: float) -> str:
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def probe_duration(video: Path) -> float:
    try:
        return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(video)]))
    except Exception:
        return 0.0


# ---------------------------------------------------------------- summary

def llm(messages: list[dict], max_tokens: int = 1500) -> str:
    req = urllib.request.Request(
        f"{OLLAMA_URL}/chat/completions",
        data=json.dumps({"model": OLLAMA_MODEL, "messages": messages, "temperature": 0.2,
                         "max_tokens": max_tokens}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=900) as r:
        content = json.load(r)["choices"][0]["message"]["content"] or ""
    return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()


SYSTEM = (
    "You write concise, accurate meeting notes from a speaker-labelled transcript. "
    "'Me' is the person who recorded the meeting; 'Others' (or 'Others 1', 'Others 2', ...) "
    "are the remote participants. Speech recognition may contain errors; infer sensibly "
    "but never invent facts that are not in the transcript."
)

NOTES_FORMAT = """Write the notes in Markdown with exactly this structure:

# <A short, specific title for the meeting, 3 to 7 words>

## Summary
<2 to 4 sentences>

## Key points
- ...

## Decisions
- ... (write "- None recorded" if there were none)

## Action items
- [ ] **<Who>**: <what> (<deadline if mentioned>)
(write "- None recorded" if there were none)

Output only the Markdown."""

CHUNK_CHARS = 7000  # Ollama runs gemma with a 4k-token context; stay well inside it


def summarise(transcript_lines: list[str]) -> str:
    chunks, cur = [], ""
    for line in transcript_lines:
        if cur and len(cur) + len(line) > CHUNK_CHARS:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        chunks.append(cur)

    if len(chunks) == 1:
        material = "Transcript:\n\n" + chunks[0]
    else:
        partials = []
        for i, c in enumerate(chunks, 1):
            log(f"summarising part {i}/{len(chunks)}")
            partials.append(llm([
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content":
                    f"This is part {i} of {len(chunks)} of a meeting transcript. Extract, as terse "
                    "bullet points: topics discussed, facts and numbers mentioned, decisions, and "
                    "action items with who owns them.\n\n" + c},
            ], max_tokens=700))
        material = "Notes extracted from consecutive parts of the meeting:\n\n" + "\n\n".join(
            f"### Part {i}\n{p}" for i, p in enumerate(partials, 1))
        while len(material) > CHUNK_CHARS * 1.3:  # very long meeting: compress once more
            material = llm([{"role": "system", "content": SYSTEM}, {"role": "user", "content":
                "Condense these meeting notes, keeping every decision and action item:\n\n"
                + material[: int(CHUNK_CHARS * 1.3)]}], max_tokens=900) + material[int(CHUNK_CHARS * 1.3):]
    return llm([{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"{material}\n\n{NOTES_FORMAT}"}])


def slugify(title: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")
    return s[:60].strip("-") or "meeting"


# ---------------------------------------------------------------- process

def cmd_process(mdir: Path) -> int:
    t0 = time.time()
    try:
        mdir = process(mdir)
    except Exception:
        log(f"processing failed for {mdir}:\n{traceback.format_exc()}")
        notify("Hindsight: processing failed", f"See {LOG_FILE}", "critical")
        return 1
    log(f"processed {mdir} in {time.time() - t0:.0f}s")
    notify("✓ Meeting notes ready", str(mdir))
    return 0


def process(mdir: Path) -> Path:
    mkv, mp4 = mdir / "recording.mkv", mdir / "recording.mp4"
    if mkv.exists():
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(mkv),
                        "-map", "0", "-c", "copy", "-movflags", "+faststart", str(mp4)], check=True)
        mkv.unlink()
    if not mp4.exists():
        raise FileNotFoundError(mp4)
    (mdir / "ffmpeg.log").unlink(missing_ok=True)
    duration = probe_duration(mp4)
    log(f"processing {mdir} ({duration:.0f}s)")

    tmp = mdir / ".tmp"
    tmp.mkdir(exist_ok=True)
    try:
        me = extract_track(mp4, 0, tmp / "me.wav")
        others = extract_track(mp4, 1, tmp / "others.wav")
        utts = transcribe(vad_segments(me), "Me")
        other_utts = transcribe(vad_segments(others), "Others")
        if other_utts:
            try:
                label_others(other_utts, diarize(others))
            except Exception:
                log(f"diarization skipped:\n{traceback.format_exc()}")
        utts = merge_turns(utts + other_utts)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    started = mdir.name[:15]
    when = dt.datetime.strptime(started, "%Y-%m-%d_%H%M")
    lines = [f"**[{hms(u['start'])}] {u['speaker']}:** {u['text']}" for u in utts]
    speakers = sorted({u["speaker"] for u in utts})

    if lines:
        notes = summarise(lines)
        m = re.search(r"^#\s+(.+)$", notes, flags=re.M)
        title = m.group(1).strip().strip("*") if m else "Meeting"
    else:
        title = "Meeting (no speech detected)"
        notes = f"# {title}\n\nNo speech was detected in either audio track."

    final = MEETINGS / f"{started}_{slugify(title)}"
    if final != mdir:
        n = 2
        while final.exists():
            final = MEETINGS / f"{started}_{slugify(title)}-{n}"
            n += 1
        mdir.rename(final)
        mdir = final

    meta = (f"*{when:%A %d %B %Y, %H:%M} · {hms(duration)} · "
            f"Speakers: {', '.join(speakers) or 'none'} · "
            f"[Transcript](transcript.md) · [Recording](recording.mp4)*")
    notes = re.sub(r"^(#\s+.+)$", lambda m: m.group(1) + "\n\n" + meta, notes, count=1, flags=re.M)
    if meta not in notes:
        notes = meta + "\n\n" + notes
    (mdir / "notes.md").write_text(notes.rstrip() + "\n")
    (mdir / "transcript.md").write_text(
        f"# Transcript: {title}\n\n*{when:%A %d %B %Y, %H:%M} · {hms(duration)}*\n\n"
        + ("\n\n".join(lines) if lines else "_No speech detected._") + "\n")
    return mdir


# ---------------------------------------------------------------- main

def main(argv: list[str]) -> int:
    cmds = {"start": cmd_start, "stop": cmd_stop, "toggle": cmd_toggle, "status": cmd_status,
            "cleanup": lambda: (cleanup_old_videos(), 0)[1]}
    if len(argv) >= 1 and argv[0] in cmds and len(argv) == 1:
        return cmds[argv[0]]()
    if len(argv) == 2 and argv[0] == "process":
        return cmd_process(Path(argv[1]).expanduser().resolve())
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
