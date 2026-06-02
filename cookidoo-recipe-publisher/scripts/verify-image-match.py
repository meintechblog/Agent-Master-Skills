#!/usr/bin/env python3
"""Verify a user-provided photo plausibly matches a HelloFresh recipe.

Strategy (no external deps, no ML models):
  1. Download the HelloFresh og:image to a local tmp file.
  2. Compute a 16x16 average-hash perceptual fingerprint for both images
     using only stdlib (struct + zlib + tiny PNG/JPEG decoder via PIL if
     available, else falls back to a pixel-stats comparison via macOS sips).
  3. Report a similarity score 0-100. Above ~40 = same dish class. Below ~25 =
     likely wrong image. The reviewer (Claude) should do a final visual
     comparison if the score lands in the [25-50] uncertainty band.

The reliable workflow is: this script flags suspicious mismatches → the LLM
caller (skill orchestrator) confirms visually by reading both images.

Usage:
  ./verify-image-match.py --user-image <path> --hf-url <url>
  ./verify-image-match.py --user-image <path> --hf-image-url <direct-url>

Exit codes:
  0 = images plausibly match (score >= 50)
  2 = uncertain — needs human/LLM review (score 25-49)
  1 = mismatch likely (score < 25) OR error
  64 = usage error
"""
import sys, argparse, json, hashlib, pathlib, urllib.request, urllib.error, tempfile, subprocess, re


def fetch_url(url: str, out: pathlib.Path):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (cookidoo-recipe-publisher skill)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            out.write_bytes(r.read())
    except urllib.error.URLError as e:
        print(f"network error fetching {url}: {e}", file=sys.stderr); sys.exit(1)


def extract_hf_image_url(recipe_url: str) -> str:
    """Run the sibling extract-hellofresh.py to get the recipe's og:image."""
    sibling = pathlib.Path(__file__).parent / "extract-hellofresh.py"
    if not sibling.exists():
        print(f"sibling script missing: {sibling}", file=sys.stderr); sys.exit(1)
    try:
        out = subprocess.check_output([sys.executable, str(sibling), recipe_url], timeout=30)
        data = json.loads(out)
        img = data.get("image_url")
        if not img: print("HelloFresh recipe has no image_url", file=sys.stderr); sys.exit(1)
        return img
    except subprocess.CalledProcessError as e:
        print(f"extract-hellofresh.py failed: {e}", file=sys.stderr); sys.exit(1)


