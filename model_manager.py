# -*- coding: utf-8 -*-
"""
model_manager.py
분류 모델(RandomForest)의 학습/저장/불러오기/예측을 담당합니다.
- 작은 데이터셋에서도 비교적 안정적으로 동작하는 RandomForest 사용
- StandardScaler + LabelEncoder 포함하여 model 폴더에 함께 저장
"""

import os
import json
import pickle
import numpy as np
from datetime import datetime

from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.model_selection import cross_val_score

from feature_extractor import extract_features

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
    def train(self, image_paths, labels, progress_cb=None):
        """
        image_paths: 이미지 경로 리스트
        labels: 동일 길이의 문자열 라벨(작업자 판정값) 리스트
        progress_cb: (idx, total) 진행률 콜백 (선택)
        """
        if len(image_paths) < 2:
            raise ValueError("학습을 위해서는 최소 2장 이상의 라벨링된 이미지가 필요합니다.")
        if len(set(labels)) < 2:
            raise ValueError("학습을 위해서는 최소 2개 이상의 서로 다른 판정 유형이 필요합니다.")

        feats = []
        for i, p in enumerate(image_paths):
            feats.append(extract_features(p))
            if progress_cb:
                progress_cb(i + 1, len(image_paths))
        X = np.vstack(feats)

        self.label_encoder = LabelEncoder()
        y = self.label_encoder.fit_transform(labels)

        self.scaler = StandardScaler()
        Xs = self.scaler.fit_transform(X)

        self.clf = RandomForestClassifier(
            n_estimators=300,
            max_depth=None,
            min_samples_leaf=1,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
        )
        self.clf.fit(Xs, y)

        # 교차검증 정확도 (데이터가 너무 적으면 생략)
        self.cv_accuracy = None
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
