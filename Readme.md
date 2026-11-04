# Professional Audio Extractor

A lightweight, portable desktop application built with Python and Tkinter to extract audio tracks from video files with zero quality loss.

## Features
- Clean graphical user interface (GUI).
- Automatically detects and lists all audio tracks in a video.
- Supports lossless stream copying for formats like AC3, EAC3, DTS, FLAC, and Opus.
- Option to convert audio into MP3, WAV, AAC, or FLAC.
- Zero installations required (portable app).

## How to Build
To build the standalone `.exe` yourself:
```bash
pip install imageio-ffmpeg pyinstaller
pyinstaller --onefile --noconsole app.py