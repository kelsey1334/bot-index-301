import logging
import requests
import xml.etree.ElementTree as ET
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram import Update
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession
import os

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
SERVICE_ACCOUNT_FILE = "api-index.json"

SCOPES = ["https://www.googleapis.com/auth/indexing"]
credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
authed_session = AuthorizedSession(credentials)

INDEXING_ENDPOINT = "https://indexing.googleapis.com/v3/urlNotifications:publish"

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

def index_all(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        update.message.reply_text("❌ Vui lòng nhập domain: /index_all abc.com")
        return

    domain = context.args[0]
    sitemap_url = f"https://{domain}/sitemap_index.xml"

    try:
        urls = parse_sitemap(sitemap_url)
        total = len(urls)
        update.message.reply_text(f"🔍 Tìm thấy {total} URL. Bắt đầu gửi Google Indexing...")

        success, fail = 0, 0
        for url in urls:
            result = index_url(url)
            if "error" in result:
                fail += 1
                update.message.reply_text(f"❌ {url}\nLỗi: {result['error']['message']}")
            else:
                success += 1
                update.message.reply_text(f"✅ {url}")
        update.message.reply_text(f"🎯 Hoàn tất. Thành công: {success}, Thất bại: {fail}")

    except Exception as e:
        update.message.reply_text(f"❌ Lỗi: {str(e)}")

def main():
    updater = Updater(BOT_TOKEN)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("index_all", index_all))
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
