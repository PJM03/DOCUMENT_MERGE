import ctypes
from ctypes import wintypes
import io
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
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
# Main PDF Merge Application
# ---------------------------------------------------------------------------
class PdfMergeApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("PDF 병합 프로그램 (PDF Merger)")
        self.root.geometry("840x540")
        self.root.minsize(720, 440)

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
        self.style.configure("Treeview", rowheight=26, font=default_font)
        self.style.configure("Treeview.Heading", font=("맑은 고딕", 9, "bold"))
        self.style.configure("Merge.TButton", font=("맑은 고딕", 10, "bold"), padding=(12, 6))
        self.style.configure("Action.TButton", padding=(8, 4))

        # Internal list of dicts: {"path": str, "name": str, "pages": int, "size_str": str}
        self.pdf_items = []
        self._drag_start_index = None
        self._drag_active = False

        self._build_ui()
        self._setup_file_drop()

    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Top instruction & toolbar
        top_frame = ttk.Frame(main_frame)
        top_frame.pack(fill=tk.X, pady=(0, 8))

        # Pack button bar on the right FIRST so it never gets clipped by the label
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
            text="탐색기에서 PDF 파일들을 아래 목록에 드래그 앤 드롭하거나 [파일 추가...] 버튼을 클릭하세요.\n"
            "목록에 추가된 파일은 마우스 드래그 앤 드롭으로 병합 순서를 변경할 수 있습니다.",
            justify=tk.LEFT,
        )
        info_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Center list area (Labelframe for native Windows grouping)
        list_group = ttk.LabelFrame(main_frame, text="병합할 PDF 파일 목록 (드래그하여 순서 변경)", padding=6)
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
        self.tree.column("pages", width=80, anchor=tk.CENTER, stretch=False)
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

        # Bottom controls (Up/Down helper buttons, summary label, MERGE button)
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill=tk.X, pady=(10, 0))

        order_btn_frame = ttk.Frame(bottom_frame)
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
            bottom_frame,
            text="MERGE (병합하기)",
            style="Merge.TButton",
            command=self.start_merge,
        )
        self.btn_merge.pack(side=tk.RIGHT)

        self.status_var = tk.StringVar(value="총 0개 파일 (0 페이지)")
        self.lbl_status = ttk.Label(bottom_frame, textvariable=self.status_var)
        self.lbl_status.pack(side=tk.LEFT, padx=4)

    def _setup_file_drop(self):
        # Hook root window, top-level OS frame, and treeview so dropping anywhere in the window works seamlessly
        self._drop_root = Win32FileDropTarget(self.root, self.on_external_files_dropped)
        self._drop_tree = Win32FileDropTarget(self.tree, self.on_external_files_dropped)
        if sys.platform == "win32":
            top_hwnd = user32.GetParent(self.root.winfo_id())
            if top_hwnd:
                self._drop_top = Win32FileDropTarget(
                    self.root, self.on_external_files_dropped, hwnd=top_hwnd
                )

    # -----------------------------------------------------------------------
    # Adding / Removing / Displaying PDF Files
    # -----------------------------------------------------------------------
    def add_files_dialog(self):
        file_paths = filedialog.askopenfilenames(
            parent=self.root,
            title="추가할 PDF 파일 선택",
            filetypes=[("PDF 파일 (*.pdf)", "*.pdf"), ("모든 파일 (*.*)", "*.*")],
        )
        if file_paths:
            self.add_pdf_paths(list(file_paths))

    def on_external_files_dropped(self, raw_paths):
        expanded = []
        for p in raw_paths:
            if os.path.isdir(p):
                # If a folder is dropped, gather PDF files inside it
                for entry in sorted(os.listdir(p)):
                    full = os.path.join(p, entry)
                    if os.path.isfile(full) and full.lower().endswith(".pdf"):
                        expanded.append(full)
            else:
                expanded.append(p)
        self.add_pdf_paths(expanded)

    @staticmethod
    def _format_size(num_bytes: int) -> str:
        if num_bytes < 1024:
            return f"{num_bytes} B"
        elif num_bytes < 1024 * 1024:
            return f"{num_bytes / 1024:.1f} KB"
        else:
            return f"{num_bytes / (1024 * 1024):.2f} MB"

    def add_pdf_paths(self, paths):
        non_pdf = []
        failed = []

        for path in paths:
            norm_path = os.path.normpath(path)
            if not norm_path.lower().endswith(".pdf"):
                non_pdf.append(os.path.basename(norm_path))
                continue

            if not os.path.isfile(norm_path):
                failed.append(f"{os.path.basename(norm_path)} (파일을 찾을 수 없음)")
                continue

            try:
                reader = PdfReader(norm_path)
                if reader.is_encrypted:
                    try:
                        reader.decrypt("")
                    except Exception:
                        pass
                num_pages = len(reader.pages)
                size_bytes = os.path.getsize(norm_path)
                self.pdf_items.append(
                    {
                        "path": norm_path,
                        "name": os.path.basename(norm_path),
                        "pages": num_pages,
                        "size_str": self._format_size(size_bytes),
                    }
                )
            except Exception as e:
                failed.append(f"{os.path.basename(norm_path)} ({e})")

        self._refresh_tree()

        if non_pdf or failed:
            msgs = []
            if non_pdf:
                msgs.append("PDF 파일이 아니어서 제외된 항목:\n- " + "\n- ".join(non_pdf[:10]))
            if failed:
                msgs.append("읽기 실패한 PDF 파일:\n- " + "\n- ".join(failed[:10]))
            messagebox.showwarning("파일 추가 안내", "\n\n".join(msgs), parent=self.root)

    def _refresh_tree(self, selected_indices=None):
        self.tree.delete(*self.tree.get_children())
        total_pages = 0
        item_ids = []
        for idx, item in enumerate(self.pdf_items):
            total_pages += item["pages"]
            iid = self.tree.insert(
                "",
                tk.END,
                values=(
                    idx + 1,
                    item["name"],
                    f"{item['pages']} 페이지",
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

        self.status_var.set(f"총 {len(self.pdf_items)}개 파일 ({total_pages} 페이지)")

    def remove_selected(self):
        selected = self.tree.selection()
        if not selected:
            return
        children = list(self.tree.get_children())
        indices = sorted([children.index(iid) for iid in selected], reverse=True)
        for idx in indices:
            if 0 <= idx < len(self.pdf_items):
                del self.pdf_items[idx]
        self._refresh_tree()

    def clear_all(self):
        if not self.pdf_items:
            return
        self.pdf_items.clear()
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
                self.pdf_items[idx - 1], self.pdf_items[idx] = (
                    self.pdf_items[idx],
                    self.pdf_items[idx - 1],
                )
                new_indices.append(idx - 1)
            self._refresh_tree(selected_indices=new_indices)
        else:
            if indices[-1] >= len(self.pdf_items) - 1:
                return
            new_indices = []
            for idx in reversed(indices):
                self.pdf_items[idx + 1], self.pdf_items[idx] = (
                    self.pdf_items[idx],
                    self.pdf_items[idx + 1],
                )
                new_indices.append(idx + 1)
            self._refresh_tree(selected_indices=sorted(new_indices))

    # -----------------------------------------------------------------------
    # Internal Drag-and-Drop Reordering in Treeview
    # -----------------------------------------------------------------------
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
            item = self.pdf_items.pop(self._drag_start_index)
            self.pdf_items.insert(target_index, item)
            self._drag_start_index = target_index
            self._refresh_tree(selected_indices=[target_index])

    def _on_tree_release(self, event):
        if self._drag_active:
            self.root.config(cursor="")
        self._drag_start_index = None
        self._drag_active = False

    # -----------------------------------------------------------------------
    # Merge Process with Page-by-Page Progress Dialog -> Save File Dialog
    # -----------------------------------------------------------------------
    def start_merge(self):
        if not self.pdf_items:
            messagebox.showinfo(
                "알림", "병합할 PDF 파일을 먼저 추가해 주세요.", parent=self.root
            )
            return

        total_pages = sum(item["pages"] for item in self.pdf_items)
        if total_pages <= 0:
            messagebox.showwarning(
                "경고", "병합할 페이지가 없습니다.", parent=self.root
            )
            return

        # Create native modal progress dialog
        prog_win = tk.Toplevel(self.root)
        prog_win.title("PDF 병합 진행 중...")
        prog_win.resizable(False, False)
        prog_win.transient(self.root)
        prog_win.grab_set()

        # Center over main window
        self.root.update_idletasks()
        w, h = 420, 150
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
            frame, text="PDF 페이지를 병합하고 있습니다...", font=("맑은 고딕", 9, "bold")
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

        items_snapshot = list(self.pdf_items)
        result_holder = {"buffer": None, "error": None}

        def worker():
            try:
                writer = PdfWriter()
                current_page = 0
                # If total_pages is small, add a tiny pause so the user can visually see
                # the page-by-page progression cleanly on the progress bar.
                step_delay = min(0.03, 0.8 / max(1, total_pages))

                for item in items_snapshot:
                    if cancelled["flag"]:
                        return
                    reader = PdfReader(item["path"])
                    if reader.is_encrypted:
                        try:
                            reader.decrypt("")
                        except Exception:
                            pass

                    file_name = item["name"]
                    for page in reader.pages:
                        if cancelled["flag"]:
                            return
                        writer.add_page(page)
                        current_page += 1
                        pct = int((current_page / total_pages) * 100)

                        # Schedule UI update on main thread
                        self.root.after(
                            0,
                            self._update_progress_ui,
                            prog_win,
                            progress_bar,
                            lbl_detail,
                            current_page,
                            total_pages,
                            pct,
                            file_name,
                        )
                        if step_delay > 0:
                            time.sleep(step_delay)

                if cancelled["flag"]:
                    return

                buf = io.BytesIO()
                writer.write(buf)
                writer.close()
                result_holder["buffer"] = buf.getvalue()
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
                f"PDF 병합 중 오류가 발생했습니다:\n{result_holder['error']}",
                parent=self.root,
            )
            return

        pdf_bytes = result_holder["buffer"]
        if not pdf_bytes:
            return

        # Default directory and file name
        initial_dir = (
            os.path.dirname(self.pdf_items[0]["path"]) if self.pdf_items else os.getcwd()
        )
        save_path = filedialog.asksaveasfilename(
            parent=self.root,
            title="병합된 PDF 파일 저장",
            initialdir=initial_dir,
            initialfile="merged.pdf",
            defaultextension=".pdf",
            filetypes=[("PDF 파일 (*.pdf)", "*.pdf"), ("모든 파일 (*.*)", "*.*")],
        )

        if not save_path:
            return

        try:
            with open(save_path, "wb") as f:
                f.write(pdf_bytes)
            messagebox.showinfo(
                "저장 완료",
                f"PDF 병합 및 저장이 완료되었습니다.\n\n저장 경로:\n{save_path}",
                parent=self.root,
            )
        except Exception as e:
            messagebox.showerror(
                "저장 실패",
                f"파일을 저장하는 중 오류가 발생했습니다:\n{e}",
                parent=self.root,
            )


def main():
    enable_high_dpi()
    root = tk.Tk()
    app = PdfMergeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
