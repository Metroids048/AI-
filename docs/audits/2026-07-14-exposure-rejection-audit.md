# Exposure Rejection Audit

- Generated: 2026-07-14T14:50:15.482955+00:00
- Lookback days: 14
- Total orders (all time): 1687
- Exposure rejections (lookback): 597

## Rejection code counts

| code | count |
| --- | ---: |
| max_symbol_exposure_exceeded | 597 |
| max_total_exposure_exceeded | 476 |

## account_equity buckets in rejected orders

- <=10000: 597

## Diagnosis

- Root cause: 25/25 samples request notional >25% equity with open_positions=0 / symbol_exposure=0 — sizing path (risk_per_trade*leverage) exceeded max_symbol_exposure; not ghost holdings. Fix: cap notional to max_position_fraction.

## Sample rows (up to 25)

| symbol | codes | equity | sym_exp | tot_exp | req_notional | open_pos | at |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| ARB/USDT | max_symbol_exposure_exceeded | 8431.674999998617 |  |  | 6745.339999998893 |  | 2026-07-11 08:24:44 |
| ARB/USDT | max_symbol_exposure_exceeded | 8592.549999998759 |  |  | 6874.039999999008 |  | 2026-07-11 04:00:49 |
| UNI/USDT | max_symbol_exposure_exceeded | 8665.149999998823 |  |  | 6932.119999999059 |  | 2026-07-11 02:03:01 |
| UNI/USDT | max_symbol_exposure_exceeded | 8732.799999998882 |  |  | 6986.239999999106 |  | 2026-07-11 00:14:58 |
| FIL/USDT | max_symbol_exposure_exceeded | 8872.224999999005 |  |  | 7097.779999999204 |  | 2026-07-10 20:26:04 |
| FIL/USDT | max_symbol_exposure_exceeded | 8873.049999999006 |  |  | 7098.439999999205 |  | 2026-07-10 20:24:41 |
| FIL/USDT | max_symbol_exposure_exceeded | 8873.874999999007 |  |  | 7099.0999999992055 |  | 2026-07-10 20:23:19 |
| FIL/USDT | max_symbol_exposure_exceeded | 8874.699999999008 |  |  | 7099.759999999206 |  | 2026-07-10 20:21:57 |
| DOT/USDT | max_symbol_exposure_exceeded | 8875.524999999008 |  |  | 7100.419999999206 |  | 2026-07-10 20:20:35 |
| FIL/USDT | max_symbol_exposure_exceeded | 8875.524999999008 |  |  | 7100.419999999207 |  | 2026-07-10 20:20:35 |
| FIL/USDT | max_symbol_exposure_exceeded,max_total_exposure_exceeded | 8909.349999999038 |  |  | 7127.479999999232 |  | 2026-07-10 19:24:21 |
| FIL/USDT | max_symbol_exposure_exceeded | 8910.174999999039 |  |  | 7128.139999999231 |  | 2026-07-10 19:22:59 |
| FIL/USDT | max_symbol_exposure_exceeded | 8910.99999999904 |  |  | 7128.799999999232 |  | 2026-07-10 19:21:37 |
| DOT/USDT | max_symbol_exposure_exceeded | 8925.024999999052 |  |  | 7140.019999999241 |  | 2026-07-10 18:58:16 |
| DOT/USDT | max_symbol_exposure_exceeded | 8925.849999999053 |  |  | 7140.679999999243 |  | 2026-07-10 18:56:54 |
| DOT/USDT | max_symbol_exposure_exceeded | 8926.674999999053 |  |  | 7141.339999999243 |  | 2026-07-10 18:55:32 |
| UNI/USDT | max_symbol_exposure_exceeded | 8926.674999999053 |  |  | 7141.3399999992425 |  | 2026-07-10 18:55:32 |
| APT/USDT | max_symbol_exposure_exceeded | 8926.674999999053 |  |  | 7141.339999999243 |  | 2026-07-10 18:55:32 |
| DOT/USDT | max_symbol_exposure_exceeded | 8959.674999999083 |  |  | 7167.739999999266 |  | 2026-07-10 18:00:38 |
| LINK/USDT | max_symbol_exposure_exceeded,max_total_exposure_exceeded | 8964.624999999087 |  |  | 7171.69999999927 |  | 2026-07-10 17:52:23 |
| FIL/USDT | max_symbol_exposure_exceeded,max_total_exposure_exceeded | 8972.874999999094 |  |  | 7178.299999999276 |  | 2026-07-10 17:38:36 |
| LINK/USDT | max_symbol_exposure_exceeded | 8975.349999999096 |  |  | 7180.279999999277 |  | 2026-07-10 17:34:29 |
| LINK/USDT | max_symbol_exposure_exceeded | 8988.549999999108 |  |  | 7190.839999999287 |  | 2026-07-10 17:12:11 |
| BCH/USDT | max_symbol_exposure_exceeded | 9027.324999999142 |  |  | 7221.859999999313 |  | 2026-07-10 16:06:58 |
| UNI/USDT | max_symbol_exposure_exceeded | 9038.874999999152 |  |  | 7231.099999999322 |  | 2026-07-10 15:49:59 |
