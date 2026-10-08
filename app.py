import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

import gradio as gr

# ----------------------------- settings -----------------------------
REPO_URL = "https://github.com/kamilstanuch/AutoCrop-Vertical.git"
REPO_DIR = os.path.abspath("AutoCrop-Vertical")
MAX_MINUTES = 10          # reject longer videos (protects free CPU hardware)
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")   # optional, set as a Space secret
APP_USER = os.environ.get("APP_USER", "khan")

RATIOS = {
    "9:16 (Shorts / Reels / TikTok)": "9:16",
    "4:5 (Instagram portrait)": "4:5",
    "3:4": "3:4",
    "2:3": "2:3",
    "1:1 (Square)": "1:1",
    "16:9 (Landscape)": "16:9",
    "21:9 (Ultrawide)": "21:9",
    "Custom": None,
}


# ----------------------------- bootstrap -----------------------------
def bootstrap():
    """Fetch AutoCrop-Vertical and install its dependencies on first start."""
    if not os.path.isdir(REPO_DIR):
        subprocess.run(["git", "clone", "--depth", "1", REPO_URL, REPO_DIR], check=True)
    marker = os.path.join(REPO_DIR, ".deps_installed")
    if not os.path.exists(marker):
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r",
             os.path.join(REPO_DIR, "requirements.txt")],
            check=True,
        )
        open(marker, "w").close()
    # Optional JS runtime that helps yt-dlp with YouTube (non-fatal if it fails)
    if not shutil.which("deno"):
        try:
            subprocess.run("curl -fsSL https://deno.land/install.sh | sh -s -- -y",
                           shell=True, check=False, timeout=120,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os.environ["PATH"] += os.pathsep + os.path.expanduser("~/.deno/bin")
        except Exception:
            pass


bootstrap()


# ----------------------------- helpers -----------------------------
def video_minutes(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip()
        return float(out) / 60
    except Exception:
        return None


def download_youtube(url, workdir, max_height, cookies_file, log):
    outtmpl = os.path.join(workdir, "input.%(ext)s")
    cmd = ["yt-dlp", "--no-playlist", "--merge-output-format", "mp4",
           "-f", f"bv*[height<={max_height}]+ba/b[height<={max_height}]",
           "-o", outtmpl]
    if shutil.which("deno"):
        cmd += ["--js-runtimes", "deno"]
    if cookies_file:
        cmd += ["--cookies", cookies_file]
    cmd.append(url)
    res = subprocess.run(cmd, capture_output=True, text=True)
    log.append(res.stdout[-1500:] + res.stderr[-1500:])
    for name in os.listdir(workdir):
        if name.startswith("input."):
            return os.path.join(workdir, name)
    raise gr.Error(
        "Could not download that link. YouTube often blocks cloud servers "
        "('confirm you're not a bot'). Try uploading a cookies.txt below, or "
        "download the video yourself and upload the file instead."
    )


# ----------------------------- main job -----------------------------
def process(video_file, link, ratio_choice, custom_ratio, quality, max_height,
            cookies_file, progress=gr.Progress()):
    log = []
    workdir = tempfile.mkdtemp(prefix="khan_")

    ratio = RATIOS.get(ratio_choice) or (custom_ratio or "").strip()
    if not re.fullmatch(r"\d+:\d+", ratio):
        raise gr.Error("Invalid ratio. Use the form W:H, for example 9:16.")

    progress(0.05, desc="Getting your video...")
    if video_file:
        src = os.path.join(workdir, "input" + os.path.splitext(video_file)[1])
        shutil.copy(video_file, src)
    elif link and link.strip():
        src = download_youtube(link.strip(), workdir, max_height, cookies_file, log)
    else:
        raise gr.Error("Upload a video or paste a link first.")

    mins = video_minutes(src)
    if mins and mins > MAX_MINUTES:
        raise gr.Error(f"Video is {mins:.1f} min. The limit here is {MAX_MINUTES} min.")

    out_path = os.path.join(workdir, f"KhanCutShorts_{uuid.uuid4().hex[:6]}.mp4")
    cmd = [sys.executable, "main.py", "-i", src, "-o", out_path,
           "--ratio", ratio, "--quality", quality]

    progress(0.2, desc="Detecting subjects and cropping (this can take a while)...")
    res = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True)
    log.append(res.stdout[-3000:] + res.stderr[-3000:])

    if res.returncode != 0 or not os.path.exists(out_path):
        raise gr.Error("Conversion failed:\n" + (res.stderr[-1200:] or res.stdout[-1200:]))

    progress(1.0, desc="Done")
    return out_path, out_path, "\n".join(log)


# ----------------------------- UI -----------------------------
with gr.Blocks(title="KhanCutShorts") as demo:
    gr.Markdown("# ✂️ KhanCutShorts\nUpload a video **or** paste a link, choose a ratio, then convert.")

    with gr.Row():
        with gr.Column():
            video_in = gr.File(label="Upload a video", file_types=["video"], type="filepath")
            link_in = gr.Textbox(label="...or paste a YouTube / video link",
                                 placeholder="https://www.youtube.com/watch?v=...")
            ratio_in = gr.Dropdown(list(RATIOS.keys()), value="9:16 (Shorts / Reels / TikTok)",
                                   label="Ratio")
            custom_in = gr.Textbox(label="Custom ratio (only if 'Custom' is selected)",
                                   placeholder="5:7")
            quality_in = gr.Dropdown(["fast", "balanced", "high"], value="balanced",
                                     label="Quality")
            with gr.Accordion("Link download options", open=False):
                height_in = gr.Dropdown(["720", "1080", "1440", "2160"], value="1080",
                                        label="Max download height")
                cookies_in = gr.File(label="cookies.txt (optional, helps with YouTube blocks)",
                                     type="filepath")
            btn = gr.Button("Convert", variant="primary")
        with gr.Column():
            video_out = gr.Video(label="Result")
            file_out = gr.File(label="Download")
            log_out = gr.Textbox(label="Log", lines=8)

    btn.click(process,
              inputs=[video_in, link_in, ratio_in, custom_in, quality_in, height_in, cookies_in],
              outputs=[video_out, file_out, log_out])

if __name__ == "__main__":
    auth = (APP_USER, APP_PASSWORD) if APP_PASSWORD else None
    demo.queue(max_size=5).launch(auth=auth)
