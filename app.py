import telegram
print("telegram imported successfully")
import os
import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from hijridate import Hijri
import re

# ── CONFIG ─────────────────────────────────────────────
BOT_TOKEN = "8235502388:AAF_BZqe01W3VP77zWschDXCVqJmcJ4SbO0"
SHEET_ID  = "1N0SSSWmiYZalnKvfL5LIcPOXH2_GTJXnnI11NdeeMxU"
TIMEZONE  = "Asia/Riyadh"
SCOPES    = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/spreadsheets"
]
# ───────────────────────────────────────────────────────

def get_google_services():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())
    calendar = build("calendar", "v3", credentials=creds)
    sheets   = build("sheets",   "v4", credentials=creds)
    return calendar, sheets

def parse_hijri_date_string(date_str):
    date_str = date_str.strip()
    parts = date_str.replace("هـ", "").strip().split("/")
    parts = [p.strip() for p in parts]
    if len(parts[0]) == 4:
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
    else:
        day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
    gregorian = Hijri(year, month, day).to_gregorian()
    return gregorian, f"{year}/{month:02d}/{day:02d} هـ"

def parse_time_string(time_str):
    time_str = time_str.strip()
    is_pm = "PM" in time_str or "مساءً" in time_str or "مساء" in time_str
    is_am = "AM" in time_str or "صباحاً" in time_str or "صباح" in time_str
    time_clean = re.sub(r"[^\d:]", "", time_str).strip()
    hour, minute = map(int, time_clean.split(":"))
    if is_pm and hour != 12:
        hour += 12
    if is_am and hour == 12:
        hour = 0
    if is_pm:
        period = "مساءً"
    elif is_am:
        period = "صباحاً"
    elif hour < 12:
        period = "صباحاً"
    else:
        period = "مساءً"
    return hour, minute, period

def find_existing_calendar_event(calendar_svc, case_number):
    now = datetime.datetime.utcnow() - datetime.timedelta(days=365)
    events_result = calendar_svc.events().list(
        calendarId="primary",
        timeMin=now.isoformat() + "Z",
        maxResults=50,
        singleEvents=True,
        orderBy="startTime",
        q=f"قضية رقم {case_number}"
    ).execute()
    events = events_result.get("items", [])
    if events:
        return events[0]
    return None

def find_existing_sheet_row(sheets_svc, case_number):
    result = sheets_svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:A"
    ).execute()
    values = result.get("values", [])
    for i, row in enumerate(values):
        if row and str(row[0]).strip() == str(case_number).strip():
            return i + 1
    return None

