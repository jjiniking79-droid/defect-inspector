# -*- coding: utf-8 -*-
"""
feature_extractor.py
불량 이미지에서 AI 학습/판정에 사용할 특징(feature) 벡터를 추출하는 모듈입니다.

이 프로그램의 핵심은 "학습과 자동 판정"이므로, 여러 배치/조명 조건에서 촬영된
이미지에도 안정적으로 동작하도록 아래와 같이 설계했습니다.

1) 조명 정규화 (CLAHE)
   - 이미지마다 밝기/대비가 달라도 특징이 크게 흔들리지 않도록, 명암을 국소적으로
     균일화(CLAHE)한 뒤 특징을 추출합니다. "이미지가 바뀌면 신뢰율이 낮아지는" 문제의
     가장 큰 원인 중 하나가 조명/노출 차이이기 때문에, 이를 우선 정규화합니다.

2) 색상 특징 (Hue/Saturation 히스토그램, 밝기(V) 채널은 제외)
   - 밝기(V)는 조명에 따라 크게 변하므로 제외하고, 색상 자체의 특징(Hue, Saturation)만
     사용해 조명 변화에 덜 민감하게 만듭니다.

3) 텍스처 특징 (LBP - Local Binary Pattern)
   - 표면의 미세한 질감/얼룩을 조명 변화에 강인하게 표현하는 대표적인 텍스처 기술자입니다.

4) 텍스처 특징 (GLCM - Gray-Level Co-occurrence Matrix)
   - 대비(contrast), 균질성(homogeneity), 에너지(energy), 상관성(correlation) 등
     결함의 표면 거칠기/패턴을 정량화합니다. 스크래치성 결함과 얼룩성 결함을 구분하는 데
     특히 유용합니다.

5) 결함 형태 특징 (Otsu 이진화 + Contour 분석, CLAHE 정규화 이미지 기준)
   - 결함 면적 비율, 결함 개수, 둘레, 종횡비(가늘고 긴 정도),
     원형도(circularity, 핀홀성), extent(채움 비율)

6) 질감/에지 구조 (HOG, CLAHE 정규화 이미지 기준)

7) 선명도 (라플라시안 분산, CLAHE 정규화 이미지 기준)

또한 학습 시에는 augment_variants()를 통해 동일 이미지의 좌우/상하 반전, 약간의 회전,
밝기 변화(밝게/어둡게) 버전을 함께 학습시켜, 실제 현장에서 촬영 조건이 조금씩
달라져도 잘 인식하도록 데이터를 증강합니다.
"""

import numpy as np
import cv2
from skimage.feature import hog, local_binary_pattern, graycomatrix, graycoprops

IMG_SIZE = 256      # 결함 영역 분석용 표준 크기
HOG_SIZE = 96        # HOG 계산용 축소 크기
LBP_RADIUS = 2
LBP_POINTS = 8 * LBP_RADIUS
GLCM_LEVELS = 32     # GLCM 계산을 위한 그레이레벨 양자화 단계


def _load_image_any(path):
    """한글 경로 등에서도 안전하게 이미지를 읽기 위해 numpy 방식으로 로드"""
    with open(path, "rb") as f:
        data = np.frombuffer(f.read(), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"이미지를 열 수 없습니다: {path}")
    return img


def _clahe_normalize(gray):
    """조명(밝기/대비) 편차를 줄이기 위한 국소 히스토그램 균일화"""
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _color_histogram_hs(img_bgr):
    """Hue/Saturation 히스토그램 (조명에 민감한 V 채널은 제외)"""
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    hist = []
    for ch, bins in ((0, 24), (1, 16)):  # Hue: 24bin, Saturation: 16bin
        h = cv2.calcHist([hsv], [ch], None, [bins], [0, 256])
        h = cv2.normalize(h, h, norm_type=cv2.NORM_L1).flatten()
        hist.extend(h.tolist())
    return np.array(hist, dtype=np.float32)


def _lbp_histogram(gray_eq):
    lbp = local_binary_pattern(gray_eq, LBP_POINTS, LBP_RADIUS, method="uniform")
    n_bins = LBP_POINTS + 2
    hist, _ = np.histogram(lbp.ravel(), bins=n_bins, range=(0, n_bins), density=True)
    return hist.astype(np.float32)


