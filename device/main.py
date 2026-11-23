"""
ESP32 Internal Flash main.py Bootstrapper for Inkplate 10.
Includes ultra-fast deep-sleep WAKE button page flip (~1.9s total from deep sleep,
~1.7s subsequent flips) directly from compressed /cache_p1.rle and /cache_p2.rle.
NO re-rendering, NO status corruption, NO wasted resources.
"""

import gc
import os
import sys
import time
import machine
from inkplate10 import Inkplate
import rle_cache


def _get_page() -> int:
    try:
        with open("/page.txt", "r") as f:
            return 2 if f.read().strip() == "2" else 1
    except Exception:
        return 1


def _set_page(p: int) -> None:
    try:
        with open("/page.txt", "w") as f:
            f.write("2" if int(p) == 2 else "1")
    except Exception:
        pass


mode = getattr(Inkplate, "INKPLATE_2BIT", getattr(Inkplate, "INKPLATE_3BIT", 1))
display = Inkplate(mode)
display.begin()
fb = display._framebuf()
fb_len = len(fb)

wake_reason = 0
try:
    wake_reason = machine.wake_reason()
except Exception:
    pass

wake_pin = machine.Pin(36, machine.Pin.IN)
btn_pressed = False
try:
    btn_pressed = (wake_pin.value() == 0)
except Exception:
    pass

caches_ready = rle_cache.cache_exists(1) and rle_cache.cache_exists(2)
is_timer_sync = (wake_reason == 4)

if caches_ready and not is_timer_sync:
    saved_page = _get_page()
    if wake_reason == 2 or btn_pressed:
        cur_page = 2 if saved_page == 1 else 1
    else:
        cur_page = saved_page
    _set_page(cur_page)

    print(f"FAST FLIP: Showing Page {cur_page} from cache...")

    _btn_latched = False
    _last_irq_ms = time.ticks_ms()

    def _on_wake_irq(pin):
        global _btn_latched, _last_irq_ms
        now = time.ticks_ms()
        if time.ticks_diff(now, _last_irq_ms) > 180:
            _btn_latched = True
            _last_irq_ms = now

    # 1. Decompress active page directly into framebuffer (~200ms)
    rle_cache.load_page_cache(cur_page, fb)
    fb_cache = {cur_page: bytes(fb)}

    # Wait for initial button release before arming falling-edge IRQ for next press
    t_rel = time.ticks_ms()
    while wake_pin.value() == 0 and time.ticks_diff(time.ticks_ms(), t_rel) < 800:
        time.sleep_ms(10)
    time.sleep_ms(25)
    _btn_latched = False
    _last_irq_ms = time.ticks_ms()
    wake_pin.irq(trigger=machine.Pin.IRQ_FALLING, handler=_on_wake_irq)

    # 2. Refresh E-Ink panel (~1.67s)
    display.display()

    # 3. Pre-decode opposite page into SPIRAM so subsequent flips are instantaneous (~1.7s)
    other_page = 2 if cur_page == 1 else 1
    other_buf = bytearray(fb_len)
    if rle_cache.load_page_cache(other_page, other_buf):
        fb_cache[other_page] = bytes(other_buf)
    gc.collect()

    # 4. Stay awake for 60s for instant (~1.7s) subsequent flips
    mv = memoryview(fb)
    t_wait = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t_wait) < 60000:
        if _btn_latched or wake_pin.value() == 0:
            _btn_latched = False
            cur_page = 2 if cur_page == 1 else 1
            _set_page(cur_page)
            print(f"FAST FLIP: Page {cur_page}...")
            if cur_page in fb_cache:
                mv[:] = fb_cache[cur_page]
            else:
                rle_cache.load_page_cache(cur_page, fb)
            t_rel = time.ticks_ms()
            while wake_pin.value() == 0 and time.ticks_diff(time.ticks_ms(), t_rel) < 800:
                time.sleep_ms(10)
            time.sleep_ms(25)
            _btn_latched = False
            _last_irq_ms = time.ticks_ms()
            display.display()
            t_wait = time.ticks_ms()
        time.sleep_ms(20)

    # 5. Clean up SPIRAM and re-arm EXT0 wake for deep sleep
    fb_cache.clear()
    gc.collect()
    try:
        wake_pin.irq(handler=None)
        import esp32
        esp32.wake_on_ext0(pin=wake_pin, level=0)
    except Exception:
        pass
    print("Fast wake complete. Returning to deep sleep...")
    machine.deepsleep(30 * 60 * 1000)
else:
    for p in ("/", "/assets", "/sd", "/sd/assets"):
        if p not in sys.path:
            sys.path.append(p)
    import inkplate_app
    inkplate_app.run(display)
