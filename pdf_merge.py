import copy
import ctypes
from ctypes import wintypes
import io
import os
import posixpath
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import xml.etree.ElementTree as ET
import zipfile
from pypdf import PdfReader, PdfWriter


# ---------------------------------------------------------------------------
# Windows High-DPI & Native Drag-and-Drop (WM_DROPFILES with Unicode support)
# ---------------------------------------------------------------------------
def enable_high_dpi():
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


WM_DROPFILES = 0x0233
GWL_WNDPROC = -4
MSGFLT_ALLOW = 1

if sys.platform == "win32":
    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(
        LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )

    SetWindowLongPtrW = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, WNDPROC]
    SetWindowLongPtrW.restype = ctypes.c_void_p

    CallWindowProcW = user32.CallWindowProcW
    CallWindowProcW.argtypes = [
        ctypes.c_void_p,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    CallWindowProcW.restype = LRESULT

    DragAcceptFiles = shell32.DragAcceptFiles
    DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
    DragAcceptFiles.restype = None

    DragQueryFileW = shell32.DragQueryFileW
    DragQueryFileW.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        wintypes.LPWSTR,
        wintypes.UINT,
    ]
    DragQueryFileW.restype = wintypes.UINT

    DragFinish = shell32.DragFinish
    DragFinish.argtypes = [wintypes.HANDLE]
    DragFinish.restype = None


class Win32FileDropTarget:
    """Native 64-bit/32-bit Unicode WM_DROPFILES hook for Tkinter widgets."""

    def __init__(self, widget: tk.Misc, callback, hwnd: int = None):
        self.widget = widget
        self.callback = callback
        self.hwnd = hwnd
        self._old_wndproc = None
        self._new_wndproc = None
        if sys.platform == "win32":
            self._hook()

    def _hook(self):
        self.widget.update_idletasks()
        hwnd = self.hwnd or self.widget.winfo_id()

        # Allow drag-and-drop even if running elevated (UIPI bypass for WM_DROPFILES)
        try:
            ChangeWindowMessageFilterEx = user32.ChangeWindowMessageFilterEx
            ChangeWindowMessageFilterEx.argtypes = [
                wintypes.HWND,
                wintypes.UINT,
                wintypes.DWORD,
                ctypes.c_void_p,
            ]
            ChangeWindowMessageFilterEx(hwnd, WM_DROPFILES, 1, None)
            ChangeWindowMessageFilterEx(hwnd, 0x0049, 1, None)  # WM_COPYGLOBALDATA
        except Exception:
            pass

        DragAcceptFiles(hwnd, True)

        def _wndproc(hWnd, msg, wParam, lParam):
            if msg == WM_DROPFILES:
                hDrop = wintypes.HANDLE(wParam)
                count = DragQueryFileW(hDrop, 0xFFFFFFFF, None, 0)
                files = []
                for i in range(count):
                    length = DragQueryFileW(hDrop, i, None, 0)
                    buf = ctypes.create_unicode_buffer(length + 1)
                    DragQueryFileW(hDrop, i, buf, length + 1)
                    files.append(buf.value)
                DragFinish(hDrop)
                self.widget.after(10, lambda f=files: self.callback(f))
                return 0
            return CallWindowProcW(self._old_wndproc, hWnd, msg, wParam, lParam)

        self._new_wndproc = WNDPROC(_wndproc)
        self._old_wndproc = SetWindowLongPtrW(hwnd, GWL_WNDPROC, self._new_wndproc)


# ---------------------------------------------------------------------------
# OWPML / HWPX Inspection & Merging Engine
# ---------------------------------------------------------------------------
HWPX_NAMESPACES = {
    "ha": "http://www.hancom.co.kr/hwpml/2011/app",
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hp10": "http://www.hancom.co.kr/hwpml/2016/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
    "hc": "http://www.hancom.co.kr/hwpml/2011/core",
    "hh": "http://www.hancom.co.kr/hwpml/2011/head",
    "hhs": "http://www.hancom.co.kr/hwpml/2011/history",
    "hm": "http://www.hancom.co.kr/hwpml/2011/master-page",
    "hpf": "http://www.hancom.co.kr/schema/2011/hpf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "opf": "http://www.idpf.org/2007/opf/",
    "ooxmlchart": "http://www.hancom.co.kr/hwpml/2016/ooxmlchart",
    "hwpunitchar": "http://www.hancom.co.kr/hwpml/2016/HwpUnitChar",
    "epub": "http://www.idpf.org/2007/ops",
    "config": "urn:oasis:names:tc:opendocument:xmlns:config:1.0",
}

for _pfx, _uri in HWPX_NAMESPACES.items():
    ET.register_namespace(_pfx, _uri)


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _register_xml_namespaces(xml_bytes: bytes):
    try:
        for _, (prefix, uri) in ET.iterparse(io.BytesIO(xml_bytes), events=("start-ns",)):
            if prefix and uri:
                ET.register_namespace(prefix, uri)
    except Exception:
        pass


def _get_section_paths(zf: zipfile.ZipFile) -> list[str]:
    names = set(zf.namelist())
    if "Contents/content.hpf" in names:
        try:
            hpf_bytes = zf.read("Contents/content.hpf")
            _register_xml_namespaces(hpf_bytes)
            root = ET.fromstring(hpf_bytes)
            id_to_href = {}
            for elem in root.iter():
                if _local_name(elem.tag) == "item":
                    item_id = elem.get("id")
                    href = elem.get("href")
                    if item_id and href:
                        id_to_href[item_id] = href
            sec_paths = []
            for elem in root.iter():
                if _local_name(elem.tag) == "itemref":
                    idref = elem.get("idref")
                    href = id_to_href.get(idref, "")
                    if href:
                        if not href.startswith("Contents/") and f"Contents/{href}" in names:
                            href = f"Contents/{href}"
                        if href in names and posixpath.basename(href).startswith("section"):
                            sec_paths.append(href)
            if sec_paths:
                return sec_paths
        except Exception:
            pass

    sec_files = [
        n
        for n in names
        if re.match(r"^Contents/section\d+\.xml$", n, re.IGNORECASE)
    ]
    sec_files.sort(
        key=lambda s: int(re.search(r"(\d+)", posixpath.basename(s)).group(1))
    )
    return sec_files


