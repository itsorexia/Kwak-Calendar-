"""Streamlit port of the 7 x 5 Photo Grid Designer.

Run locally with: streamlit run app.py
Optional HEIC support: pip install pillow-heif
"""
from __future__ import annotations

import base64
import io
import json
import uuid
from dataclasses import asdict, dataclass
from typing import Any

import streamlit as st
from streamlit_image_coordinates import streamlit_image_coordinates
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROWS, COLS = 5, 7
DPI, PAGE_W, PAGE_H = 600, 3600, 2400
THEME = {"bg": "#F3F7FF", "border": "#C9D9FF", "text": "#3A4A6A", "empty": "#DDE8FF", "white": "#FFFFFF", "selected": "#FFD66E", "accent": "#3E7BFF"}
IMAGE_TYPES = ["png", "jpg", "jpeg", "webp", "bmp", "gif", "tif", "tiff", "heic", "heif"]


@dataclass
class Region:
    id: str
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    image_data: str = ""  # base64 source bytes keep projects portable
    image_name: str = ""
    text: str = ""
    rotation: int = 0
    flip_h: bool = False
    flip_v: bool = False
    image_offset_x: float = 0.0
    image_offset_y: float = 0.0
    crop_box: list[float] | None = None

    def cells(self):
        return {(r, c) for r in range(self.row, self.row + self.rowspan) for c in range(self.col, self.col + self.colspan)}


def fresh_regions():
    regs = {}
    lookup = {}
    for r in range(ROWS):
        for c in range(COLS):
            rid = f"r{r}c{c}"
            regs[rid] = Region(rid, r, c)
            lookup[(r, c)] = rid
    return regs, lookup


def state_init():
    if "regions" not in st.session_state:
        st.session_state.regions, st.session_state.lookup = fresh_regions()
        st.session_state.selected = set()
        st.session_state.fit = "cover"
        st.session_state.bg = "sky"
        st.session_state.text_size = 100
        st.session_state.text_alpha = 150


def bg_rgb():
    return (255, 255, 255) if st.session_state.bg == "white" else (243, 247, 255)


def decode_image(region: Region) -> Image.Image | None:
    if not region.image_data:
        return None
    raw = base64.b64decode(region.image_data)
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception as exc:
        if region.image_name.lower().endswith((".heic", ".heif")):
            try:
                import pillow_heif  # type: ignore
                pillow_heif.register_heif_opener()
                im = Image.open(io.BytesIO(raw)); im.load()
            except Exception as heif_exc:
                raise ValueError("HEIC/HEIF를 열 수 없습니다. pillow-heif 설치 후 다시 실행하세요.") from heif_exc
        else:
            raise ValueError(f"이미지를 열 수 없습니다: {exc}") from exc
    im = ImageOps.exif_transpose(im).convert("RGBA")
    if region.flip_h: im = ImageOps.mirror(im)
    if region.flip_v: im = ImageOps.flip(im)
    if region.rotation: im = im.rotate(-region.rotation, expand=True, resample=Image.Resampling.BICUBIC)
    bg = Image.new("RGBA", im.size, (*bg_rgb(), 255)); bg.alpha_composite(im)
    return bg.convert("RGB")


def render_photo(region: Region, size: tuple[int, int], mode=None, offsets=None):
    mode = mode or st.session_state.fit
    ox, oy = offsets or (region.image_offset_x, region.image_offset_y)
    if "_photo_render_cache" not in st.session_state:
        st.session_state["_photo_render_cache"] = {}
    cache = st.session_state["_photo_render_cache"]
    image_key = (hash(region.image_data), len(region.image_data))
    key = (image_key, region.image_name, region.rotation, region.flip_h, region.flip_v,
           tuple(size), mode, bg_rgb(), round(float(ox), 5), round(float(oy), 5),
           tuple(region.crop_box) if region.crop_box else None)
    if key in cache:
        return cache[key]
    im = decode_image(region)
    if im is None: return None
    if mode == "contain":
        fitted = ImageOps.contain(im, size, method=Image.Resampling.LANCZOS)
        out = Image.new("RGB", size, bg_rgb())
        x = round(max(0, size[0] - fitted.width) * (ox + 1) / 2)
        y = round(max(0, size[1] - fitted.height) * (oy + 1) / 2)
        out.paste(fitted, (x, y))
    else:
        out = ImageOps.fit(im, size, method=Image.Resampling.LANCZOS, centering=((ox + 1) / 2, (oy + 1) / 2))
    if region.crop_box:
        left, top, right, bottom = [max(0.0, min(1.0, float(v))) for v in region.crop_box]
        x1, y1 = int(left * size[0]), int(top * size[1])
        x2, y2 = int(right * size[0]), int(bottom * size[1])
        if x2 > x1 and y2 > y1:
            out = out.crop((x1, y1, x2, y2)).resize(size, Image.Resampling.LANCZOS)
    if len(cache) >= 120:
        cache.pop(next(iter(cache)))
    cache[key] = out
    return out


