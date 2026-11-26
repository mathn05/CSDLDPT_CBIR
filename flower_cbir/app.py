"""Interactive flower retrieval and inspection of the actual query features."""
import hashlib
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flower_cbir.core.schema import FEATURE_NAMES, GROUP_SLICES
from flower_cbir.services.database import FlowerDatabase
from flower_cbir.services.search import FlowerSearchEngine
from flower_cbir.settings import default_weights, validate_weights


GROUPS = ("F1 · Màu HSV", "F2 · Màu Lab theo vành", "F3 · Kết cấu LBP", "F4 · Hình dạng và cấu trúc")


@st.cache_resource
def database():
    return FlowerDatabase()


@st.cache_resource(max_entries=1)
def load_engine(version_id):
    return FlowerSearchEngine(database().load_index())


def show_segmentation(query):
    st.header("1. Phân đoạn hoa")
    mask = query["mask_binary"]
    seg = query["segmentation"]
    source = "final_segmentation_v2" if seg["method"] == "final_segmentation_v2" else "DeepLabV3+"
    match = f" · Khớp ảnh trong kho {seg['filename']}" if "filename" in seg else " · Dự đoán trên ảnh truy vấn"
    st.write(f"Nguồn mask **{source}**{match}")
    images = (query["image_rgb"], mask, query["overlay"])
    labels = ("Ảnh đầu vào", f"Mask dùng trích đặc trưng · {np.mean(mask > 0):.1%} diện tích", "Biên mask trên ảnh")
    for col, image, label in zip(st.columns(3), images, labels):
        col.image(image, caption=label)


def show_features(query, normalizer):
    st.header("2. Trích 163 đặc trưng")
    geom = query["explanation"]["geometry"]
    tabs = st.tabs(GROUPS)
    raw = query["raw_vector"]
    with tabs[0]:
        st.caption("Phân bố sắc độ và độ bão hòa trên vùng hoa (72 ô), cùng phân bố độ sáng (6 ô).")
        st.bar_chart(pd.Series(raw[:72], index=FEATURE_NAMES[:72], name="Tần suất"))
        st.bar_chart(pd.Series(raw[72:78], index=FEATURE_NAMES[72:78], name="Tần suất"))
    with tabs[1]:
        left, right = st.columns([1, 2])
        palette = np.array([[250, 209, 99], [71, 175, 155], [99, 118, 213]], dtype=np.uint8)
        rings = np.where(geom["m_bool"][:, :, None], palette[geom["f2_ring_map"]], 0)
        left.image(rings, caption="Vành trong, giữa và ngoài")
        right.dataframe(pd.DataFrame(query["explanation"]["f2"]["ring_stats"]).rename(
            columns={"ring": "Vành (0–2)", "count": "Số pixel"}), hide_index=True)
    with tabs[2]:
        st.caption("Hai histogram mẫu nhị phân cục bộ, tính trên pixel thuộc mask hoa.")
        for col, part, label in zip(st.columns(2), (slice(96, 106), slice(106, 124)), ("8 điểm · bán kính 1", "16 điểm · bán kính 2")):
            col.bar_chart(pd.Series(raw[part], name=label))
    with tabs[3]:
        left, right = st.columns([1, 2])
        left.image(geom["m_int_binary"], caption="Mask bên trong sau co biên, dùng đo cấu trúc")
        right.dataframe(pd.Series(query["explanation"]["f4"]["scalars"], name="Giá trị"))
        right.bar_chart(pd.Series(query["explanation"]["f4"]["radial_spectrum"], name="Phổ bán kính"))

        angles, edges = st.columns(2)
        angles.caption("Phân bố hướng gradient theo 4 vành (IAP)")
        angles.dataframe(pd.DataFrame(query["explanation"]["f4"]["iap_matrix"],
                                      index=["Vành 1", "Vành 2", "Vành 3", "Vành 4"],
                                      columns=["Hướng 1", "Hướng 2", "Hướng 3", "Hướng 4"]))
        edges.caption("Mật độ cạnh theo 5 vành (IEP)")
        edges.bar_chart(pd.Series(query["explanation"]["f4"]["d_vals"],
                                  index=range(1, 6), name="Mật độ cạnh"))

    st.header("3. Chuẩn hóa vector truy vấn")
    st.caption("F1 và F3 giữ tần suất histogram. F2 và F4 dùng (giá trị thô − μ) / σ theo từng chiều của kho ảnh. Chiều có độ lệch chuẩn bằng hoặc gần 0 dùng hệ số chia 1.")
    table = pd.DataFrame({
        "Nhóm": [name.split(" · ")[0] for name, part in zip(GROUPS, GROUP_SLICES) for _ in FEATURE_NAMES[part]],
        "Đặc trưng": FEATURE_NAMES, "Giá trị thô": raw,
        "μ áp dụng": normalizer.mean, "σ áp dụng": normalizer.std,
        "Sau chuẩn hóa": query["norm_vector"],
    })
    group = st.selectbox("Xem các chiều đặc trưng", ["Tất cả", "F1", "F2", "F3", "F4"])
    st.dataframe(table if group == "Tất cả" else table[table["Nhóm"] == group], hide_index=True)


