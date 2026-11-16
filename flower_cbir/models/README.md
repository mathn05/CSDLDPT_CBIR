# Checkpoint DeepLabv3

Trọng số và dữ liệu huấn luyện không được commit vào Git. Để chạy bản cấu hình hiện tại, đặt checkpoint inference-only đã được huấn luyện tại:

`flower_cbir/models/deeplabv3_flower_v3.pt`

SHA256 của checkpoint đã kiểm chứng trên máy phát triển:

`15142c42782958026c11e8d316d79e62a59fc9fd9817946d0a1c9ce7c784c062`

Checkpoint chứa state dict `model` của DeepLabV3 ResNet50 hai lớp, có auxiliary head. Có thể chuyển checkpoint huấn luyện đáng tin cậy bằng `tools/export_deeplab_checkpoint.py`; hoặc chuẩn bị dữ liệu và tinh chỉnh bằng các script trong `tools`, như hướng dẫn ở README gốc.

Khi dùng checkpoint khác, cập nhật `segmentation.checkpoint` và môi trường PyTorch trong `flower_cbir/config/pipeline.json`, rồi dựng lại index và kiểm tra đồng bộ. Không trộn vector của hai checkpoint.
