import logging
import requests
import xml.etree.ElementTree as ET
from telegram.ext import Updater, CommandHandler, CallbackContext, MessageHandler, Filters, CallbackQueryHandler
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession
import os, json, re
from datetime import datetime, timedelta, timezone

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = os.getenv("BOT_TOKEN")

SCOPES = ["https://www.googleapis.com/auth/indexing"]
if os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON"):
    creds_json = json.loads(os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON"))
    credentials = service_account.Credentials.from_service_account_info(
        creds_json, scopes=SCOPES
    )
else:
    credentials = service_account.Credentials.from_service_account_file(
        "api-index.json", scopes=SCOPES
    )

authed_session = AuthorizedSession(credentials)
INDEXING_ENDPOINT = "https://indexing.googleapis.com/v3/urlNotifications:publish"

# ===========================
# Quota tracking
# ===========================
DAILY_LIMIT = 200
used_requests = 0
current_day = datetime.utcnow().date()

def check_quota():
    global used_requests, current_day
    today = datetime.utcnow().date()
    if today != current_day:
        current_day = today
        used_requests = 0
    remaining = max(0, DAILY_LIMIT - used_requests)
    return used_requests, remaining

def add_quota(count=1):
    global used_requests
    used_requests += count

def quota_message():
    used, remaining = check_quota()
    reset_time_vn = (datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                     + timedelta(days=1)).replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=7)))
    msg = (f"📊 Hôm nay đã dùng {used}/{DAILY_LIMIT} request.\n"
           f"👉 Còn lại {remaining} lượt.\n"
           f"🔄 Quota reset lúc {reset_time_vn.strftime('%H:%M, %d-%m-%Y')} (giờ VN).")
    if remaining <= 20:
        msg += "\n⚠️ Quota sắp hết, ưu tiên URL quan trọng!"
    return msg

# ===========================
# Helpers
# ===========================
def extract_domain(text):
    text = text.strip()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"/.*$", "", text)
    return text

def index_url(url: str):
    body = {"url": url, "type": "URL_UPDATED"}
    response = authed_session.post(INDEXING_ENDPOINT, json=body)
    return response.json()

def parse_sitemap(url):
    urls = []
    r = requests.get(url)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    if root.tag.endswith("sitemapindex"):
        for sitemap in root.findall("sm:sitemap", ns):
            loc = sitemap.find("sm:loc", ns).text
            urls.extend(parse_sitemap(loc))
    elif root.tag.endswith("urlset"):
        for url_tag in root.findall("sm:url", ns):
            loc = url_tag.find("sm:loc", ns).text
            urls.append(loc)
    return urls

# ===========================
# Commands & Handlers
# ===========================
def start(update: Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("🚀 Bắt đầu Index", callback_data="ask_domain")],
        [InlineKeyboardButton("📊 Kiểm tra quota", callback_data="check_quota")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(
        "👋 Xin chào!\nMình là *Index Bot*.\n\n"
        "Bạn có thể ép Google index sitemap của domain.\n"
        "👉 Hãy chọn một chức năng bên dưới:",
        reply_markup=reply_markup
    )

def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()

    if query.data == "ask_domain":
        query.edit_message_text("✍️ Vui lòng nhập domain hoặc URL (ví dụ: `abc.com` hoặc `https://blog.abc.com/post`).")
        context.user_data["awaiting_domain"] = True

    elif query.data == "check_quota":
        query.edit_message_text(quota_message())

def handle_text(update: Update, context: CallbackContext):
    if context.user_data.get("awaiting_domain"):
        domain = extract_domain(update.message.text)
        context.user_data["awaiting_domain"] = False

        keyboard = [
            [InlineKeyboardButton(f"✅ Bắt đầu index {domain}", callback_data=f"index::{domain}")],
            [InlineKeyboardButton("❌ Hủy", callback_data="cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        update.message.reply_text(
            f"⚠️ Domain/Subdomain: `{domain}`\n\n"
            "Trước khi chạy, cần add email:\n"
            "`api-index@api-index-470509.iam.gserviceaccount.com`\n"
            "👉 vào GSC với quyền *Owner*.\n\n"
            "Bạn có muốn bắt đầu index ngay không?",
            reply_markup=reply_markup
        )

def run_index(domain, query):
    sitemap_url_https = f"https://{domain}/sitemap_index.xml"
    sitemap_url_http = f"http://{domain}/sitemap_index.xml"
    try:
        try:
            urls = parse_sitemap(sitemap_url_https)
        except Exception:
            urls = parse_sitemap(sitemap_url_http)

        total = len(urls)
        query.edit_message_text(
            f"🔍 Tìm thấy {total} URL trong sitemap.\n" + quota_message()
        )

        success, fail = 0, 0
        for url in urls:
            used, remaining = check_quota()
            if remaining <= 0:
                query.message.reply_text("🚫 Hết quota hôm nay!")
                break

            result = index_url(url)
            add_quota(1)

            if "error" in result:
                fail += 1
                query.message.reply_text(f"❌ {url}\nLỗi: {result['error']['message']}")
            else:
                success += 1
                query.message.reply_text(f"✅ {url}")

        query.message.reply_text(
            f"🎯 Hoàn tất. Thành công: {success}, Thất bại: {fail}\n" + quota_message()
        )

    except Exception as e:
        query.message.reply_text(f"❌ Lỗi: {str(e)}")

def button_confirm(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    if query.data.startswith("index::"):
        domain = query.data.split("::")[1]
        run_index(domain, query)
    elif query.data == "cancel":
        query.edit_message_text("❌ Đã hủy thao tác.")

# ===========================
# MAIN
# ===========================
def main():
    updater = Updater(BOT_TOKEN)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CallbackQueryHandler(button_handler, pattern="^(ask_domain|check_quota)$"))
    dp.add_handler(CallbackQueryHandler(button_confirm, pattern="^(index::.*|cancel)$"))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
