# -*- coding: utf-8 -*-
"""
feature_extractor.py
불량 이미지에서 AI 학습/판정에 사용할 특징(feature) 벡터를 추출하는 모듈입니다.

추출하는 특징 구성:
 1) 색상 히스토그램 (HSV, 3채널 x 16bin = 48차원)
 2) 결함 영역 형태 특징 (Otsu 이진화 + Contour 분석)
    - 결함 면적 비율, 결함 개수, 평균/최대 둘레, 평균 종횡비(가늘고 긴 정도),
      평균 원형도(circularity, 1에 가까울수록 원형=핀홀성, 낮을수록 선형=갈림성),
      평균 extent(경계상자 대비 채움 비율)
 3) 질감(HOG) 특징 (64x64 축소본, coarse cell 사용 -> 저차원)
 4) 밝기/대비 통계 (평균, 표준편차, 라플라시안 분산=선명도)

이 조합은 다음과 같은 목적에 맞춰 설계했습니다.
 - 핀홀: 작고 둥근(원형도 높음) 결함
 - K/NK 갈림(균열): 가늘고 긴(종횡비 큼, 원형도 낮음) 선형 결함
 - K/NK 유기(이물): 불규칙한 색상/면적 큰 얼룩성 결함
"""

import numpy as np
import cv2
from skimage.feature import hog

IMG_SIZE = 256      # 결함 영역 분석용 표준 크기
HOG_SIZE = 64        # HOG 계산용 축소 크기


def _load_image_any(path):
    """한글 경로 등에서도 안전하게 이미지를 읽기 위해 numpy 방식으로 로드"""
    with open(path, "rb") as f:
        data = np.frombuffer(f.read(), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"이미지를 열 수 없습니다: {path}")
    return img


def _color_histogram(img_bgr):
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    hist = []
    for ch in range(3):
        h = cv2.calcHist([hsv], [ch], None, [16], [0, 256])
        h = cv2.normalize(h, h).flatten()
        hist.extend(h.tolist())
    return np.array(hist, dtype=np.float32)


def _defect_shape_features(gray):
    # Otsu 이진화로 결함(밝거나 어두운 영역) 후보 분리
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # 배경이 더 넓은 영역을 차지한다고 가정하고, 결함(소수 영역)을 흰색(255)으로 통일
    white_ratio = np.mean(th == 255)
    if white_ratio > 0.5:
        th = cv2.bitwise_not(th)

    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    total_area = gray.shape[0] * gray.shape[1]

    areas, perims, aspect_ratios, circularities, extents = [], [], [], [], []
    for c in contours:
        area = cv2.contourArea(c)
        if area < 4:  # 노이즈 제거
            continue
        perim = cv2.arcLength(c, True)
        x, y, w, h = cv2.boundingRect(c)
        aspect = max(w, h) / max(1, min(w, h))
        circularity = (4 * np.pi * area) / (perim * perim) if perim > 0 else 0
        extent = area / max(1, (w * h))
        areas.append(area)
        perims.append(perim)
        aspect_ratios.append(aspect)
        circularities.append(circularity)
        extents.append(extent)

    defect_area_ratio = float(np.sum(areas)) / total_area if areas else 0.0
    num_defects = len(areas)
    avg_perim = float(np.mean(perims)) if perims else 0.0
    max_perim = float(np.max(perims)) if perims else 0.0
    avg_aspect = float(np.mean(aspect_ratios)) if aspect_ratios else 0.0
    avg_circularity = float(np.mean(circularities)) if circularities else 0.0
    avg_extent = float(np.mean(extents)) if extents else 0.0

    return np.array([
        defect_area_ratio, num_defects, avg_perim, max_perim,
        avg_aspect, avg_circularity, avg_extent
    ], dtype=np.float32)


def _texture_hog(gray):
    small = cv2.resize(gray, (HOG_SIZE, HOG_SIZE))
    feat = hog(
        small,
        orientations=8,
        pixels_per_cell=(16, 16),
        cells_per_block=(2, 2),
        block_norm="L2-Hys",
        feature_vector=True,
    )
    return feat.astype(np.float32)


def _brightness_stats(gray):
    mean = float(np.mean(gray))
    std = float(np.std(gray))
    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return np.array([mean, std, lap_var], dtype=np.float32)


def extract_features(path):
    """
    이미지 경로를 입력받아 하나의 1차원 numpy 특징 벡터를 반환합니다.
    """
    img = _load_image_any(path)
    img = cv2.resize(img, (IMG_SIZE, IMG_SIZE))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    f1 = _color_histogram(img)
    f2 = _defect_shape_features(gray)
    f3 = _texture_hog(gray)
    f4 = _brightness_stats(gray)

    return np.concatenate([f1, f2, f3, f4]).astype(np.float32)


FEATURE_DIM_NOTE = "color(48) + shape(7) + hog(가변) + stats(3)"