def show_results(result):
    st.header("4. Năm ảnh gần nhất")
    weights = result["weights"]
    formula = " + ".join(f"{w:.3f} × D{i}" for i, w in enumerate(weights, 1))
    st.caption(f"D = {formula}. Xếp theo D tăng dần. Độ tương đồng S = 1 − D.")
    rows = result["results"]
    for col, row in zip(st.columns(len(rows)), rows):
        col.image(row["image_path"], caption=f"{row['rank']}. {row['filename']} · {row['similarity']:.1%}")
    st.dataframe([{"Hạng": r["rank"], "Ảnh": r["filename"],
                   **{k: r[k] for k in ("D1", "D2", "D3", "D4")}, "D tổng": r["distance"]}
                  for r in rows], hide_index=True)


def main():
    st.set_page_config(page_title="Truy vấn ảnh hoa", layout="wide")
    st.title("Truy vấn ảnh hoa theo nội dung")
    try:
        db = database()
        engine = load_engine(db.active_version())
    except Exception as exc:
        st.error(str(exc))
        return
    with st.sidebar:
        st.header("Trọng số khoảng cách")
        values = [st.slider(name, 0.0, 1.0, value=w, step=0.05)
                  for name, w in zip(GROUPS, default_weights())]
        if not any(values):
            st.warning("Chọn ít nhất một trọng số lớn hơn 0.")
            return
        weights = validate_weights(values)

    upload = st.file_uploader("Tải ảnh truy vấn lên", type=["png", "jpg", "jpeg", "webp"])
    if upload is None:
        return
    source, filename = upload.getvalue(), upload.name
    identity = (engine.version_id, hashlib.sha256(source).hexdigest())
    if st.session_state.get("identity") != identity:
        st.session_state.pop("result", None)
    try:
        if st.button("Truy vấn", type="primary"):
            with st.spinner("Đang trích đặc trưng và xếp hạng…"):
                result = engine.search(source, weights=weights)
                st.session_state.update(identity=identity, result=result)
                info = result["query_info"]
                db.log_retrieval(filename, info["norm_vector"], result["results"], result["weights"],
                                 result["timings"]["total_backend_ms"], engine.version_id,
                                 {"segmentation": info["segmentation"]}, source)
        result = st.session_state.get("result")
        if result is None:
            return
        if not np.allclose(result["weights"], weights):
            result = engine.rerank(result, weights)
            st.session_state.result = result
        show_segmentation(result["query_info"])
        show_features(result["query_info"], engine.normalizer)
        show_results(result)
    except (ValueError, OSError, RuntimeError) as exc:
        st.error(str(exc))


if __name__ == "__main__":
    main()