def inspect_hwpx(path: str) -> dict:
    """Validate an HWPX file and return estimated page count and paragraph count."""
    if not zipfile.is_zipfile(path):
        raise ValueError("유효한 HWPX (ZIP) 컨테이너 파일이 아닙니다.")

    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        if "Contents/header.xml" not in names:
            raise ValueError("Contents/header.xml 파일이 없는 손상된 HWPX 문서입니다.")
        sec_paths = _get_section_paths(zf)
        if not sec_paths:
            raise ValueError("본문 섹션(Contents/section*.xml)이 없습니다.")

        total_paras = 0
        estimated_pages = 0

        for sp in sec_paths:
            sec_bytes = zf.read(sp)
            _register_xml_namespaces(sec_bytes)
            sec_root = ET.fromstring(sec_bytes)
            sec_pages = 0
            for child in list(sec_root):
                if _local_name(child.tag) == "p":
                    total_paras += 1
                    is_new_page = child.get("pageBreak") == "1"
                    if not is_new_page:
                        for sub in child.iter():
                            if _local_name(sub.tag) == "lineseg" and sub.get("vertpos") == "0":
                                is_new_page = True
                                break
                    if is_new_page:
                        sec_pages += 1
            estimated_pages += max(1, sec_pages)

        return {
            "pages": max(1, estimated_pages),
            "paragraphs": max(1, total_paras),
        }


