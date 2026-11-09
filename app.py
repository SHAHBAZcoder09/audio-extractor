from __future__ import annotations
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
from typing import Any

APP_TITLE = "Professional Audio Extractor"
SUPPORTED_VIDEO_TYPES = (
    "*.mp4", "*.mkv", "*.avi", "*.mov", "*.m4v", "*.ts",
    "*.webm", "*.flv", "*.wmv", "*.mpeg", "*.mpg",
)


def app_roots() -> list[Path]:
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    roots.append(Path(__file__).resolve().parent)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def find_tool(executable_name: str) -> str | None:
    for root in app_roots():
        for candidate in (root / "tools" / executable_name, root / executable_name):
            if candidate.is_file():
                return str(candidate)
    return shutil.which(executable_name)


def resolve_ffmpeg() -> str | None:
    return find_tool("ffmpeg.exe" if os.name == "nt" else "ffmpeg")


def resolve_ffprobe() -> str | None:
    return find_tool("ffprobe.exe" if os.name == "nt" else "ffprobe")


def hidden_subprocess_options() -> dict[str, Any]:
    options: dict[str, Any] = {}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        options["startupinfo"] = startupinfo
    return options


def format_time(seconds: float) -> str:
    if seconds < 0 or not seconds == seconds:
        seconds = 0
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def human_bitrate(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "Unknown bitrate"
    if number <= 0:
        return "Unknown bitrate"
    return f"{number / 1000:.0f} kb/s"


def stream_description(stream: dict[str, Any], number: int) -> str:
    codec = stream.get("codec_name") or "Unknown codec"
    language = (stream.get("tags") or {}).get("language", "und")
    title = (stream.get("tags") or {}).get("title")
    channels = stream.get("channels")
    channel_layout = stream.get("channel_layout")
    channel_text = channel_layout or (f"{channels} ch" if channels else "Unknown channels")
    sample_rate = stream.get("sample_rate")
    sample_text = (
        f"{int(sample_rate) / 1000:g} kHz"
        if str(sample_rate or "").isdigit()
        else "Unknown sample rate"
    )
    bitrate = human_bitrate(stream.get("bit_rate"))
    language_text = str(language).upper() if language and language != "und" else "Language unknown"
    title_text = f" | {title}" if title else ""
    return (
        f"Track {number + 1} | {codec} | {channel_text} | {sample_text} | "
        f"{language_text} | {bitrate}{title_text}"
    )


class AudioExtractorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("760x625")
        self.root.minsize(700, 590)

        self.video_path_var = tk.StringVar()
        self.save_path_var = tk.StringVar()
        self.track_var = tk.StringVar()
        self.format_var = tk.StringVar(value="MP3 (.mp3)")
        self.status_var = tk.StringVar(value="Ready")
        self.progress_text_var = tk.StringVar(value="0%")
        self.elapsed_var = tk.StringVar(value="Elapsed: 00:00")
        self.track_info_var = tk.StringVar(value="Select a video to inspect its audio tracks.")

        self.audio_streams: list[dict[str, Any]] = []
        self.track_display_to_stream: dict[str, dict[str, Any]] = {}
        self.total_duration: float | None = None
        self.ffmpeg_exe: str | None = None
        self.ffprobe_exe: str | None = None

        self.messages: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.current_process: subprocess.Popen[str] | None = None
        self.process_lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.is_running = False
        self.is_closing = False

        self._build_ui()
        self._refresh_tool_status()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self._poll_messages)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        ttk.Label(outer, text="Video File:", font=("Arial", 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 5)
        )

        video_frame = ttk.Frame(outer)
        video_frame.grid(row=1, column=0, sticky="ew")
        video_frame.columnconfigure(0, weight=1)
        ttk.Entry(video_frame, textvariable=self.video_path_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(video_frame, text="Browse...", command=self.browse_video).grid(
            row=0, column=1, padx=(8, 0)
        )

        ttk.Label(outer, text="Audio Track:", font=("Arial", 10, "bold")).grid(
            row=2, column=0, sticky="w", pady=(14, 5)
        )
        self.track_dropdown = ttk.Combobox(outer, textvariable=self.track_var, state="readonly")
        self.track_dropdown.grid(row=3, column=0, sticky="ew")
        self.track_dropdown.bind("<<ComboboxSelected>>", self.on_track_selected)
        ttk.Label(outer, textvariable=self.track_info_var, wraplength=710, justify="left").grid(
            row=4, column=0, sticky="w", pady=(5, 0)
        )

        ttk.Label(outer, text="Output Format:", font=("Arial", 10, "bold")).grid(
            row=5, column=0, sticky="w", pady=(14, 5)
        )
        self.format_dropdown = ttk.Combobox(
            outer,
            textvariable=self.format_var,
            state="readonly",
            values=[
                "MP3 (.mp3)",
                "WAV (.wav)",
                "AAC (.m4a)",
                "FLAC (.flac)",
                "Original Track Format (Lossless Copy)",
            ],
        )
        self.format_dropdown.grid(row=6, column=0, sticky="ew")
        self.format_dropdown.bind("<<ComboboxSelected>>", self.update_default_save_extension)

        ttk.Label(outer, text="Save Destination:", font=("Arial", 10, "bold")).grid(
            row=7, column=0, sticky="w", pady=(14, 5)
        )
        save_frame = ttk.Frame(outer)
        save_frame.grid(row=8, column=0, sticky="ew")
        save_frame.columnconfigure(0, weight=1)
        ttk.Entry(save_frame, textvariable=self.save_path_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(save_frame, text="Save As...", command=self.browse_save).grid(
            row=0, column=1, padx=(8, 0)
        )

        action_frame = ttk.Frame(outer)
        action_frame.grid(row=9, column=0, sticky="ew", pady=(20, 12))
        action_frame.columnconfigure(0, weight=1)
        self.extract_btn = ttk.Button(
            action_frame, text="Start Extraction", command=self.start_extraction_thread
        )
        self.extract_btn.grid(row=0, column=0, sticky="ew")
        self.cancel_btn = ttk.Button(
            action_frame, text="Cancel", command=self.cancel_extraction, state="disabled"
        )
        self.cancel_btn.grid(row=0, column=1, padx=(8, 0))

        progress_frame = ttk.Frame(outer)
        progress_frame.grid(row=10, column=0, sticky="ew")
        progress_frame.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(
            progress_frame, orient="horizontal", mode="determinate", maximum=100
        )
        self.progress.grid(row=0, column=0, sticky="ew")
        ttk.Label(progress_frame, textvariable=self.progress_text_var, width=6, anchor="e").grid(
            row=0, column=1, padx=(8, 0)
        )

        info_frame = ttk.Frame(outer)
        info_frame.grid(row=11, column=0, sticky="ew", pady=(8, 0))
        info_frame.columnconfigure(0, weight=1)
        ttk.Label(info_frame, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Label(info_frame, textvariable=self.elapsed_var).grid(row=0, column=1, sticky="e")

        ttk.Separator(outer).grid(row=12, column=0, sticky="ew", pady=(15, 8))
        self.tools_status_label = ttk.Label(outer, text="", wraplength=710, justify="left")
        self.tools_status_label.grid(row=13, column=0, sticky="w")

    def _refresh_tool_status(self) -> None:
        self.ffmpeg_exe = resolve_ffmpeg()
        self.ffprobe_exe = resolve_ffprobe()
        if self.ffmpeg_exe and self.ffprobe_exe:
            text = "Media tools: FFmpeg and FFprobe are available."
        elif self.ffmpeg_exe:
            text = (
                "Media tools: FFmpeg is available. FFprobe is missing; "
                "limited stream detection will be used."
            )
        else:
            text = (
                "Media tools: FFmpeg is missing. Add ffmpeg.exe and ffprobe.exe "
                "to the application's tools folder."
            )
        self.tools_status_label.config(text=text)

    def _set_busy(self, busy: bool) -> None:
        self.is_running = busy
        self.extract_btn.config(state="disabled" if busy else "normal")
        self.cancel_btn.config(state="normal" if busy else "disabled")
        self.track_dropdown.config(state="disabled" if busy else "readonly")
        self.format_dropdown.config(state="disabled" if busy else "readonly")

    def browse_video(self) -> None:
        if self.is_running:
            return
        path = filedialog.askopenfilename(
            title="Select Video File",
            filetypes=[("Video Files", " ".join(SUPPORTED_VIDEO_TYPES)), ("All Files", "*.*")],
        )
        if path:
            self.video_path_var.set(path)
            self.load_streams(path)

    def load_streams(self, video_path: str) -> None:
        self._refresh_tool_status()
        if not self.ffmpeg_exe:
            messagebox.showerror(
                "FFmpeg Not Found",
                "FFmpeg could not be located. Put ffmpeg.exe in the application's tools folder.",
            )
            return

        self.status_var.set("Inspecting video and audio streams...")
        self.track_info_var.set("Reading audio stream metadata...")
        self.track_dropdown["values"] = []
        self.track_var.set("")
        self.audio_streams.clear()
        self.track_display_to_stream.clear()

        try:
            streams, duration, used_probe = self._probe_media(video_path)
            self.audio_streams = streams
            self.total_duration = duration

            if not streams:
                self.status_var.set("No audio tracks found")
                self.track_info_var.set(
                    "This video does not contain a detectable audio stream."
                )
                messagebox.showerror("No Audio Tracks", "No audio tracks were found in this video.")
                return

            labels: list[str] = []
            for audio_number, stream in enumerate(streams):
                label = stream_description(stream, audio_number)
                labels.append(label)
                self.track_display_to_stream[label] = stream

            self.track_dropdown["values"] = labels
            self.track_dropdown.current(0)
            self.on_track_selected()
            self.status_var.set(
                f"Found {len(streams)} audio track(s)."
                + ("" if used_probe else " Limited metadata mode.")
            )
            self.update_default_save_extension()
        except Exception as exc:
            self.status_var.set("Video inspection failed")
            messagebox.showerror("Video Inspection Failed", str(exc))

    def _probe_media(self, video_path: str) -> tuple[list[dict[str, Any]], float | None, bool]:
        if self.ffprobe_exe:
            cmd = [
                self.ffprobe_exe,
                "-v", "error",
                "-show_entries",
                "format=duration:stream=index,codec_name,codec_long_name,profile,codec_type,"
                "sample_rate,channels,channel_layout,bit_rate,duration:"
                "stream_tags=language,title",
                "-of", "json",
                video_path,
            ]
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                **hidden_subprocess_options(),
            )
            if result.returncode != 0:
                raise RuntimeError(
                    (result.stderr or "FFprobe could not inspect this file.").strip()
                )
            data = json.loads(result.stdout or "{}")
            streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
            duration = self._safe_float((data.get("format") or {}).get("duration"))
            return streams, duration, True

        cmd = [self.ffmpeg_exe or "ffmpeg", "-hide_banner", "-i", video_path]
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            **hidden_subprocess_options(),
        )
        text_output = result.stderr or ""
        duration = None
        duration_match = re.search(r"Duration:\s*(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)", text_output)
        if duration_match:
            hours, minutes, seconds = duration_match.groups()
            duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)

        streams: list[dict[str, Any]] = []
        for line in text_output.splitlines():
            if "Audio:" not in line:
                continue
            index_match = re.search(r"Stream #\d+:(\d+)(?:\[[^]]+\])?(?:\([^)]*\))?:\s*Audio:", line)
            if not index_match:
                continue
            codec_part = line.split("Audio:", 1)[1].strip()
            codec = codec_part.split(",", 1)[0].strip().split()[0]
            sample_match = re.search(r"(\d{4,6})\s*Hz", codec_part)
            channel_match = re.search(r"(mono|stereo|\d+\.\d+(?:\(.*?\))?)", codec_part)
            bitrate_match = re.search(r"(\d+)\s*kb/s", codec_part)
            streams.append(
                {
                    "index": int(index_match.group(1)),
                    "codec_name": codec,
                    "codec_type": "audio",
                    "sample_rate": sample_match.group(1) if sample_match else None,
                    "channels": (
                        1
                        if channel_match and channel_match.group(1) == "mono"
                        else (2 if channel_match and channel_match.group(1) == "stereo" else None)
                    ),
                    "channel_layout": channel_match.group(1) if channel_match else None,
                    "bit_rate": int(bitrate_match.group(1)) * 1000 if bitrate_match else None,
                    "tags": {},
                }
            )
        return streams, duration, False

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            parsed = float(value)
            return parsed if parsed > 0 else None
        except (TypeError, ValueError):
            return None

    def on_track_selected(self, event: Any = None) -> None:
        stream = self.track_display_to_stream.get(self.track_var.get())
        if not stream:
            return
        codec = stream.get("codec_name", "Unknown")
        sample_rate = stream.get("sample_rate") or "Unknown"
        channels = stream.get("channels") or "Unknown"
        layout = stream.get("channel_layout") or "Unknown layout"
        language = (stream.get("tags") or {}).get("language", "Unknown")
        title = (stream.get("tags") or {}).get("title", "No title")
        bitrate = human_bitrate(stream.get("bit_rate"))
        profile = stream.get("profile") or "Unknown profile"
        self.track_info_var.set(
            f"Codec: {codec} ({profile})  |  Language: {language}  |  "
            f"Channels: {channels} ({layout})  |  Sample rate: {sample_rate} Hz  |  "
            f"Bitrate: {bitrate}  |  Title: {title}"
        )
        if "Original Track Format" in self.format_var.get():
            self.update_default_save_extension()

    def _original_extension(self) -> str:
        stream = self.track_display_to_stream.get(self.track_var.get(), {})
        codec = str(stream.get("codec_name", "")).lower()
        extension_map = {
            "mp3": ".mp3",
            "aac": ".m4a",
            "ac3": ".ac3",
            "eac3": ".eac3",
            "dts": ".dts",
            "flac": ".flac",
            "opus": ".opus",
            "vorbis": ".ogg",
            "alac": ".m4a",
            "wavpack": ".wv",
            "truehd": ".thd",
        }
        return extension_map.get(codec, ".mka")

    def update_default_save_extension(self, event: Any = None) -> None:
        video = self.video_path_var.get()
        if not video:
            return
        base = os.path.splitext(video)[0]
        fmt = self.format_var.get()
        if fmt.startswith("MP3"):
            extension = ".mp3"
        elif fmt.startswith("WAV"):
            extension = ".wav"
        elif fmt.startswith("AAC"):
            extension = ".m4a"
        elif fmt.startswith("FLAC"):
            extension = ".flac"
        else:
            extension = self._original_extension()
        self.save_path_var.set(base + "_extracted" + extension)

    def browse_save(self) -> None:
        fmt = self.format_var.get()
        if fmt.startswith("MP3"):
            default_extension, filetypes = ".mp3", [("MP3 Audio", "*.mp3")]
        elif fmt.startswith("WAV"):
            default_extension, filetypes = ".wav", [("WAV Audio", "*.wav")]
        elif fmt.startswith("AAC"):
            default_extension, filetypes = ".m4a", [("AAC Audio", "*.m4a")]
        elif fmt.startswith("FLAC"):
            default_extension, filetypes = ".flac", [("FLAC Audio", "*.flac")]
        else:
            default_extension, filetypes = self._original_extension(), [("Audio Files", "*.*")]

        path = filedialog.asksaveasfilename(
            title="Save Audio As",
            defaultextension=default_extension,
            filetypes=filetypes,
        )
        if path:
            self.save_path_var.set(path)

    def start_extraction_thread(self) -> None:
        if self.is_running:
            return
        video_path = self.video_path_var.get().strip()
        output_path = self.save_path_var.get().strip()
        selected_label = self.track_var.get()
        stream = self.track_display_to_stream.get(selected_label)

        if not video_path or not os.path.isfile(video_path):
            messagebox.showerror("Video Required", "Please select an existing video file.")
            return
        if not output_path:
            messagebox.showerror("Destination Required", "Please select an output destination.")
            return
        if not stream:
            messagebox.showerror("Audio Track Required", "Please select an audio track.")
            return
        if not self.ffmpeg_exe:
            self._refresh_tool_status()
        if not self.ffmpeg_exe:
            messagebox.showerror(
                "FFmpeg Not Found",
                "FFmpeg is missing. Add ffmpeg.exe to the application's tools folder.",
            )
            return

        output_parent = os.path.dirname(os.path.abspath(output_path))
        if not os.path.isdir(output_parent):
            messagebox.showerror(
                "Destination Not Found", "The selected output folder does not exist."
            )
            return
        if os.path.abspath(video_path) == os.path.abspath(output_path):
            messagebox.showerror(
                "Invalid Destination", "The output file cannot be the same as the source video."
            )
            return
        if os.path.exists(output_path):
            overwrite = messagebox.askyesno(
                "Confirm Overwrite",
                f"This file already exists:\n\n{output_path}\n\nDo you want to replace it?",
            )
            if not overwrite:
                return

        job = {
            "video_path": video_path,
            "output_path": output_path,
            "stream_index": int(stream.get("index", 0)),
            "format": self.format_var.get(),
            "duration": self.total_duration,
            "ffmpeg": self.ffmpeg_exe,
        }
        self.cancel_event.clear()
        self.progress.configure(mode="determinate")
        self.progress["value"] = 0
        self.progress_text_var.set("0%")
        self.elapsed_var.set("Elapsed: 00:00")
        self.status_var.set("Starting extraction...")
        self._set_busy(True)
        self.worker_thread = threading.Thread(
            target=self.run_extraction, args=(job,), daemon=True
        )
        self.worker_thread.start()

    def run_extraction(self, job: dict[str, Any]) -> None:
        output_path = job["output_path"]
        output_file = Path(output_path)
        temporary_path = str(
            output_file.with_name(output_file.stem + ".partial" + output_file.suffix)
        )
        try:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)

            cmd = [
                job["ffmpeg"], "-hide_banner", "-nostats", "-nostdin",
                "-progress", "pipe:1", "-i", job["video_path"],
                "-map", f"0:{job['stream_index']}", "-vn", "-sn", "-dn",
            ]
            fmt = job["format"]
            if fmt.startswith("MP3"):
                cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k"])
            elif fmt.startswith("WAV"):
                cmd.extend(["-c:a", "pcm_s16le"])
            elif fmt.startswith("AAC"):
                cmd.extend(["-c:a", "aac", "-b:a", "192k"])
            elif fmt.startswith("FLAC"):
                cmd.extend(["-c:a", "flac"])
            else:
                cmd.extend(["-c:a", "copy"])
            cmd.extend(["-n", temporary_path])

            process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **hidden_subprocess_options(),
            )
            with self.process_lock:
                self.current_process = process

            stderr_lines: list[str] = []
            stderr_lock = threading.Lock()

            def collect_stderr() -> None:
                if process.stderr is None:
                    return
                for line in process.stderr:
                    with stderr_lock:
                        stderr_lines.append(line.rstrip())

            stderr_thread = threading.Thread(target=collect_stderr, daemon=True)
            stderr_thread.start()
            start_time = time.monotonic()
            last_seconds = 0.0

            if process.stdout is not None:
                for raw_line in process.stdout:
                    if self.cancel_event.is_set():
                        break
                    line = raw_line.strip()
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    if key in ("out_time_us", "out_time_ms"):
                        try:
                            last_seconds = max(0.0, int(value) / 1_000_000)
                        except ValueError:
                            pass
                        duration = job.get("duration")
                        percent = (
                            min(99.9, (last_seconds / duration) * 100)
                            if duration and duration > 0
                            else None
                        )
                        elapsed = time.monotonic() - start_time
                        self.messages.put(("progress", percent, last_seconds, elapsed))
                    elif key == "progress" and value == "end":
                        self.messages.put(
                            ("progress", 100.0, last_seconds, time.monotonic() - start_time)
                        )

            if self.cancel_event.is_set():
                try:
                    process.terminate()
                except OSError:
                    pass
            return_code = process.wait()
            stderr_thread.join(timeout=2)
            with self.process_lock:
                self.current_process = None

            with stderr_lock:
                error_text = "\n".join(stderr_lines)

            if self.cancel_event.is_set():
                if os.path.exists(temporary_path):
                    os.remove(temporary_path)
                self.messages.put(("done", "cancelled", "Extraction was cancelled."))
                return

            if return_code == 0 and os.path.isfile(temporary_path):
                os.replace(temporary_path, output_path)
                self.messages.put(("done", "success", output_path))
            else:
                if os.path.exists(temporary_path):
                    os.remove(temporary_path)
                message = self._extract_error_message(error_text, return_code)
                self.messages.put(("done", "failure", message))

        except Exception as exc:
            with self.process_lock:
                process = self.current_process
                self.current_process = None
            if process and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
            if os.path.exists(temporary_path):
                try:
                    os.remove(temporary_path)
                except OSError:
                    pass
            self.messages.put(("done", "failure", str(exc)))

    @staticmethod
    def _extract_error_message(error_text: str, return_code: int) -> str:
        lines = error_text.splitlines()
        useful = [
            line
            for line in lines
            if any(
                word in line.lower()
                for word in ("error", "invalid", "unable", "unknown", "failed", "not found")
            )
        ]
        details = "\n".join(useful[-8:]) if useful else "\n".join(lines[-12:])
        return (
            f"FFmpeg exited with code {return_code}.\n\n"
            f"{details or 'No diagnostic details were returned.'}"
        )

    def cancel_extraction(self) -> None:
        if not self.is_running:
            return
        self.cancel_event.set()
        self.cancel_btn.config(state="disabled")
        self.status_var.set("Cancelling extraction...")
        with self.process_lock:
            process = self.current_process
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def _poll_messages(self) -> None:
        try:
            while True:
                message = self.messages.get_nowait()
                if message[0] == "progress":
                    _, percent, processed_seconds, elapsed = message
                    if percent is None:
                        self.status_var.set(f"Processed {format_time(processed_seconds)}")
                    else:
                        shown = max(0.0, min(100.0, float(percent)))
                        self.progress["value"] = shown
                        self.progress_text_var.set(f"{shown:.0f}%")
                        self.status_var.set(f"Processing audio: {format_time(processed_seconds)}")
                    self.elapsed_var.set(f"Elapsed: {format_time(elapsed)}")
                elif message[0] == "done":
                    _, result, detail = message
                    self._set_busy(False)
                    self.current_process = None
                    if result == "success":
                        self.progress["value"] = 100
                        self.progress_text_var.set("100%")
                        self.status_var.set("Extraction completed successfully.")
                        messagebox.showinfo(
                            "Extraction Complete",
                            f"Audio successfully extracted.\n\nSaved to:\n{detail}",
                        )
                    elif result == "cancelled":
                        self.status_var.set("Extraction cancelled.")
                        messagebox.showinfo("Extraction Cancelled", detail)
                    else:
                        self.status_var.set("Extraction failed.")
                        messagebox.showerror("Extraction Failed", detail)
                    if self.is_closing:
                        self.root.destroy()
                        return
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(100, self._poll_messages)

    def on_close(self) -> None:
        if self.is_running:
            should_close = messagebox.askyesno(
                "Extraction Running",
                "An extraction is in progress. Cancel it and close the application?",
            )
            if not should_close:
                return
            self.is_closing = True
            self.cancel_extraction()
            return
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    app = AudioExtractorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()