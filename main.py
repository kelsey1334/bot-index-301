import logging
import requests
import xml.etree.ElementTree as ET
from telegram.ext import (
    Updater, CommandHandler, CallbackContext,
    MessageHandler, Filters, CallbackQueryHandler
)
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ParseMode
)
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession
import os, json, re
from datetime import datetime, timedelta, timezone

# ===========================
# Logging
# ===========================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
SCOPES = ["https://www.googleapis.com/auth/indexing"]
INDEXING_ENDPOINT = "https://indexing.googleapis.com/v3/urlNotifications:publish"
DAILY_LIMIT = 200

# ===========================
# Load nhiều API
# ===========================
API_CREDENTIALS = [
    {"name": "API1", "json": os.getenv("API1_JSON")},
    {"name": "API2", "json": os.getenv("API2_JSON")},
    {"name": "API3", "json": os.getenv("API3_JSON")},
    {"name": "API4", "json": os.getenv("API4_JSON")},
    {"name": "API5", "json": os.getenv("API5_JSON")},
]

APIs = []
for api in API_CREDENTIALS:
    if api["json"]:
        creds_json = json.loads(api["json"])
        creds = service_account.Credentials.from_service_account_info(
            creds_json, scopes=SCOPES
        )
        APIs.append({
            "name": api["name"],
            "session": AuthorizedSession(creds),
            "email": creds_json["client_email"],
            "used": 0,
            "day": datetime.utcnow().date()
        })

# ===========================
# Quota tracking
# ===========================
def check_api_quota(api):
    today = datetime.utcnow().date()
    if today != api["day"]:
        api["day"] = today
        api["used"] = 0
    return DAILY_LIMIT - api["used"]

def add_quota(api, count=1):
    api["used"] += count

def quota_message(api):
    remaining = check_api_quota(api)
    reset_time_vn = (datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                     + timedelta(days=1)).replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=7)))
    msg = (f"{api['name']} ({api['email']}): "
           f"đã dùng {api['used']}/{DAILY_LIMIT}, còn {remaining}.\n"
           f"Reset lúc {reset_time_vn.strftime('%H:%M, %d-%m-%Y')} (giờ VN).")
    return msg

# ===========================
# Helpers
# ===========================
def extract_domain(text):
    text = text.strip()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"/.*$", "", text)
    return text

def index_with_api(api, url):
    body = {"url": url, "type": "URL_UPDATED"}
    response = api["session"].post(INDEXING_ENDPOINT, json=body)
    add_quota(api, 1)
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

def chunk_list(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

# ===========================
# Commands & Handlers
# ===========================
def start(update: Update, context: CallbackContext):
    keyboard = [
        [KeyboardButton("🚀 Bắt đầu Index")],
        [KeyboardButton("📊 Kiểm tra quota")],
        [KeyboardButton("❌ Hủy")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    update.message.reply_text(
        "👋 Xin chào!\nMình là *Index Bot*.\n\n"
        "Bạn có thể chọn chức năng từ menu bên dưới:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

def handle_menu(update: Update, context: CallbackContext):
    text = update.message.text

    if text == "📊 Kiểm tra quota":
        msgs = [quota_message(api) for api in APIs]
        update.message.reply_text("📊 Tình trạng quota:\n\n" + "\n".join(msgs))

    elif text == "🚀 Bắt đầu Index":
        update.message.reply_text("✍️ Nhập domain hoặc URL (ví dụ: `abc.com` hoặc `https://blog.abc.com/post`).",
                                  parse_mode=ParseMode.MARKDOWN)
        context.user_data["awaiting_domain"] = True

    elif text == "❌ Hủy":
        context.user_data["awaiting_domain"] = False
        update.message.reply_text("❌ Đã hủy thao tác.")

    elif context.user_data.get("awaiting_domain"):
        domain = extract_domain(text)
        context.user_data["awaiting_domain"] = False

        # Parse sitemap & đếm URL
        sitemap_url_https = f"https://{domain}/sitemap_index.xml"
        sitemap_url_http = f"http://{domain}/sitemap_index.xml"
        try:
            try:
                urls = parse_sitemap(sitemap_url_https)
            except Exception:
                urls = parse_sitemap(sitemap_url_http)
        except Exception as e:
            update.message.reply_text(f"❌ Không lấy được sitemap: {str(e)}")
            return

        total = len(urls)
        context.user_data["urls"] = urls

        # Kiểm tra quota trên tất cả API
        candidates = []
        details = []
        for api in APIs:
            remaining = check_api_quota(api)
            details.append(quota_message(api))
            if remaining >= total:
                candidates.append(api)

        if not candidates:
            update.message.reply_text(
                f"🔍 Tìm thấy {total} URL.\n"
                f"❌ Không API nào đủ quota!\n\n"
                "📊 Tình trạng hiện tại:\n" + "\n".join(details)
            )
            return

        # Hiển thị button chọn API
        buttons = [[InlineKeyboardButton(f"{api['name']} ({api['email']})", callback_data=f"index::{api['name']}")] for api in candidates]
        reply_markup = InlineKeyboardMarkup(buttons)

        update.message.reply_text(
            f"🔍 Tìm thấy {total} URL trong sitemap của `{domain}`.\n\n"
            "👉 Hãy chọn API để chạy index. "
            "Nhớ add email của API đó vào GSC với quyền *Owner* trước:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )

def run_index(api, urls, query):
    total = len(urls)
    query.edit_message_text(f"🚀 Bắt đầu index {total} URL bằng {api['name']} ({api['email']}).")

    success, fail = 0, 0
    for batch in chunk_list(urls, 10):
        remaining = check_api_quota(api)
        if remaining <= 0:
            query.message.reply_text("🚫 Hết quota cho API này!")
            break

        batch_results = []
        for url in batch:
            result = index_with_api(api, url)
            if "error" in result:
                fail += 1
                batch_results.append(f"❌ `{url}`")
            else:
                success += 1
                batch_results.append(f"✅ `{url}`")

        query.message.reply_text(
            "\n".join(batch_results),
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )

    query.message.reply_text(
        f"🎯 Hoàn tất bằng {api['name']}. Thành công: {success}, Thất bại: {fail}\n{quota_message(api)}"
    )

def button_confirm(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    if query.data.startswith("index::"):
        api_name = query.data.split("::")[1]
        api = next(a for a in APIs if a["name"] == api_name)
        urls = context.user_data.get("urls", [])
        run_index(api, urls, query)
    elif query.data == "cancel":
        query.edit_message_text("❌ Đã hủy thao tác.")

# ===========================
# MAIN
# ===========================
def main():
    updater = Updater(BOT_TOKEN)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_menu))
    dp.add_handler(CallbackQueryHandler(button_confirm, pattern="^(index::.*|cancel)$"))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
