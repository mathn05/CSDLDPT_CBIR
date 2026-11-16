# CBIR ảnh hoa theo Báo cáo lần 1

Chương trình truy vấn và công cụ `trich_rut_minh` dùng **cùng mã nguồn** cho tiền xử lý, DeepLabv3, làm sạch mask, hình học, F1–F4 và chuẩn hóa. Không sao chép lại các công thức vào script export/import.

## Chạy trên máy hiện tại

Môi trường `.venv` chạy ứng dụng, OpenCV, NumPy và PostgreSQL. DeepLabv3 chạy bằng môi trường PyTorch/GPU đã có tại `../deeplabv3/.venv`; đường dẫn được cấu hình trong `flower_cbir/config/pipeline.json`. Worker giữ model trong bộ nhớ và dùng float32, chế độ eval, các phép tính xác định. Cả hai công cụ gọi cùng worker và cùng checkpoint.

```powershell
# Xây dựng mask DeepLabv3, đặc trưng, CSV và đồng bộ cả PostgreSQL
.venv/Scripts/python.exe main.py --index

# Mở giao diện
.venv/Scripts/python.exe main.py --app

# Ảnh mới: tự động phân đoạn và truy vấn
.venv/Scripts/python.exe main.py --search path/to/flower.jpg

# Nếu cần chỉ rõ bounding box bông hoa trong hệ tọa độ ảnh gốc
.venv/Scripts/python.exe main.py --search path/to/flower.jpg --flower-box 30 20 400 350

# Kiểm tra thực tế toàn bộ kho bằng cách chạy lại DeepLabv3 và hai API trích rút
.venv/Scripts/python.exe main.py --verify-sync

# Kiểm tra tính toàn vẹn của index đang phục vụ
.venv/Scripts/python.exe main.py --test

# Chọn trọng số trên validation và đánh giá test; không đổi công thức đặc trưng
.venv/Scripts/python.exe main.py --evaluate
```

`--index` bây giờ đồng bộ cả đặc trưng thô, tham số chuẩn hóa, vector chuẩn hóa và phiên bản đang phục vụ trong PostgreSQL. Nó cũng xuất CSV cùng phiên bản. Không chỉ tạo một index NumPy riêng như trước. Chạy lại khi dữ liệu không đổi sẽ dùng mask/vector đã xác minh; không tạo normalization mới vô ích.

Repository giữ mã chương trình, công cụ CSV/PostgreSQL, script huấn luyện và hướng dẫn cần thiết. Bộ test local, báo cáo sinh ra, tài liệu nháp, backup, cache, dữ liệu ảnh và checkpoint được bỏ qua bằng `.gitignore`. Các test local đang có trên máy vẫn chạy được bằng `python -m unittest discover -s tests -v`.

## Quy tắc ảnh và mask

Kho dữ liệu đầu vào là các ảnh **RGB PNG 384×384 đã chuẩn bị theo báo cáo**. Ảnh này được giữ nguyên để không crop hai lần. Nếu đưa một ảnh 384×384 chưa có bố cục chuẩn, hãy chỉ bounding box khi cần.

Với ảnh mới có kích thước khác, hệ thống dùng DeepLabv3 để ước lượng bounding box ban đầu, cắt vuông quanh tâm box với cạnh `1.10 × max(width,height)`, đệm xám `(128,128,128)` khi vượt biên, resize 384×384 rồi dự đoán mask cuối. Có thể chỉ box thủ công. Bước định vị tự động là phần triển khai bổ sung; quy tắc crop trong báo cáo được giữ nguyên.

Mask kho được tạo lại bằng cùng checkpoint và cùng pipeline dùng cho query; lưu riêng trong `flower_cbir/data/deeplab_masks`. Không sửa ảnh hay mask nguồn trong `../postgres_data`. Mask 0/1 và 0/255 được hiểu nhất quán; giữ thành phần liên thông lớn nhất theo lân cận 8. Không tự chuyển sang GrabCut khi model lỗi hoặc trả mask rỗng.

Các quy tắc F1–F4 đã nêu trong báo cáo được giữ nguyên: F2 chia đều bán kính; LBP tính trên toàn ảnh xám rồi chọn tâm trong M; mask nội bộ dùng elip 5×5, ngoài biên 0; Solidity dùng diện tích đa giác bao lồi; Fourier 128 tia, bước 0,25; IEP dùng Gaussian 5×5 sigma=1 và Canny 50/150 L2. Chi tiết nằm trong `flower_cbir/config/report_contract.json`.

## CSV và nhập PostgreSQL riêng

