from __future__ import annotations
from zipfile import ZipFile
from lxml import etree
import re
import argparse
import os, re, math, colorsys,cv2
from collections import Counter, defaultdict
  
from PIL import Image
import numpy as np
""" 
Extract a single theme color from one or multiple images.
- Picks a representative, saturated color from assets (logos/key figures).
- Optionally enforces a darker theme suitable for white text (prefer_dark=True).
- Ensures WCAG contrast with white (>=4.5:1) when possible; otherwise falls back to black text.

 
Usage (Python):
    from pick_theme_color import pick_theme_color

    theme_hex, text_hex = pick_theme_color(
        images=["logo.png", "figure.jpg"],
        k=4, alpha=1.3, seed=42, prefer_dark=True
    )
    print(theme_hex, text_hex)

CLI:
    python apply_color.py "SlideGen/<4o_4o>_images_and_tables/STEP_A_General_and_Scalable_Framework_for_Solving_Video_Inverse_Problems/STEP_A_General_and_Scalable_Framework_for_Solving_Video_Inverse_Problems-with-image-refs_artifacts/image_000001_08e29a109457665ea4351547cc076a85e6477fcafe3b214587fc5800f3815e7f.png"    "SlideGen/<4o_4o>_images_and_tables/STEP_A_General_and_Scalable_Framework_for_Solving_Video_Inverse_Problems/STEP_A_General_and_Scalable_Framework_for_Solving_Video_Inverse_Problems-with-image-refs_artifacts/image_000002_038700ae8ee32f7f4ae00bbaf303305bfecf0a5b991d945c4fd3c6d5550cda98.png" --prefer-dark
 
"""


import os
import sys
from typing import List, Tuple, Union

import numpy as np

# Import cv2 / sklearn lazily in functions to avoid import errors at module import-time.
def _lazy_imports():
    import cv2  
    return cv2

def _rgb_to_luminance(rgb: np.ndarray) -> float:
    """WCAG relative luminance of an sRGB color (RGB in 0..255)."""
    rgb = np.array(rgb, dtype=np.float64) / 255.0
    def f(u): return (u / 12.92) if (u <= 0.03928) else (((u + 0.055) / 1.055) ** 2.4)
    R, G, B = f(rgb[0]), f(rgb[1]), f(rgb[2])
    return 0.2126 * R + 0.7152 * G + 0.0722 * B
 
def _clamp_rgb(rgb: np.ndarray) -> np.ndarray:
    return np.clip(np.round(rgb).astype(np.int32), 0, 255)
 
def _to_hex(rgb: np.ndarray) -> str:
    r, g, b = _clamp_rgb(rgb).tolist()
    return f"#{r:02X}{g:02X}{b:02X}"
 

def _mask_and_stack_pixels(images: List[Union[str, np.ndarray]], min_sat: float, min_v: float, max_v: float) -> np.ndarray:
    cv2, _ = _lazy_imports()
    pixels_all = []

    for src in images:
        if isinstance(src, str):
            if not os.path.exists(src):
                continue
            img_bgr = cv2.imread(src, cv2.IMREAD_COLOR)
            if img_bgr is None:
                continue
        else:
            arr = np.asarray(src)
            if arr.ndim != 3 or arr.shape[2] != 3:
                continue
            if arr.dtype != np.uint8:
                arr = np.clip(arr, 0, 255).astype(np.uint8)
            img_bgr = arr[:, :, ::-1]  # assume RGB -> BGR

        h, w = img_bgr.shape[:2]
        short = min(h, w)
        scale = 256.0 / max(1, short)
        new_w, new_h = int(round(w * scale)), int(round(h * scale))
        img_bgr = cv2.resize(img_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)

        hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
        H, S, V = cv2.split(hsv)

        mask = np.ones(H.shape, dtype=bool)
        # near-white background: low S & very high V
        mask &= ~(((S / 255.0) < float(min_sat)) & ((V / 255.0) > float(max_v)))
        # near-black
        mask &= ~((V / 255.0) < float(min_v))
        # low saturation greys
        mask &= ~((S / 255.0) < float(min_sat))

        pts = img_bgr[mask]
        if pts.size == 0:
            pts = img_bgr.reshape(-1, 3)

        if len(pts) > 15000:
            idx = np.random.RandomState(0).choice(len(pts), 15000, replace=False)
            pts = pts[idx]

        pixels_all.append(pts)

    if not pixels_all:
        raise ValueError("No valid pixels collected from the provided images.")

    return np.vstack(pixels_all)
 
