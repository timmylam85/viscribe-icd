# Kế hoạch hoàn thiện ViScribe-ICD

MVP hỗ trợ bác sĩ ghi chép SOAP và duyệt mã ICD-10 từ hội thoại khám tiếng Việt. Luồng nghiệm thu đi từ ghi âm hoặc tải file đến transcript cuối, tạo bản nháp SOAP và gợi ý ICD-10, sau đó bác sĩ sửa, chọn mã và xác nhận lưu. Môi trường phát triển và demo dùng hội thoại mô phỏng.

Phần A của [hướng dẫn đề tài](HUONG_DAN_DE_TAI.md) xác định phạm vi nghiên cứu và chỉ số đánh giá. Phần B và `HUONG_DAN.md` trong `hivn-azure-ai.zip` là cơ sở cho phương án kỹ thuật. Kế hoạch bên dưới sắp lại thứ tự triển khai theo yêu cầu duyệt mock UI, duyệt logic backend và nghiệm thu từng phase.

## Các mốc duyệt

- **Duyệt mock UI ở Phase 1.** Bạn đã duyệt mock tương tác dùng ca ngoại trú mô phỏng.
- **Duyệt thiết kế backend ở Phase 2.** Bạn đã duyệt [bản thiết kế](THIET_KE_BACKEND_DE_DUYET.md).
- **Nghiệm thu từng phase.** Mỗi lần bàn giao có phần demo hoặc tài liệu duyệt, kết quả theo tiêu chí, và các điểm còn mở. Chuyển phase sau khi bạn xác nhận. Thay đổi luồng đã duyệt sẽ được trình lại phần liên quan trước khi hiện thực.

## Phase 0 — Đồng bộ Git và chốt điểm xuất phát

- Đã xác thực SSH bằng `timmylam85`, checkout `main` theo `origin/main` và kiểm tra tác giả commit local `timmylam85 <tlam@captechu.edu>`.
- Điểm xuất phát trên GitHub là commit `68d2ed3` gồm `README.md` và `.gitignore`. Nền tảng backend, frontend, cơ sở dữ liệu và kiểm thử đều cần khởi tạo. Giữ mốc tham chiếu 6 sprint, mỗi sprint 2 tuần, và cập nhật theo kết quả dựng môi trường và kiểm tra Azure ở Phase 3.
- Dùng ca ngoại trú mô phỏng để phát triển và demo. Chuyên khoa nghiên cứu và tập khoảng 200 mã cần chốt với người có chuyên môn trước khi đánh giá offline. Phiên bản và file danh mục ICD-10 tiếng Việt cần chốt trước khi nghiệm thu bộ nạp ở Phase 3.
- **Nghiệm thu** bằng kết quả xác thực SSH, nhánh `main` theo dõi remote và kiểm kê source ở trên.

## Phase 1 — Mock UI để duyệt

- Làm mock tương tác cho danh sách buổi khám và màn hình buổi khám. Thể hiện tạo ca, ghi/dừng, tải file, sinh bản nháp, sửa S/O/A/P, bấm bằng chứng để nhảy tới transcript, chọn hoặc bỏ từng mã, tìm mã thủ công và xác nhận.
- Mock thể hiện trạng thái chờ, lỗi có thể thử lại, mất kết nối ghi âm, thiếu bằng chứng, ca đã xác nhận, và cách hiển thị ở `silent`/`assisted`. Mã gợi ý ban đầu đều ở trạng thái chưa chọn.
- **Nghiệm thu** bằng cách đi qua một ca hoàn chỉnh và các ca lỗi trên mock. Bạn duyệt bố cục, nội dung và thao tác trước khi code UI.

## Phase 2 — Thiết kế logic backend để duyệt

- Trình sơ đồ tuần tự cho cả tải file và ghi âm trực tiếp. Thiết kế dùng transcript cuối để sinh SOAP/ICD. Nêu rõ nguồn của mỗi trường, điểm phân vai và ẩn danh, dữ liệu được lưu, và thời điểm tạo `ai_runs`.
- Trình hợp đồng API và JSON, trạng thái ca và job, cơ chế giới hạn quota, retry và tránh sinh trùng khi bấm lại. Định nghĩa cách giữ bản nháp AI, lưu bản bác sĩ sửa và ghi quyết định duyệt theo từng mã.
- Trình quy tắc phân biệt chẩn đoán xác nhận, nghi ngờ và đã loại trừ. Chốt cùng người có chuyên môn trường hợp nào đủ điều kiện để gợi ý mã, tiêu chí về độ chi tiết, giới/tuổi và mã loại trừ khi có căn cứ trong danh mục, cùng cách xử lý bằng chứng thiếu hoặc sai. Đề xuất kiểm tra bằng chứng trên transcript đã ẩn danh theo ID đoạn và chuỗi trích, cho phép chuẩn hóa khoảng trắng.
- Trình luồng chọn top-30 ứng viên, xếp hạng và kiểm tra bằng danh mục. Chốt cách trả về khi không đủ căn cứ hoặc ứng viên, cùng cách chặn kết quả của `silent` từ API dành cho bác sĩ. Đề xuất ẩn cả SOAP do AI sinh và gợi ý ICD trong chế độ này để giữ tính ngầm của nghiên cứu.
- **Nghiệm thu** khi bạn duyệt sơ đồ, API/schema và các ví dụ mô phỏng, gồm ca đủ căn cứ, ca chẩn đoán đã loại trừ, mã ngoài danh mục, bằng chứng sai, timeout/429 và yêu cầu xác nhận lặp.