Xem `trich_rut_minh/README.md`. Các lệnh vẫn tách biệt `extract`, `check`, `load`, `all`; script chỉ là lớp công cụ quanh cùng bộ xử lý của ứng dụng. CSV dùng 17 chữ số có nghĩa cho giá trị float32 để vòng export/import giữ nguyên bit.

PostgreSQL lấy cấu hình từ `database.local.json` hoặc `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`. Không ghi thông tin đăng nhập vào CSV, manifest hoặc mã nguồn.

## Phiên bản và kiểm tra đồng bộ

Mỗi batch ghi hash mã nguồn lõi, cấu hình cố định, checkpoint, thư viện xử lý và thông tin runtime PyTorch/CUDA. Đổi một phần ảnh hưởng biểu diễn phải tạo index mới. Loader từ chối vector cũ không có contract hoặc không khớp; cùng 163 chiều chưa đủ để coi là tương thích.

`cbir_index_versions` quản lý phiên bản đang hoạt động; `cbir_index_entries` giữ snapshot từng vector và metadata. Các bảng hiện có `flower_images`, `flower_features`, `feature_normalization`, `normalized_features` được cập nhật trong **cùng một transaction** với việc chuyển phiên bản. Dữ liệu thô được đọc từ các cột thật, không khôi phục ngược từ vector Z-score. Lịch sử lưu phiên bản, trọng số và ngữ cảnh crop/phân đoạn.

Trước lần ghi, hệ thống lưu snapshot PostgreSQL trong `output/backups/*-postgres.json.gz`. Lỗi trong transaction giữ nguyên phiên bản cũ. Nguồn ảnh/mask được kiểm tra SHA256 trước import và khi load. `output/verification/synchronization.json` ghi số ảnh khớp từng tiêu chí mask, vector thô, vector chuẩn hóa, CSV và API của Minh; khoảng cách với chính ảnh phải bằng 0.

## DeepLabv3 và việc tinh chỉnh

Model là DeepLabV3 ResNet50 hai lớp nền/hoa. Checkpoint được triển khai là bản inference-only trong `flower_cbir/models`, không chứa optimizer. Chuẩn hóa đầu vào ImageNet giống mã huấn luyện. Chỉ model phân đoạn được tinh chỉnh; các descriptor thủ công không đổi.

`tools/finetune_deeplab.py` dùng 573 ảnh gallery để tinh chỉnh các head, chọn checkpoint theo Dice trên 200 ảnh validation và đánh giá 227 ảnh test sau khi chọn. Split và hash nguồn nằm trong `flower_cbir/models/segmentation_training/split.json`, kết quả trong `metrics.json`. Backbone được đóng băng. Nguồn pretrained trước đó và nhóm ảnh gần trùng chưa được xác minh hoàn toàn; không coi các kết quả này là chứng nhận trên mọi ảnh thu thập mới.

```powershell
# Nếu cần chuẩn bị lại split/mask tham chiếu trên máy khác hoặc cho một run mới
.venv/Scripts/python.exe tools/prepare_segmentation_data.py --output output/new_segmentation_run/split.json

# Chạy lại thí nghiệm phân đoạn với môi trường PyTorch có sẵn.
# Dùng thư mục output mới, rồi chỉ triển khai checkpoint sau khi đánh giá.
../deeplabv3/.venv/Scripts/python.exe tools/finetune_deeplab.py `
  --manifest output/new_segmentation_run/split.json `
  --source flower_cbir/models/deeplabv3_flower.pt `
  --output output/new_segmentation_run --epochs 10 --patience 3
```

Khi chuyển máy, đặt `segmentation.python_executable` tới Python có PyTorch/torchvision phù hợp và `device` là `cuda` hoặc `cpu`, giữ checkpoint hoặc huấn luyện lại. Sau thay đổi runtime/model, chạy `--index` và `--verify-sync` trên máy đó. Khớp bit được kiểm tra trong cùng runtime đã ghi nhận; không hứa hẹn mọi GPU/thư viện khác nhau đều sinh đúng từng bit giống nhau.

## Chất lượng và phạm vi của “khớp 100%”

“Khớp 100%” ở đây là cùng thuật toán, cùng quy ước, và kiểm tra bằng `np.array_equal` trên dữ liệu export/query/DB trong môi trường ghi nhận. Điều đó không có nghĩa phân đoạn hay tìm kiếm chính xác 100%. Dice và P@5 là các phép đo chất lượng riêng. Trọng số được chọn trên validation; test không được dùng để chọn trọng số. Nhãn loài từ tên ảnh còn mang tính tạm thời, nên cần kiểm tra thêm relevance thị giác của kết quả.