def _mask_and_stack_pixels(images, min_v: float, max_v: float) -> np.ndarray:
    """
    只按近黑/近白做掩膜：
      - 丢 near-black: V < min_v
      - 丢 near-white: V > max_v
    同时丢掉 alpha 很低（透明）的像素，避免被当黑。
    返回 RGB uint8 的像素数组。
    """
    cv2 = _lazy_imports()
    all_rgb = []

    for src in images:
        # 读取，保留 alpha（可能是 RGBA）
        if isinstance(src, str):
            img = cv2.imread(src, cv2.IMREAD_UNCHANGED)
            if img is None:
                continue
        else:
            arr = np.asarray(src)
            if arr.ndim != 3 or arr.shape[2] not in (3, 4):
                continue
            if arr.dtype != np.uint8:
                arr = np.clip(arr, 0, 255).astype(np.uint8)
            img = arr[..., ::-1] if arr.shape[2] in (3, 4) else arr  # RGB(A)->BGR(A)

        # 透明像素不要
        if img.shape[2] == 4:
            bgr, a = img[:, :, :3], img[:, :, 3]
            keep_alpha = a > 10
            if not np.any(keep_alpha):
                continue
            pts_bgr = bgr[keep_alpha]
        else:
            pts_bgr = img.reshape(-1, 3)

        # 限流
        if len(pts_bgr) > 300000:
            idx = np.random.RandomState(0).choice(len(pts_bgr), 300000, replace=False)
            pts_bgr = pts_bgr[idx]

        # 只用 V 做近黑/近白掩膜
        hsv = cv2.cvtColor(pts_bgr.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.float32)
        Vn = hsv[:, 2] / 255.0
        keep = (Vn >= float(min_v)) & (Vn <= float(max_v))
        pts_bgr = pts_bgr[keep]
        if pts_bgr.size == 0:
            continue

        all_rgb.append(pts_bgr[:, ::-1])  # BGR->RGB

    if not all_rgb:
        return np.empty((0, 3), dtype=np.uint8)
    return np.vstack(all_rgb).astype(np.uint8)
 
def _most_frequent_exact_color(pixels_rgb: np.ndarray) -> np.ndarray:
    """
    在像素集合中找“出现次数最多的精确 RGB 值”（已排除近黑/近白）。
    用 24bit 键（r<<16|g<<8|b）计数，返回该 RGB。
    """
    if pixels_rgb.size == 0:
        # 兜底：给个安全默认
        return np.array([43, 95, 166], dtype=np.int32)  # #2B5FA6

    r = pixels_rgb[:, 0].astype(np.uint32)
    g = pixels_rgb[:, 1].astype(np.uint32)
    b = pixels_rgb[:, 2].astype(np.uint32)
    keys = (r << 16) | (g << 8) | b

    uniq, counts = np.unique(keys, return_counts=True)
    # 直接取频次最高的精确像素值
    best_key = uniq[np.argmax(counts)]
    best_rgb = np.array([(best_key >> 16) & 255, (best_key >> 8) & 255, best_key & 255], dtype=np.int32)
    return best_rgb
def _deg_to_cv_h(deg: float) -> float:
    # OpenCV: H ∈ [0,179]，等于角度的一半
    return (deg % 360) / 2.0

def pick_theme_color(
    images: list,
    prefer_dark: bool = True,
    min_sat: float = 0.25,      # 没用到，保留形参
    min_v: float = 0.00,
    max_v: float = 0.99,
    qstep: int = 16,            # 没用到，保留形参
    target_v_for_dark: float = 0.38,
    return_base_hex: bool = False,
) -> str | tuple[str, str]:
    """
    简化版：只找像素点，排除近白/近黑后，拿出现次数最多的精确颜色返回。
    """
    pixels_rgb = _mask_and_stack_pixels(images, min_v=min_v, max_v=max_v)
    if pixels_rgb.size == 0:
        # 兜底：直接返回默认主题色
        theme_hex = "#2B5FA6"
        return (theme_hex, theme_hex) if return_base_hex else theme_hex

    base_rgb = _most_frequent_exact_color(pixels_rgb)
    theme_rgb = base_rgb.copy()

    # if prefer_dark:
    #     theme_rgb = move_right_then_down(theme_rgb, down=0.08, fallback_hue_deg=215)
    theme_rgb = move_right_then_down_adaptive(
        theme_rgb,
        target_v=0.40,   #  0.38~0.45
        gamma=3.8,        
        sat_floor=0.7,  
        fallback_hue_deg= 215
    )
    theme_hex = _to_hex(theme_rgb)
    base_hex  = _to_hex(base_rgb)
    if return_base_hex:
        return theme_hex, base_hex

    return theme_hex


 

 
