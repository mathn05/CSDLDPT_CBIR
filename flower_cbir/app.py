import os
import sys
from pathlib import Path
import html
import time
import hashlib

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
import numpy as np
import cv2
import matplotlib.pyplot as plt

from flower_cbir.core.preprocess import load_image
from flower_cbir.core.segmentation import clean_mask, create_mask_overlay
from flower_cbir.core.features import extract_all_features
from flower_cbir.core.distance import compute_overall_distance
from flower_cbir.services.search import FlowerSearchEngine
from flower_cbir.services.database import FlowerDatabase
from flower_cbir.evaluation.evaluate import run_correctness_checks, run_ablation_study
from flower_cbir.settings import default_weights
from flower_cbir.core.contract import extraction_contract, fingerprint

plt.style.use('dark_background')


def safe_search(*args, **kwargs):
    try:
        return engine.search(*args, **kwargs)
    except (ValueError, RuntimeError, OSError, cv2.error) as error:
        st.error(f'Không thể xử lý ảnh: {error}')
        st.stop()


# --- Page Configuration & Custom Styling ---
st.set_page_config(
    page_title="CBIR - Hệ thống Tìm kiếm Ảnh Bông hoa",
    page_icon="🌸",
    layout="wide",
    initial_sidebar_state="expanded"
)

CUSTOM_CSS = """
<style>
    /* Global layout polish */
    .block-container {
        padding-top: 1.6rem;
        padding-bottom: 2.5rem;
    }

    /* Hero Banner */
    .hero-banner {
        background: linear-gradient(135deg, rgba(255, 75, 114, 0.14) 0%, rgba(255, 133, 51, 0.10) 50%, rgba(99, 102, 241, 0.12) 100%);
        border: 1px solid rgba(255, 255, 255, 0.10);
        border-radius: 18px;
        padding: 22px 28px;
        margin-bottom: 1.4rem;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.22);
    }
    .hero-kicker {
        display: inline-block;
        font-size: 0.76rem;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: #ff9a76;
        background: rgba(255, 133, 51, 0.14);
        border: 1px solid rgba(255, 133, 51, 0.30);
        padding: 4px 12px;
        border-radius: 999px;
        margin-bottom: 10px;
    }
    .main-header {
        font-size: 2.05rem;
        font-weight: 800;
        letter-spacing: -0.01em;
        background: linear-gradient(90deg, #ff6b8b 0%, #ffa260 55%, #ffd166 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.35rem;
        line-height: 1.25;
    }
    .sub-header {
        color: #cbd5e1;
        font-size: 0.96rem;
        margin-bottom: 0;
        opacity: 0.9;
    }
    .pill-row {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 12px;
    }
    .info-pill {
        font-size: 0.78rem;
        color: #e2e8f0;
        background: rgba(15, 23, 42, 0.55);
        border: 1px solid rgba(148, 163, 184, 0.22);
        border-radius: 999px;
        padding: 4px 12px;
    }

    /* Section Headers */
    .section-title {
        font-size: 1.22rem;
        font-weight: 700;
        color: #f8fafc;
        margin: 0.5rem 0 0.85rem 0;
        display: flex;
        align-items: center;
        gap: 8px;
    }

    /* Result Cards & Rank Badges */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 14px !important;
        border-color: rgba(148, 163, 184, 0.18) !important;
        background: linear-gradient(180deg, rgba(30, 41, 59, 0.45) 0%, rgba(15, 23, 42, 0.55) 100%);
        transition: border-color 0.2s ease, transform 0.2s ease;
    }
    div[data-testid="stVerticalBlockBorderWrapper"]:hover {
        border-color: rgba(255, 133, 51, 0.45) !important;
    }
    .rank-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 8px;
    }
    .metric-badge {
        display: inline-block;
        padding: 3px 11px;
        border-radius: 999px;
        font-weight: 700;
        font-size: 0.76rem;
        letter-spacing: 0.03em;
    }
    .badge-rank-1 { background: linear-gradient(135deg, #ffd700, #f59e0b); color: #111827; box-shadow: 0 2px 10px rgba(245, 158, 11, 0.35); }
    .badge-rank-2 { background: linear-gradient(135deg, #e2e8f0, #94a3b8); color: #0f172a; }
    .badge-rank-3 { background: linear-gradient(135deg, #d97706, #b45309); color: #fff; }
    .badge-rank-other { background: rgba(71, 85, 105, 0.75); color: #f1f5f9; border: 1px solid rgba(148, 163, 184, 0.25); }

    .file-label {
        font-weight: 700;
        font-size: 0.94rem;
        color: #f8fafc;
        margin-top: 6px;
        margin-bottom: 4px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    /* Similarity Progress Bar */
    .sim-row {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        font-size: 0.84rem;
        margin-bottom: 4px;
    }
    .sim-score {
        font-size: 1.08rem;
        font-weight: 800;
        color: #34d399;
    }
    .dist-caption {
        font-size: 0.78rem;
        color: #94a3b8;
        font-family: monospace;
    }
    .sim-track {
        width: 100%;
        height: 7px;
        background: rgba(148, 163, 184, 0.18);
        border-radius: 999px;
        overflow: hidden;
        margin-bottom: 10px;
    }
    .sim-fill {
        height: 100%;
        background: linear-gradient(90deg, #10b981 0%, #34d399 60%, #fbbf24 100%);
        border-radius: 999px;
    }

    /* Mini Contribution Bars inside Result Cards */
    .contrib-box {
        background: rgba(15, 23, 42, 0.55);
        border: 1px solid rgba(148, 163, 184, 0.14);
        border-radius: 10px;
        padding: 10px 12px;
        margin-top: 6px;
    }
    .contrib-row {
        margin-bottom: 7px;
    }
    .contrib-row:last-child {
        margin-bottom: 0;
    }
    .contrib-meta {
        display: flex;
        justify-content: space-between;
        font-size: 0.75rem;
        color: #cbd5e1;
        margin-bottom: 2px;
    }
    .contrib-track {
        width: 100%;
        height: 5px;
        background: rgba(148, 163, 184, 0.15);
        border-radius: 999px;
        overflow: hidden;
    }
    .contrib-fill {
        height: 100%;
        border-radius: 999px;
    }

    /* Weight Bar in Sidebar */
    .weight-stack {
        display: flex;
        width: 100%;
        height: 10px;
        border-radius: 999px;
        overflow: hidden;
        margin: 8px 0 6px 0;
        border: 1px solid rgba(255, 255, 255, 0.12);
    }
    .weight-legend {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 4px 10px;
        font-size: 0.76rem;
        color: #cbd5e1;
        margin-bottom: 4px;
    }
    .dot {
        display: inline-block;
        width: 8px;
        height: 8px;
        border-radius: 50%;
        margin-right: 5px;
    }

    /* Metric styling */
    div[data-testid="stMetric"] {
        background: rgba(30, 41, 59, 0.55);
        border: 1px solid rgba(148, 163, 184, 0.16);
        border-radius: 12px;
        padding: 10px 14px;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.8rem !important;
        color: #94a3b8 !important;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.25rem !important;
        font-weight: 700 !important;
    }

    /* Tabs */
    .stTabs [data-baseweb="tab-list"] {
        gap: 10px;
        background: rgba(15, 23, 42, 0.45);
        padding: 6px;
        border-radius: 12px;
        border: 1px solid rgba(148, 163, 184, 0.14);
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        padding: 8px 18px;
        font-weight: 600;
    }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

GROUP_COLORS = {
    "F1": "#ff6b6b",
    "F2": "#feca57",
    "F3": "#48dbfb",
    "F4": "#a29bfe",
}


@st.cache_resource
def get_search_engine(active_version_id, contract_fingerprint):
    """Cache the search engine and index in memory for instant query responses."""
    db = FlowerDatabase()
    try:
        engine = FlowerSearchEngine(db.load_index())
    except Exception:
        db.close()
        raise
    return engine, db


@st.cache_data(show_spinner=False, max_entries=32)
def load_gallery_visual_explanation(image_path: str, mask_path: str):
    """Load RGB, cleaned mask, overlay and intermediate explanation signals for a gallery item."""
    rgb = load_image(image_path)
    raw_mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
    if raw_mask is None:
        raise FileNotFoundError(f"Không tìm thấy mask: {mask_path}")
    mask = clean_mask(raw_mask)
    overlay = create_mask_overlay(rgb, mask)
    _, explanation = extract_all_features(rgb, mask)
    return {
        "image_rgb": rgb,
        "mask_binary": mask,
        "overlay": overlay,
        "explanation": explanation,
    }


def draw_concentric_rings(image_rgb: np.ndarray, geom: dict) -> np.ndarray:
    """Draw centroid and 3 concentric Lab rings (rho = 1/3, 2/3, 1.0) on an RGB image."""
    ring_vis = image_rgb.copy()
    xc, yc = int(round(geom["centroid"][0])), int(round(geom["centroid"][1]))
    cv2.circle(ring_vis, (xc, yc), 4, (255, 75, 75), -1)
    r_max = int(round(geom["r_max"]))
    for r_factor, color in [(1 / 3, (255, 230, 80)), (2 / 3, (64, 224, 208)), (1.0, (218, 112, 214))]:
        cv2.circle(ring_vis, (xc, yc), int(r_max * r_factor), color, 1, lineType=cv2.LINE_AA)
    return ring_vis


# Load engine and database
try:
    probe = FlowerDatabase()
    try:
        active_version_id = probe.active_version()
    finally:
        probe.close()
    engine, db = get_search_engine(active_version_id, fingerprint(extraction_contract()))
except Exception as e:
    st.error(f"Lỗi khởi tạo hệ thống tìm kiếm: {e}")
    st.stop()

filename_to_idx = {m["filename"]: idx for idx, m in enumerate(engine.metadata)}


# --- Sidebar Navigation & Settings ---
with st.sidebar:
    st.markdown("### 🌸 CBIR Flower Retrieval")
    st.caption(f"Phiên bản Index: `{engine.version_id}`")
    s_c1, s_c2 = st.columns(2)
    s_c1.metric("Kho ảnh", f"{engine.num_images}")
    s_c2.metric("Số chiều", "163D")

    st.divider()
    st.markdown("#### ⚙️ Trọng số Nhóm Đặc trưng")
    configured_weights = default_weights()
    w1 = st.slider("w₁ · F1 Màu tổng thể (78D)", 0.0, 1.0, configured_weights[0], 0.05)
    w2 = st.slider("w₂ · F2 Màu theo vành Lab (18D)", 0.0, 1.0, configured_weights[1], 0.05)
    w3 = st.slider("w₃ · F3 Kết cấu LBP (28D)", 0.0, 1.0, configured_weights[2], 0.05)
    w4 = st.slider("w₄ · F4 Hình dạng & Cấu trúc (39D)", 0.0, 1.0, configured_weights[3], 0.05)

    w_sum = w1 + w2 + w3 + w4
    if w_sum > 0:
        weights = (w1 / w_sum, w2 / w_sum, w3 / w_sum, w4 / w_sum)
    else:
        weights = (0.25, 0.25, 0.25, 0.25)

    st.markdown(
        f"""
        <div class="weight-stack">
            <div style="width:{weights[0]*100:.2f}%; background:{GROUP_COLORS['F1']};"></div>
            <div style="width:{weights[1]*100:.2f}%; background:{GROUP_COLORS['F2']};"></div>
            <div style="width:{weights[2]*100:.2f}%; background:{GROUP_COLORS['F3']};"></div>
            <div style="width:{weights[3]*100:.2f}%; background:{GROUP_COLORS['F4']};"></div>
        </div>
        <div class="weight-legend">
            <div><span class="dot" style="background:{GROUP_COLORS['F1']};"></span>F1: <b>{weights[0]*100:.0f}%</b></div>
            <div><span class="dot" style="background:{GROUP_COLORS['F2']};"></span>F2: <b>{weights[1]*100:.0f}%</b></div>
            <div><span class="dot" style="background:{GROUP_COLORS['F3']};"></span>F3: <b>{weights[2]*100:.0f}%</b></div>
            <div><span class="dot" style="background:{GROUP_COLORS['F4']};"></span>F4: <b>{weights[3]*100:.0f}%</b></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption("📌 Kết quả trả về mặc định: **Top 5 ảnh giống nhất** theo yêu cầu đề bài.")