## Phase 3 — Nền tảng và danh mục ICD-10

- Dựa trên kiểm kê source, giữ phần dùng được. Phương án từ tài liệu là FastAPI, PostgreSQL/pgvector và React + TypeScript. Thiết lập môi trường local, migration, cấu hình backend, quyền truy cập theo ca và mẫu `.env.example`.
- Xây bộ nạp danh mục theo phiên bản, hỗ trợ chạy lại, kiểm tra mã trùng và nguồn dữ liệu quy tắc, sau đó tạo embedding và triển khai API tìm mã theo tên hoặc mã. Chạy smoke test hữu hạn bằng dữ liệu giả trên 5 deployment HIVN khi có quyền truy cập.
- **Nghiệm thu** khi môi trường dựng lại được, nạp lại không sinh mã trùng, tìm được mã mẫu đã xác minh trong top 5, và có kết quả riêng cho từng deployment. Danh mục fixture có thể dùng để dev trong lúc chờ file chính thức. Nghiệm thu danh mục dùng cho nghiên cứu dựa trên file đã chốt.
- **Kết quả để duyệt.** Migration đã chạy trên PostgreSQL 16 với pgvector. Danh mục mẫu có 8 mã, trong đó 7 mã có thể chọn và đã có embedding. Chạy lại giữ nguyên dữ liệu. Tra “viêm họng” trả mã `J02.9` trong top 5. Đã xác minh quyền truy cập theo bác sĩ, 10 kiểm thử tự động đạt và cả 5 deployment HIVN chạy thành công với dữ liệu mô phỏng. [Hướng dẫn chạy thử](../README.md#phase-3--chạy-môi-trường-phát-triển).

## Phase 4 — Upload và chép lời từ file

- Thực hiện upload theo mock đã duyệt, xác thực định dạng và giới hạn file, chia đoạn khi cần và tổng hợp thành một transcript cuối. Theo dõi trạng thái job, timeout và lỗi quota. Chạy lại hoặc upload lại có chủ đích theo quy tắc đã duyệt.
- **Nghiệm thu** bằng một file mô phỏng khoảng 10 phút có transcript cuối, trạng thái và bản ghi lượt gọi AI. Demo file lỗi, lỗi 429 mô phỏng, và yêu cầu lặp mà không tạo kết quả trùng.
- **Kết quả để duyệt.** Ca `demo-phase4-10min` đã chạy trên Azure STT, lưu một transcript cuối từ 5 đoạn và ghi 5 lượt gọi trong `ai_runs`. Kiểm thử tích hợp mô phỏng `429`, giữ đoạn đã xong, tiếp tục đúng đoạn và xác nhận gửi lặp chỉ có một job. Đã kiểm tra tải tệp lỗi và tải tệp hợp lệ qua giao diện trên màn hình hẹp. [Hướng dẫn chạy thử](../README.md#phase-4--tải-audio-và-chép-lời).

## Phase 5 — SOAP, gợi ý ICD và bác sĩ xác nhận

- Từ transcript cuối, phân vai và ẩn danh, tạo SOAP đúng schema, giữ nguyên thông tin thiếu và liên kết bằng chứng về từng đoạn. Truy xuất và xếp hạng mã rồi áp quy tắc kiểm tra theo thiết kế đã duyệt. Tích hợp SOAP editor, đối chiếu bằng chứng và chọn mã thủ công theo mock đã duyệt.
- Lưu riêng bản AI và bản bác sĩ xác nhận. Bác sĩ chủ động chọn, bỏ hoặc thêm mã. Ghi thời điểm và quyết định để phục vụ đánh giá.
- **Nghiệm thu** trên ít nhất 10 hội thoại mô phỏng có SOAP đúng schema, 0 mã ngoài danh mục ở các gợi ý hiển thị và các ca thiếu bằng chứng được xử lý theo thiết kế. Demo tải file → tạo bản nháp → sửa/chọn → xác nhận → mở lại để kiểm tra SOAP cuối, mã đã lưu và nhật ký quyết định. Kiểm tra xác nhận lặp và quyền truy cập theo ca.

## Phase 6 — Ghi âm và transcript realtime

- Dùng AudioWorklet chuẩn hóa âm thanh thành PCM16 mono 24 kHz, gửi qua relay WebSocket và hiển thị transcript tạm. Sau khi dừng ghi, chép lời lại từ file để đưa transcript cuối vào cùng pipeline với luồng upload.
- **Nghiệm thu** khi nói vào micro thấy chữ chạy, dừng ghi nhận transcript cuối, sau đó hoàn thành ca qua cùng màn hình duyệt. Demo từ chối quyền micro, ngắt kết nối và chuyển sang đường upload theo mock đã duyệt.

## Phase 7 — Nghiệm thu MVP dùng cho nghiên cứu

- Kiểm tra quyền truy cập cho `silent` và `assisted`, nhật ký và `/admin/usage`. Chuẩn hóa `ai_runs` theo từng lượt gọi và từng lần thử lại, ghi deployment, phiên bản model/prompt khi có, thông số, trạng thái, độ trễ, token và chi phí ước tính. Trường dịch vụ không trả về được đánh dấu là thiếu. Bản xuất đánh giá dùng dữ liệu đã ẩn danh.
- Chạy cùng một bộ ca qua ba cấu hình từ đề cương. `(a)` LLM tự sinh mã, `(b)` truy xuất theo embedding, `(c)` truy xuất, xếp hạng và kiểm tra quy tắc. Bản xuất giữ kết quả đánh giá từng cấu hình, kể cả mã sai để đo tỷ lệ lỗi. Luồng bác sĩ chỉ nhận mã qua kiểm tra danh mục.
- **Nghiệm thu** bằng demo end-to-end cả upload và realtime, kiểm thử quyền truy cập hai chế độ, đối soát nhật ký và bản xuất CSV có mã ca giả danh, cấu hình và phiên bản tương ứng. Báo cáo chi phí trung bình mỗi ca, độ trễ đầu-cuối và tỷ lệ lỗi/429 trên tập demo.

## Cách áp dụng tài liệu HIVN

| Tác vụ | Tích hợp đề xuất theo hướng dẫn HIVN |
| --- | --- |
| Phân vai, ẩn danh, SOAP | `gpt-5.6-luna-1` qua resource chat, endpoint `/openai/v1/`. Smoke test khả năng structured output theo schema của dự án. |
| Xếp hạng ICD | `gpt-5.6-terra-1` qua cùng resource chat, truyền tập mã ứng viên ở cấu hình `(c)`. |
| Embedding | `text-embedding-3-small`, vector 1536 chiều, qua resource chat. |
| Chép lời file | `gpt-transcribe` qua resource STT, dùng `AzureOpenAI` với `api_version` của resource. |
| Chép lời realtime | `gpt-live-transcribe` qua relay WebSocket, URL có `intent=transcription`. Backend xử lý khóa STT và gửi PCM16 mono 24 kHz dạng base64. |

Resource chat ở West US 3 và STT ở Canada Central theo tài liệu HIVN, mỗi resource có khóa riêng. Mức 10 request/phút được tài liệu ghi cho mỗi deployment STT là đầu vào thiết kế hàng đợi. Kiểm tra lại quota, giới hạn file, phiên bản giao thức và khả năng structured output ở Phase 3. Tách giới hạn theo deployment và ưu tiên điều chỉnh từ cấu hình. Khóa lưu ở môi trường backend, zip và `.env` ở ngoài repo, log vận hành dùng metadata hoặc dữ liệu đã ẩn danh.

## Sau MVP — Đánh giá offline và pilot

Đánh giá offline cần kịch bản mô phỏng, transcript và SOAP tham chiếu, cùng hai người mã hoá độc lập và quy trình phân xử. Đo WER/CER/MTER theo giọng và điều kiện ghi âm, mức đầy đủ và lỗi thêm thông tin của SOAP, top-1/top-5 ICD ở mức chương, 3 ký tự và 4 ký tự, precision/recall và mức đồng thuận. Việc chạy pilot với bệnh nhân thật phụ thuộc đề cương, cơ sở đối tác, duyệt đạo đức, quy trình đồng ý tham gia và duyệt xử lý dữ liệu trên cloud. Đầu vào này được làm rõ song song với MVP mô phỏng.
