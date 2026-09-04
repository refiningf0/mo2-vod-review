"""Prepare a frame crop so OCR can read MO2's combat log.

The log is white text drawn over whatever the world looks like, and the right
treatment depends entirely on that background:

  * Dark scenes (dungeons, night, shadowed terrain) already have strong
    contrast between the white text and the ground behind it. Upscaling and
    pushing contrast is enough, and it keeps the anti-aliased strokes intact.
  * Bright scenes (sand, snow, sky) leave the text barely brighter than the
    ground. Contrast alone cannot separate them, so those need an adaptive
    threshold comparing each pixel to its local surroundings.

Using the wrong one is costly: thresholding a dark scene shredded the strokes
and lost every timestamp, while contrast alone on a bright scene let the
background flood in. So the crop is measured first and the method chosen to
match it.
"""
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

BRIGHT = 118  # mean luminance above which a crop counts as a bright scene


def _upscale(img, scale):
    return img.resize((img.width * scale, img.height * scale), Image.LANCZOS)


def _dark_recipe(img, scale, contrast):
    """Upscale and boost contrast -- best when the text already stands out."""
    return ImageEnhance.Contrast(_upscale(img, scale)).enhance(contrast)


def _bright_recipe(img, scale, radius, delta):
    """Adaptive threshold -- needed when text and background are both pale."""
    a = np.asarray(_upscale(img, scale).convert("RGB")).astype(np.float32)
    grey = 0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]
    gi = Image.fromarray(grey.astype(np.uint8), mode="L")
    local = np.asarray(gi.filter(ImageFilter.BoxBlur(radius))).astype(np.float32)
    return Image.fromarray(np.where(grey > local + delta, 0, 255).astype(np.uint8), "L")


def plain(img, scale=3):
    """Bigger, and nothing else.

    The treatments above help a great deal on some frames and take text away on
    others: pushing contrast on an already-legible line crushes the
    anti-aliasing that tells an "8" from a "B", and a line that reads perfectly
    from the raw crop can come back with its whole tail missing. Neither
    treatment wins everywhere, so the pipeline reads every frame both ways and
    lets the cross-frame consensus sort it out.
    """
    return _upscale(img if img.mode == "RGB" else img.convert("RGB"), scale)


def prep(img, scale=3, contrast=2.0, radius=9, delta=18, force=None):
    """Return an image ready for OCR.

    force: "dark" or "bright" to override the automatic choice.
    """
    if img.mode != "RGB":
        img = img.convert("RGB")

    if force is None:
        small = img.convert("L").resize((64, 64))
        mode = "bright" if float(np.asarray(small).mean()) > BRIGHT else "dark"
    else:
        mode = force

    if mode == "bright":
        return _bright_recipe(img, scale, radius, delta)
    return _dark_recipe(img, scale, contrast)


def prep_file(src, dst, **kw):
    prep(Image.open(src), **kw).save(dst)
    return dst


if __name__ == "__main__":
    import sys
    print(prep_file(sys.argv[1], sys.argv[2]))