def _set_color_node(parent, hexval):
    for child in list(parent):
        if child.tag in (f"{{{NS['a']}}}srgbClr", f"{{{NS['a']}}}sysClr"):
            parent.remove(child)
    srgb = etree.Element(f"{{{NS['a']}}}srgbClr")
    srgb.set("val", _normalize_hex(hexval))
    parent.insert(0, srgb)

def _patch_theme_xml(xml_bytes, kv):
    root = etree.fromstring(xml_bytes)
    clr = root.find(".//a:clrScheme", namespaces=NS)
    if clr is None:
        return xml_bytes
    name2elem = {}
    for el in clr:
        if not isinstance(el.tag, str): 
            continue
        nm = el.get("name", "").lower()
        tag = etree.QName(el).localname.lower()
        if nm: name2elem[nm] = el
        name2elem.setdefault(tag, el)
    changed = False
    for key, hexv in kv.items():
        k = key.lower()
        if k in name2elem:
            _set_color_node(name2elem[k], hexv)
            changed = True
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes") if changed else xml_bytes

def set_theme_colors(pptx_in, pptx_out, colors_dict):
    changed_any = False
    with ZipFile(pptx_in, "r") as zin, ZipFile(pptx_out, "w") as zout:
        theme_paths = [p for p in zin.namelist() if p.startswith("ppt/theme/") and p.endswith(".xml")]
        for name in zin.namelist():
            data = zin.read(name)
            if name in theme_paths:
                patched = _patch_theme_xml(data, colors_dict)
                if patched != data: changed_any = True
                zout.writestr(name, patched)
            else:
                zout.writestr(name, data)
    if not changed_any:
        print("[warn] No theme was modified (the template may not reference theme*.xml).")
    else:
        print(f"[ok] Finished writing: {pptx_out}")


NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}

def _normalize_hex(s):
    s = s.strip().lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", s):
        raise ValueError(f"Bad hex color: {s}")
    return s.upper()

def _set_color_node(parent, hexval): 
    for child in list(parent):
        if child.tag in (f"{{{NS['a']}}}srgbClr", f"{{{NS['a']}}}sysClr"):
            parent.remove(child)
    srgb = etree.Element(f"{{{NS['a']}}}srgbClr")
    srgb.set("val", hexval)
    parent.insert(0, srgb)
def rgb_to_hex(rgb):
    r,g,b = [int(round(max(0,min(255,x)))) for x in rgb]
    return f"{r:02X}{g:02X}{b:02X}"

def hex_to_rgb(h):
    h=_normalize_hex(h)
    return tuple(int(h[i:i+2],16) for i in (0,2,4))

def move_right_then_down(rgb, down=0.10, fallback_hue_deg=215):
     
    import cv2
    rgb = np.array(rgb, dtype=np.uint8)
    hsv = cv2.cvtColor(np.uint8([[rgb]]), cv2.COLOR_RGB2HSV).astype(np.float32)
    h, s, v = hsv[0, 0]

    
    if s <= 1 and fallback_hue_deg is not None:
        h = _deg_to_cv_h(fallback_hue_deg)   
    
    s = 235.0 
    
    down = float(np.clip(down, 0.0, 0.9))
    v = np.clip(v * (1.0 - down), 0, 255)

    out = cv2.cvtColor(np.uint8([[[h, s, v]]]), cv2.COLOR_HSV2RGB)[0, 0]
    return np.clip(np.round(out).astype(np.int32), 0, 255)

def to_hex(rgb):
    r, g, b = [int(x) for x in rgb]
    return f"#{r:02X}{g:02X}{b:02X}"
def rgb_to_hsv01(r,g,b):
    return colorsys.rgb_to_hsv(r/255.0, g/255.0, b/255.0)  # H[0,1),S[0,1],V[0,1]

def hsv01_to_rgb(h,s,v):
    rr,gg,bb = colorsys.hsv_to_rgb(h,s,v)
    return int(round(rr*255)), int(round(gg*255)), int(round(bb*255))

def clamp(v,a,b): return max(a, min(b, v))
 