class HwpxMerger:
    """Pure-Python OWPML (.hwpx) merger preserving styles, fonts, tables, and BinData images."""

    @staticmethod
    def _element_signature(elem: ET.Element, ignore_attrs=("id",)) -> str:
        clone = copy.deepcopy(elem)
        for k in ignore_attrs:
            if k in clone.attrib:
                del clone.attrib[k]
        return ET.tostring(clone, encoding="unicode")

    @classmethod
    def _merge_header_and_build_maps(
        cls, base_header_root: ET.Element, src_header_root: ET.Element, bin_map: dict
    ) -> dict:
        maps = {
            "font": {},        # (lang, old_id) -> new_id
            "borderFill": {},  # old_id -> new_id
            "charPr": {},      # old_id -> new_id
            "tabPr": {},       # old_id -> new_id
            "numbering": {},   # old_id -> new_id
            "bullet": {},      # old_id -> new_id
            "paraPr": {},      # old_id -> new_id
            "style": {},       # old_id -> new_id
            "bin": bin_map,    # old_bin_id -> new_bin_id
        }

        base_ref = next(
            (e for e in base_header_root.iter() if _local_name(e.tag) == "refList"),
            None,
        )
        src_ref = next(
            (e for e in src_header_root.iter() if _local_name(e.tag) == "refList"),
            None,
        )
        if base_ref is None or src_ref is None:
            return maps

        def find_group(parent, local_tag):
            for ch in parent:
                if _local_name(ch.tag) == local_tag:
                    return ch
            return None

        # 1. fontfaces (per language)
        base_ff_group = find_group(base_ref, "fontfaces")
        src_ff_group = find_group(src_ref, "fontfaces")
        if base_ff_group is not None and src_ff_group is not None:
            base_langs = {
                ff.get("lang"): ff
                for ff in base_ff_group
                if _local_name(ff.tag) == "fontface"
            }
            for src_ff in src_ff_group:
                if _local_name(src_ff.tag) != "fontface":
                    continue
                lang = src_ff.get("lang", "")
                if lang not in base_langs:
                    new_ff = copy.deepcopy(src_ff)
                    base_ff_group.append(new_ff)
                    base_langs[lang] = new_ff
                    for f_elem in new_ff:
                        if _local_name(f_elem.tag) == "font":
                            fid = f_elem.get("id")
                            if fid is not None:
                                maps["font"][(lang, fid)] = fid
                    continue

                dst_ff = base_langs[lang]
                existing_fonts = {}
                max_id = -1
                for f_elem in dst_ff:
                    if _local_name(f_elem.tag) == "font":
                        fid = f_elem.get("id", "0")
                        try:
                            max_id = max(max_id, int(fid))
                        except ValueError:
                            pass
                        key = (f_elem.get("face", ""), f_elem.get("type", ""))
                        if key not in existing_fonts:
                            existing_fonts[key] = fid

                for f_elem in src_ff:
                    if _local_name(f_elem.tag) != "font":
                        continue
                    old_id = f_elem.get("id")
                    if old_id is None:
                        continue
                    key = (f_elem.get("face", ""), f_elem.get("type", ""))
                    if key in existing_fonts:
                        maps["font"][(lang, old_id)] = existing_fonts[key]
                    else:
                        max_id += 1
                        new_id = str(max_id)
                        new_f = copy.deepcopy(f_elem)
                        new_f.set("id", new_id)
                        dst_ff.append(new_f)
                        existing_fonts[key] = new_id
                        maps["font"][(lang, old_id)] = new_id

                font_count = sum(1 for e in dst_ff if _local_name(e.tag) == "font")
                dst_ff.set("fontCnt", str(font_count))

        def merge_collection(group_tag: str, item_tag: str, map_key: str, start_id: int, transform_fn=None):
            src_grp = find_group(src_ref, group_tag)
            if src_grp is None:
                return
            dst_grp = find_group(base_ref, group_tag)
            if dst_grp is None:
                dst_grp = ET.Element(src_grp.tag, src_grp.attrib)
                base_ref.append(dst_grp)

            sig_to_id = {}
            max_id = start_id - 1
            for item in dst_grp:
                if _local_name(item.tag) == item_tag:
                    iid = item.get("id")
                    if iid is not None:
                        try:
                            max_id = max(max_id, int(iid))
                        except ValueError:
                            pass
                        sig_to_id[cls._element_signature(item)] = iid

            for src_item in src_grp:
                if _local_name(src_item.tag) != item_tag:
                    continue
                old_id = src_item.get("id")
                if old_id is None:
                    continue
                new_item = copy.deepcopy(src_item)
                if transform_fn is not None:
                    transform_fn(new_item)
                sig = cls._element_signature(new_item)
                if sig in sig_to_id:
                    maps[map_key][old_id] = sig_to_id[sig]
                else:
                    max_id += 1
                    new_id = str(max_id)
                    new_item.set("id", new_id)
                    dst_grp.append(new_item)
                    sig_to_id[sig] = new_id
                    maps[map_key][old_id] = new_id

            cnt = sum(1 for e in dst_grp if _local_name(e.tag) == item_tag)
            dst_grp.set("itemCnt", str(cnt))

        # 2. borderFills (1-based)
        def transform_border_fill(elem: ET.Element):
            for sub in elem.iter():
                if "binaryItemIDRef" in sub.attrib:
                    old_bin = sub.get("binaryItemIDRef")
                    if old_bin in maps["bin"]:
                        sub.set("binaryItemIDRef", maps["bin"][old_bin])

        merge_collection("borderFills", "borderFill", "borderFill", 1, transform_border_fill)

        # 3. charProperties (0-based)
        lang_attr_map = {
            "hangul": "HANGUL",
            "latin": "LATIN",
            "hanja": "HANJA",
            "japanese": "JAPANESE",
            "other": "OTHER",
            "symbol": "SYMBOL",
            "user": "USER",
        }

        def transform_char_pr(elem: ET.Element):
            if "borderFillIDRef" in elem.attrib:
                old_bf = elem.get("borderFillIDRef")
                if old_bf in maps["borderFill"]:
                    elem.set("borderFillIDRef", maps["borderFill"][old_bf])
            for sub in elem.iter():
                if _local_name(sub.tag) == "fontRef":
                    for attr_name, lang_code in lang_attr_map.items():
                        if attr_name in sub.attrib:
                            old_f = sub.get(attr_name)
                            if (lang_code, old_f) in maps["font"]:
                                sub.set(attr_name, maps["font"][(lang_code, old_f)])
                if "borderFillIDRef" in sub.attrib and sub is not elem:
                    old_bf = sub.get("borderFillIDRef")
                    if old_bf in maps["borderFill"]:
                        sub.set("borderFillIDRef", maps["borderFill"][old_bf])

        merge_collection("charProperties", "charPr", "charPr", 0, transform_char_pr)

        # 4. tabProperties (0-based)
        merge_collection("tabProperties", "tabPr", "tabPr", 0, None)

        # 5. numberings (1-based)
        def transform_numbering_or_bullet(elem: ET.Element):
            for sub in elem.iter():
                if "charPrIDRef" in sub.attrib:
                    old_cp = sub.get("charPrIDRef")
                    if old_cp in maps["charPr"]:
                        sub.set("charPrIDRef", maps["charPr"][old_cp])

        merge_collection("numberings", "numbering", "numbering", 1, transform_numbering_or_bullet)

        # 6. bullets (1-based)
        merge_collection("bullets", "bullet", "bullet", 1, transform_numbering_or_bullet)

        # 7. paraProperties (0-based)
        def transform_para_pr(elem: ET.Element):
            if "tabPrIDRef" in elem.attrib:
                old_tp = elem.get("tabPrIDRef")
                if old_tp in maps["tabPr"]:
                    elem.set("tabPrIDRef", maps["tabPr"][old_tp])
            for sub in elem.iter():
                lname = _local_name(sub.tag)
                if lname == "heading":
                    htype = sub.get("type", "")
                    idref = sub.get("idRef")
                    if htype == "NUMBER" and idref in maps["numbering"]:
                        sub.set("idRef", maps["numbering"][idref])
                    elif htype == "BULLET" and idref in maps["bullet"]:
                        sub.set("idRef", maps["bullet"][idref])
                elif lname == "border":
                    old_bf = sub.get("borderFillIDRef")
                    if old_bf in maps["borderFill"]:
                        sub.set("borderFillIDRef", maps["borderFill"][old_bf])

        merge_collection("paraProperties", "paraPr", "paraPr", 0, transform_para_pr)

        # 8. styles (0-based)
        def transform_style(elem: ET.Element):
            if "paraPrIDRef" in elem.attrib:
                old_pp = elem.get("paraPrIDRef")
                if old_pp in maps["paraPr"]:
                    elem.set("paraPrIDRef", maps["paraPr"][old_pp])
            if "charPrIDRef" in elem.attrib:
                old_cp = elem.get("charPrIDRef")
                if old_cp in maps["charPr"]:
                    elem.set("charPrIDRef", maps["charPr"][old_cp])

        merge_collection("styles", "style", "style", 0, transform_style)

        return maps

    @staticmethod
    def _remap_paragraph_refs(p_elem: ET.Element, maps: dict):
        for elem in p_elem.iter():
            if "paraPrIDRef" in elem.attrib:
                val = elem.get("paraPrIDRef")
                if val in maps["paraPr"]:
                    elem.set("paraPrIDRef", maps["paraPr"][val])
            if "styleIDRef" in elem.attrib:
                val = elem.get("styleIDRef")
                if val in maps["style"]:
                    elem.set("styleIDRef", maps["style"][val])
            if "charPrIDRef" in elem.attrib:
                val = elem.get("charPrIDRef")
                if val in maps["charPr"]:
                    elem.set("charPrIDRef", maps["charPr"][val])
            if "borderFillIDRef" in elem.attrib:
                val = elem.get("borderFillIDRef")
                if val in maps["borderFill"]:
                    elem.set("borderFillIDRef", maps["borderFill"][val])
            if "binaryItemIDRef" in elem.attrib:
                val = elem.get("binaryItemIDRef")
                if val in maps["bin"]:
                    elem.set("binaryItemIDRef", maps["bin"][val])
            if "tabPrIDRef" in elem.attrib:
                val = elem.get("tabPrIDRef")
                if val in maps["tabPr"]:
                    elem.set("tabPrIDRef", maps["tabPr"][val])

    @staticmethod
    def _strip_section_definitions(p_elem: ET.Element):
        """Remove secPr and colPr from the first paragraph of an appended section."""
        for child in list(p_elem):
            lname = _local_name(child.tag)
            if lname == "linesegarray":
                p_elem.remove(child)
            elif lname == "run":
                for rchild in list(child):
                    rlname = _local_name(rchild.tag)
                    if rlname in ("secPr", "colPr"):
                        child.remove(rchild)
                    elif rlname == "ctrl":
                        for cchild in list(rchild):
                            if _local_name(cchild.tag) in ("colPr", "secPr", "pageNum"):
                                rchild.remove(cchild)
                        if len(rchild) == 0 and not (rchild.text and rchild.text.strip()):
                            child.remove(rchild)

    @classmethod
    def merge_hwpx_files(
        cls,
        items: list[dict],
        page_break_between_files: bool,
        progress_callback,
        is_cancelled_fn,
    ) -> bytes:
        if not items:
            raise ValueError("병합할 HWPX 파일이 없습니다.")

        total_pages = sum(item["pages"] for item in items)
        completed_pages = 0
        step_delay = min(0.03, 0.8 / max(1, total_pages))

        # Load base HWPX archive (first file)
        base_path = items[0]["path"]
        base_entries: dict[str, bytes] = {}
        with zipfile.ZipFile(base_path, "r") as zf:
            for name in zf.namelist():
                base_entries[name] = zf.read(name)
            base_sec_paths = _get_section_paths(zf)

        if not base_sec_paths:
            raise ValueError(f"첫 번째 파일({items[0]['name']})에 유효한 섹션이 없습니다.")

        _register_xml_namespaces(base_entries["Contents/header.xml"])
        base_header_root = ET.fromstring(base_entries["Contents/header.xml"])

        target_sec_path = base_sec_paths[-1]
        _register_xml_namespaces(base_entries[target_sec_path])
        target_sec_root = ET.fromstring(base_entries[target_sec_path])

        base_hpf_root = None
        base_manifest = None
        existing_bin_ids = set()
        if "Contents/content.hpf" in base_entries:
            _register_xml_namespaces(base_entries["Contents/content.hpf"])
            base_hpf_root = ET.fromstring(base_entries["Contents/content.hpf"])
            for elem in base_hpf_root.iter():
                if _local_name(elem.tag) == "manifest":
                    base_manifest = elem
                elif _local_name(elem.tag) == "item":
                    iid = elem.get("id")
                    if iid:
                        existing_bin_ids.add(iid)

        # Advance progress for the first (base) file's pages
        first_pages = items[0]["pages"]
        for _ in range(first_pages):
            if is_cancelled_fn():
                return b""
            completed_pages += 1
            progress_callback(completed_pages, total_pages, items[0]["name"])
            if step_delay > 0:
                time.sleep(step_delay)

        # Merge subsequent HWPX files
        for doc_idx, item in enumerate(items[1:], start=2):
            if is_cancelled_fn():
                return b""

            with zipfile.ZipFile(item["path"], "r") as zf:
                src_names = set(zf.namelist())
                sec_paths = _get_section_paths(zf)
                if not sec_paths or "Contents/header.xml" not in src_names:
                    raise ValueError(f"{item['name']}: 유효하지 않은 HWPX 구조입니다.")

                # 1. Copy BinData items and build bin_map
                bin_map = {}
                if "Contents/content.hpf" in src_names:
                    src_hpf_bytes = zf.read("Contents/content.hpf")
                    _register_xml_namespaces(src_hpf_bytes)
                    src_hpf_root = ET.fromstring(src_hpf_bytes)
                    for elem in src_hpf_root.iter():
                        if _local_name(elem.tag) == "item":
                            href = elem.get("href", "")
                            old_id = elem.get("id", "")
                            if href.startswith("BinData/") or f"BinData/{posixpath.basename(href)}" in src_names:
                                zip_bin_path = href if href in src_names else f"BinData/{posixpath.basename(href)}"
                                if zip_bin_path in src_names:
                                    ext = posixpath.splitext(zip_bin_path)[1]
                                    new_id = f"merged_bin_{doc_idx}_{old_id}"
                                    while new_id in existing_bin_ids:
                                        new_id += "_"
                                    existing_bin_ids.add(new_id)
                                    new_href = f"BinData/{new_id}{ext}"
                                    base_entries[new_href] = zf.read(zip_bin_path)
                                    bin_map[old_id] = new_id
                                    if base_manifest is not None:
                                        new_item_elem = copy.deepcopy(elem)
                                        new_item_elem.set("id", new_id)
                                        new_item_elem.set("href", new_href)
                                        base_manifest.append(new_item_elem)

                # 2. Merge header.xml definitions and build ID mapping table
                src_header_bytes = zf.read("Contents/header.xml")
                _register_xml_namespaces(src_header_bytes)
                src_header_root = ET.fromstring(src_header_bytes)
                id_maps = cls._merge_header_and_build_maps(
                    base_header_root, src_header_root, bin_map
                )

                # 3. Extract and transform all paragraphs from source sections
                appended_paragraphs = []
                for sp in sec_paths:
                    sec_bytes = zf.read(sp)
                    _register_xml_namespaces(sec_bytes)
                    sec_root = ET.fromstring(sec_bytes)
                    for child in list(sec_root):
                        if _local_name(child.tag) == "p":
                            p_copy = copy.deepcopy(child)
                            cls._remap_paragraph_refs(p_copy, id_maps)
                            # Remove cached linesegarray so Hangul reflows cleanly
                            for sub in list(p_copy):
                                if _local_name(sub.tag) == "linesegarray":
                                    p_copy.remove(sub)
                            appended_paragraphs.append(p_copy)

                if appended_paragraphs:
                    first_p = appended_paragraphs[0]
                    cls._strip_section_definitions(first_p)
                    first_p.set("pageBreak", "1" if page_break_between_files else "0")
                    for p_elem in appended_paragraphs:
                        target_sec_root.append(p_elem)

                # Advance progress for this file's pages
                for _ in range(item["pages"]):
                    if is_cancelled_fn():
                        return b""
                    completed_pages += 1
                    progress_callback(completed_pages, total_pages, item["name"])
                    if step_delay > 0:
                        time.sleep(step_delay)

        # Serialize updated XML trees back into base_entries
        base_entries["Contents/header.xml"] = ET.tostring(
            base_header_root, encoding="utf-8", xml_declaration=True
        )
        base_entries[target_sec_path] = ET.tostring(
            target_sec_root, encoding="utf-8", xml_declaration=True
        )
        if base_hpf_root is not None:
            base_entries["Contents/content.hpf"] = ET.tostring(
                base_hpf_root, encoding="utf-8", xml_declaration=True
            )

        # Update Preview/PrvText.txt with combined text
        all_texts = []
        for elem in target_sec_root.iter():
            if _local_name(elem.tag) == "t" and elem.text:
                all_texts.append(elem.text)
        if "Preview/PrvText.txt" in base_entries:
            base_entries["Preview/PrvText.txt"] = "\r\n".join(all_texts).encode("utf-8")

        # Write out valid OWPML ZIP container (mimetype first, uncompressed)
        out_buf = io.BytesIO()
        with zipfile.ZipFile(out_buf, "w", compression=zipfile.ZIP_DEFLATED) as out_zf:
            if "mimetype" in base_entries:
                zi = zipfile.ZipInfo("mimetype")
                zi.compress_type = zipfile.ZIP_STORED
                out_zf.writestr(zi, base_entries["mimetype"])
            for name, data in base_entries.items():
                if name == "mimetype":
                    continue
                out_zf.writestr(name, data)

        return out_buf.getvalue()