def font(size: int):
    candidates = ["Freesentation-7Bold.ttf", "malgunbd.ttf", "NanumGothicBold.ttf", "NotoSansCJK-Bold.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "DejaVuSans-Bold.ttf"]
    for name in candidates:
        try: return ImageFont.truetype(name, size)
        except Exception: pass
    return ImageFont.load_default(size=size)


def wrap_text(draw, text, f, maxw):
    result = []
    for raw in text.strip().splitlines() or [text.strip()]:
        units = raw.split(" ") if " " in raw else list(raw)
        sep = " " if " " in raw else ""
        line = ""
        for unit in units:
            trial = unit if not line else line + sep + unit
            if draw.textbbox((0, 0), trial, font=f)[2] <= maxw or not line: line = trial
            else: result.append(line); line = unit
        if line: result.append(line)
    return result or [""]


def text_layer(text, w, h, has_photo):
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
    px = max(5, int(min(w, h) * .70 * st.session_state.text_size / 100))
    f = font(px); pad = max(1, int(min(w, h) * .02)); lines = wrap_text(d, text.strip(), f, max(1, w - pad * 2))
    heights = [d.textbbox((0, 0), line, font=f)[3] - d.textbbox((0, 0), line, font=f)[1] for line in lines]
    spacing = max(0, int(px * .12)); y = (h - sum(heights) - spacing * (len(lines)-1)) / 2
    color = (255, 255, 255, st.session_state.text_alpha) if has_photo else (58, 74, 106, 255)
    for line, lh in zip(lines, heights):
        b = d.textbbox((0, 0), line, font=f); x = (w - (b[2]-b[0]))/2
        d.text((x-b[0], y-b[1]), line, font=f, fill=color); y += lh + spacing
    return layer


def compose_preview():
    cell, pad = 105, 3
    w, h = COLS*cell, ROWS*cell
    out = Image.new("RGB", (w+20, h+20), THEME["white"]); d = ImageDraw.Draw(out)
    lookup = st.session_state.lookup; regs = st.session_state.regions
    for rg in sorted(regs.values(), key=lambda x: (x.row, x.col)):
        x1, y1 = 10+rg.col*cell+pad, 10+rg.row*cell+pad
        x2, y2 = 10+(rg.col+rg.colspan)*cell-pad, 10+(rg.row+rg.rowspan)*cell-pad
        selected = bool(rg.cells() & st.session_state.selected)
        d.rectangle((x1,y1,x2,y2), fill=THEME["white"] if rg.image_data else THEME["empty"], outline=THEME["selected"] if selected else THEME["border"], width=4 if selected else 2)
        if rg.image_data:
            try:
                photo = render_photo(rg, (x2-x1, y2-y1)); out.paste(photo, (x1,y1))
                d = ImageDraw.Draw(out); d.rectangle((x1,y1,x2,y2), outline=THEME["selected"] if selected else THEME["border"], width=4 if selected else 2)
            except Exception: d.text((x1+4,y1+4), "이미지 오류", fill="red")
        if rg.text:
            layer = text_layer(rg.text, x2-x1, y2-y1, bool(rg.image_data)); out_rgba=out.convert("RGBA"); out_rgba.alpha_composite(layer, (x1,y1)); out=out_rgba.convert("RGB"); d=ImageDraw.Draw(out)
        if not rg.image_data and not rg.text:
            label = f"{rg.row+1}-{rg.col+1}"
            box = d.textbbox((0, 0), label)
            d.text(((x1+x2-(box[2]-box[0]))//2, (y1+y2-(box[3]-box[1]))//2), label, fill="#8BA4D6")
    return out


def render_print():
    page = Image.new("RGB", (PAGE_W, PAGE_H), (243,247,255)); d = ImageDraw.Draw(page)
    margin = int(.10*DPI); cell=min((PAGE_W-2*margin)//COLS, (PAGE_H-2*margin)//ROWS)
    gw,gh=cell*COLS,cell*ROWS; x0,y0=(PAGE_W-gw)//2,(PAGE_H-gh)//2
    d.rounded_rectangle((x0-24,y0-24,x0+gw+24,y0+gh+24), radius=24, fill="white", outline=THEME["border"], width=4)
    for rg in sorted(st.session_state.regions.values(), key=lambda x:(x.row,x.col)):
        x1,y1=x0+rg.col*cell+6,y0+rg.row*cell+6; x2,y2=x0+(rg.col+rg.colspan)*cell-6,y0+(rg.row+rg.rowspan)*cell-6
        d.rectangle((x1,y1,x2,y2), fill="white" if rg.image_data else THEME["empty"], outline=THEME["border"], width=4)
        if rg.image_data:
            try:
                photo=render_photo(rg,(x2-x1+1,y2-y1+1)); page.paste(photo,(x1,y1)); d=ImageDraw.Draw(page); d.rectangle((x1,y1,x2,y2),outline=THEME["border"],width=4)
            except Exception:
                # Keep the rest of the project exportable if an image codec is
                # unavailable (for example HEIC without pillow-heif).
                pass
        if rg.text:
            layer=text_layer(rg.text,x2-x1+1,y2-y1+1,bool(rg.image_data)); rgba=page.convert("RGBA"); rgba.alpha_composite(layer,(x1,y1)); page=rgba.convert("RGB"); d=ImageDraw.Draw(page)
    return page


def selected_regions():
    return list({st.session_state.lookup[c] for c in st.session_state.selected if c in st.session_state.lookup})


def select_cell(r, c):
    rg=st.session_state.regions[st.session_state.lookup[(r,c)]]
    if rg.cells() <= st.session_state.selected: st.session_state.selected -= rg.cells()
    else: st.session_state.selected |= rg.cells()


def select_rectangle(r0, c0, r1, c1):
    selected = set()
    for r in range(min(r0, r1), max(r0, r1) + 1):
        for c in range(min(c0, c1), max(c0, c1) + 1):
            rid = st.session_state.lookup[(r, c)]
            selected.update(st.session_state.regions[rid].cells())
    st.session_state.selected = selected


def move_photo_by_drag(region: Region, dx: float, dy: float, cell_size: int = 105):
    """Pan the photo under the pointer, using the same normalized offsets as export."""
    source = decode_image(region)
    if source is None:
        return
    pad = 3
    target_w = max(1, region.colspan * cell_size - 2 * pad)
    target_h = max(1, region.rowspan * cell_size - 2 * pad)
    if st.session_state.fit == "cover":
        scale = max(target_w / source.width, target_h / source.height)
        overflow_x = max(0.0, source.width * scale - target_w)
        overflow_y = max(0.0, source.height * scale - target_h)
        # Dragging the visible photo right means shifting the crop window left.
        if overflow_x > 0.5:
            region.image_offset_x = max(-1.0, min(1.0, region.image_offset_x - 2 * dx / overflow_x))
        if overflow_y > 0.5:
            region.image_offset_y = max(-1.0, min(1.0, region.image_offset_y - 2 * dy / overflow_y))
    else:
        scale = min(target_w / source.width, target_h / source.height)
        spare_x = max(0.0, target_w - source.width * scale)
        spare_y = max(0.0, target_h - source.height * scale)
        if spare_x > 0.5:
            region.image_offset_x = max(-1.0, min(1.0, region.image_offset_x + 2 * dx / spare_x))
        if spare_y > 0.5:
            region.image_offset_y = max(-1.0, min(1.0, region.image_offset_y + 2 * dy / spare_y))


def save_project():
    return json.dumps({"rows":ROWS,"cols":COLS,"fit_mode":st.session_state.fit,"photo_bg_mode":st.session_state.bg,"text_size":st.session_state.text_size,"photo_text_alpha":st.session_state.text_alpha,"regions":[asdict(x) for x in st.session_state.regions.values()]},ensure_ascii=False,indent=2).encode()


def load_project(data):
    obj=json.loads(data); regs={}; lookup={}
    for item in obj["regions"]:
        rg=Region(**item); regs[rg.id]=rg
        for c in rg.cells(): lookup[c]=rg.id
    for r in range(ROWS):
        for c in range(COLS):
            if (r,c) not in lookup:
                rid=f"r{r}c{c}"; regs[rid]=Region(rid,r,c); lookup[(r,c)]=rid
    st.session_state.regions,st.session_state.lookup=regs,lookup
    st.session_state.selected=set(); st.session_state.crop_mode_region_id=None; st.session_state.last_grid_tap=None
    st.session_state.fit=obj.get("fit_mode","cover"); st.session_state.bg=obj.get("photo_bg_mode","sky")
    st.session_state.text_size=max(20,min(260,int(obj.get("text_size",obj.get("photo_text_size",100)))))
    st.session_state.text_alpha=max(40,min(255,int(obj.get("photo_text_alpha",150))))


def main():
    st.set_page_config(page_title="Photo Grid Designer", page_icon="🖼️", layout="wide")
    state_init()
    st.markdown("<style>.stApp{background:#F3F7FF}.block-container{padding-top:1.2rem}.stButton>button{border-color:#C9D9FF}</style>",unsafe_allow_html=True)
    st.title("7 × 5 Photo Grid Designer")
    st.caption("4 × 6 inch 인화지 · 600 DPI · 프로젝트 JSON 저장/불러오기")

    with st.expander("프로젝트", expanded=False):
        a,b=st.columns([1,2])
        with a:
            if st.button("새 프로젝트", use_container_width=True):
                st.session_state.regions,st.session_state.lookup=fresh_regions(); st.session_state.selected=set()
                st.session_state.crop_mode_region_id=None; st.session_state.last_grid_tap=None; st.rerun()
            st.download_button("프로젝트 JSON 저장",save_project(),"photo_grid_project.json","application/json",use_container_width=True)
        with b:
            up=st.file_uploader("프로젝트 JSON 열기",type=["json"],key="project_upload")
            if up and st.button("불러오기",key="load_project"):
                try: load_project(up.getvalue()); st.success("프로젝트를 불러왔습니다."); st.rerun()
                except Exception as e: st.error(f"프로젝트를 읽지 못했습니다: {e}")

    left,right=st.columns([1.45,1],gap="large")
    with left:
        st.subheader("사진 배치")
        st.caption("셀 클릭: 선택 · 셀을 가로질러 드래그: 범위 선택 · 사진 셀 안에서 드래그: 사진 이동 · 사진 더블클릭: 자르기")
        preview = compose_preview()
        click = streamlit_image_coordinates(
            preview,
            key="interactive_photo_grid",
            use_column_width="always",
            click_and_drag=True,
        )
        if click and click.get("unix_time") != st.session_state.get("last_grid_click"):
            st.session_state.last_grid_click = click.get("unix_time")
            iw = max(1, int(click.get("width") or preview.width))
            ih = max(1, int(click.get("height") or preview.height))
            start_x = float(click.get("x1", click.get("x", 0)))
            start_y = float(click.get("y1", click.get("y", 0)))
            end_x = float(click.get("x2", start_x))
            end_y = float(click.get("y2", start_y))
            px = start_x * preview.width / iw
            py = start_y * preview.height / ih
            dx = (end_x - start_x) * preview.width / iw
            dy = (end_y - start_y) * preview.height / ih
            cell, origin = 105, 10
            if origin <= px < origin + COLS * cell and origin <= py < origin + ROWS * cell:
                col = int((px - origin) // cell)
                row = int((py - origin) // cell)
                rg = st.session_state.regions[st.session_state.lookup[(row, col)]]
                is_drag = abs(dx) + abs(dy) > 3
                crop_mode_id = st.session_state.get("crop_mode_region_id")
                if crop_mode_id:
                    crop_region = st.session_state.regions.get(crop_mode_id)
                    if is_drag and crop_region and rg.id == crop_region.id and crop_region.image_data:
                        crop_x1 = origin + crop_region.col * cell + 3
                        crop_y1 = origin + crop_region.row * cell + 3
                        crop_x2 = origin + (crop_region.col + crop_region.colspan) * cell - 3
                        crop_y2 = origin + (crop_region.row + crop_region.rowspan) * cell - 3
                        drag_x2 = px + dx
                        drag_y2 = py + dy
                        left = max(crop_x1, min(px, drag_x2))
                        top = max(crop_y1, min(py, drag_y2))
                        right = min(crop_x2, max(px, drag_x2))
                        bottom = min(crop_y2, max(py, drag_y2))
                        if right - left >= 5 and bottom - top >= 5:
                            new_box = ((left - crop_x1) / (crop_x2 - crop_x1),
                                       (top - crop_y1) / (crop_y2 - crop_y1),
                                       (right - crop_x1) / (crop_x2 - crop_x1),
                                       (bottom - crop_y1) / (crop_y2 - crop_y1))
                            old_box = crop_region.crop_box or [0.0, 0.0, 1.0, 1.0]
                            crop_region.crop_box = [
                                old_box[0] + (old_box[2] - old_box[0]) * new_box[0],
                                old_box[1] + (old_box[3] - old_box[1]) * new_box[1],
                                old_box[0] + (old_box[2] - old_box[0]) * new_box[2],
                                old_box[1] + (old_box[3] - old_box[1]) * new_box[3],
                            ]
                            st.session_state.crop_mode_region_id = None
                            st.session_state.selected = crop_region.cells()
                    st.rerun()
                elif is_drag:
                    end_px, end_py = px + dx, py + dy
                    inside_grid = (origin <= end_px < origin + COLS * cell and
                                   origin <= end_py < origin + ROWS * cell)
                    if inside_grid:
                        end_col = int((end_px - origin) // cell)
                        end_row = int((end_py - origin) // cell)
                    else:
                        end_row, end_col = row, col
                    if (end_row, end_col) != (row, col):
                        select_rectangle(row, col, end_row, end_col)
                    elif rg.image_data:
                        try:
                            move_photo_by_drag(rg, dx, dy)
                        except Exception as exc:
                            st.error(f"사진 위치를 바꾸지 못했습니다: {exc}")
                        st.session_state.selected = rg.cells()
                    else:
                        st.session_state.selected = rg.cells()
                else:
                    event_time = float(click.get("unix_time") or 0)
                    previous = st.session_state.get("last_grid_tap")
                    elapsed = event_time - previous[0] if previous else float("inf")
                    if elapsed > 10:
                        elapsed /= 1000.0
                    is_double = (not is_drag and previous and previous[1] == rg.id and 0 <= elapsed <= 0.65)
                    if is_double and rg.image_data:
                        st.session_state.crop_mode_region_id = rg.id
                        st.session_state.selected = rg.cells()
                        st.session_state.last_grid_tap = None
                    elif is_drag:
                        st.session_state.selected = rg.cells()
                    else:
                        st.session_state.last_grid_tap = (event_time, rg.id)
                        select_cell(row, col)
                st.rerun()

        crop_mode_id = st.session_state.get("crop_mode_region_id")
        if crop_mode_id:
            crop_region = st.session_state.regions.get(crop_mode_id)
            if crop_region:
                st.info(f"{crop_region.row+1}행 {crop_region.col+1}열 사진 자르기: 미리보기에서 남길 영역을 드래그하세요.")
                if st.button("자르기 취소", key="cancel_crop_mode"):
                    st.session_state.crop_mode_region_id = None
                    st.rerun()

    with right:
        st.subheader("편집")
        st.write(f"선택: {len(st.session_state.selected)}개 셀 / {len(selected_regions())}개 영역")
        fit=st.radio("사진 맞춤",["cover","contain"],format_func=lambda x:"채우기" if x=="cover" else "전체 보기",horizontal=True,key="fit")
        bg=st.radio("사진 바깥 여백",["sky","white"],format_func=lambda x:"하늘색" if x=="sky" else "흰색",horizontal=True,key="bg")
        st.slider("글씨 크기",20,260,key="text_size",format="%d%%")
        st.slider("사진 위 글씨 투명도",40,255,key="text_alpha")
        chosen=selected_regions(); regs=st.session_state.regions
        if not st.session_state.selected: st.info("먼저 왼쪽에서 셀을 선택하세요.")
        else:
            target=regs[st.session_state.lookup[sorted(st.session_state.selected)[0]]]
            files=st.file_uploader("선택한 셀에 사진 추가 (여러 장이면 선택 셀 순서대로 배치)",type=IMAGE_TYPES,accept_multiple_files=True,key="photos_upload")
            if files and st.button("사진 넣기",use_container_width=True):
                cells=sorted(st.session_state.selected)
                for i,f in enumerate(files):
                    if i>=len(cells): break
                    rg=regs[st.session_state.lookup[cells[i]]]; rg.image_data=base64.b64encode(f.getvalue()).decode(); rg.image_name=f.name
                    rg.rotation=0; rg.flip_h=rg.flip_v=False; rg.image_offset_x=rg.image_offset_y=0; rg.crop_box=None
                st.rerun()
            text=st.text_area("셀 글씨",value=target.text,key=f"text_edit_{target.id}",height=80)
            if st.button("글씨 적용",use_container_width=True):
                target.text=text; st.rerun()
            act1,act2=st.columns(2)
            with act1:
                if st.button("↻ 90° 회전",use_container_width=True):
                    for rid in chosen:
                        if regs[rid].image_data: regs[rid].rotation=(regs[rid].rotation+90)%360
                    st.rerun()
                if st.button("좌우 반전",use_container_width=True):
                    for rid in chosen:
                        if regs[rid].image_data: regs[rid].flip_h=not regs[rid].flip_h
                    st.rerun()
            with act2:
                if st.button("↺ 90° 회전",use_container_width=True):
                    for rid in chosen:
                        if regs[rid].image_data: regs[rid].rotation=(regs[rid].rotation-90)%360
                    st.rerun()
                if st.button("상하 반전",use_container_width=True):
                    for rid in chosen:
                        if regs[rid].image_data: regs[rid].flip_v=not regs[rid].flip_v
                    st.rerun()
            if target.image_data:
                with st.expander("사진 위치 조정"):
                    st.caption("채우기에서는 자를 중심, 전체 보기에서는 여백 안의 사진 위치를 조정합니다.")
                    target.image_offset_x=st.slider("가로 중심",-100,100,int(target.image_offset_x*100),key=f"ox_{target.id}")/100
                    target.image_offset_y=st.slider("세로 중심",-100,100,int(target.image_offset_y*100),key=f"oy_{target.id}")/100
                    if st.button("사진 원위치",key="reset_transform"):
                        for rid in chosen:
                            regs[rid].rotation=0; regs[rid].flip_h=regs[rid].flip_v=False; regs[rid].image_offset_x=regs[rid].image_offset_y=0; regs[rid].crop_box=None
                        st.rerun()
                    if target.crop_box and st.button("자르기 초기화", key=f"reset_crop_{target.id}"):
                        for rid in chosen: regs[rid].crop_box=None
                        st.rerun()
            m1,m2,m3=st.columns(3)
            with m1:
                if st.button("셀 합치기",use_container_width=True):
                    cells=st.session_state.selected; rs=[x[0] for x in cells]; cs=[x[1] for x in cells]
                    rect={(r,c) for r in range(min(rs),max(rs)+1) for c in range(min(cs),max(cs)+1)}
                    if cells!=rect: st.error("빈틈 없는 직사각형으로 선택해야 합니다.")
                    else:
                        old=chosen; first=regs[old[0]] if old else None; nr=Region(f"m{uuid.uuid4().hex[:8]}",min(rs),min(cs),max(rs)-min(rs)+1,max(cs)-min(cs)+1)
                        if first:
                            for key in ("image_data","image_name","text","rotation","flip_h","flip_v","image_offset_x","image_offset_y","crop_box"): setattr(nr,key,getattr(first,key))
                        for rid in old: regs.pop(rid,None)
                        regs[nr.id]=nr
                        for cell in rect: st.session_state.lookup[cell]=nr.id
                        st.session_state.selected=rect; st.rerun()
            with m2:
                if st.button("셀 분리",use_container_width=True):
                    for rid in chosen:
                        rg=regs[rid]
                        if rg.rowspan*rg.colspan<=1: continue
                        cells=rg.cells(); regs.pop(rid)
                        for r,c in cells:
                            nid=f"r{r}c{c}"; regs[nid]=Region(nid,r,c); st.session_state.lookup[(r,c)]=nid
                        st.session_state.selected|=cells
                    st.rerun()
            with m3:
                if st.button("선택 삭제",use_container_width=True):
                    for rid in chosen:
                        regs[rid].image_data=""; regs[rid].image_name=""; regs[rid].text=""; regs[rid].rotation=0; regs[rid].flip_h=regs[rid].flip_v=False; regs[rid].image_offset_x=regs[rid].image_offset_y=0; regs[rid].crop_box=None
                    st.rerun()
            if st.button("선택 해제",use_container_width=True): st.session_state.selected=set(); st.rerun()
            photo_regions=[regs[rid] for rid in chosen if regs[rid].image_data]
            if photo_regions:
                st.markdown("**사진 이동/교환**")
                names={f"{rg.row+1}행 {rg.col+1}열 · {rg.image_name or '사진'}":rg.id for rg in photo_regions}
                src_label=st.selectbox("옮길 사진",list(names),key="move_src")
                destination=st.selectbox("도착 셀",[f"{r+1}행 {c+1}열" for r in range(ROWS) for c in range(COLS)],key="move_dst")
                if st.button("이동 / 사진 교환"):
                    src=regs[names[src_label]]; rr,cc=map(lambda x:int(x)-1,destination.replace("행 ","|").replace("열","").split("|")); dst=regs[st.session_state.lookup[(rr,cc)]]
                    attrs=("image_data","image_name","rotation","flip_h","flip_v","image_offset_x","image_offset_y","crop_box")
                    a={k:getattr(src,k) for k in attrs}; b={k:getattr(dst,k) for k in attrs}
                    for k in attrs: setattr(src,k,b[k]); setattr(dst,k,a[k])
                    st.session_state.selected=dst.cells(); st.rerun()

        st.divider(); st.subheader("내보내기")
        print_signature = (
            st.session_state.fit, st.session_state.bg,
            st.session_state.text_size, st.session_state.text_alpha,
            tuple((rg.id, rg.row, rg.col, rg.rowspan, rg.colspan, rg.image_data, rg.image_name,
                   rg.text, rg.rotation, rg.flip_h, rg.flip_v, rg.image_offset_x,
                   rg.image_offset_y, tuple(rg.crop_box) if rg.crop_box else None)
                  for rg in sorted(regs.values(), key=lambda item: item.id)),
        )
        for cache_name in ("print_png_cache", "print_pdf_cache"):
            old_cache = st.session_state.get(cache_name)
            if old_cache and old_cache[0] != print_signature:
                st.session_state.pop(cache_name, None)
        png_col, pdf_col = st.columns(2)
        with png_col:
            if st.button("PNG 만들기", key="make_print_png", use_container_width=True):
                with st.spinner("600 DPI PNG를 만드는 중…"):
                    page = render_print(); buf = io.BytesIO(); page.save(buf, format="PNG", dpi=(DPI, DPI))
                    st.session_state.print_png_cache = (print_signature, buf.getvalue())
            png_result = st.session_state.get("print_png_cache")
            if png_result and png_result[0] == print_signature:
                st.download_button("PNG 다운로드 · 600 DPI", png_result[1], "photo_grid_4x6_600dpi.png", "image/png", use_container_width=True)
        with pdf_col:
            if st.button("PDF 만들기", key="make_print_pdf", use_container_width=True):
                with st.spinner("600 DPI PDF를 만드는 중…"):
                    png_result = st.session_state.get("print_png_cache")
                    if png_result and png_result[0] == print_signature:
                        page = Image.open(io.BytesIO(png_result[1])).convert("RGB")
                    else:
                        page = render_print()
                    pdfbuf = io.BytesIO(); page.save(pdfbuf, format="PDF", resolution=DPI)
                    st.session_state.print_pdf_cache = (print_signature, pdfbuf.getvalue())
            pdf_result = st.session_state.get("print_pdf_cache")
            if pdf_result and pdf_result[0] == print_signature:
                st.download_button("PDF 다운로드 / 인쇄", pdf_result[1], "photo_grid_4x6_600dpi.pdf", "application/pdf", use_container_width=True)
        st.caption("PDF를 열어 인쇄할 때 실제 크기(100%)로 출력하세요.")


if __name__ == "__main__": main()