def parse_court_message(text):
    # تنظيف
    text = re.sub(r"~~\s*~~|~~", "", text)
    text = text.replace("\r", "")
    lines = text.split("\n")

    # ── رقم القضية ──
    case_number = "غير محدد"
    for i, line in enumerate(lines):
        if "رقم" in line:
            digits = re.findall(r"\d{5,}", line)
            if digits:
                case_number = digits[0]
                break
            if i + 1 < len(lines):
                digits = re.findall(r"\d{5,}", lines[i + 1].strip())
                if digits:
                    case_number = digits[0]
                    break

    # ── الطرفين ──
    from_match    = re.search(r"المقامة من\s*:\s*([^\n]+)", text)
    against_match = re.search(r"ضد\s*:\s*([^\n]+)", text)
    plaintiff = from_match.group(1).strip()    if from_match    else ""
    defendant = against_match.group(1).strip() if against_match else ""

    # ── الوقت والزمان ──
    time_match = re.search(r"الساعة[:\s]*([\d:]+\s*(?:AM|PM|صباحاً|مساءً|صباح|مساء)?)", text)
    time_str   = time_match.group(1).strip() if time_match else "09:00 AM"
    hour, minute, period = parse_time_string(time_str)

    # ── رابط الجلسة ──
    link_match = re.search(r"https?://\S+", text)
    session_link = link_match.group(0).strip() if link_match else ""

    # ── نوع الرسالة والتاريخ ──
    is_change = "تغيير موعد" in text

    if is_change:
        date_match = re.search(r"إلى تاريخ[^\d]*([\d/]+)", text)
        if not date_match:
            raise ValueError("لم يتم العثور على التاريخ الجديد")
        greg_date, hijri_label = parse_hijri_date_string(date_match.group(1).strip())
        status = "معدل"
    else:
        date_match = re.search(r"(?:بتاريخ|المحددة بتاريخ)[^\d\n]*\n?\s*([\d/]+)", text)
        if not date_match:
            raise ValueError("لم يتم العثور على التاريخ")
        greg_date, hijri_label = parse_hijri_date_string(date_match.group(1).strip())
        status = "جديد"

    start = datetime.datetime(greg_date.year, greg_date.month, greg_date.day, hour, minute)
    end   = start + datetime.timedelta(hours=1)

    title = f"قضية  {case_number} | {plaintiff} ضد {defendant}"
    notes = f"رقم القضية: {case_number}\nالمدعي: {plaintiff}\nالمدعى عليه: {defendant}"

    return {
        "is_change": is_change,
        "title": title,
        "notes": notes,
        "case_number": case_number,
        "hijri_label": hijri_label,
        "start": start,
        "end": end,
        "period": period,
        "status": status,
        "plaintiff": plaintiff,
        "defendant": defendant,
        "session_link": session_link,
    }

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    try:
        data = parse_court_message(text)
        calendar_svc, sheets_svc = get_google_services()

        new_start = {"dateTime": data["start"].isoformat(), "timeZone": TIMEZONE}
        new_end   = {"dateTime": data["end"].isoformat(),   "timeZone": TIMEZONE}

        existing_event = find_existing_calendar_event(calendar_svc, data["case_number"])
        existing_row   = find_existing_sheet_row(sheets_svc, data["case_number"])

        if existing_event or data["is_change"]:
            # ── تعديل event موجود: الوقت فقط بدون تغيير الاسم أو الوصف ──
            if existing_event:
                existing_event["start"] = new_start
                existing_event["end"]   = new_end
                created = calendar_svc.events().update(
                    calendarId="primary",
                    eventId=existing_event["id"],
                    body=existing_event
                ).execute()
                action = "تم تعديل الموعد"
                emoji  = "🔄"
            else:
                # تغيير موعد لكن ما في event موجود — ننشئ جديد
                event = {
                    "summary":     data["title"],
                    "description": data["notes"],
                    "start":       new_start,
                    "end":         new_end,
                }
                created = calendar_svc.events().insert(calendarId="primary", body=event).execute()
                action = "تم إنشاء الموعد"
                emoji  = "✅"

            # ── تعديل الشيت: التاريخ والوقت والزمان والحالة فقط (C إلى G) ──
            if existing_row:
                sheets_svc.spreadsheets().values().update(
                    spreadsheetId=SHEET_ID,
                    range=f"Sheet1!C{existing_row}:G{existing_row}",
                    valueInputOption="USER_ENTERED",
                    body={"values": [[
                        data["hijri_label"],
                        data["start"].strftime("%Y-%m-%d"),
                        data["start"].strftime("%H:%M"),
                        data["period"],
                        data["status"],
                    ]]}
                ).execute()
            else:
                # القضية موجودة بالتقويم لكن مو بالشيت — نضيف صف جديد
                sheets_svc.spreadsheets().values().append(
                    spreadsheetId=SHEET_ID,
                    range="Sheet1!A:J",
                    valueInputOption="USER_ENTERED",
                    body={"values": [[
                        data["case_number"],
                        data["title"],
                        data["hijri_label"],
                        data["start"].strftime("%Y-%m-%d"),
                        data["start"].strftime("%H:%M"),
                        data["period"],
                        data["status"],
                        data["plaintiff"],
                        data["defendant"],
                        data["session_link"],
                    ]]}
                ).execute()

        else:
            # ── قضية جديدة كلياً ──
            event = {
                "summary":     data["title"],
                "description": data["notes"],
                "start":       new_start,
                "end":         new_end,
            }
            created = calendar_svc.events().insert(calendarId="primary", body=event).execute()
            action = "تم إنشاء الموعد"
            emoji  = "✅"

            sheets_svc.spreadsheets().values().append(
                spreadsheetId=SHEET_ID,
                range="Sheet1!A:J",
                valueInputOption="USER_ENTERED",
                body={"values": [[
                    data["case_number"],
                    data["title"],
                    data["hijri_label"],
                    data["start"].strftime("%Y-%m-%d"),
                    data["start"].strftime("%H:%M"),
                    data["period"],
                    data["status"],
                    data["plaintiff"],
                    data["defendant"],
                    data["session_link"],
                ]]}
            ).execute()

        await update.message.reply_text(
            f"{emoji} {action}!\n"
            f"📋 {data['title']}\n"
            f"🗓 {data['hijri_label']}\n"
            f"⏰ {data['start'].strftime('%H:%M')} {data['period']}\n"
            f"👤 المدعي: {data['plaintiff']}\n"
            f"👤 المدعى عليه: {data['defendant']}\n"
            f"🔗 {created.get('htmlLink')}"
        )

    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")

# ── Run ──
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
print("Bot is running...")
app.run_polling()