# ---------------------------------------------------------------------------
# Dedicated Category Merge Pane Base Class & Subclasses (PDF / HWPX)
# ---------------------------------------------------------------------------
class BaseMergePane(ttk.Frame):
    """Base screen for a specific document category (PDF or HWPX)."""

    category_label = "문서"
    file_ext = ".pdf"
    file_dialog_types = [("모든 파일 (*.*)", "*.*")]
    default_save_name = "merged.pdf"

    def __init__(self, parent: tk.Misc, root: tk.Tk):
        super().__init__(parent, padding=12)
        self.root = root
        self.items: list[dict] = []
        self._drag_start_index = None
        self._drag_active = False

        self._build_pane_ui()

    def _build_pane_ui(self):
        # Top instruction & toolbar
        top_frame = ttk.Frame(self)
        top_frame.pack(fill=tk.X, pady=(0, 8))

        # Pack button bar on the right FIRST so it never gets clipped
        btn_bar = ttk.Frame(top_frame)
        btn_bar.pack(side=tk.RIGHT, padx=(12, 0))

        self.btn_add = ttk.Button(
            btn_bar,
            text="파일 추가...",
            width=11,
            style="Action.TButton",
            command=self.add_files_dialog,
        )
        self.btn_add.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_remove = ttk.Button(
            btn_bar,
            text="선택 삭제",
            width=11,
            style="Action.TButton",
            command=self.remove_selected,
        )
        self.btn_remove.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_clear = ttk.Button(
            btn_bar,
            text="전체 삭제",
            width=11,
            style="Action.TButton",
            command=self.clear_all,
        )
        self.btn_clear.pack(side=tk.LEFT)

        info_lbl = ttk.Label(
            top_frame,
            text=f"탐색기에서 {self.category_label} 파일들을 아래 목록에 드래그 앤 드롭하거나 [파일 추가...] 버튼을 클릭하세요.\n"
            "목록에 추가된 파일은 마우스 드래그 앤 드롭으로 병합 순서를 변경할 수 있습니다.",
            justify=tk.LEFT,
        )
        info_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Center list area
        list_group = ttk.LabelFrame(
            self,
            text=f"병합할 {self.category_label} 파일 목록 (드래그하여 순서 변경)",
            padding=6,
        )
        list_group.pack(fill=tk.BOTH, expand=True)

        tree_container = ttk.Frame(list_group)
        tree_container.pack(fill=tk.BOTH, expand=True)

        columns = ("order", "name", "pages", "size", "path")
        self.tree = ttk.Treeview(
            tree_container,
            columns=columns,
            show="headings",
            selectmode="extended",
        )
        self.tree.heading("order", text="순서")
        self.tree.heading("name", text="파일명")
        self.tree.heading("pages", text="페이지 수")
        self.tree.heading("size", text="크기")
        self.tree.heading("path", text="전체 경로")

        self.tree.column("order", width=50, anchor=tk.CENTER, stretch=False)
        self.tree.column("name", width=210, anchor=tk.W, stretch=True)
        self.tree.column("pages", width=95, anchor=tk.CENTER, stretch=False)
        self.tree.column("size", width=85, anchor=tk.E, stretch=False)
        self.tree.column("path", width=270, anchor=tk.W, stretch=True)

        vsb = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_container, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_container.rowconfigure(0, weight=1)
        tree_container.columnconfigure(0, weight=1)

        # Bind internal drag-and-drop reordering & keyboard shortcuts on Treeview
        self.tree.bind("<ButtonPress-1>", self._on_tree_press)
        self.tree.bind("<B1-Motion>", self._on_tree_motion)
        self.tree.bind("<ButtonRelease-1>", self._on_tree_release)
        self.tree.bind("<Delete>", lambda e: self.remove_selected())

        # Hook file drop directly onto this pane's Treeview as well
        self._drop_tree = Win32FileDropTarget(self.tree, self.on_external_files_dropped)

        # Bottom controls
        self.bottom_frame = ttk.Frame(self)
        self.bottom_frame.pack(fill=tk.X, pady=(10, 0))

        order_btn_frame = ttk.Frame(self.bottom_frame)
        order_btn_frame.pack(side=tk.LEFT)

        self.btn_up = ttk.Button(
            order_btn_frame, text="▲ 위로", width=8, command=lambda: self.move_selected(-1)
        )
        self.btn_up.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_down = ttk.Button(
            order_btn_frame, text="▼ 아래로", width=8, command=lambda: self.move_selected(1)
        )
        self.btn_down.pack(side=tk.LEFT, padx=(0, 12))

        self.btn_merge = ttk.Button(
            self.bottom_frame,
            text=f"MERGE ({self.category_label} 병합하기)",
            style="Merge.TButton",
            command=self.start_merge,
        )
        self.btn_merge.pack(side=tk.RIGHT)

        self.status_var = tk.StringVar(value="총 0개 파일 (0 페이지)")
        self.lbl_status = ttk.Label(self.bottom_frame, textvariable=self.status_var)
        self.lbl_status.pack(side=tk.LEFT, padx=4)

        self._build_extra_bottom_controls(self.bottom_frame)

    def _build_extra_bottom_controls(self, bottom_frame: ttk.Frame):
        """Hook for subclass-specific options in the bottom bar."""
        pass

    @staticmethod
    def _format_size(num_bytes: int) -> str:
        if num_bytes < 1024:
            return f"{num_bytes} B"
        elif num_bytes < 1024 * 1024:
            return f"{num_bytes / 1024:.1f} KB"
        else:
            return f"{num_bytes / (1024 * 1024):.2f} MB"

    def add_files_dialog(self):
        file_paths = filedialog.askopenfilenames(
            parent=self.root,
            title=f"추가할 {self.category_label} 파일 선택",
            filetypes=self.file_dialog_types,
        )
        if file_paths:
            self.add_file_paths(list(file_paths))

    def on_external_files_dropped(self, raw_paths):
        expanded = []
        for p in raw_paths:
            if os.path.isdir(p):
                for entry in sorted(os.listdir(p)):
                    full = os.path.join(p, entry)
                    if os.path.isfile(full) and full.lower().endswith(self.file_ext):
                        expanded.append(full)
            else:
                expanded.append(p)
        self.add_file_paths(expanded)

    def inspect_file(self, norm_path: str) -> dict:
        raise NotImplementedError

    def perform_merge_worker(self, items_snapshot: list[dict], progress_cb, is_cancelled_fn) -> bytes:
        raise NotImplementedError

    def add_file_paths(self, paths):
        wrong_ext = []
        failed = []

        for path in paths:
            norm_path = os.path.normpath(path)
            if not norm_path.lower().endswith(self.file_ext):
                wrong_ext.append(os.path.basename(norm_path))
                continue

            if not os.path.isfile(norm_path):
                failed.append(f"{os.path.basename(norm_path)} (파일을 찾을 수 없음)")
                continue

            try:
                info = self.inspect_file(norm_path)
                size_bytes = os.path.getsize(norm_path)
                item = {
                    "path": norm_path,
                    "name": os.path.basename(norm_path),
                    "pages": info["pages"],
                    "page_label": info.get("page_label", f"{info['pages']} 페이지"),
                    "size_str": self._format_size(size_bytes),
                }
                self.items.append(item)
            except Exception as e:
                failed.append(f"{os.path.basename(norm_path)} ({e})")

        self._refresh_tree()

        if wrong_ext or failed:
            msgs = []
            if wrong_ext:
                msgs.append(
                    f"현재 [{self.category_label} 병합] 화면에서는 {self.file_ext.upper()} 파일만 추가할 수 있습니다.\n"
                    f"제외된 항목:\n- " + "\n- ".join(wrong_ext[:10])
                )
            if failed:
                msgs.append(
                    f"읽기 실패한 {self.category_label} 파일:\n- " + "\n- ".join(failed[:10])
                )
            messagebox.showwarning("파일 추가 안내", "\n\n".join(msgs), parent=self.root)

    def _refresh_tree(self, selected_indices=None):
        self.tree.delete(*self.tree.get_children())
        total_pages = 0
        item_ids = []
        for idx, item in enumerate(self.items):
            total_pages += item["pages"]
            iid = self.tree.insert(
                "",
                tk.END,
                values=(
                    idx + 1,
                    item["name"],
                    item["page_label"],
                    item["size_str"],
                    item["path"],
                ),
            )
            item_ids.append(iid)

        if selected_indices:
            to_select = [item_ids[i] for i in selected_indices if 0 <= i < len(item_ids)]
            if to_select:
                self.tree.selection_set(to_select)
                self.tree.see(to_select[0])

        self.status_var.set(f"총 {len(self.items)}개 파일 ({total_pages} 페이지)")

    def remove_selected(self):
        selected = self.tree.selection()
        if not selected:
            return
        children = list(self.tree.get_children())
        indices = sorted([children.index(iid) for iid in selected], reverse=True)
        for idx in indices:
            if 0 <= idx < len(self.items):
                del self.items[idx]
        self._refresh_tree()

    def clear_all(self):
        if not self.items:
            return
        self.items.clear()
        self._refresh_tree()

    def move_selected(self, direction: int):
        selected = self.tree.selection()
        if not selected:
            return
        children = list(self.tree.get_children())
        indices = sorted([children.index(iid) for iid in selected])
        if direction < 0:
            if indices[0] == 0:
                return
            new_indices = []
            for idx in indices:
                self.items[idx - 1], self.items[idx] = (
                    self.items[idx],
                    self.items[idx - 1],
                )
                new_indices.append(idx - 1)
            self._refresh_tree(selected_indices=new_indices)
        else:
            if indices[-1] >= len(self.items) - 1:
                return
            new_indices = []
            for idx in reversed(indices):
                self.items[idx + 1], self.items[idx] = (
                    self.items[idx],
                    self.items[idx + 1],
                )
                new_indices.append(idx + 1)
            self._refresh_tree(selected_indices=sorted(new_indices))

    # Internal Drag-and-Drop Reordering in Treeview
    def _on_tree_press(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region not in ("cell", "tree"):
            self._drag_start_index = None
            return
        iid = self.tree.identify_row(event.y)
        if not iid:
            self._drag_start_index = None
            return
        children = list(self.tree.get_children())
        if iid in children:
            self._drag_start_index = children.index(iid)
            self._drag_active = False

    def _on_tree_motion(self, event):
        if self._drag_start_index is None:
            return

        target_iid = self.tree.identify_row(event.y)
        children = list(self.tree.get_children())
        if not children:
            return

        if not target_iid:
            if event.y < 0:
                target_index = 0
            elif event.y >= self.tree.winfo_height():
                target_index = len(children) - 1
            else:
                return
        else:
            target_index = children.index(target_iid)

        if target_index != self._drag_start_index:
            self._drag_active = True
            self.root.config(cursor="fleur")
            item = self.items.pop(self._drag_start_index)
            self.items.insert(target_index, item)
            self._drag_start_index = target_index
            self._refresh_tree(selected_indices=[target_index])

    def _on_tree_release(self, event):
        if self._drag_active:
            self.root.config(cursor="")
        self._drag_start_index = None
        self._drag_active = False

    def start_merge(self):
        if not self.items:
            messagebox.showinfo(
                "알림",
                f"병합할 {self.category_label} 파일을 먼저 추가해 주세요.",
                parent=self.root,
            )
            return

        total_pages = sum(item["pages"] for item in self.items)
        if total_pages <= 0:
            messagebox.showwarning(
                "경고", "병합할 페이지가 없습니다.", parent=self.root
            )
            return

        prog_win = tk.Toplevel(self.root)
        prog_win.title(f"{self.category_label} 병합 진행 중...")
        prog_win.resizable(False, False)
        prog_win.transient(self.root)
        prog_win.grab_set()

        self.root.update_idletasks()
        w, h = 440, 155
        rx = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        ry = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
        prog_win.geometry(f"{w}x{h}+{max(0, rx)}+{max(0, ry)}")

        cancelled = {"flag": False}

        def on_cancel():
            cancelled["flag"] = True
            lbl_detail.config(text="취소 중...")
            btn_cancel.config(state=tk.DISABLED)

        prog_win.protocol("WM_DELETE_WINDOW", on_cancel)

        frame = ttk.Frame(prog_win, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        lbl_title = ttk.Label(
            frame,
            text=f"{self.category_label} 문서를 병합하고 있습니다...",
            font=("맑은 고딕", 9, "bold"),
        )
        lbl_title.pack(anchor=tk.W, pady=(0, 6))

        lbl_detail = ttk.Label(frame, text=f"진행도: 0 / {total_pages} 페이지 (0%)")
        lbl_detail.pack(anchor=tk.W, pady=(0, 6))

        progress_bar = ttk.Progressbar(
            frame, orient=tk.HORIZONTAL, mode="determinate", maximum=total_pages, value=0
        )
        progress_bar.pack(fill=tk.X, pady=(0, 12))

        btn_cancel = ttk.Button(frame, text="취소", width=10, command=on_cancel)
        btn_cancel.pack(anchor=tk.E)

        items_snapshot = list(self.items)
        result_holder = {"buffer": None, "error": None}

        def progress_cb(current_page: int, total: int, file_name: str):
            pct = int((current_page / max(1, total)) * 100)
            self.root.after(
                0,
                self._update_progress_ui,
                prog_win,
                progress_bar,
                lbl_detail,
                current_page,
                total,
                pct,
                file_name,
            )

        def worker():
            try:
                data = self.perform_merge_worker(
                    items_snapshot,
                    progress_cb,
                    lambda: cancelled["flag"],
                )
                if not cancelled["flag"]:
                    result_holder["buffer"] = data
            except Exception as e:
                result_holder["error"] = str(e)
            finally:
                self.root.after(
                    0,
                    self._on_merge_finished,
                    prog_win,
                    cancelled,
                    result_holder,
                )

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _update_progress_ui(
        prog_win, progress_bar, lbl_detail, current_page, total_pages, pct, file_name
    ):
        if not prog_win.winfo_exists():
            return
        progress_bar["value"] = current_page
        lbl_detail.config(
            text=f"진행도: {current_page} / {total_pages} 페이지 ({pct}%) - {file_name}"
        )
        prog_win.update_idletasks()

    def _on_merge_finished(self, prog_win, cancelled, result_holder):
        if prog_win.winfo_exists():
            prog_win.grab_release()
            prog_win.destroy()

        if cancelled["flag"]:
            return

        if result_holder["error"]:
            messagebox.showerror(
                "병합 오류",
                f"{self.category_label} 병합 중 오류가 발생했습니다:\n{result_holder['error']}",
                parent=self.root,
            )
            return

        merged_bytes = result_holder["buffer"]
        if not merged_bytes:
            return

        initial_dir = (
            os.path.dirname(self.items[0]["path"]) if self.items else os.getcwd()
        )
        save_path = filedialog.asksaveasfilename(
            parent=self.root,
            title=f"병합된 {self.category_label} 파일 저장",
            initialdir=initial_dir,
            initialfile=self.default_save_name,
            defaultextension=self.file_ext,
            filetypes=self.file_dialog_types,
        )

        if not save_path:
            return

        try:
            with open(save_path, "wb") as f:
                f.write(merged_bytes)
            messagebox.showinfo(
                "저장 완료",
                f"{self.category_label} 병합 및 저장이 완료되었습니다.\n\n저장 경로:\n{save_path}",
                parent=self.root,
            )
        except Exception as e:
            messagebox.showerror(
                "저장 실패",
                f"파일을 저장하는 중 오류가 발생했습니다:\n{e}",
                parent=self.root,
            )


class PdfMergePane(BaseMergePane):
    """Dedicated screen for merging PDF files."""

    category_label = "PDF"
    file_ext = ".pdf"
    file_dialog_types = [("PDF 파일 (*.pdf)", "*.pdf"), ("모든 파일 (*.*)", "*.*")]
    default_save_name = "merged.pdf"

    def inspect_file(self, norm_path: str) -> dict:
        reader = PdfReader(norm_path)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                pass
        num_pages = len(reader.pages)
        return {
            "pages": num_pages,
            "page_label": f"{num_pages} 페이지",
        }

    def perform_merge_worker(
        self, items_snapshot: list[dict], progress_cb, is_cancelled_fn
    ) -> bytes:
        writer = PdfWriter()
        total_pages = sum(item["pages"] for item in items_snapshot)
        current_page = 0
        step_delay = min(0.03, 0.8 / max(1, total_pages))

        for item in items_snapshot:
            if is_cancelled_fn():
                return b""
            reader = PdfReader(item["path"])
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception:
                    pass

            file_name = item["name"]
            for page in reader.pages:
                if is_cancelled_fn():
                    return b""
                writer.add_page(page)
                current_page += 1
                progress_cb(current_page, total_pages, file_name)
                if step_delay > 0:
                    time.sleep(step_delay)

        if is_cancelled_fn():
            return b""

        buf = io.BytesIO()
        writer.write(buf)
        writer.close()
        return buf.getvalue()


class HwpxMergePane(BaseMergePane):
    """Dedicated screen for merging HWPX files."""

    category_label = "HWPX"
    file_ext = ".hwpx"
    file_dialog_types = [("HWPX 파일 (*.hwpx)", "*.hwpx"), ("모든 파일 (*.*)", "*.*")]
    default_save_name = "merged.hwpx"

    def _build_extra_bottom_controls(self, bottom_frame: ttk.Frame):
        self.page_break_var = tk.BooleanVar(value=True)
        self.chk_page_break = ttk.Checkbutton(
            bottom_frame,
            text="파일마다 새 페이지(쪽 나눔)로 구분",
            variable=self.page_break_var,
        )
        self.chk_page_break.pack(side=tk.RIGHT, padx=(8, 14))

    def inspect_file(self, norm_path: str) -> dict:
        info = inspect_hwpx(norm_path)
        return {
            "pages": info["pages"],
            "page_label": f"{info['pages']} 페이지 ({info['paragraphs']} 문단)",
        }

    def perform_merge_worker(
        self, items_snapshot: list[dict], progress_cb, is_cancelled_fn
    ) -> bytes:
        page_break = bool(self.page_break_var.get())
        return HwpxMerger.merge_hwpx_files(
            items_snapshot,
            page_break_between_files=page_break,
            progress_callback=progress_cb,
            is_cancelled_fn=is_cancelled_fn,
        )


# ---------------------------------------------------------------------------
# Main Application Window with Category Tabs (Notebook)
# ---------------------------------------------------------------------------
class DocumentMergeApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("문서 병합 프로그램 - PDF / HWPX Merger")
        self.root.geometry("880x580")
        self.root.minsize(760, 460)

        # Configure native Windows look and feel
        self.style = ttk.Style(self.root)
        available_themes = self.style.theme_names()
        if "vista" in available_themes:
            self.style.theme_use("vista")
        elif "xpnative" in available_themes:
            self.style.theme_use("xpnative")
        elif "winnative" in available_themes:
            self.style.theme_use("winnative")

        default_font = ("맑은 고딕", 9)
        self.root.option_add("*Font", default_font)
        self.style.configure(".", font=default_font)
        self.style.configure("TNotebook.Tab", font=("맑은 고딕", 10, "bold"), padding=(18, 6))
        self.style.configure("Treeview", rowheight=26, font=default_font)
        self.style.configure("Treeview.Heading", font=("맑은 고딕", 9, "bold"))
        self.style.configure("Merge.TButton", font=("맑은 고딕", 10, "bold"), padding=(12, 6))
        self.style.configure("Action.TButton", padding=(8, 4))

        self._build_ui()
        self._setup_file_drop()

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # Category 1: PDF Merge Screen
        self.pdf_pane = PdfMergePane(self.notebook, self.root)
        self.notebook.add(self.pdf_pane, text="   PDF 병합   ")

        # Category 2: HWPX Merge Screen
        self.hwpx_pane = HwpxMergePane(self.notebook, self.root)
        self.notebook.add(self.hwpx_pane, text="   HWPX 병합   ")

    def get_active_pane(self) -> BaseMergePane:
        current_tab = self.notebook.select()
        if current_tab == str(self.hwpx_pane):
            return self.hwpx_pane
        return self.pdf_pane

    def on_external_files_dropped(self, raw_paths):
        self.get_active_pane().on_external_files_dropped(raw_paths)

    def _setup_file_drop(self):
        self._drop_root = Win32FileDropTarget(self.root, self.on_external_files_dropped)
        if sys.platform == "win32":
            top_hwnd = user32.GetParent(self.root.winfo_id())
            if top_hwnd:
                self._drop_top = Win32FileDropTarget(
                    self.root, self.on_external_files_dropped, hwnd=top_hwnd
                )


# Backwards-compatible alias
PdfMergeApp = DocumentMergeApp


def main():
    enable_high_dpi()
    root = tk.Tk()
    app = DocumentMergeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
