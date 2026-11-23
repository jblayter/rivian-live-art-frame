"""
Fast RLE Framebuffer Cache for Inkplate 10.
Compresses 495,000-byte 3-bit / 4-bit framebuffers to ~100 KB in ~300ms using MicroPython Viper.
Decompresses in ~175ms.
"""

import os
import sys

if sys.implementation.name != "micropython":
    class _MockMP:
        @staticmethod
        def viper(fn):
            return fn
    import builtins
    builtins.micropython = _MockMP()
    builtins.ptr8 = int


@micropython.viper
def rle_decode(src: ptr8, src_len: int, dst: ptr8, dst_len: int) -> int:
    si = 0
    di = 0
    while si < src_len and di < dst_len:
        b = src[si]
        si += 1
        if b == 0xAA:
            cnt = src[si]
            si += 1
            val = src[si]
            si += 1
            for _ in range(cnt):
                dst[di] = val
                di += 1
        else:
            dst[di] = b
            di += 1
    return di


@micropython.viper
def rle_encode(src: ptr8, src_len: int, dst: ptr8, dst_max: int) -> int:
    si = 0
    di = 0
    while si < src_len:
        val = src[si]
        run = 1
        while (si + run) < src_len and run < 255 and src[si + run] == val:
            run += 1
        if run >= 4 or val == 0xAA:
            dst[di] = 0xAA
            dst[di + 1] = run
            dst[di + 2] = val
            di += 3
            si += run
        else:
            for _ in range(run):
                dst[di] = val
                di += 1
            si += run
    return di


def cache_path(page_num: int) -> str:
    return f"/cache_p{int(page_num)}.rle"


def cache_exists(page_num: int) -> bool:
    try:
        return os.stat(cache_path(page_num))[6] > 1000
    except Exception:
        return False


def save_page_cache(page_num: int, fb) -> int:
    """Encodes fb (495000 bytes) and writes to /cache_p{page_num}.rle."""
    p = cache_path(page_num)
    tmp_p = p + ".tmp"
    fb_len = len(fb)
    enc_buf = bytearray(fb_len)
    enc_len = rle_encode(fb, fb_len, enc_buf, fb_len)
    try:
        os.remove(tmp_p)
    except OSError:
        pass
    with open(tmp_p, "wb") as f:
        f.write(memoryview(enc_buf)[:enc_len])
    try:
        os.remove(p)
    except OSError:
        pass
    os.rename(tmp_p, p)
    return enc_len


def load_page_cache(page_num: int, fb) -> bool:
    """Reads /cache_p{page_num}.rle and decodes into fb."""
    p = cache_path(page_num)
    try:
        sz = os.stat(p)[6]
        enc_buf = bytearray(sz)
        with open(p, "rb") as f:
            f.readinto(enc_buf)
        dec_len = rle_decode(enc_buf, sz, fb, len(fb))
        return dec_len == len(fb)
    except Exception as e:
        print(f"Error loading cache for page {page_num}:", e)
        return False