# --- Main Application Hero Header ---
st.markdown(
    f"""
    <div class="hero-banner">
        <div class="hero-kicker">🌸 Hệ Cơ sở Dữ liệu Đa phương tiện · Nhóm 09</div>
        <div class="main-header">HỆ THỐNG TRUY VẤN ẢNH BÔNG HOA THEO NỘI DUNG (CBIR)</div>
        <p class="sub-header">
            Phân đoạn vùng hoa tự động với <b>DeepLabV3+ (384×384)</b> kết hợp trích rút <b>163 đặc trưng thị giác (F1–F4)</b>,
            chuẩn hóa chọn lọc Z-score và đo khoảng cách tổ hợp Hellinger + RMS trên PostgreSQL.
        </p>
        <div class="pill-row">
            <span class="info-pill">🎨 F1: HSV Histogram (78D)</span>
            <span class="info-pill">🎯 F2: 3-Ring CIELAB (18D)</span>
            <span class="info-pill">🧶 F3: Multi-Radius LBP riu2 (28D)</span>
            <span class="info-pill">📐 F4: Hình học + Fourier + IAP + IEP (39D)</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

tab_search, tab_explain, tab_eval, tab_history = st.tabs([
    "🔍 Tìm kiếm Tương đồng",
    "🔬 Giải thích & Đối chiếu Trung gian",
    "🧪 Đánh giá & Ablation Study",
    "📜 Lịch sử Truy vấn"
])


# --- Helper Functions: Plotting Figures ---
def _style_ax(ax, title=""):
    ax.set_facecolor("#131c2e")
    if title:
        ax.set_title(title, fontsize=10, fontweight="bold", color="#f8fafc", pad=8)
    ax.tick_params(colors="#cbd5e1", labelsize=8.5)
    for spine in ax.spines.values():
        spine.set_color("#334155")


def plot_f1_visuals(exp_f1):
    hs_2d = exp_f1["hs_2d"]
    v_hist = exp_f1["v_hist"]

    fig, axes = plt.subplots(1, 2, figsize=(10.2, 3.4), facecolor="#0f172a")
    im = axes[0].imshow(hs_2d.T, cmap="magma", aspect="auto", origin="lower")
    _style_ax(axes[0], "HSV: Heatmap phân bố H–S (12 × 6 bins)")
    axes[0].set_xlabel("Hue Bin (0..11, mỗi 30°)", color="#cbd5e1", fontsize=8.5)
    axes[0].set_ylabel("Saturation Bin (0..5)", color="#cbd5e1", fontsize=8.5)
    cbar = plt.colorbar(im, ax=axes[0], fraction=0.046, pad=0.04)
    cbar.ax.tick_params(colors="#cbd5e1", labelsize=8)

    axes[1].bar(range(1, 7), v_hist, color="#fbbf24", edgecolor="#fde68a", alpha=0.88, width=0.65)
    _style_ax(axes[1], "Độ sáng Value Histogram (6 bins)")
    axes[1].set_xlabel("Khoảng sáng V (1..6)", color="#cbd5e1", fontsize=8.5)
    axes[1].set_ylabel("Tần suất chuẩn hóa", color="#cbd5e1", fontsize=8.5)
    axes[1].set_ylim(0, max(0.1, float(np.max(v_hist)) * 1.22))
    axes[1].grid(axis="y", color="#334155", linestyle="--", alpha=0.45)

    fig.tight_layout()
    return fig


def plot_f3_visuals(exp_f3):
    lbp8 = exp_f3["lbp8_hist"]
    lbp16 = exp_f3["lbp16_hist"]

    fig, axes = plt.subplots(1, 2, figsize=(10.2, 3.2), facecolor="#0f172a")
    axes[0].bar(range(len(lbp8)), lbp8, color="#38bdf8", edgecolor="#bae6fd", alpha=0.88)
    _style_ax(axes[0], "LBP riu2 (P=8, R=1) — 10 Nhãn")
    axes[0].set_xlabel("Mã LBP (0..8 đồng nhất, 9 không đồng nhất)", color="#cbd5e1", fontsize=8.5)
    axes[0].set_ylabel("Tần suất", color="#cbd5e1", fontsize=8.5)
    axes[0].grid(axis="y", color="#334155", linestyle="--", alpha=0.45)

    axes[1].bar(range(len(lbp16)), lbp16, color="#a78bfa", edgecolor="#ddd6fe", alpha=0.88)
    _style_ax(axes[1], "LBP riu2 (P=16, R=2) — 18 Nhãn")
    axes[1].set_xlabel("Mã LBP (0..16 đồng nhất, 17 không đồng nhất)", color="#cbd5e1", fontsize=8.5)
    axes[1].set_ylabel("Tần suất", color="#cbd5e1", fontsize=8.5)
    axes[1].grid(axis="y", color="#334155", linestyle="--", alpha=0.45)

    fig.tight_layout()
    return fig


def plot_f4_visuals(exp_f4):
    r_signal = exp_f4["r_signal"]
    spectrum = exp_f4["radial_spectrum"]
    iap = exp_f4["iap_matrix"]
    d_vals = exp_f4["d_vals"]

    fig, axes = plt.subplots(2, 2, figsize=(10.2, 6.4), facecolor="#0f172a")

    axes[0, 0].plot(r_signal, color="#f472b6", lw=1.9)
    axes[0, 0].fill_between(range(len(r_signal)), r_signal, color="#f472b6", alpha=0.14)
    _style_ax(axes[0, 0], "Tín hiệu bán kính tia r[n] (128 tia)")
    axes[0, 0].set_xlabel("Chỉ số tia n (0..127)", color="#cbd5e1", fontsize=8.5)
    axes[0, 0].set_ylabel("Bán kính r (px)", color="#cbd5e1", fontsize=8.5)
    axes[0, 0].grid(color="#334155", linestyle="--", alpha=0.4)

    markerline, stemlines, baseline = axes[0, 1].stem(range(1, 13), spectrum)
    plt.setp(markerline, color="#fb923c", markersize=6)
    plt.setp(stemlines, color="#fb923c", linewidth=1.6)
    plt.setp(baseline, color="#475569", linewidth=1)
    _style_ax(axes[0, 1], "Phổ bán kính Fourier S[1]..S[12]")
    axes[0, 1].set_xlabel("Bậc tần số k", color="#cbd5e1", fontsize=8.5)
    axes[0, 1].set_ylabel("|R[k]| / |R[0]|", color="#cbd5e1", fontsize=8.5)
    axes[0, 1].set_xticks(range(1, 13))
    axes[0, 1].grid(axis="y", color="#334155", linestyle="--", alpha=0.4)

    im = axes[1, 0].imshow(iap, cmap="plasma", aspect="auto")
    _style_ax(axes[1, 0], "Ma trận góc nội bộ IAP (4 vành × 4 hướng)")
    axes[1, 0].set_xlabel("Khoảng hướng m (0: tiếp tuyến, 2: dọc tia)", color="#cbd5e1", fontsize=8.5)
    axes[1, 0].set_ylabel("Vành bán kính trên M_int (0..3)", color="#cbd5e1", fontsize=8.5)
    axes[1, 0].set_xticks(range(4))
    axes[1, 0].set_yticks(range(4))
    cbar = plt.colorbar(im, ax=axes[1, 0], fraction=0.046, pad=0.04)
    cbar.ax.tick_params(colors="#cbd5e1", labelsize=8)

    axes[1, 1].plot(range(1, 6), d_vals, marker="o", color="#34d399", lw=2.1, markersize=6)
    axes[1, 1].fill_between(range(1, 6), d_vals, color="#34d399", alpha=0.14)
    _style_ax(
        axes[1, 1],
        f"Mật độ cạnh IEP 5 vành (S_edge={exp_f4['s_edge']:.3f}, CV={exp_f4['cv_edge']:.3f})"
    )
    axes[1, 1].set_xlabel("Vành nội bộ k (1=tâm .. 5=ngoài)", color="#cbd5e1", fontsize=8.5)
    axes[1, 1].set_ylabel("Mật độ cạnh d_k", color="#cbd5e1", fontsize=8.5)
    axes[1, 1].set_xticks(range(1, 6))
    axes[1, 1].grid(color="#334155", linestyle="--", alpha=0.4)

    fig.tight_layout()
    return fig


def plot_pair_distance_breakdown(details_single: dict, weights_tuple: tuple, total_d: float, total_s: float):
    """Plot intermediate distance & weighted contribution breakdown between Query and a Top-5 match."""
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 3.6), facecolor="#0f172a")

    # Left: Weighted group contributions w_i * D_i vs raw D_i
    labels_g = [
        "F1 · Màu tổng thể",
        "F2 · Màu 3 vành Lab",
        "F3 · Kết cấu LBP",
        "F4 · Hình dạng & Cấu trúc",
    ]
    d_vals = [
        float(details_single["D1"]),
        float(details_single["D2"]),
        float(details_single["D3"]),
        float(details_single["D4"]),
    ]
    w_contribs = [w * d for w, d in zip(weights_tuple, d_vals)]
    y_pos = np.arange(len(labels_g))
    bar_colors = [GROUP_COLORS["F1"], GROUP_COLORS["F2"], GROUP_COLORS["F3"], GROUP_COLORS["F4"]]

    axes[0].barh(y_pos - 0.18, d_vals, height=0.34, color=bar_colors, alpha=0.38, label="Khoảng cách nhóm Dᵢ ∈ [0,1]")
    bars_w = axes[0].barh(y_pos + 0.18, w_contribs, height=0.34, color=bar_colors, alpha=0.95, label="Đóng góp có trọng số wᵢ·Dᵢ")
    _style_ax(axes[0], f"Tổng hợp khoảng cách D = {total_d:.4f}  →  Tương đồng S = {total_s*100:.1f}%")
    axes[0].set_yticks(y_pos)
    axes[0].set_yticklabels(labels_g, color="#e2e8f0", fontsize=8.5)
    axes[0].invert_yaxis()
    axes[0].set_xlim(0, max(0.65, max(d_vals) * 1.18))
    axes[0].grid(axis="x", color="#334155", linestyle="--", alpha=0.4)
    axes[0].legend(loc="lower right", fontsize=7.8, facecolor="#0f172a", edgecolor="#334155")
    for bar, d_v, wc_v in zip(bars_w, d_vals, w_contribs):
        axes[0].text(
            max(d_v, wc_v) + 0.015,
            bar.get_y() + bar.get_height() / 2,
            f"wᵢDᵢ={wc_v:.3f} (Dᵢ={d_v:.3f})",
            va="center",
            color="#e2e8f0",
            fontsize=7.8,
        )

    # Right: All 9 sub-distances across the 163D pipeline
    sub_names = [
        "F1: H–S (72D)",
        "F1: Value (6D)",
        "F2: Lab 3 vành (18D)",
        "F3: LBP8,1 (10D)",
        "F3: LBP16,2 (18D)",
        "F4: Scalars (4D)",
        "F4: Fourier (12D)",
        "F4: IAP (16D)",
        "F4: IEP (7D)",
    ]
    sub_vals = [
        float(details_single["d_hs"]),
        float(details_single["d_v"]),
        float(details_single["D2"]),
        float(details_single["d_lbp8"]),
        float(details_single["d_lbp16"]),
        float(details_single["d4_scalars"]),
        float(details_single["d4_fourier"]),
        float(details_single["d4_iap"]),
        float(details_single["d4_iep"]),
    ]
    sub_colors = [
        GROUP_COLORS["F1"], GROUP_COLORS["F1"],
        GROUP_COLORS["F2"],
        GROUP_COLORS["F3"], GROUP_COLORS["F3"],
        GROUP_COLORS["F4"], GROUP_COLORS["F4"], GROUP_COLORS["F4"], GROUP_COLORS["F4"],
    ]
    y_sub = np.arange(len(sub_names))
    bars_s = axes[1].barh(y_sub, sub_vals, height=0.62, color=sub_colors, alpha=0.88)
    _style_ax(axes[1], "Khoảng cách chi tiết trên 9 khối đặc trưng con")
    axes[1].set_yticks(y_sub)
    axes[1].set_yticklabels(sub_names, color="#e2e8f0", fontsize=8)
    axes[1].invert_yaxis()
    axes[1].set_xlim(0, max(0.7, max(sub_vals) * 1.18))
    axes[1].grid(axis="x", color="#334155", linestyle="--", alpha=0.4)
    for bar, val in zip(bars_s, sub_vals):
        axes[1].text(val + 0.012, bar.get_y() + bar.get_height() / 2, f"{val:.3f}", va="center", color="#cbd5e1", fontsize=7.8)

    fig.tight_layout()
    return fig


def plot_pair_signal_comparison(q_raw: np.ndarray, t_raw: np.ndarray, q_exp: dict, t_exp: dict, target_name: str):
    """Side-by-side comparison of intermediate feature distributions between Query and Target."""
    fig, axes = plt.subplots(2, 2, figsize=(11.0, 6.2), facecolor="#0f172a")

    # 1. Hue marginal distribution (12 Hue bins summed over 6 S bins) + V (6 bins) -> show 12 Hue bins
    q_hue = np.sum(q_exp["f1"]["hs_2d"], axis=1)
    t_hue = np.sum(t_exp["f1"]["hs_2d"], axis=1)
    x_h = np.arange(12)
    axes[0, 0].bar(x_h - 0.18, q_hue, width=0.35, color="#ff6b8b", label="Ảnh truy vấn (Query)")
    axes[0, 0].bar(x_h + 0.18, t_hue, width=0.35, color="#38bdf8", label=target_name)
    _style_ax(axes[0, 0], "F1 · Phân bố Sắc độ Hue trên 12 khoảng (mỗi 30°)")
    axes[0, 0].set_xticks(x_h)
    axes[0, 0].set_xlabel("Hue Bin (0..11)", color="#cbd5e1", fontsize=8)
    axes[0, 0].set_ylabel("Tỷ lệ pixel vùng hoa", color="#cbd5e1", fontsize=8)
    axes[0, 0].legend(fontsize=7.8, facecolor="#0f172a", edgecolor="#334155")
    axes[0, 0].grid(axis="y", color="#334155", linestyle="--", alpha=0.4)

    # 2. F2: Mean L*, a*, b* across 3 concentric rings
    q_f2 = q_raw[78:96].reshape(3, 6)  # [mu_L, std_L, mu_a, std_a, mu_b, std_b]
    t_f2 = t_raw[78:96].reshape(3, 6)
    rings = np.array([1, 2, 3])
    for ch_idx, ch_label, ch_color in [(0, "μ_L*", "#facc15"), (2, "μ_a*", "#f87171"), (4, "μ_b*", "#60a5fa")]:
        axes[0, 1].plot(rings, q_f2[:, ch_idx], marker="o", lw=2.0, color=ch_color, linestyle="-", label=f"Query {ch_label}")
        axes[0, 1].plot(rings, t_f2[:, ch_idx], marker="s", lw=1.8, color=ch_color, linestyle="--", alpha=0.8, label=f"Match {ch_label}")
    _style_ax(axes[0, 1], "F2 · Xu hướng màu Lab trung bình từ Tâm (Vành 1) → Cánh (Vành 3)")
    axes[0, 1].set_xticks(rings)
    axes[0, 1].set_xticklabels(["Vành 1 (Tâm)", "Vành 2 (Giữa)", "Vành 3 (Ngoài)"], fontsize=8)
    axes[0, 1].legend(ncol=3, fontsize=7.0, facecolor="#0f172a", edgecolor="#334155")
    axes[0, 1].grid(color="#334155", linestyle="--", alpha=0.4)

    # 3. F3: Multi-radius LBP (28 bins)
    q_f3 = q_raw[96:124]
    t_f3 = t_raw[96:124]
    x_lbp = np.arange(28)
    axes[1, 0].plot(x_lbp, q_f3, marker="o", markersize=3.5, lw=1.6, color="#ff6b8b", label="Query")
    axes[1, 0].plot(x_lbp, t_f3, marker="s", markersize=3.5, lw=1.6, color="#38bdf8", linestyle="--", label=target_name)
    axes[1, 0].axvline(9.5, color="#64748b", linestyle=":", lw=1.2)
    _style_ax(axes[1, 0], "F3 · Hồ sơ kết cấu LBP₈,₁ (0..9) | LBP₁₆,₂ (10..27)")
    axes[1, 0].set_xlabel("Chỉ số bin LBP (28 chiều)", color="#cbd5e1", fontsize=8)
    axes[1, 0].set_ylabel("Tần suất", color="#cbd5e1", fontsize=8)
    axes[1, 0].legend(fontsize=7.8, facecolor="#0f172a", edgecolor="#334155")
    axes[1, 0].grid(color="#334155", linestyle="--", alpha=0.4)

    # 4. F4: Fourier Radial Spectrum S[1..12] & IEP 5-ring Edge Profile overlay (Fourier S[1..12])
    q_fourier = q_raw[128:140]
    t_fourier = t_raw[128:140]
    x_k = np.arange(1, 13)
    axes[1, 1].bar(x_k - 0.18, q_fourier, width=0.35, color="#ff6b8b", label="Query S[k]")
    axes[1, 1].bar(x_k + 0.18, t_fourier, width=0.35, color="#38bdf8", label=f"{target_name} S[k]")
    _style_ax(axes[1, 1], "F4.5 · Phổ bán kính Fourier S[1]..S[12] (Nhịp biên cánh)")
    axes[1, 1].set_xticks(x_k)
    axes[1, 1].set_xlabel("Bậc tần số k", color="#cbd5e1", fontsize=8)
    axes[1, 1].set_ylabel("|R[k]| / |R[0]|", color="#cbd5e1", fontsize=8)
    axes[1, 1].legend(fontsize=7.8, facecolor="#0f172a", edgecolor="#334155")
    axes[1, 1].grid(axis="y", color="#334155", linestyle="--", alpha=0.4)

    fig.tight_layout()
    return fig


def render_contribution_bars_html(item: dict) -> str:
    """Render compact HTML progress bars for weighted contributions w_i * D_i and raw D_i."""
    c = item["contributions"]
    total_d = max(item["distance"], 1e-9)
    rows = [
        ("F1 Màu sắc", item["D1"], c["w1_D1"], GROUP_COLORS["F1"]),
        ("F2 Màu vùng", item["D2"], c["w2_D2"], GROUP_COLORS["F2"]),
        ("F3 Kết cấu", item["D3"], c["w3_D3"], GROUP_COLORS["F3"]),
        ("F4 Hình dạng", item["D4"], c["w4_D4"], GROUP_COLORS["F4"]),
    ]
    parts = ['<div class="contrib-box">']
    for label, d_val, wd_val, color in rows:
        share_pct = min(100.0, max(0.0, (wd_val / total_d) * 100.0)) if item["distance"] > 0 else 0.0
        parts.append(
            f'<div class="contrib-row">'
            f'<div class="contrib-meta">'
            f'<span><span class="dot" style="background:{color};"></span>{label} (D={d_val:.3f})</span>'
            f'<span><b>+{wd_val:.3f}</b> ({share_pct:.0f}%)</span>'
            f'</div>'
            f'<div class="contrib-track"><div class="contrib-fill" style="width:{share_pct:.1f}%; background:{color};"></div></div>'
            f'</div>'
        )
    parts.append("</div>")
    return "".join(parts)


# --- TAB 1: SEARCH ---
with tab_search:
    with st.container(border=True):
        up_col1, up_col2 = st.columns([2.2, 1])
        with up_col1:
            uploaded_file = st.file_uploader(
                "📤 Tải lên ảnh bông hoa truy vấn (kích thước chuẩn 384×384 pixel, bông hoa nằm giữa khung hình)",
                type=["jpg", "jpeg", "png"],
            )
        with up_col2:
            st.markdown("**Quy chuẩn đầu vào**")
            st.caption(
                "• Kích thước đồng nhất: `384 × 384 px`\n"
                "• Góc chụp trực diện, 1 bông hoa ở tâm\n"
                "• Tách nền tự động bằng DeepLabV3+ v2"
            )

        query_source = uploaded_file.getvalue() if uploaded_file is not None else None
        query_filename = uploaded_file.name if uploaded_file is not None else None

        if query_source is not None:
            extraction_identity = (engine.version_id, hashlib.sha256(query_source).hexdigest())
            if st.session_state.get('extraction_identity') != extraction_identity:
                st.session_state.pop('last_query_result', None)
            elif 'last_query_result' in st.session_state:
                previous = st.session_state['last_query_result']
                if tuple(previous['weights']) != tuple(weights):
                    st.session_state['last_query_result'] = engine.rerank(previous, weights)

            btn_search = st.button("🚀 BẮT ĐẦU TRUY VẤN ẢNH TƯƠNG ĐỒNG", type="primary", width='stretch')
        else:
            btn_search = False
            st.info("👆 Hãy chọn một tệp ảnh bông hoa `384×384` từ máy tính để bắt đầu truy vấn.")

    if query_source is not None and (btn_search or "last_query_result" in st.session_state):
        if btn_search:
            with st.spinner("Đang phân đoạn vùng hoa DeepLabV3+, trích xuất vector 163 chiều và so khớp CSDL..."):
                search_res = safe_search(query_source, weights=weights)
                st.session_state["last_query_result"] = search_res
                st.session_state["query_filename"] = query_filename or "uploaded_image.png"
                st.session_state['extraction_identity'] = extraction_identity

                db.log_retrieval(
                    query_filename=st.session_state["query_filename"],
                    query_norm_vec=search_res["query_info"]["norm_vector"],
                    top5_results=search_res["results"],
                    weights=weights,
                    execution_ms=search_res["timings"]["total_backend_ms"],
                    version_id=engine.version_id,
                    query_context={
                        'preparation': search_res['query_info']['preparation'],
                        'segmentation': search_res['query_info']['segmentation'],
                    }
                )

        res_data = st.session_state["last_query_result"]
        q_info = res_data["query_info"]
        results = res_data["results"]
        timings = res_data["timings"]
        q_geom = q_info["explanation"]["geometry"]
        area_pct = float(q_geom["A"]) / (384.0 * 384.0) * 100.0

        st.markdown('<div class="section-title">📌 Tổng quan Ảnh Truy vấn & Hiệu năng Xử lý</div>', unsafe_allow_html=True)

        with st.container(border=True):
            col_q_orig, col_q_over, col_q_mask, col_summary = st.columns([1, 1, 1, 2.3])
            with col_q_orig:
                st.image(q_info["image_rgb"], caption="Ảnh gốc (384×384)", width='stretch')
            with col_q_over:
                st.image(q_info["overlay"], caption="Tách nền DeepLabV3+", width='stretch')
            with col_q_mask:
                st.image(q_info["mask_binary"], caption=f"Mặt nạ M ({area_pct:.1f}% khung)", width='stretch')

            with col_summary:
                st.markdown(f"**Tệp truy vấn:** `{st.session_state.get('query_filename', '')}`")
                t_col1, t_col2, t_col3, t_col4 = st.columns(4)
                t_col1.metric("Tiền xử lý", f"{timings['preprocess_ms']:.1f} ms")
                t_col2.metric("DeepLabV3+", f"{timings['segmentation_ms']:.1f} ms")
                t_col3.metric("Trích rút 163D", f"{timings['extraction_ms']:.1f} ms")
                t_col4.metric("So khớp 1.000 ảnh", f"{timings['search_ms']:.2f} ms")

                st.caption(
                    f"⏱️ Tổng thời gian Backend: **{timings['total_backend_ms']:.1f} ms**  ·  "
                    f"Diện tích hoa: **{int(q_geom['A']):,} px** ({area_pct:.1f}%)  ·  "
                    f"Bán kính tham chiếu r_max: **{q_geom['r_max']:.1f} px**"
                )
                if res_data.get('reused_query_features'):
                    st.success("⚡ Đã tái sử dụng vector đặc trưng 163D của ảnh truy vấn để xếp hạng lại tức thì theo bộ trọng số mới.")

        st.markdown(
            '<div class="section-title">🏆 Top 5 Ảnh Bông hoa Tương đồng Nhất</div>',
            unsafe_allow_html=True,
        )

        # Render Top 5 cards in a single 5-column row
        cols_top = st.columns(len(results)) if len(results) > 0 else []
        for col_idx, item in enumerate(results):
            with cols_top[col_idx]:
                with st.container(border=True):
                    badge_class = f"badge-rank-{item['rank']}" if item['rank'] <= 3 else "badge-rank-other"
                    safe_fname = html.escape(item["filename"])
                    st.markdown(
                        f'<div class="rank-header">'
                        f'<span class="metric-badge {badge_class}">RANK #{item["rank"]}</span>'
                        f'<span class="dist-caption">ID #{item["image_id"]}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    if os.path.exists(item["image_path"]):
                        st.image(item["image_path"], width='stretch')
                    else:
                        st.warning(f"File {item['filename']} không khả dụng")

                    sim_pct = item["similarity"] * 100.0
                    st.markdown(
                        f'<div class="file-label" title="{safe_fname}">{safe_fname}</div>'
                        f'<div class="sim-row">'
                        f'<span class="sim-score">{sim_pct:.1f}%</span>'
                        f'<span class="dist-caption">D = {item["distance"]:.4f}</span>'
                        f'</div>'
                        f'<div class="sim-track"><div class="sim-fill" style="width:{sim_pct:.1f}%;"></div></div>',
                        unsafe_allow_html=True,
                    )

                    with st.expander("🔎 Mặt nạ & Đóng góp wᵢ·Dᵢ"):
                        if os.path.exists(item["mask_path"]):
                            m_col1, m_col2 = st.columns(2)
                            m_col1.image(item["image_path"], caption="Ảnh kho", width='stretch')
                            m_col2.image(item["mask_path"], caption="Mask kho", width='stretch')
                        st.markdown(render_contribution_bars_html(item), unsafe_allow_html=True)
                        st.caption(
                            f"**Chi tiết F4:** Scalars `{item['f4_details']['scalars']:.3f}` · "
                            f"Fourier `{item['f4_details']['fourier']:.3f}` · "
                            f"IAP `{item['f4_details']['iap']:.3f}` · "
                            f"IEP `{item['f4_details']['iep']:.3f}`"
                        )

        st.info("💡 Chuyển sang tab **🔬 Giải thích & Đối chiếu Trung gian** để xem trực quan toàn bộ đặc trưng trung gian của ảnh truy vấn và đối chiếu trực tiếp với từng ảnh trong Top 5.")


# --- TAB 2: EXPLAINABILITY & INTERMEDIATE COMPARISON ---
with tab_explain:
    if "last_query_result" in st.session_state:
        res_data = st.session_state["last_query_result"]
        q_info = res_data["query_info"]
        results = res_data["results"]
        geom = q_info["explanation"]["geometry"]
        f1_exp = q_info["explanation"]["f1"]
        f2_exp = q_info["explanation"]["f2"]
        f3_exp = q_info["explanation"]["f3"]
        f4_exp = q_info["explanation"]["f4"]

        # =====================================================================
        # PART A: SIDE-BY-SIDE INTERMEDIATE COMPARISON (QUERY VS TOP 5 RESULT)
        # =====================================================================
        st.markdown(
            '<div class="section-title">⚖️ Đối chiếu Kết quả Trung gian: Ảnh Truy vấn vs Ảnh Kết quả Top 5</div>',
            unsafe_allow_html=True,
        )
        st.caption(
            "Minh họa trực tiếp quá trình tính toán độ tương đồng từ phân đoạn vùng hoa, chia vành đồng tâm, "
            "trích xuất đặc trưng F1–F4 đến tổng hợp khoảng cách có trọng số."
        )

        match_options = [
            f"Rank #{r['rank']} — {r['filename']} (Tương đồng {r['similarity']*100:.1f}%, D = {r['distance']:.4f})"
            for r in results
        ]
        selected_label = st.selectbox("Chọn ảnh trong Top 5 để đối chiếu chi tiết với ảnh truy vấn:", match_options, index=0)
        selected_rank_idx = match_options.index(selected_label)
        selected_item = results[selected_rank_idx]
        gallery_idx = filename_to_idx.get(selected_item["filename"])

        if gallery_idx is not None and os.path.exists(selected_item["image_path"]) and os.path.exists(selected_item["mask_path"]):
            target_vis = load_gallery_visual_explanation(selected_item["image_path"], selected_item["mask_path"])
            target_exp = target_vis["explanation"]
            target_geom = target_exp["geometry"]
            target_raw = engine.raw_matrix[gallery_idx]
            target_norm = engine.normalized_matrix[gallery_idx]

            _, _, pair_details = compute_overall_distance(
                q_info["norm_vector"], target_norm, weights=weights
            )

            # 1. Visual side-by-side masks & concentric rings
            with st.container(border=True):
                cmp_left, cmp_right = st.columns(2)
                q_rings_img = draw_concentric_rings(q_info["image_rgb"], geom)
                t_rings_img = draw_concentric_rings(target_vis["image_rgb"], target_geom)

                with cmp_left:
                    st.markdown(f"**📤 Ảnh Truy vấn:** `{st.session_state.get('query_filename', 'query.png')}`")
                    qc1, qc2, qc3 = st.columns(3)
                    qc1.image(q_info["overlay"], caption="Viền tách nền", width='stretch')
                    qc2.image(q_info["mask_binary"], caption="Mặt nạ M", width='stretch')
                    qc3.image(q_rings_img, caption="Tâm & 3 Vành Lab", width='stretch')
                    st.caption(
                        f"Diện tích A: **{int(geom['A']):,} px** · M_int: **{int(geom['A_int']):,} px** · "
                        f"Chu vi P: **{geom['perimeter']:.1f} px** · r_max: **{geom['r_max']:.1f} px**"
                    )

                with cmp_right:
                    st.markdown(
                        f"**🏆 Kết quả Rank #{selected_item['rank']}:** `{selected_item['filename']}` "
                        f"— Tương đồng **{selected_item['similarity']*100:.1f}%**"
                    )
                    tc1, tc2, tc3 = st.columns(3)
                    tc1.image(target_vis["overlay"], caption="Viền tách nền", width='stretch')
                    tc2.image(target_vis["mask_binary"], caption="Mặt nạ M", width='stretch')
                    tc3.image(t_rings_img, caption="Tâm & 3 Vành Lab", width='stretch')
                    st.caption(
                        f"Diện tích A: **{int(target_geom['A']):,} px** · M_int: **{int(target_geom['A_int']):,} px** · "
                        f"Chu vi P: **{target_geom['perimeter']:.1f} px** · r_max: **{target_geom['r_max']:.1f} px**"
                    )

            # 2. Formula & Distance Breakdown Chart
            with st.container(border=True):
                w1_v, w2_v, w3_v, w4_v = weights
                c_vals = selected_item["contributions"]
                st.markdown("**📐 Minh họa Công thức Tính Khoảng cách Tổ hợp & Độ tương đồng:**")
                st.latex(
                    rf"D(q, t) = {w1_v:.2f}\cdot\underbrace{{{selected_item['D1']:.4f}}}_{{D_1}} + "
                    rf"{w2_v:.2f}\cdot\underbrace{{{selected_item['D2']:.4f}}}_{{D_2}} + "
                    rf"{w3_v:.2f}\cdot\underbrace{{{selected_item['D3']:.4f}}}_{{D_3}} + "
                    rf"{w4_v:.2f}\cdot\underbrace{{{selected_item['D4']:.4f}}}_{{D_4}} = "
                    rf"{c_vals['w1_D1']:.4f} + {c_vals['w2_D2']:.4f} + {c_vals['w3_D3']:.4f} + {c_vals['w4_D4']:.4f} = "
                    rf"\mathbf{{{selected_item['distance']:.4f}}} \implies S = 1 - D = \mathbf{{{selected_item['similarity']*100:.2f}\%}}"
                )
                fig_dist = plot_pair_distance_breakdown(
                    pair_details, weights, selected_item["distance"], selected_item["similarity"]
                )
                st.pyplot(fig_dist)
                plt.close(fig_dist)

            # 3. Side-by-side Feature Signal Charts + Shape Scalars Table
            with st.container(border=True):
                st.markdown("**📊 So sánh Trực quan Phân bố Đặc trưng Trung gian (F1 · F2 · F3 · F4):**")
                fig_sig = plot_pair_signal_comparison(
                    q_info["raw_vector"], target_raw, q_info["explanation"], target_exp, selected_item["filename"]
                )
                st.pyplot(fig_sig)
                plt.close(fig_sig)

                q_sc = f4_exp["scalars"]
                t_sc = target_exp["f4"]["scalars"]
                scalar_cmp_rows = [
                    {
                        "Chỉ số trung gian": "F4.1 · Độ tròn (Circularity)",
                        "Ảnh Truy vấn": f"{q_sc['circularity']:.4f}",
                        f"Rank #{selected_item['rank']} ({selected_item['filename']})": f"{t_sc['circularity']:.4f}",
                        "Độ lệch tuyệt đối |Δ|": f"{abs(q_sc['circularity'] - t_sc['circularity']):.4f}",
                    },
                    {
                        "Chỉ số trung gian": "F4.2 · Độ đặc bao lồi (Solidity)",
                        "Ảnh Truy vấn": f"{q_sc['solidity']:.4f}",
                        f"Rank #{selected_item['rank']} ({selected_item['filename']})": f"{t_sc['solidity']:.4f}",
                        "Độ lệch tuyệt đối |Δ|": f"{abs(q_sc['solidity'] - t_sc['solidity']):.4f}",
                    },
                    {
                        "Chỉ số trung gian": "F4.3 · Tỷ số khung hình chính (Aspect Ratio)",
                        "Ảnh Truy vấn": f"{q_sc['aspect_ratio']:.4f}",
                        f"Rank #{selected_item['rank']} ({selected_item['filename']})": f"{t_sc['aspect_ratio']:.4f}",
                        "Độ lệch tuyệt đối |Δ|": f"{abs(q_sc['aspect_ratio'] - t_sc['aspect_ratio']):.4f}",
                    },
                    {
                        "Chỉ số trung gian": "F4.4 · Độ lấp đầy hộp xoay (Rotated Extent)",
                        "Ảnh Truy vấn": f"{q_sc['rotated_extent']:.4f}",
                        f"Rank #{selected_item['rank']} ({selected_item['filename']})": f"{t_sc['rotated_extent']:.4f}",
                        "Độ lệch tuyệt đối |Δ|": f"{abs(q_sc['rotated_extent'] - t_sc['rotated_extent']):.4f}",
                    },
                    {
                        "Chỉ số trung gian": "F4.7 · Độ dốc mật độ cạnh nội bộ (S_edge)",
                        "Ảnh Truy vấn": f"{f4_exp['s_edge']:.4f}",
                        f"Rank #{selected_item['rank']} ({selected_item['filename']})": f"{target_exp['f4']['s_edge']:.4f}",
                        "Độ lệch tuyệt đối |Δ|": f"{abs(f4_exp['s_edge'] - target_exp['f4']['s_edge']):.4f}",
                    },
                    {
                        "Chỉ số trung gian": "F4.7 · Hệ số biến thiên cạnh nội bộ (CV_edge)",
                        "Ảnh Truy vấn": f"{f4_exp['cv_edge']:.4f}",
                        f"Rank #{selected_item['rank']} ({selected_item['filename']})": f"{target_exp['f4']['cv_edge']:.4f}",
                        "Độ lệch tuyệt đối |Δ|": f"{abs(f4_exp['cv_edge'] - target_exp['f4']['cv_edge']):.4f}",
                    },
                ]
                st.dataframe(scalar_cmp_rows, width='stretch', hide_index=True)

        st.divider()

        # =====================================================================
        # PART B: DETAILED SINGLE-IMAGE BREAKDOWN OF QUERY IMAGE
        # =====================================================================
        st.markdown(
            '<div class="section-title">🔬 Hồ sơ Trích xuất Chi tiết của Ảnh Truy vấn (163 Chiều)</div>',
            unsafe_allow_html=True,
        )

        with st.container(border=True):
            st.markdown("##### 1. Hình học và Mặt nạ Phân đoạn (M & M_int)")
            g_col1, g_col2, g_col3, g_col4 = st.columns(4)
            g_col1.image(q_info["image_rgb"], caption="Ảnh RGB 384×384", width='stretch')
            g_col2.image(q_info["mask_binary"], caption="Mặt nạ M (Liên thông 8 lớn nhất)", width='stretch')
            g_col3.image(geom["m_int_binary"], caption="Mặt nạ nội bộ M_int (M ⊖ B₅)", width='stretch')
            ring_vis = draw_concentric_rings(q_info["image_rgb"], geom)
            xc, yc = int(round(geom["centroid"][0])), int(round(geom["centroid"][1]))
            g_col4.image(ring_vis, caption=f"Trọng tâm ({xc},{yc}) & 3 Vành Lab", width='stretch')

        with st.container(border=True):
            st.markdown("##### 2. Đặc trưng Màu sắc Tổng thể & Theo Vị trí (F1: 78D · F2: 18D)")
            col_f1_plot, col_f2_table = st.columns([1.45, 1])
            with col_f1_plot:
                fig_f1 = plot_f1_visuals(f1_exp)
                st.pyplot(fig_f1)
                plt.close(fig_f1)
            with col_f2_table:
                st.markdown("**F2 · Thống kê CIELAB trên 3 vành bán kính:**")
                ring_rows = [
                    {
                        "Vành": f"Vành {r['ring']+1}",
                        "Số pixel": f"{r['count']:,}",
                        "μ_L* / σ_L*": f"{r['mu_L']:.1f} / {r['std_L']:.1f}",
                        "μ_a* / σ_a*": f"{r['mu_a']:.1f} / {r['std_a']:.1f}",
                        "μ_b* / σ_b*": f"{r['mu_b']:.1f} / {r['std_b']:.1f}",
                    }
                    for r in f2_exp["ring_stats"]
                ]
                st.dataframe(ring_rows, width='stretch', hide_index=True)

        with st.container(border=True):
            st.markdown("##### 3. Đặc trưng Kết cấu Bề mặt Multi-Radius LBP (F3: 28D)")
            fig_f3 = plot_f3_visuals(f3_exp)
            st.pyplot(fig_f3)
            plt.close(fig_f3)

        with st.container(border=True):
            st.markdown("##### 4. Đặc trưng Hình dạng & Cấu trúc Nội bộ (F4: 39D)")
            sc = f4_exp["scalars"]
            s_col1, s_col2, s_col3, s_col4 = st.columns(4)
            s_col1.metric("F4.1 Độ tròn (Circularity)", f"{sc['circularity']:.4f}")
            s_col2.metric("F4.2 Độ đặc (Solidity)", f"{sc['solidity']:.4f}")
            s_col3.metric("F4.3 Tỷ số trục (Aspect Ratio)", f"{sc['aspect_ratio']:.4f}")
            s_col4.metric("F4.4 Lấp đầy hộp (Rotated Extent)", f"{sc['rotated_extent']:.4f}")

            fig_f4 = plot_f4_visuals(f4_exp)
            st.pyplot(fig_f4)
            plt.close(fig_f4)

        with st.expander("🧬 Xem toàn bộ giá trị số Vector 163 Chiều (Thô & Chuẩn hóa)"):
            vec_col1, vec_col2 = st.columns(2)
            with vec_col1:
                st.markdown("**Vector Thô (Raw Vector 163D - float32):**")
                st.text_area("Raw 163D", value=str(q_info["raw_vector"].tolist()), height=150, label_visibility="collapsed")
            with vec_col2:
                st.markdown("**Vector Chuẩn hóa Chọn lọc (Z-score F2 & F4, giữ nguyên F1 & F3):**")
                st.text_area("Normalized 163D", value=str(q_info["norm_vector"].tolist()), height=150, label_visibility="collapsed")

    else:
        st.info("💡 Vui lòng tải ảnh và thực hiện một lượt truy vấn ở tab **🔍 Tìm kiếm Tương đồng** để mở khóa chế độ đối chiếu kết quả trung gian.")


# --- TAB 3: EVALUATION & ABLATION STUDY ---
with tab_eval:
    st.markdown('<div class="section-title">🧪 Kiểm chứng Bất biến Toán học & Nghiên cứu Loại bỏ (Ablation Study)</div>', unsafe_allow_html=True)

    col_chk, col_abl = st.columns([1, 1.25])

    with col_chk:
        with st.container(border=True):
            st.markdown("##### 1. Kiểm tra Toàn vẹn Chỉ mục & Bất biến Toán học")
            st.caption("Xác minh kích thước 1000×163, tổng xác suất histogram F1/F3 = 1, tính bất biến chuẩn hóa chọn lọc và khoảng cách tự thân D(q, q) = 0.")
            if st.button("▶️ Chạy Kiểm tra Toàn diện", key="btn_run_tests", width='stretch'):
                with st.spinner("Đang kiểm tra ma trận 1.000 vector..."):
                    report = run_correctness_checks(engine)
                    for t in report["tests"]:
                        if t["passed"]:
                            st.success(f"✅ **{t['name']}** — {t['detail']}")
                        else:
                            st.error(f"❌ **{t['name']}** — {t['detail']}")

    with col_abl:
        with st.container(border=True):
            st.markdown("##### 2. Thử nghiệm Đóng góp Nhóm Đặc trưng (Ablation Study)")
            ablate_idx = st.number_input("Chọn chỉ số ảnh mẫu trong kho (0..999):", 0, engine.num_images - 1, 0)
            sample_meta = engine.metadata[ablate_idx]
            if os.path.exists(sample_meta["image_path"]):
                ab_c1, ab_c2 = st.columns([1, 3])
                ab_c1.image(sample_meta["image_path"], caption=sample_meta["filename"], width='stretch')
                ab_c2.markdown(
                    f"**Ảnh mẫu đang chọn:** `{sample_meta['filename']}` (ID #{sample_meta['image_id']})\n\n"
                    "So sánh sự thay đổi của kết quả Top-1 và điểm tương đồng khi chỉ bật từng nhóm đặc trưng hoặc loại bỏ lần lượt F1, F2, F3, F4."
                )
            if st.button("🔬 Chạy Ablation Study", key="btn_run_ablation", type="primary", width='stretch'):
                with st.spinner("Đang tính toán 11 cấu hình trọng số..."):
                    abl_res = run_ablation_study(engine, query_idx=ablate_idx)
                    summary_table = []
                    for name, data in abl_res.items():
                        top1 = data["top5"][0]["filename"]
                        sim1 = data["top5"][0]["similarity"]
                        summary_table.append({
                            "Cấu hình Thử nghiệm": name,
                            "Trọng số (w₁..w₄)": ", ".join(f"{w:.2f}" for w in data["weights"]),
                            "Top-1 Kết quả": top1,
                            "Tương đồng S(Top-1)": f"{sim1*100:.1f}%",
                            "Thời gian": f"{data['search_ms']:.2f} ms",
                        })
                    st.dataframe(summary_table, width='stretch', hide_index=True)


# --- TAB 4: RETRIEVAL HISTORY ---
with tab_history:
    st.markdown('<div class="section-title">📜 Nhật ký Truy vấn trong PostgreSQL</div>', unsafe_allow_html=True)
    history_records = db.get_history(limit=20)

    if len(history_records) > 0:
        avg_ms = float(np.mean([r["execution_ms"] for r in history_records]))
        h_m1, h_m2, h_m3 = st.columns(3)
        h_m1.metric("Số lượt hiển thị gần nhất", f"{len(history_records)} lượt")
        h_m2.metric("Thời gian xử lý trung bình", f"{avg_ms:.1f} ms")
        h_m3.metric("Phiên bản CSDL hiện hành", engine.version_id[:24] + "...")

        with st.container(border=True):
            h_table = []
            for r in history_records:
                weights_str = (
                    "(" + ", ".join(f"{float(w):.2f}" for w in r["weights"]) + ")"
                    if r["weights"] is not None else "Chưa ghi"
                )
                h_table.append({
                    "Mã truy vấn": f"#{r['query_id']}",
                    "Ảnh truy vấn": r["query_filename"],
                    "Top-1 Kết quả": r["top5"][0]["filename"] if len(r["top5"]) > 0 else "N/A",
                    "Độ tương đồng": f"{r['top5'][0]['similarity']*100:.1f}%" if len(r["top5"]) > 0 else "N/A",
                    "Trọng số (w₁..w₄)": weights_str,
                    "Thời gian": f"{r['execution_ms']:.1f} ms",
                    "Thời điểm": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["timestamp"])),
                })
            st.dataframe(h_table, width='stretch', hide_index=True)
    else:
        st.info("Chưa có lịch sử truy vấn nào được ghi nhận trong PostgreSQL.")
