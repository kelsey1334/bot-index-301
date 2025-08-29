import logging
import requests
import xml.etree.ElementTree as ET
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram import Update
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession
import os, json, re
from datetime import datetime

# ===========================
# Logging setup
# ===========================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = os.getenv("BOT_TOKEN")

# ===========================
# Google API Credentials
# ===========================
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

# ===========================
# Helpers
# ===========================
def extract_domain(text):
    """Chuẩn hóa domain/subdomain từ user input"""
    text = text.strip()
    text = re.sub(r"^https?://", "", text)     # bỏ http:// hoặc https://
    text = re.sub(r"/.*$", "", text)           # bỏ path sau domain
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
# Commands
# ===========================
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Xin chào!\n"
        "Mình là *Index Bot*.\n\n"
        "📌 Hướng dẫn sử dụng:\n"
        "1. Add email sau vào Google Search Console (GSC) với quyền *Owner* cho domain cần index:\n"
        "`api-index@api-index-470509.iam.gserviceaccount.com`\n\n"
        "2. Lệnh để chạy:\n"
        "`/index_all yourdomain.com`\n"
        "👉 Bot sẽ crawl toàn bộ sitemap và gửi URL lên Google Indexing API.\n\n"
        "ℹ️ Bạn có thể nhập domain, subdomain, hoặc cả URL — bot sẽ tự chuẩn hoá."
    )

def index_all(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        update.message.reply_text(
            "❓ Bạn muốn index cho domain nào?\n"
            "Ví dụ: `/index_all abc.com` hoặc `/index_all https://blog.abc.com/post-1`"
        )
        return

    domain = extract_domain(context.args[0])
    sitemap_url_https = f"https://{domain}/sitemap_index.xml"
    sitemap_url_http = f"http://{domain}/sitemap_index.xml"

    update.message.reply_text(
        f"⚠️ Domain/Subdomain: `{domain}`\n\n"
        "Trước khi chạy, cần add email:\n"
        "`api-index@api-index-470509.iam.gserviceaccount.com`\n"
        "👉 vào Google Search Console với quyền *Owner*.\n"
    )

    try:
        # Ưu tiên HTTPS, fallback sang HTTP
        try:
            urls = parse_sitemap(sitemap_url_https)
        except Exception:
            urls = parse_sitemap(sitemap_url_http)

        total = len(urls)
        used, remaining = check_quota()
        update.message.reply_text(
            f"🔍 Tìm thấy {total} URL trong sitemap.\n"
            f"📊 Hôm nay đã dùng {used}/{DAILY_LIMIT} request.\n"
            f"👉 Còn lại {remaining} lượt."
        )

        success, fail = 0, 0
        for url in urls:
            used, remaining = check_quota()
            if remaining <= 0:
                update.message.reply_text("🚫 Hết quota Google Indexing API hôm nay!")
                break

            result = index_url(url)
            add_quota(1)

            if "error" in result:
                fail += 1
                update.message.reply_text(f"❌ {url}\nLỗi: {result['error']['message']}")
            else:
                success += 1
                update.message.reply_text(f"✅ {url}")

        used, remaining = check_quota()
        update.message.reply_text(
            f"🎯 Hoàn tất. Thành công: {success}, Thất bại: {fail}\n"
            f"📊 Đã dùng {used}/{DAILY_LIMIT} request. Còn {remaining} lượt hôm nay."
        )

    except Exception as e:
        update.message.reply_text(f"❌ Lỗi: {str(e)}")

# ===========================
# MAIN
# ===========================
def main():
    updater = Updater(BOT_TOKEN)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("index_all", index_all))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
