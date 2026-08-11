# -*- coding: utf-8 -*-
"""
data_store.py
- 판정 유형(카테고리) 목록 관리
- 유형별 정의(룰 텍스트) 관리
- 작업자가 라벨링한 이미지 누적 데이터셋 관리 (재학습 시 과거 데이터도 함께 사용)
"""

import os
import json
import shutil
import csv
from datetime import datetime

DATA_DIR = "data"
LABELED_DIR = os.path.join(DATA_DIR, "labeled_images")
CATEGORIES_FILE = os.path.join(DATA_DIR, "categories.json")
RULES_FILE = os.path.join(DATA_DIR, "rules.json")
DATASET_CSV = os.path.join(DATA_DIR, "dataset.csv")  # path,label,added_at

DEFAULT_CATEGORIES = ["K 유기", "K 갈림", "NK 유기", "핀홀"]


def _ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(LABELED_DIR, exist_ok=True)


def load_categories():
    _ensure_dirs()
    if os.path.exists(CATEGORIES_FILE):
        with open(CATEGORIES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    save_categories(DEFAULT_CATEGORIES)
    return list(DEFAULT_CATEGORIES)


def save_categories(categories):
    _ensure_dirs()
    with open(CATEGORIES_FILE, "w", encoding="utf-8") as f:
        json.dump(categories, f, ensure_ascii=False, indent=2)


def load_rules():
    _ensure_dirs()
    if os.path.exists(RULES_FILE):
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_rules(rules_dict):
    _ensure_dirs()
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(rules_dict, f, ensure_ascii=False, indent=2)


def add_labeled_image(src_path, label):
    """
    라벨링된 이미지를 데이터셋 폴더(data/labeled_images/<유형>/)로 복사 보관하고
    dataset.csv 에 기록을 남깁니다. 저장된 경로를 반환합니다.
    """
    _ensure_dirs()
    label_dir = os.path.join(LABELED_DIR, _safe_name(label))
    os.makedirs(label_dir, exist_ok=True)

    base = os.path.basename(src_path)
    dst = os.path.join(label_dir, base)
    # 파일명 중복 방지
    if os.path.exists(dst) and os.path.abspath(dst) != os.path.abspath(src_path):
        name, ext = os.path.splitext(base)
        dst = os.path.join(label_dir, f"{name}_{datetime.now().strftime('%H%M%S%f')}{ext}")

    if os.path.abspath(dst) != os.path.abspath(src_path):
        shutil.copy2(src_path, dst)

    _append_dataset_row(dst, label)
    return dst


def _safe_name(name):
    return "".join(c for c in name if c not in '\\/:*?"<>|').strip() or "미분류"


def _append_dataset_row(path, label):
    _ensure_dirs()
    file_exists = os.path.exists(DATASET_CSV)
    with open(DATASET_CSV, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(["path", "label", "added_at"])
        w.writerow([path, label, datetime.now().isoformat(timespec="seconds")])


def load_dataset():
    """누적된 학습용 데이터셋 (path, label) 리스트 반환 (존재하는 파일만)"""
    _ensure_dirs()
    rows = []
    if os.path.exists(DATASET_CSV):
        with open(DATASET_CSV, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if os.path.exists(row["path"]):
                    rows.append((row["path"], row["label"]))
    return rows


def backup_all(dest_dir):
    """model/, data/ 전체를 타임스탬프 폴더로 백업"""
    _ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = os.path.join(dest_dir, f"defect_inspector_backup_{stamp}")
    os.makedirs(target, exist_ok=True)

    if os.path.isdir(DATA_DIR):
        shutil.copytree(DATA_DIR, os.path.join(target, "data"), dirs_exist_ok=True)
    if os.path.isdir("model"):
        shutil.copytree("model", os.path.join(target, "model"), dirs_exist_ok=True)

    return target
