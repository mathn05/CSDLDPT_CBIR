# Truy vấn ảnh hoa theo nội dung

Ứng dụng Streamlit nhận ảnh hoa, tách vùng hoa, trích vector 163 chiều và trả về năm ảnh gần nhất trong kho PostgreSQL. Tải ảnh riêng hoặc ảnh mẫu từ thư mục `examples/` lên giao diện để truy vấn.

## Chọn nguồn mask

Mỗi ảnh trong `dataset/images/` có một mask cùng tên trong `dataset/masks/`. Trong 1.000 mask đang dùng, 983 mask là bản v2 đã duyệt và 17 mask do DeepLabV3+ bổ sung. Khi lập chỉ mục cho ảnh thiếu mask, hệ thống lưu dự đoán vào cùng thư mục này. Trường `mask_source` trong PNG dự đoán lưu nguồn DeepLab để lần lập chỉ mục sau vẫn nhận biết được. Nhãn dùng khi lập chỉ mục nằm ở `dataset/imagelabels.mat`. Đường dẫn ảnh và mask của phiên bản hiện hành được lưu tương đối từ thư mục chứa `main.py`.

Trên 983 ảnh có mask đã duyệt, so sánh dự đoán DeepLab với v2 cho IoU trung bình **0,9842** và Dice trung bình **0,9920**. Có 35 ảnh có IoU dưới 0,95. Vì v2 đã được dùng để tinh chỉnh mô hình, các số này đo mức đồng thuận trên kho hiện tại.

Ảnh truy vấn có dữ liệu RGB trùng khớp với ảnh trong kho dùng lại mask của bản ghi đó, kể cả khi đổi tên tệp. Ảnh mới dùng DeepLabV3+. Hệ thống dùng trực tiếp ảnh 384 × 384 và báo lỗi nếu kích thước khác yêu cầu. Trước khi tính đặc trưng, hệ thống giữ thành phần liên thông lớn nhất của mask.

## Chạy trên Windows

Trên máy mới, lấy đủ các file PNG qua Git LFS nếu dùng Git, hoặc giải nén bộ dự án có sẵn `dataset/`. Tạo hai môi trường từ thư mục dự án. Ứng dụng dùng Python 3.12 và worker DeepLab dùng Python 3.14. Worker chạy trên CPU với cùng bản PyTorch đã dùng để tạo chỉ mục. Checkpoint và mã hậu xử lý nằm trong `flower_cbir/model/`.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
py -3.14 -m venv .venv-deeplab
.venv-deeplab\Scripts\python.exe -m pip install -r requirements-deeplab.txt
```

Cấu hình PostgreSQL trong `database/connection.local.json` theo `database/connection.example.json`. Với cơ sở dữ liệu trống, tạo các bảng từ `database/schema.sql` trước khi lập chỉ mục. Chạy các lệnh sau từ thư mục dự án.

```powershell
.venv\Scripts\python.exe main.py --index
.venv\Scripts\python.exe main.py --app
```

Lệnh `--index` tính lại chỉ mục khi ảnh, mask hoặc cấu hình thay đổi, khớp tham số chuẩn hóa trên toàn kho rồi đồng bộ PostgreSQL. Khi đã có chỉ mục hiện hành, chỉ cần chạy `--app`. Có thể truy vấn từ dòng lệnh bằng `main.py --search "đường-dẫn-ảnh"`.

## Đọc kết quả truy vấn

1. Xem ảnh RGB dùng để trích đặc trưng, mask nhị phân, diện tích hoa và đường biên phủ trên ảnh. Nguồn mask được ghi ngay cạnh kết quả.
2. Xem từng nhóm đặc trưng gồm histogram HSV (78 chiều), thống kê Lab trên ba vành (18), histogram LBP (28), hình dạng và cấu trúc (39).
3. Đối chiếu từng giá trị thô, hệ số chuẩn hóa và giá trị đưa vào phép so khớp. F1/F3 giữ histogram. F2/F4 dùng Z-score theo từng chiều.
4. Đọc khoảng cách D1–D4 và điểm tổng hợp của năm ảnh gần nhất. Đổi trọng số ở thanh bên để xếp hạng lại bằng vector vừa trích. Ảnh trong kho có thể khớp chính nó với khoảng cách bằng 0.
