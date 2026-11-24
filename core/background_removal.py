"""Background removal in OUR container (2026-11-25) — free, no vendor.

Higgsfield's public REST API has no removal route: the verify script's three
candidate paths (remove-background, v1/…, higgsfield-ai/…) all answered 404
model_not_found. So the agents' cut-outs (composite poses, "שמירה למאגר" on
/admin/characters/) are made here, with U^2-Net-p (u2netp, ~4.6 MB, the small
salient-object model rembg ships), run by onnxruntime on the CPU.

Same model and the same pre/post-processing as rembg's u2netp session
(resize to 320x320, divide by the max, ImageNet mean/std, min-max the first
output into the alpha mask) — without rembg's own dependency tree (opencv,
scipy, scikit-image, pymatting), which would cost image size and memory on a
512Mi service for nothing we use.

Memory (measured 2026-11-25, onnxruntime 1.22.1 + numpy 2.2.6): nothing
until the first removal; then numpy + onnxruntime + the session stay at about
+45-50 MB, and each removal peaks about +166 MB above that for ~0.6 s
(320x320 activations; the input size barely matters — it is resized first).
One session for the process, CPU arena off, BASIC graph optimisation, one
thread; one removal at a time (a lock), so a burst never stacks peaks.
The caller still VERIFIES the result (agent_characters.ensure_transparent).

The model file is baked into the image at build time (Dockerfile →
BG_MODEL_PATH); without it, removal fails with a clear BackgroundModelMissing.
"""
import io
import os
import threading

BG_MODEL_PATH = os.environ.get("BG_MODEL_PATH", "/app/models/u2netp.onnx")
SIZE = 320
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)
MAX_SIDE = 2048                  # larger inputs are scaled down first (memory)

_session = None
_lock = threading.Lock()


class BackgroundModelMissing(RuntimeError):
    """The u2netp model file is not in the container."""


def available() -> bool:
    return os.path.isfile(BG_MODEL_PATH)


def _get_session():
    global _session
    if _session is None:
        if not available():
            raise BackgroundModelMissing(f"background model not found at {BG_MODEL_PATH} (Dockerfile bakes it in)")
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.enable_cpu_mem_arena = False
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        # Measured 2026-11-25 (onnxruntime 1.22.1, peak working set of one
        # removal above the loaded session): full optimisation +235 MB,
        # BASIC +166 MB, none +175 MB, arena on +268 MB — so BASIC, arena off.
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        _session = ort.InferenceSession(BG_MODEL_PATH, sess_options=opts, providers=["CPUExecutionProvider"])
    return _session


def mask(img):
    """The alpha mask (PIL "L", the image's size) of the salient object."""
    import numpy as np
    from PIL import Image
    small = img.convert("RGB").resize((SIZE, SIZE), Image.LANCZOS)
    arr = np.asarray(small, dtype=np.float32)
    arr = arr / max(float(arr.max()), 1e-6)
    arr = (arr - np.array(MEAN, dtype=np.float32)) / np.array(STD, dtype=np.float32)
    arr = arr.transpose(2, 0, 1)[None, ...].astype(np.float32)
    session = _get_session()
    out = session.run(None, {session.get_inputs()[0].name: arr})[0][0, 0]
    lo, hi = float(out.min()), float(out.max())
    pred = (out - lo) / (hi - lo) if hi > lo else out * 0
    m = Image.fromarray((pred * 255).clip(0, 255).astype("uint8"), "L")
    return m.resize(img.size, Image.LANCZOS)


def cut_out(content: bytes) -> bytes:
    """The image with its background removed, as an RGBA PNG."""
    if not available():
        raise BackgroundModelMissing(f"background model not found at {BG_MODEL_PATH} (Dockerfile bakes it in)")
    from PIL import Image
    img = Image.open(io.BytesIO(content))
    img.load()
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    with _lock:
        alpha = mask(img)
    rgba = img.convert("RGBA")
    rgba.putalpha(alpha)
    buf = io.BytesIO()
    rgba.save(buf, "PNG")
    return buf.getvalue()


def remove_background(api_key: str, image_url: str, _download=None) -> dict:
    """The same interface the vendor call had: {'content' (PNG), 'credits': 0}.
    `api_key` is unused (nothing leaves the container but the download of
    our own generated image)."""
    if _download is None:
        from core.media_gen_service import _download
    return {"content": cut_out(_download(image_url)), "credits": 0}
