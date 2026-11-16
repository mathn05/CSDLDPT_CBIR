# Dữ liệu hoạt động và hiện vật cũ

Ứng dụng truy vấn đọc phiên bản `active` trong PostgreSQL qua `FlowerDatabase.load_index()`. CSV tương ứng nằm ở `trich_rut_minh/features_163.csv` và manifest đi kèm.

Các artifact NumPy có kiểm tra hash nằm trong `versions/<version_id>/`. `active_index.json` trỏ tới batch trên đĩa; PostgreSQL có con trỏ hoạt động riêng được chuyển cùng transaction ghi dữ liệu. Lệnh `main.py --index` đồng bộ hai phía và xuất CSV.

Các tệp NumPy/JSON ở ngay thư mục này và `flower_cbir.db` được giữ lại từ phiên bản trước để tham khảo. Chúng **không phải nguồn mà chương trình hiện tại sử dụng**; không ghép chúng với normalizer hay CSV của phiên bản mới. Muốn lấy dữ liệu mới, dùng API đọc PostgreSQL hoặc batch có version/manifest.

`deeplab_masks/` chứa mask model tạo, phân theo nhận diện model/runtime và hash pixel ảnh. `training_reference/` là bản làm sạch của mask tham chiếu dùng cho lần tinh chỉnh, không phải mask truy vấn. Không sửa các mask của batch đã công bố; thay nguồn hoặc model thì tạo batch mới.
