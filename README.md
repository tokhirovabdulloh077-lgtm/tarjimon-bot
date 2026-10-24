# 🇺🇿 O'zbekcha Tarjimon Bot

Istalgan tildagi (Inglizcha, Ruscha, Turkcha, Arabcha, Xitoycha va h.k.) matnlarni avtomatik aniqlab, **faqat O'zbek tiliga** tarjima qilib beruvchi Telegram bot.

Agar o'zbek tilida matn yuborilsa, uni Ingliz tiliga tarjima qiladi!

## Imkoniyatlari
- 🌐 100 dan ortiq tillarni avtomatik aniqlash (Auto-detect);
- 🇺🇿 Faqat O'zbek tiliga tezkor tarjima;
- ⚡️ Inline rejim (istalgan chatda `@bot_nomi matn` deb yozib tarjimani jo'natish mumkin);
- 🆓 **100% tekin serverlarga** (Render.com, Koyeb, Railway) joylashga to'liq tayyor.

## Mahalliy ishga tushirish (Kompyuterda)
1. `.env` fayliga `@BotFather` dan olingan tokenni yozing:
   ```env
   BOT_TOKEN=YOUR_BOT_TOKEN_HERE
   ```
2. Botni ishga tushiring:
   ```bash
   ./venv/bin/python bot.py
   ```

## Render.com da 100% Tekin 24/7 ishga tushirish:
1. Loyihani GitHub'ga yuklaysiz.
2. [Render.com](https://render.com) ga kirib, **New + ➔ Background Worker** (yoki Web Service) tanlaysiz.
3. GitHub reponi ulab, Environment Variable ga:
   - `BOT_TOKEN` = `sizning_tokeningiz`
   qo'shasiz va **Deploy** tugmasini bosasiz.
Tamom! Bot 24/7 tekinga ishlab turadi.