def move_right_then_down_adaptive(
    rgb,
    *,
    target_v: float = 0.70,
    gamma: float = 0.0,
    v_cap: float | None = 0.99,
    sat_target: float = 0.80,
    sat_blend: float = 0.30,
    sat_cap: float = 0.99,
    sat_floor: float = 0.40,
    fallback_hue_deg: int = 215,
    up_gain: float = 0.50,      
    v_floor: float =0.6  
):
    import numpy as np, cv2
    def _deg_to_cv_h(deg: float) -> float: return (deg % 360) / 2.0

    rgb = np.array(rgb, dtype=np.uint8)
    hsv = cv2.cvtColor(np.uint8([[rgb]]), cv2.COLOR_RGB2HSV).astype(np.float32)
    h, s, v = hsv[0, 0]

    s_norm, v_norm = s/255.0, v/255.0
    if s <= 1:
        h = _deg_to_cv_h(fallback_hue_deg)
        s_norm = max(s_norm, float(sat_floor))

    s_goal = float(np.clip(sat_target, 0.0, 1.0))
    s_norm = (1.0 - float(sat_blend)) * s_norm + float(sat_blend) * s_goal
    s_norm = float(np.clip(s_norm, float(sat_floor), float(sat_cap)))
    s = s_norm * 255.0
    mid_rgb = cv2.cvtColor(np.uint8([[[h, s, v]]]), cv2.COLOR_HSV2RGB)[0,0]
    
    print("[MID after RIGHT] RGB:", mid_rgb.tolist(), "HEX:", _to_hex(mid_rgb))

    tv = float(np.clip(target_v, 0.0, 1.0))
    if v_norm > tv:
       
        d = v_norm - tv
        a = 1.0 - np.exp(-float(gamma) * d) 
        v_norm = v_norm - a * d
    elif v_norm < tv and up_gain > 0.0:
       
        v_norm = v_norm + float(np.clip(up_gain, 0.0, 1.0)) * (tv - v_norm)

    if v_floor is not None:
        v_norm = max(v_norm, float(v_floor))
    if v_cap is not None:
        v_norm = min(v_norm, float(v_cap))

    v = np.clip(v_norm * 255.0, 0, 255)

    out = cv2.cvtColor(np.uint8([[[h, s, v]]]), cv2.COLOR_HSV2RGB)[0, 0]
    return np.clip(np.round(out).astype(np.int32), 0, 255)


def _patch_single_theme_color(xml_bytes, target_key, hexval):
    root = etree.fromstring(xml_bytes)
    clr = root.find(".//a:clrScheme", namespaces=NS)
    if clr is None:
        raise RuntimeError("cannot find <a:clrScheme>")

    target_key = target_key.lower()  # e.g., 'accent1'
   
    name2elem = {}
    for el in clr:
        if not isinstance(el.tag, str): 
            continue
        nm = el.get("name", "").lower()
        tag = etree.QName(el).localname.lower()
        if nm: name2elem[nm] = el
        name2elem.setdefault(tag, el)

 
    _set_color_node(name2elem[target_key], hexval)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

def set_one_theme_color(pptx_in, pptx_out, color_hex, target_key="accent1"):
    hexval = _normalize_hex(color_hex)
    with ZipFile(pptx_in, "r") as zin, ZipFile(pptx_out, "w") as zout:
        theme_paths = [p for p in zin.namelist() if p.startswith("ppt/theme/") and p.endswith(".xml")]
        for name in zin.namelist():
            data = zin.read(name)
            if name in theme_paths:   
                try:
                    patched = _patch_single_theme_color(data, target_key, hexval)
                    zout.writestr(name, patched)
                except Exception: 
                    zout.writestr(name, data)
            else:
                zout.writestr(name, data)

if __name__ == "__main__": 
 
    imgs = [
        # "SlideGen/experiment/metrics/geom/expriment_material/grey.png"
        # "SlideGen/<4o_4o>_images_and_tables/STEP_A_General_and_Scalable_Framework_for_Solving_Video_Inverse_Problems/STEP_A_General_and_Scalable_Framework_for_Solving_Video_Inverse_Problems-with-image-refs_artifacts/image_000001_08e29a109457665ea4351547cc076a85e6477fcafe3b214587fc5800f3815e7f.png" ,
        "SlideGen/<4o_4o>_images_and_tables/STEP_A_General_and_Scalable_Framework_for_Solving_Video_Inverse_Problems/STEP_A_General_and_Scalable_Framework_for_Solving_Video_Inverse_Problems-with-image-refs_artifacts/image_000002_038700ae8ee32f7f4ae00bbaf303305bfecf0a5b991d945c4fd3c6d5550cda98.png"
    ]
    theme_hex, base_hex = pick_theme_color(
        images=imgs,
        prefer_dark=True,
        min_sat=0.5, min_v=0.1, max_v=0.99 ,qstep=16,
        return_base_hex=True,     
    )
    print("base_hex :", base_hex)   
    print("theme_hex:", theme_hex)   
    
    set_one_theme_color("SlideGen/utils/slides_template/slides3_template_daizi.pptx",
        "SlideGen/utils/slides_template/slides3_template_final.pptx",
        theme_hex, target_key = "dk2"  )
    
    print(" Done.  ")