def to_grayscale_thumbnail(src: pathlib.Path, size: int = 16):
    """Resize to size×size grayscale. Prefer PIL; fall back to macOS `sips`.
    Returns a list of `size*size` brightness integers in [0, 255].
    """
    try:
        from PIL import Image
        with Image.open(src) as im:
            im = im.convert("L").resize((size, size), Image.LANCZOS)
            return list(im.getdata())
    except ImportError:
        pass
    # Fallback for macOS without PIL: use sips to resize + convert
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = pathlib.Path(tmp.name)
    try:
        subprocess.run(
            ["sips", "-s", "format", "png", "-z", str(size), str(size),
             str(src), "--out", str(tmp_path)],
            check=True, capture_output=True
        )
        # Read PNG manually via stdlib (no PIL). Inflate the IDAT chunk to get raw pixels.
        # This is the messy fallback path — try to keep PIL installed if you care about reliability.
        import struct, zlib
        raw = tmp_path.read_bytes()
        if raw[:8] != b"\x89PNG\r\n\x1a\n":
            print("sips output not a PNG", file=sys.stderr); sys.exit(1)
        idat = b""; width = height = depth = ctype = 0
        i = 8
        while i < len(raw):
            length = struct.unpack(">I", raw[i:i+4])[0]
            ctype_tag = raw[i+4:i+8]; data = raw[i+8:i+8+length]; i += 8 + length + 4
            if ctype_tag == b"IHDR":
                width, height, depth, ctype = struct.unpack(">IIBB", data[:10])
            elif ctype_tag == b"IDAT":
                idat += data
            elif ctype_tag == b"IEND":
                break
        decompressed = zlib.decompress(idat)
        # Apply PNG filter unfilter (basic: assume filter type 0 / None on each row).
        # For sips-resized small images this often holds.
        bpp = {0: 1, 2: 3, 4: 2, 6: 4}.get(ctype, 1)
        stride = width * bpp
        pixels = []
        for row in range(height):
            offset = row * (stride + 1)
            row_bytes = decompressed[offset + 1: offset + 1 + stride]
            if ctype == 0:  # grayscale
                pixels.extend(row_bytes)
            elif ctype == 2:  # RGB
                for k in range(0, stride, 3):
                    r, g, b = row_bytes[k], row_bytes[k+1], row_bytes[k+2]
                    pixels.append((r * 299 + g * 587 + b * 114) // 1000)
            elif ctype == 6:  # RGBA
                for k in range(0, stride, 4):
                    r, g, b = row_bytes[k], row_bytes[k+1], row_bytes[k+2]
                    pixels.append((r * 299 + g * 587 + b * 114) // 1000)
            else:
                pixels.extend(row_bytes)
        return pixels[:size * size] if len(pixels) >= size * size else pixels
    finally:
        if tmp_path.exists(): tmp_path.unlink()


def avg_hash(pixels):
    """Average hash: 1 bit per pixel above mean brightness."""
    if not pixels: return 0
    mean = sum(pixels) / len(pixels)
    bits = 0
    for px in pixels:
        bits = (bits << 1) | (1 if px > mean else 0)
    return bits


def hamming_distance(a, b, nbits):
    return bin(a ^ b).count("1")


def color_signature(src: pathlib.Path):
    """Coarse RGB histogram signature: 4 buckets per channel = 64-dim vector.
    Catches obvious color-class mismatch (orange curry vs green stir-fry).
    Falls back to None if PIL not available.
    """
    try:
        from PIL import Image
        with Image.open(src) as im:
            im = im.convert("RGB").resize((32, 32))
            buckets = [0] * 64  # 4*4*4 = 64 buckets
            for r, g, b in im.getdata():
                idx = (r // 64) * 16 + (g // 64) * 4 + (b // 64)
                buckets[idx] += 1
            total = sum(buckets) or 1
            return [b / total for b in buckets]
    except ImportError:
        return None


def cosine_similarity(a, b):
    if not a or not b or len(a) != len(b): return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def score_match(user_img: pathlib.Path, hf_img: pathlib.Path):
    size = 16
    u_px = to_grayscale_thumbnail(user_img, size)
    h_px = to_grayscale_thumbnail(hf_img, size)
    u_hash = avg_hash(u_px)
    h_hash = avg_hash(h_px)
    nbits = size * size
    hd = hamming_distance(u_hash, h_hash, nbits)
    # Avg-hash similarity: 100 = identical, 0 = all bits flipped
    hash_sim = 100 * (1 - hd / nbits)

    u_color = color_signature(user_img)
    h_color = color_signature(hf_img)
    color_sim = cosine_similarity(u_color, h_color) * 100 if u_color and h_color else None

    # Composite: prefer color when available (more robust against composition diffs)
    if color_sim is not None:
        composite = 0.4 * hash_sim + 0.6 * color_sim
    else:
        composite = hash_sim

    return {
        "hash_similarity": round(hash_sim, 1),
        "color_similarity": round(color_sim, 1) if color_sim is not None else None,
        "composite_score": round(composite, 1),
        "hamming_distance": hd,
    }


def main():
    ap = argparse.ArgumentParser(description="Verify user photo matches HelloFresh recipe.")
    ap.add_argument("--user-image", required=True, type=pathlib.Path)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--hf-url", help="HelloFresh recipe URL (script will fetch og:image)")
    g.add_argument("--hf-image-url", help="Direct URL to the HF reference image")
    ap.add_argument("--json", action="store_true", help="Output as JSON instead of human-readable")
    args = ap.parse_args()

    if not args.user_image.exists():
        print(f"user image not found: {args.user_image}", file=sys.stderr); sys.exit(1)

    hf_url = args.hf_image_url or extract_hf_image_url(args.hf_url)

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        hf_path = pathlib.Path(tmp.name)
    try:
        fetch_url(hf_url, hf_path)
        result = score_match(args.user_image, hf_path)
        result["user_image"] = str(args.user_image)
        result["hf_image_url"] = hf_url

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"User image:    {result['user_image']}")
            print(f"HF reference:  {result['hf_image_url']}")
            print(f"Hash similarity:  {result['hash_similarity']}%  (Hamming distance: {result['hamming_distance']})")
            if result['color_similarity'] is not None:
                print(f"Color similarity: {result['color_similarity']}%")
            print(f"Composite score:  {result['composite_score']}%")
            print()
            if result['composite_score'] >= 50:
                print("✓ MATCH — images plausibly show the same dish")
                sys.exit(0)
            elif result['composite_score'] >= 25:
                print("⚠ UNCERTAIN — needs visual review (LLM should look at both images)")
                sys.exit(2)
            else:
                print("✗ MISMATCH likely — images probably show different dishes")
                sys.exit(1)
    finally:
        if hf_path.exists(): hf_path.unlink()


if __name__ == "__main__":
    main()
