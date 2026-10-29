# Binance AI Agent (Spot + Futures)

Vòng lặp: **tin tức RSS + chỉ báo (EMA/RSI/ATR) → Claude đề xuất (tool use, JSON) → lớp risk bằng code kiểm tra & tính size → gửi lệnh + SL/TP → log**.

## Cài đặt
```bash
pip install -r requirements.txt
cp .env.example .env      # điền ANTHROPIC_API_KEY (+ key Binance nếu dùng testnet/live)
python test_offline.py    # kiểm tra nhanh logic, không cần mạng
python bot.py
```

## Các lựa chọn (config.yaml)
| Option | Giá trị | Ý nghĩa |
|---|---|---|
| `market` | `spot` / `futures` | Spot chỉ long. Futures (USDT-M) long + short (tuỳ `allow_short`) |
| `mode` | `paper` / `testnet` / `live` | paper = vốn ảo, giá thật, không cần key Binance. live cần `I_ACCEPT_LIVE_TRADING=YES` |
| `timeframe` | 5m, 15m, 1h, 4h... | Khung thời gian của nến phân tích |
| `decision_every_seconds` | số giây | Tần suất gọi LLM (quyết định chi phí API) |
| `risk.*` | | % rủi ro/lệnh, trần vị thế, lỗ tối đa/ngày, RR tối thiểu, số lệnh/ngày |
| `futures.leverage` / `margin_mode` | 3 / isolated | Đòn bẩy và kiểu ký quỹ |

## Lộ trình nên theo
1. `paper` vài tuần, đọc `decisions.jsonl` (có lý do của LLM + lý do risk chấp nhận/từ chối).
2. `testnet` để kiểm tra việc gửi lệnh, SL/TP thật sự nằm trên sàn.
3. `live` với số vốn nhỏ bạn chấp nhận mất hết. Dùng sub-account riêng, **tắt Withdraw**, giới hạn IP.

## Cách bot xử lý SL/TP
- **Futures**: SL và TP là lệnh `reduceOnly` đặt trên sàn. Nếu đặt SL lỗi, bot đóng vị thế ngay.
- **Spot**: SL là lệnh `STOP_LOSS_LIMIT` trên sàn; TP do bot theo dõi (kiểm tra mỗi `exit_check_every_seconds`), vì spot không cho 2 lệnh bán cùng số dư (trừ OCO). Nếu bot sập, SL vẫn còn nhưng TP thì không.
- Dừng khẩn cấp: tạo file `STOP` cạnh `bot.py` (bot ngừng mở lệnh mới; vị thế đang có vẫn được SL bảo vệ).

## Lưu ý quan trọng
- Mình chưa chạy được với Binance thật (môi trường tạo code không có mạng). Chỉ test offline phần logic. Hãy chạy `testnet` kỹ trước.
- ccxt/Binance đổi API thường xuyên (đặc biệt lệnh điều kiện SL/TP của Futures và testnet/demo). Luôn `pip install -U ccxt`, và nếu testnet futures báo lỗi thì xem mục demo trading của ccxt.
- Spot: dùng tài khoản riêng, vì bot tính equity gồm cả coin whitelist bạn đang giữ.
- Tin tức bị coi là dữ liệu không đáng tin (chống prompt injection), nhưng LLM vẫn có thể sai. Đây không phải lời khuyên đầu tư; phần lớn bot trade thua lỗ.
