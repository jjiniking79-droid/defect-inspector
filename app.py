# -*- coding: utf-8 -*-
"""
불량 이미지 자동 판정 프로그램 (Defect Inspector)
------------------------------------------------
- 이미지 폴더/파일 불러오기 (모든 이미지 형식)
- 불량 유형 정의 텍스트 입력/저장 (작성자가 직접 정의, 참고 문서로 저장됨)
- 판정 유형(카테고리) 추가/삭제 관리 (기본: K 유기 / K 갈림 / NK 유기 / 핀홀)
- 선택 이미지 일괄 작업자 판정값 입력
- AI 학습하기 (작업자가 입력한 판정값 기반, 누적 데이터로 재학습)
- 자동 판정 (학습된 모델로 예측 + 신뢰율 표시, 신뢰율 70% 미만 행은 연핑크 음영 표시)
- 데이터/모델 저장 및 백업

실행: python app.py
배포용 EXE 빌드: build_exe.bat (PyInstaller) 참고
"""

import os
import sys
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageTk

import data_store
from model_manager import ModelManager

# PyInstaller(onefile)로 실행될 경우, 실행 파일이 위치한 폴더를 작업 폴더로 고정합니다.
# 이렇게 해야 data/, model/ 폴더가 임시 압축 해제 폴더가 아닌
# exe 파일 옆에 생성/유지되어 재실행 시에도 데이터가 보존됩니다.
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

IMAGE_EXTS = (
    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff",
    ".webp", ".jfif", ".ppm", ".pgm", ".pbm"
)

THUMB_SIZE = (200, 200)

# 표(grid) 컬럼 순서/픽셀 폭 - 헤더와 데이터 행이 같은 부모(grid)를 공유하므로
# 여기서 지정한 폭이 헤더/행 모두에 동일하게 적용되어 줄이 어긋나지 않습니다.
COLUMNS = ["선택", "이미지", "파일명", "자동판정값", "신뢰율(%)", "작업자 판정값"]
COL_WIDTHS = [60, 215, 260, 130, 110, 170]

ROW_BG_NORMAL = "#ffffff"
ROW_BG_LOWCONF = "#ffd9e8"   # 파스텔톤 연핑크
LOW_CONF_THRESHOLD = 70.0    # % 미만이면 음영 처리


class ImageRow:
    """표(grid)의 한 행에 대응하는 이미지 레코드"""
    def __init__(self, path):
        self.path = path
        self.checked = tk.BooleanVar(value=False)
        self.auto_label = tk.StringVar(value="-")
        self.confidence = tk.StringVar(value="-")
        self.worker_label = tk.StringVar(value="")
        self.thumb_img = None  # PhotoImage 참조 유지용
        self.widgets = {}  # 행의 위젯들을 보관 (파괴/재구성/배경색 변경용)


class DefectInspectorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("불량 이미지 자동 판정 프로그램")
        self.root.geometry("1320x860")

        self.categories = data_store.load_categories()
        self.rules = data_store.load_rules()
        self.model = ModelManager()
        self.rows = []  # ImageRow 목록
        self._next_grid_row = 1  # 0행은 헤더

        self.train_queue = queue.Queue()

        self._build_ui()
        self._refresh_all_category_widgets()
        self._update_status("준비 완료. 모델 학습 여부: " +
                             ("학습됨" if self.model.is_trained() else "미학습"))

    # ------------------------------------------------------------------
    # UI 구성
    # ------------------------------------------------------------------
    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        # 신뢰율 낮은 행의 콤보박스 배경색을 위한 커스텀 스타일
        style.configure("LowConf.TCombobox", fieldbackground=ROW_BG_LOWCONF)
        style.configure("Normal.TCombobox", fieldbackground=ROW_BG_NORMAL)

        # ---------- 상단 툴바 1: 불러오기 / 학습 / 판정 / 저장 ----------
        toolbar1 = ttk.Frame(self.root, padding=6)
        toolbar1.pack(side="top", fill="x")

        ttk.Button(toolbar1, text="📁 이미지 폴더 불러오기",
                   command=self.load_folder).pack(side="left", padx=3)
        ttk.Button(toolbar1, text="🖼 이미지 파일 불러오기",
                   command=self.load_files).pack(side="left", padx=3)
        ttk.Separator(toolbar1, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(toolbar1, text="🧠 AI 학습하기",
                   command=self.train_model).pack(side="left", padx=3)
        ttk.Button(toolbar1, text="⚡ 자동 판정",
                   command=self.auto_classify).pack(side="left", padx=3)
        ttk.Separator(toolbar1, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(toolbar1, text="💾 데이터 저장/백업",
                   command=self.backup_data).pack(side="left", padx=3)
        ttk.Button(toolbar1, text="🗑 목록 비우기",
                   command=self.clear_rows).pack(side="left", padx=3)

        self.progress = ttk.Progressbar(toolbar1, mode="determinate", length=180)
        self.progress.pack(side="right", padx=6)
        self.status_var = tk.StringVar(value="")
        ttk.Label(toolbar1, textvariable=self.status_var).pack(side="right", padx=8)

        # ---------- 상단 툴바 2: 판정 유형 관리 + 일괄 판정 ----------
        toolbar2 = ttk.LabelFrame(self.root, text="판정 유형 관리", padding=6)
        toolbar2.pack(side="top", fill="x", padx=6, pady=(0, 4))

        row2a = ttk.Frame(toolbar2)
        row2a.pack(side="top", fill="x")
        ttk.Label(row2a, text="새 유형 추가:").pack(side="left")
        self.new_cat_entry = ttk.Entry(row2a, width=18)
        self.new_cat_entry.pack(side="left", padx=4)
        ttk.Button(row2a, text="추가", command=self.add_category).pack(side="left", padx=4)

        ttk.Separator(row2a, orient="vertical").pack(side="left", fill="y", padx=10)

        ttk.Label(row2a, text="유형 삭제:").pack(side="left")
        self.delete_cat_combo = ttk.Combobox(row2a, values=self.categories,
                                              width=16, state="readonly")
        self.delete_cat_combo.pack(side="left", padx=4)
        ttk.Button(row2a, text="삭제", command=self.delete_category).pack(side="left", padx=4)

        row2b = ttk.Frame(toolbar2)
        row2b.pack(side="top", fill="x", pady=(6, 0))
        ttk.Label(row2b, text="선택한 이미지 일괄 판정값:").pack(side="left")
        self.batch_combo = ttk.Combobox(row2b, values=self.categories,
                                         width=16, state="readonly")
        self.batch_combo.pack(side="left", padx=4)
        ttk.Button(row2b, text="선택 항목에 적용",
                   command=self.apply_batch_label).pack(side="left", padx=4)
        ttk.Button(row2b, text="전체 선택", command=self.select_all).pack(side="left", padx=4)
        ttk.Button(row2b, text="전체 해제", command=self.deselect_all).pack(side="left", padx=4)

        # ---------- 상단 툴바 3: 불량 유형 정의(룰) 텍스트 ----------
        toolbar3 = ttk.LabelFrame(self.root, text="불량 유형 정의 (작성자가 직접 입력 / 판정 참고자료로 저장)", padding=6)
        toolbar3.pack(side="top", fill="x", padx=6, pady=(0, 4))

        rule_top = ttk.Frame(toolbar3)
        rule_top.pack(side="top", fill="x")
        ttk.Label(rule_top, text="유형 선택:").pack(side="left")
        self.rule_combo = ttk.Combobox(rule_top, values=self.categories,
                                        width=16, state="readonly")
        self.rule_combo.pack(side="left", padx=4)
        self.rule_combo.bind("<<ComboboxSelected>>", self.on_rule_category_selected)
        ttk.Button(rule_top, text="정의 저장", command=self.save_rule_text).pack(side="left", padx=4)

        self.rule_text = tk.Text(toolbar3, height=4, wrap="word")
        self.rule_text.pack(side="top", fill="x", pady=(4, 0))

        # ---------- 안내: 신뢰율 음영 표시 ----------
        legend = ttk.Frame(self.root, padding=(10, 2))
        legend.pack(side="top", fill="x")
        swatch = tk.Label(legend, text="   ", bg=ROW_BG_LOWCONF, relief="solid", borderwidth=1)
        swatch.pack(side="left", padx=(0, 4))
        ttk.Label(legend, text=f"= AI 신뢰율 {LOW_CONF_THRESHOLD:.0f}% 미만 (재검수 권장)").pack(side="left")

        # ---------- 스크롤 가능한 표 영역 (헤더 + 데이터 행이 같은 grid 부모를 공유) ----------
        list_container = ttk.Frame(self.root)
        list_container.pack(side="top", fill="both", expand=True, padx=6, pady=(4, 6))

        self.canvas = tk.Canvas(list_container, borderwidth=0, highlightthickness=0)
        vsb = ttk.Scrollbar(list_container, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.table_frame = tk.Frame(self.canvas, bg=ROW_BG_NORMAL)
        self.table_frame_id = self.canvas.create_window((0, 0), window=self.table_frame, anchor="nw")
        self.table_frame.bind("<Configure>",
                               lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                          lambda e: self.canvas.itemconfig(self.table_frame_id, width=e.width))
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        # 컬럼 폭을 고정해서 헤더와 데이터 행의 줄을 맞춤
        for i, w in enumerate(COL_WIDTHS):
            self.table_frame.grid_columnconfigure(i, minsize=w)

        # 헤더 (0행)
        for i, text in enumerate(COLUMNS):
            tk.Label(self.table_frame, text=text, font=("맑은 고딕", 9, "bold"),
                     bg="#e3e3e3", anchor="center", relief="groove", borderwidth=1
                     ).grid(row=0, column=i, sticky="nsew", padx=0, pady=0, ipady=4)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _update_status(self, text):
        self.status_var.set(text)

    # ------------------------------------------------------------------
    # 카테고리 관리
    # ------------------------------------------------------------------
    def _refresh_all_category_widgets(self):
        self.batch_combo["values"] = self.categories
        self.rule_combo["values"] = self.categories
        self.delete_cat_combo["values"] = self.categories
        for row in self.rows:
            cb = row.widgets.get("worker_combo")
            if cb is not None:
                cb["values"] = self.categories

    def add_category(self):
        name = self.new_cat_entry.get().strip()
        if not name:
            messagebox.showwarning("알림", "추가할 유형 이름을 입력하세요.")
            return
        if name in self.categories:
            messagebox.showinfo("알림", "이미 존재하는 유형입니다.")
            return
        self.categories.append(name)
        data_store.save_categories(self.categories)
        self.new_cat_entry.delete(0, "end")
        self._refresh_all_category_widgets()
        self._update_status(f"'{name}' 유형이 추가되었습니다.")

    def delete_category(self):
        cat = self.delete_cat_combo.get()
        if not cat:
            messagebox.showwarning("알림", "삭제할 유형을 먼저 선택하세요.")
            return
        if cat not in self.categories:
            return
        if not messagebox.askyesno(
            "삭제 확인",
            f"'{cat}' 유형을 목록에서 삭제하시겠습니까?\n\n"
            f"이미 이 유형으로 저장된 학습 데이터(data/labeled_images/{cat})는 "
            f"삭제되지 않고 그대로 보존됩니다.\n"
            f"단, 새로 판정할 때 더 이상 이 유형을 선택할 수 없게 됩니다."
        ):
            return
        self.categories.remove(cat)
        data_store.save_categories(self.categories)
        if cat in self.rules:
            del self.rules[cat]
            data_store.save_rules(self.rules)
        self._refresh_all_category_widgets()
        self._update_status(f"'{cat}' 유형이 삭제되었습니다.")

    # ------------------------------------------------------------------
    # 불량 유형 정의(룰 텍스트)
    # ------------------------------------------------------------------
    def on_rule_category_selected(self, event=None):
        cat = self.rule_combo.get()
        self.rule_text.delete("1.0", "end")
        self.rule_text.insert("1.0", self.rules.get(cat, ""))

    def save_rule_text(self):
        cat = self.rule_combo.get()
        if not cat:
            messagebox.showwarning("알림", "정의를 저장할 유형을 먼저 선택하세요.")
            return
        self.rules[cat] = self.rule_text.get("1.0", "end").strip()
        data_store.save_rules(self.rules)
        messagebox.showinfo("저장 완료", f"'{cat}' 유형 정의가 저장되었습니다.\n"
                                        f"(이 정의는 판정자 참고 문서로 백업에 함께 저장되며,\n"
                                        f" 실제 자동판정은 AI 학습 데이터를 기반으로 이루어집니다.)")

    # ------------------------------------------------------------------
    # 이미지 불러오기
    # ------------------------------------------------------------------
    def load_folder(self):
        folder = filedialog.askdirectory(title="이미지 폴더 선택")
        if not folder:
            return
        paths = []
        for root_dir, _, files in os.walk(folder):
            for fn in files:
                if fn.lower().endswith(IMAGE_EXTS):
                    paths.append(os.path.join(root_dir, fn))
        if not paths:
            messagebox.showinfo("알림", "선택한 폴더에서 이미지 파일을 찾지 못했습니다.")
            return
        self._add_images(paths)

    def load_files(self):
        filetypes = [
            ("이미지 파일", " ".join(f"*{e}" for e in IMAGE_EXTS)),
            ("모든 파일", "*.*"),
        ]
        paths = filedialog.askopenfilenames(title="이미지 파일 선택", filetypes=filetypes)
        if not paths:
            return
        self._add_images(list(paths))

    def _add_images(self, paths):
        existing = {r.path for r in self.rows}
        added = 0
        for p in paths:
            if p in existing:
                continue
            row = ImageRow(p)
            self._create_row_widgets(row)
            self.rows.append(row)
            added += 1
        self._update_status(f"이미지 {added}장 추가됨 (전체 {len(self.rows)}장)")

    def clear_rows(self):
        if not self.rows:
            return
        if not messagebox.askyesno("확인", "불러온 이미지 목록을 모두 비우시겠습니까? (저장된 학습 데이터는 유지됩니다)"):
            return
        for row in self.rows:
            for w in row.widgets.values():
                try:
                    w.destroy()
                except Exception:
                    pass
        self.rows = []
        self._next_grid_row = 1
        self._update_status("목록을 비웠습니다.")

    # ------------------------------------------------------------------
    # 행 위젯 생성 (헤더와 동일한 grid 부모(table_frame) 사용 -> 줄 자동 정렬)
    # ------------------------------------------------------------------
    def _create_row_widgets(self, row: ImageRow):
        r = self._next_grid_row
        self._next_grid_row += 1
        bg = ROW_BG_NORMAL

        chk = tk.Checkbutton(self.table_frame, variable=row.checked, bg=bg,
                              activebackground=bg, highlightthickness=0)
        chk.grid(row=r, column=0, sticky="nsew", padx=0, pady=1)

        try:
            im = Image.open(row.path)
            im.thumbnail(THUMB_SIZE)
            row.thumb_img = ImageTk.PhotoImage(im)
        except Exception:
            row.thumb_img = None

        thumb_lbl = tk.Label(self.table_frame, image=row.thumb_img, bg=bg)
        thumb_lbl.grid(row=r, column=1, sticky="nsew", padx=4, pady=4)

        name_lbl = tk.Label(self.table_frame, text=os.path.basename(row.path),
                             bg=bg, anchor="w", wraplength=COL_WIDTHS[2] - 10, justify="left")
        name_lbl.grid(row=r, column=2, sticky="nsew", padx=6, pady=4)

        auto_lbl = tk.Label(self.table_frame, textvariable=row.auto_label, bg=bg, anchor="center")
        auto_lbl.grid(row=r, column=3, sticky="nsew", padx=2, pady=4)

        conf_lbl = tk.Label(self.table_frame, textvariable=row.confidence, bg=bg, anchor="center")
        conf_lbl.grid(row=r, column=4, sticky="nsew", padx=2, pady=4)

        worker_combo = ttk.Combobox(self.table_frame, textvariable=row.worker_label,
                                     values=self.categories, width=16, state="readonly",
                                     style="Normal.TCombobox")
        worker_combo.grid(row=r, column=5, sticky="nsew", padx=6, pady=4)

        # 각 셀 사이 얇은 구분선 느낌을 위해 행 전체 테두리
        for col in range(len(COLUMNS)):
            self.table_frame.grid_rowconfigure(r, minsize=THUMB_SIZE[1] + 8)

        row.widgets = {
            "chk": chk, "thumb": thumb_lbl, "name": name_lbl,
            "auto": auto_lbl, "conf": conf_lbl, "worker_combo": worker_combo,
        }

    def _set_row_bg(self, row: ImageRow, low_conf: bool):
        bg = ROW_BG_LOWCONF if low_conf else ROW_BG_NORMAL
        for key in ("chk", "thumb", "name", "auto", "conf"):
            w = row.widgets.get(key)
            if w is not None:
                try:
                    w.configure(bg=bg)
                    if key == "chk":
                        w.configure(activebackground=bg)
                except Exception:
                    pass
        combo = row.widgets.get("worker_combo")
        if combo is not None:
            combo.configure(style="LowConf.TCombobox" if low_conf else "Normal.TCombobox")

    # ------------------------------------------------------------------
    # 선택/일괄 판정
    # ------------------------------------------------------------------
    def select_all(self):
        for r in self.rows:
            r.checked.set(True)

    def deselect_all(self):
        for r in self.rows:
            r.checked.set(False)

    def apply_batch_label(self):
        cat = self.batch_combo.get()
        if not cat:
            messagebox.showwarning("알림", "적용할 판정 유형을 먼저 선택하세요.")
            return
        checked = [r for r in self.rows if r.checked.get()]
        if not checked:
            messagebox.showwarning("알림", "선택(체크)된 이미지가 없습니다.")
            return
        for r in checked:
            r.worker_label.set(cat)
        self._update_status(f"선택된 {len(checked)}장에 '{cat}' 판정값을 적용했습니다.")

    # ------------------------------------------------------------------
    # AI 학습
    # ------------------------------------------------------------------
    def train_model(self):
        session_labeled = [(r.path, r.worker_label.get()) for r in self.rows
                            if r.worker_label.get().strip()]

        if not session_labeled:
            # 현재 화면에 새로 라벨링한 것이 없으면 기존 누적 데이터셋만으로 재학습 시도
            dataset = data_store.load_dataset()
            if len(dataset) < 2:
                messagebox.showwarning(
                    "알림",
                    "학습할 데이터가 없습니다.\n"
                    "먼저 이미지에 '작업자 판정값'을 입력한 뒤 학습하기를 눌러주세요."
                )
                return
            combined = dataset
        else:
            # 세션에서 라벨링한 이미지는 데이터셋 폴더로 복사/누적 저장
            for path, label in session_labeled:
                try:
                    data_store.add_labeled_image(path, label)
                except Exception as e:
                    print("labeled image copy failed:", e)
            dataset = data_store.load_dataset()
            # 중복 제거 (동일 경로 최신 라벨 우선)
            merged = {}
            for path, label in dataset:
                merged[path] = label
            for path, label in session_labeled:
                merged[path] = label
            combined = list(merged.items())

        if len(set(l for _, l in combined)) < 2:
            messagebox.showwarning("알림", "학습을 위해서는 서로 다른 판정 유형이 최소 2종류 이상 필요합니다.")
            return

        self._set_controls_enabled(False)
        self.progress.configure(mode="determinate", maximum=len(combined), value=0)
        self._update_status(f"AI 학습 중... (총 {len(combined)}장)")

        def worker():
            try:
                paths = [p for p, _ in combined]
                labels = [l for _, l in combined]

                def progress_cb(i, total):
                    self.train_queue.put(("progress", i, total))

                acc = self.model.train(paths, labels, progress_cb=progress_cb)
                self.train_queue.put(("done", acc, len(combined)))
            except Exception as e:
                self.train_queue.put(("error", str(e)))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(100, self._poll_train_queue)

    def _poll_train_queue(self):
        try:
            while True:
                msg = self.train_queue.get_nowait()
                if msg[0] == "progress":
                    _, i, total = msg
                    self.progress.configure(value=i)
                    self._update_status(f"특징 추출 중... {i}/{total}")
                elif msg[0] == "done":
                    _, acc, n = msg
                    self._set_controls_enabled(True)
                    self.progress.configure(value=0)
                    acc_txt = f"{acc*100:.1f}%" if acc is not None else "N/A (데이터 부족으로 검증 생략)"
                    self._update_status(f"학습 완료. 데이터 {n}장, 교차검증 정확도: {acc_txt}")
                    messagebox.showinfo("학습 완료",
                                         f"총 {n}장의 데이터로 학습을 완료했습니다.\n"
                                         f"교차검증 정확도: {acc_txt}")
                    return
                elif msg[0] == "error":
                    self._set_controls_enabled(True)
                    self.progress.configure(value=0)
                    messagebox.showerror("학습 오류", msg[1])
                    self._update_status("학습 중 오류가 발생했습니다.")
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._poll_train_queue)

    def _set_controls_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        # 버튼류만 순회하며 상태 변경 (Progressbar 등 제외)
        def _walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, (ttk.Button,)):
                    child.configure(state=state)
                _walk(child)
        _walk(self.root)

    # ------------------------------------------------------------------
    # 자동 판정
    # ------------------------------------------------------------------
    def auto_classify(self):
        if not self.model.is_trained():
            messagebox.showwarning("알림", "먼저 'AI 학습하기'로 모델을 학습시켜야 자동 판정을 사용할 수 있습니다.")
            return
        if not self.rows:
            messagebox.showwarning("알림", "판정할 이미지가 없습니다. 먼저 이미지를 불러오세요.")
            return

        self._set_controls_enabled(False)
        self.progress.configure(mode="determinate", maximum=len(self.rows), value=0)
        self._update_status("자동 판정 진행 중...")

        def worker():
            for i, row in enumerate(self.rows):
                try:
                    label, conf = self.model.predict(row.path)
                except Exception as e:
                    label, conf = "오류", 0.0
                self.train_queue.put(("predict_row", row, label, conf, i + 1, len(self.rows)))
            self.train_queue.put(("predict_done",))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(100, self._poll_predict_queue)

    def _poll_predict_queue(self):
        try:
            while True:
                msg = self.train_queue.get_nowait()
                if msg[0] == "predict_row":
                    _, row, label, conf, i, total = msg
                    row.auto_label.set(label if label else "-")
                    conf_pct = conf * 100 if label else 0.0
                    row.confidence.set(f"{conf_pct:.1f}" if label else "-")
                    self._set_row_bg(row, low_conf=bool(label) and conf_pct < LOW_CONF_THRESHOLD)
                    self.progress.configure(value=i)
                    self._update_status(f"자동 판정 중... {i}/{total}")
                elif msg[0] == "predict_done":
                    self._set_controls_enabled(True)
                    self.progress.configure(value=0)
                    self._update_status("자동 판정이 완료되었습니다.")
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._poll_predict_queue)

    # ------------------------------------------------------------------
    # 저장/백업
    # ------------------------------------------------------------------
    def backup_data(self):
        dest = filedialog.askdirectory(title="백업할 위치 선택")
        if not dest:
            return
        try:
            target = data_store.backup_all(dest)
            messagebox.showinfo("백업 완료", f"학습 모델 및 데이터가 백업되었습니다.\n\n{target}")
            self._update_status(f"백업 완료: {target}")
        except Exception as e:
            messagebox.showerror("백업 오류", str(e))


def main():
    root = tk.Tk()
    app = DefectInspectorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
