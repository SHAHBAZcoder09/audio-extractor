import os
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import imageio_ffmpeg

class AudioExtractorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Professional Audio Extractor")
        self.root.geometry("580x450")
        self.root.resizable(False, False)

        self.video_path_var = tk.StringVar()
        self.save_path_var = tk.StringVar()
        self.streams_list = []
        self.track_var = tk.StringVar()
        self.format_var = tk.StringVar(value="MP3 (.mp3)")

        tk.Label(root, text="Video File:", font=("Arial", 10, "bold")).pack(anchor="w", padx=20, pady=(15, 5))
        
        vid_frame = tk.Frame(root)
        vid_frame.pack(fill="x", padx=20)
        self.vid_entry = tk.Entry(vid_frame, textvariable=self.video_path_var, width=50, font=("Arial", 10))
        self.vid_entry.pack(side="left", fill="x", expand=True)
        tk.Button(vid_frame, text="Browse", command=self.browse_video, width=10).pack(side="right", padx=(10, 0))

        tk.Label(root, text="Select Audio Track:", font=("Arial", 10, "bold")).pack(anchor="w", padx=20, pady=(15, 5))
        self.track_dropdown = ttk.Combobox(root, textvariable=self.track_var, state="readonly", width=68)
        self.track_dropdown.pack(padx=20, fill="x")

        tk.Label(root, text="Output Format:", font=("Arial", 10, "bold")).pack(anchor="w", padx=20, pady=(15, 5))
        self.format_dropdown = ttk.Combobox(root, textvariable=self.format_var, state="readonly", width=68, values=[
            "MP3 (.mp3)",
            "WAV (.wav)",
            "AAC (.m4a)",
            "FLAC (.flac)",
            "Original Track Format (Lossless Copy)"
        ])
        self.format_dropdown.pack(padx=20, fill="x")
        self.format_dropdown.bind("<<ComboboxSelected>>", self.update_default_save_extension)

        tk.Label(root, text="Save Destination:", font=("Arial", 10, "bold")).pack(anchor="w", padx=20, pady=(15, 5))
        
        save_frame = tk.Frame(root)
        save_frame.pack(fill="x", padx=20)
        self.save_entry = tk.Entry(save_frame, textvariable=self.save_path_var, width=50, font=("Arial", 10))
        self.save_entry.pack(side="left", fill="x", expand=True)
        tk.Button(save_frame, text="Save As", command=self.browse_save, width=10).pack(side="right", padx=(10, 0))

        self.extract_btn = tk.Button(root, text="Start Extraction", command=self.start_extraction_thread, bg="#4CAF50", fg="white", font=("Arial", 11, "bold"), height=2)
        self.extract_btn.pack(fill="x", padx=20, pady=25)

        self.progress = ttk.Progressbar(root, orient="horizontal", mode="indeterminate")
        self.progress.pack(fill="x", padx=20, pady=(0, 15))

    def browse_video(self):
        path = filedialog.askopenfilename(
            title="Select Video File",
            filetypes=[("Video Files", "*.mp4 *.mkv *.avi *.mov *.m4v *.ts"), ("All Files", "*.*")]
        )
        if path:
            self.video_path_var.set(path)
            self.load_streams(path)

    def load_streams(self, video_path):
        try:
            cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-i", video_path]
            result = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            
            self.streams_list = []
            for line in result.stderr.splitlines():
                if "Audio:" in line:
                    self.streams_list.append(line.strip())
            
            if self.streams_list:
                formatted_streams = [f"Track {i}: {s}" for i, s in enumerate(self.streams_list)]
                self.track_dropdown['values'] = formatted_streams
                self.track_dropdown.current(0)
                self.update_default_save_extension()
            else:
                messagebox.showerror("Error", "No audio tracks found in this video!")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read video: {e}")

    def update_default_save_extension(self, event=None):
        vid = self.video_path_var.get()
        if not vid:
            return
        base = os.path.splitext(vid)[0]
        fmt = self.format_var.get()
        if "MP3" in fmt:
            self.save_path_var.set(base + "_extracted.mp3")
        elif "WAV" in fmt:
            self.save_path_var.set(base + "_extracted.wav")
        elif "AAC" in fmt:
            self.save_path_var.set(base + "_extracted.m4a")
        elif "FLAC" in fmt:
            self.save_path_var.set(base + "_extracted.flac")
        else:
            selected_text = self.track_dropdown.get().lower()
            if "e-ac-3" in selected_text or "eac3" in selected_text:
                ext = ".eac3"
            elif "ac3" in selected_text or "dolby" in selected_text:
                ext = ".ac3"
            elif "dts" in selected_text:
                ext = ".dts"
            elif "flac" in selected_text:
                ext = ".flac"
            elif "opus" in selected_text:
                ext = ".opus"
            elif "aac" in selected_text:
                ext = ".m4a"
            else:
                ext = ".mka"
            self.save_path_var.set(base + "_extracted" + ext)

    def browse_save(self):
        fmt = self.format_var.get()
        if "MP3" in fmt:
            def_ext = ".mp3"
            ft = [("MP3 Audio", "*.mp3")]
        elif "WAV" in fmt:
            def_ext = ".wav"
            ft = [("WAV Audio", "*.wav")]
        elif "AAC" in fmt:
            def_ext = ".m4a"
            ft = [("AAC Audio", "*.m4a")]
        elif "FLAC" in fmt:
            def_ext = ".flac"
            ft = [("FLAC Audio", "*.flac")]
        else:
            def_ext = ".mka"
            ft = [("Audio Files", "*.*")]

        path = filedialog.asksaveasfilename(
            title="Save Audio As",
            defaultextension=def_ext,
            filetypes=ft
        )
        if path:
            self.save_path_var.set(path)

    def start_extraction_thread(self):
        if not self.video_path_var.get() or not self.save_path_var.get():
            messagebox.showerror("Error", "Please select a video file and save destination.")
            return
        if not self.track_dropdown.get():
            messagebox.showerror("Error", "Please select an audio track.")
            return

        self.extract_btn.config(state="disabled")
        self.progress.start(10)

        thread = threading.Thread(target=self.run_extraction)
        thread.daemon = True
        thread.start()

    def run_extraction(self):
        video_path = self.video_path_var.get()
        output_path = self.save_path_var.get()
        track_text = self.track_dropdown.get()
        track_index = int(track_text.split(":")[0].replace("Track", "").strip())
        fmt = self.format_var.get()

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [ffmpeg_exe, "-y", "-i", video_path, "-map", f"0:a:{track_index}", "-vn"]

        if "MP3" in fmt:
            cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k"])
        elif "WAV" in fmt:
            cmd.extend(["-c:a", "pcm_s16le"])
        elif "AAC" in fmt:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])
        elif "FLAC" in fmt:
            cmd.extend(["-c:a", "flac"])
        else:
            cmd.extend(["-c:a", "copy"])

        cmd.append(output_path)

        try:
            process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.root.after(0, lambda: self.extraction_complete(process.returncode == 0, process.stderr, output_path))
        except Exception as ex:
            self.root.after(0, lambda: self.extraction_complete(False, str(ex), output_path))

    def extraction_complete(self, success, err_output, output_path):
        self.progress.stop()
        self.extract_btn.config(state="normal")
        if success:
            messagebox.showinfo("Success", f"Audio successfully extracted!\nSaved to:\n{output_path}")
        else:
            err_lines = [line for line in err_output.splitlines() if "error" in line.lower() or "invalid" in line.lower()]
            err_msg = "\n".join(err_lines) if err_lines else err_output[-300:]
            messagebox.showerror("Extraction Failed", f"FFmpeg returned an error:\n{err_msg}")

if __name__ == "__main__":
    root = tk.Tk()
    app = AudioExtractorApp(root)
    root.mainloop()