# viscribe-icd
ViScribe-ICD: AI-assisted SOAP clinical notes and ICD-10 coding from Vietnamese doctor–patient conversations (research project)

## Phase 3 · Chạy môi trường phát triển

Dùng Docker Compose, [uv](https://docs.astral.sh/uv/) và Python 3.12. Cấu hình này dành cho ca mô phỏng. PostgreSQL và API được chạy trên localhost.

1. Tạo `.env` từ mẫu và đặt một bearer token riêng cho từng bác sĩ thử nghiệm. Sinh token bằng `openssl rand -hex 32`. Ví dụ cấu trúc cho biến `VISCRIBE_DEV_USERS` là `{"doctor-local":"<token>"}`. Nếu đổi cổng hoặc mật khẩu PostgreSQL, cập nhật cả `VISCRIBE_DATABASE_URL`.

   ```sh
   cp -n .env.example .env
   chmod 600 .env
   docker compose up -d --wait
   uv sync --project viscribe-api --locked --python 3.12
   uv run --project viscribe-api --locked alembic -c viscribe-api/alembic.ini upgrade head
   ```

2. Nạp danh mục mẫu và khởi động API.

   ```sh
   uv run --project viscribe-api --locked python -m viscribe_api.load_catalog \
     viscribe-data/fixtures/icd10-who2019-demo.csv --version who2019-demo-v1 \
     --source-url https://icd.who.int/browse10/2019/en
   uv run --project viscribe-api --locked uvicorn viscribe_api.main:app --host 127.0.0.1 --port 8000
   ```

3. Mở [OpenAPI tại localhost](http://127.0.0.1:8000/docs), dùng **Authorize** với token của `doctor-local`, rồi tìm `viêm họng` hoặc `J02.9` ở `GET /icd/search`. `POST /encounters` nhận `{"mode":"assisted","patientRef":"demo-01"}`. Danh sách và chi tiết ca giới hạn theo chủ sở hữu. Bản danh mục được ghi theo từng ca để các lần tra cứu sau dùng đúng phiên bản.

### Danh mục ICD-10

Fixture gồm một số mã kiểm thử dựa trên [ICD-10 của WHO](https://icd.who.int/browse10/2019/en). Căn cứ cần đối soát cho danh mục tiếng Việt dùng trong nghiên cứu là [Thông tư 06/2026/TT-BYT và phụ lục Bộ Y tế](https://vanban.chinhphu.vn/?classid=1&docid=217536&pageid=27160&typegroupid=6). Chốt file nguồn, quy tắc và tập mã nghiên cứu trước khi nghiệm thu dữ liệu nghiên cứu.

Bộ nạp nhận CSV UTF-8 với `code` và `title_vi`. Có thể bổ sung `title_en`, `chapter`, `block`, `includes`, `synonyms`, `active`, `selectable`, `in_focus_set`, `excludes_codes`, `sex_restriction`, `age_min`, `age_max` và `rule_source`. Danh sách dùng dấu `|`, tuổi tính theo năm. Mã nhóm hoặc mã ngừng hiệu lực đặt `selectable=false`. Mỗi ràng buộc về tuổi, giới hoặc mã loại trừ cần URL nguồn; mã loại trừ phải có trong cùng phiên bản. Lệnh nạp kiểm tra toàn bộ file trước khi ghi. Nếu nội dung thay đổi, đặt phiên bản mới và cập nhật `VISCRIBE_ICD_CATALOG_VERSION` cho các ca tạo sau đó.

Tra cứu ưu tiên khớp mã hoặc từ khóa có dấu và không dấu. Khi từ khóa không có kết quả, API dùng vector của cùng phiên bản và deployment, nếu Azure và vector đã được cấu hình. Thêm `--embed` vào lệnh nạp để tạo vector còn thiếu. Có thể chạy lại sau lỗi để tiếp tục các batch chưa lưu.

### Kiểm tra

```sh
uv run --project viscribe-api --locked ruff check viscribe-api
uv run --project viscribe-api --locked python -m pytest viscribe-api/tests
```

Đặt `VISCRIBE_TEST_DATABASE_URL` tới PostgreSQL local đã chạy migration để bật các kiểm thử tích hợp. Các test tạo schema tạm và dọn schema sau khi chạy.

Để kiểm tra thật năm deployment HIVN, cấu hình biến `AZURE_*` ở backend theo tài liệu HIVN, rồi truyền một WAV **mô phỏng** PCM16 mono 24 kHz, dài tối đa 15 giây.

```sh
uv run --project viscribe-api --locked python -m viscribe_api.smoke_azure --audio /path/to/synthetic.wav
```

Lệnh thử mỗi deployment một lần, kiểm tra structured output của Luna và ghi kết quả, độ trễ, token nếu có vào `ai_runs`. Kết quả terminal chỉ gồm tên deployment và trạng thái.

## Phase 4 · Tải audio và chép lời

Cần `ffmpeg`, `ffprobe` và Node.js 22. Sau khi dựng PostgreSQL và nạp danh mục mẫu theo hướng dẫn trên, chạy migration mới rồi khởi động API, worker và web ở các Terminal riêng.

```sh
uv run --project viscribe-api --locked alembic -c viscribe-api/alembic.ini upgrade head
uv run --project viscribe-api --locked uvicorn viscribe_api.main:app --host 127.0.0.1 --port 8000
```

```sh
uv run --project viscribe-api --locked python -m viscribe_api.transcription_worker
```

```sh
npm --prefix viscribe-web ci
npm --prefix viscribe-web run dev
```

Worker dùng `AZURE_STT_ENDPOINT`, `AZURE_STT_API_KEY` và `AZURE_STT_API_VERSION` trong cấu hình backend. Mở [ViScribe local](http://127.0.0.1:5173/), dùng giá trị của `doctor-local` trong `VISCRIBE_DEV_USERS` của `.env`, tạo buổi khám rồi chọn bản ghi mô phỏng. Giao diện hiển thị tiến độ từng phần và transcript cuối khi hoàn tất. Swagger có các thao tác tải file, xếp hàng, xem trạng thái và thử lại.

Mặc định hỗ trợ WAV, MP3, M4A, MP4 hoặc WebM có audio, tối đa 64 MiB và 30 phút. Worker chuẩn hóa thành WAV PCM16 mono 16 kHz, chia đoạn tối đa 120 giây và ghép theo thứ tự. Mốc thời gian trong giao diện là ranh giới đoạn cắt. Bản ghi và transcript của ca mô phỏng được lưu trong PostgreSQL local, gắn với phiên bản upload và giới hạn theo chủ sở hữu ca.

Khi Azure trả `429` hoặc gặp lỗi kết nối tạm thời, worker lên lịch thử lại và giữ các đoạn đã lưu. Hàng đợi PostgreSQL cho phép worker tiếp tục sau khi khởi động lại. Giới hạn mặc định giữa hai lần gọi STT là 6,2 giây cho mỗi deployment. Các lần gửi lại cùng file hiện tại dùng lại dữ liệu và job đã có. Nếu tải file khác, kết quả cũ được giữ theo phiên bản và màn hình dùng file mới.