def _glcm_features(gray_eq):
    # 계산량 절감을 위해 축소 후 그레이레벨 양자화
    small = cv2.resize(gray_eq, (128, 128))
    quant = (small.astype(np.float32) / 256.0 * GLCM_LEVELS).astype(np.uint8)
    quant = np.clip(quant, 0, GLCM_LEVELS - 1)
    glcm = graycomatrix(quant, distances=[1, 3], angles=[0, np.pi / 4, np.pi / 2, 3 * np.pi / 4],
                         levels=GLCM_LEVELS, symmetric=True, normed=True)
    feats = []
    for prop in ("contrast", "homogeneity", "energy", "correlation"):
        vals = graycoprops(glcm, prop)
        feats.append(float(np.mean(vals)))
    return np.array(feats, dtype=np.float32)


def _defect_shape_features(gray_eq):
    blur = cv2.GaussianBlur(gray_eq, (5, 5), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    white_ratio = np.mean(th == 255)
    if white_ratio > 0.5:
        th = cv2.bitwise_not(th)

    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    total_area = gray_eq.shape[0] * gray_eq.shape[1]

    areas, perims, aspect_ratios, circularities, extents = [], [], [], [], []
    for c in contours:
        area = cv2.contourArea(c)
        if area < 4:
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


def _texture_hog(gray_eq):
    small = cv2.resize(gray_eq, (HOG_SIZE, HOG_SIZE))
    feat = hog(
        small,
        orientations=9,
        pixels_per_cell=(12, 12),
        cells_per_block=(2, 2),
        block_norm="L2-Hys",
        feature_vector=True,
    )
    return feat.astype(np.float32)


def _sharpness(gray_eq):
    return np.array([float(cv2.Laplacian(gray_eq, cv2.CV_64F).var())], dtype=np.float32)


def extract_features_from_array(img_bgr):
    """BGR numpy 이미지 배열로부터 특징 벡터를 추출합니다. (증강 이미지 처리에 사용)"""
    img = cv2.resize(img_bgr, (IMG_SIZE, IMG_SIZE))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray_eq = _clahe_normalize(gray)

    f_color = _color_histogram_hs(img)
    f_lbp = _lbp_histogram(gray_eq)
    f_glcm = _glcm_features(gray_eq)
    f_shape = _defect_shape_features(gray_eq)
    f_hog = _texture_hog(gray_eq)
    f_sharp = _sharpness(gray_eq)

    return np.concatenate([f_color, f_lbp, f_glcm, f_shape, f_hog, f_sharp]).astype(np.float32)


def extract_features(path):
    """이미지 경로를 입력받아 하나의 1차원 numpy 특징 벡터를 반환합니다."""
    img = _load_image_any(path)
    return extract_features_from_array(img)


def augment_variants(img_bgr):
    """
    학습 시에만 사용하는 데이터 증강. 동일한 결함이라도 실제 현장에서는 촬영 각도,
    좌우/상하 방향, 밝기가 조금씩 달라질 수 있으므로, 그런 변형들을 미리 만들어
    함께 학습시켜 신뢰율(일반화 성능)을 높입니다.
    """
    variants = [img_bgr]
    variants.append(cv2.flip(img_bgr, 1))   # 좌우 반전
    variants.append(cv2.flip(img_bgr, 0))   # 상하 반전

    h, w = img_bgr.shape[:2]
    for angle in (7, -7):
        M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        rotated = cv2.warpAffine(img_bgr, M, (w, h), borderMode=cv2.BORDER_REFLECT)
        variants.append(rotated)

    bright = cv2.convertScaleAbs(img_bgr, alpha=1.18, beta=12)
    dark = cv2.convertScaleAbs(img_bgr, alpha=0.82, beta=-12)
    variants.append(bright)
    variants.append(dark)

    return variants


FEATURE_DIM_NOTE = "color(40) + lbp(18) + glcm(4) + shape(7) + hog(가변) + sharp(1)"
