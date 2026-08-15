# -*- coding: utf-8 -*-
"""
model_manager.py
분류 모델(RandomForest)의 학습/저장/불러오기/예측을 담당합니다.

이 프로그램의 핵심 기능(학습/자동판정)의 정확도를 높이기 위해:
- 학습 시 이미지마다 데이터 증강(좌우/상하 반전, 회전, 밝기 변화)을 적용하여
  실제 현장 조건 변화에 강인하도록 학습 데이터를 확장합니다.
- 이상치(조명 편차 등)에 덜 민감한 RobustScaler를 사용합니다.
- 판정 유형이 1개만 있어도 학습이 가능합니다 (그 경우 자동판정은 항상 그 유형으로
  100% 신뢰율로 판정되며, 이후 다른 유형이 추가되면 정상적인 다중 분류로 전환됩니다).
"""

import os
import json
import pickle
import numpy as np
from datetime import datetime

from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import RobustScaler, LabelEncoder
from sklearn.model_selection import cross_val_score

from feature_extractor import extract_features, extract_features_from_array, augment_variants, _load_image_any

MODEL_DIR = "model"
MODEL_FILE = os.path.join(MODEL_DIR, "classifier.pkl")
META_FILE = os.path.join(MODEL_DIR, "meta.json")


class ModelManager:
    def __init__(self):
        self.clf = None
        self.scaler = None
        self.label_encoder = None
        self.trained_at = None
        self.train_count = 0
        self.cv_accuracy = None
        os.makedirs(MODEL_DIR, exist_ok=True)
        self.load()

    # ---------------- 학습 ----------------
    def train(self, image_paths, labels, progress_cb=None, augment=True):
        """
        image_paths: 이미지 경로 리스트
        labels: 동일 길이의 문자열 라벨(작업자 판정값) 리스트
        progress_cb: (idx, total) 진행률 콜백 (선택, 원본 이미지 기준 진행률)
        augment: True면 이미지마다 반전/회전/밝기 변형을 추가로 학습에 사용
        """
        if len(image_paths) < 1:
            raise ValueError("학습을 위해서는 최소 1장 이상의 라벨링된 이미지가 필요합니다.")

        feats = []
        expanded_labels = []
        total = len(image_paths)
        for i, (p, label) in enumerate(zip(image_paths, labels)):
            try:
                img = _load_image_any(p)
            except Exception:
                if progress_cb:
                    progress_cb(i + 1, total)
                continue

            if augment:
                variants = augment_variants(img)
            else:
                variants = [img]

            for v in variants:
                feats.append(extract_features_from_array(v))
                expanded_labels.append(label)

            if progress_cb:
                progress_cb(i + 1, total)

        if not feats:
            raise ValueError("학습 가능한 이미지를 하나도 읽지 못했습니다.")

        X = np.vstack(feats)

        self.label_encoder = LabelEncoder()
        y = self.label_encoder.fit_transform(expanded_labels)

        self.scaler = RobustScaler()
        Xs = self.scaler.fit_transform(X)

        num_classes = len(self.label_encoder.classes_)
        self.clf = RandomForestClassifier(
            n_estimators=400,
            max_depth=None,
            min_samples_leaf=1,
            class_weight="balanced" if num_classes > 1 else None,
            random_state=42,
            n_jobs=-1,
        )
        self.clf.fit(Xs, y)

        # 교차검증 정확도 (클래스가 1개뿐이거나 데이터가 너무 적으면 생략)
        self.cv_accuracy = None
        if num_classes >= 2:
            try:
                min_class_count = min(np.bincount(y))
                cv_folds = min(5, int(min_class_count))
                if cv_folds >= 2:
                    scores = cross_val_score(self.clf, Xs, y, cv=cv_folds)
                    self.cv_accuracy = float(np.mean(scores))
            except Exception:
                self.cv_accuracy = None

        self.trained_at = datetime.now().isoformat(timespec="seconds")
        self.train_count = len(image_paths)
        self.save()
        return self.cv_accuracy

    # ---------------- 예측 ----------------
    def predict(self, image_path):
        """단일 이미지에 대해 (예측라벨, 신뢰도[0~1]) 반환. 모델 미학습시 (None, 0.0)"""
        if self.clf is None:
            return None, 0.0
        feat = extract_features(image_path).reshape(1, -1)
        feat_s = self.scaler.transform(feat)
        proba = self.clf.predict_proba(feat_s)[0]
        idx = int(np.argmax(proba))
        label = str(self.label_encoder.inverse_transform([idx])[0])
        confidence = float(proba[idx])
        return label, confidence

    def is_trained(self):
        return self.clf is not None

    # ---------------- 저장/불러오기 ----------------
    def save(self):
        os.makedirs(MODEL_DIR, exist_ok=True)
        with open(MODEL_FILE, "wb") as f:
            pickle.dump({
                "clf": self.clf,
                "scaler": self.scaler,
                "label_encoder": self.label_encoder,
            }, f)
        with open(META_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "trained_at": self.trained_at,
                "train_count": self.train_count,
                "cv_accuracy": self.cv_accuracy,
                "classes": list(self.label_encoder.classes_) if self.label_encoder else [],
            }, f, ensure_ascii=False, indent=2)

    def load(self):
        if os.path.exists(MODEL_FILE):
            try:
                with open(MODEL_FILE, "rb") as f:
                    d = pickle.load(f)
                self.clf = d.get("clf")
                self.scaler = d.get("scaler")
                self.label_encoder = d.get("label_encoder")
                if os.path.exists(META_FILE):
                    with open(META_FILE, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    self.trained_at = meta.get("trained_at")
                    self.train_count = meta.get("train_count", 0)
                    self.cv_accuracy = meta.get("cv_accuracy")
            except Exception:
                # 손상된 모델 파일은 무시
                self.clf = None
                self.scaler = None
                self.label_encoder = None
