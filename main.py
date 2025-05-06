import time
import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from supabase import create_client, Client
from dotenv import load_dotenv
import os

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Clean text function
emoji_pattern = re.compile("[\U00010000-\U0010ffff]", flags=re.UNICODE)
def clean_text(text):
    if not text:
        return ""
    text = emoji_pattern.sub('', text)  # remove emojis
    text = re.sub(r'\s+', ' ', text)    # normalize whitespace
    return text.strip()

# Selenium setup
options = Options()
options.add_argument('--headless')
options.add_argument('--disable-gpu')
driver = webdriver.Chrome(options=options)

base_url = 'https://taikai.network'
list_url = f"{base_url}/en/hackathons"

# Supabase save functions
def save_hackathon_data(slug, data):
    cleaned_data = {
        "slug": slug,
        "url": clean_text(data.get("url", "")),
        "description": clean_text(data.get("description", ""))
    }
    response = supabase.table("hackathons").upsert(cleaned_data, on_conflict=["slug"]).select("id").execute()
    if response.data:
        hackathon_id = response.data[0]["id"]
        print(f"✅ Saved hackathon {slug} with ID {hackathon_id}")
        return hackathon_id
    else:
        print(f"❌ Failed to save or retrieve hackathon ID for {slug}")
        return None

def save_project_data(hackathon_id, project_data):
    try:
        cleaned_data = {
            "hackathon_id": hackathon_id,
            "title": clean_text(project_data.get("title", "")),
            "description": clean_text(project_data.get("description", "")),
            "tags": sorted(set(clean_text(tag) for tag in project_data.get("tags", []))),
            "url": clean_text(project_data.get("url", ""))
        }
        supabase.table("projects").insert(cleaned_data).execute()
        print(f"✅ Saved project for hackathon ID {hackathon_id}")
    except Exception as e:
        print(f"❌ Failed to save project: {e}")

# Project data extraction
def extract_project_data(hackathon_id):
    time.sleep(1)
    soup = BeautifulSoup(driver.page_source, 'html.parser')

    title = ""
    title_container = soup.find('div', class_='iwSID')
    if title_container:
        h1 = title_container.find('h1')
        if h1:
            title = h1.get_text(strip=True)

    description = ""
    body_div = soup.find('div', class_='html-editor-body')
    if body_div:
        description = body_div.get_text(strip=True, separator='\n')

    tags = []
    for ul in soup.find_all('ul', class_='tags'):
        tags += [span.get_text(strip=True) for span in ul.find_all('span')]

    save_project_data(hackathon_id, {
        "title": title,
        "description": description,
        "tags": tags,
        "url": driver.current_url
    })

# Paginated project traversal
def extract_data_from_hackathon(hackathon_id):
    while True:
        time.sleep(1)
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        project_divs = soup.select('div.gFHDc')
        print(f"Found {len(project_divs)} projects.")

        for i, div in enumerate(project_divs):
            a_tag = div.find('a')
            if not a_tag or not a_tag.get('href'):
                continue
            project_url = urljoin(driver.current_url, a_tag['href'])
            driver.get(project_url)
            extract_project_data(hackathon_id)
            driver.back()
            time.sleep(1)

        # Pagination
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        next_btn = soup.select_one('ul.pagination li.next a')
        if not next_btn or next_btn.get('aria-disabled') == 'true':
            break
        try:
            driver.execute_script("arguments[0].click();", driver.find_element("css selector", 'ul.pagination li.next a'))
            time.sleep(2)
        except Exception as e:
            print(f"Pagination failed: {e}")
            break

# Hackathon handler
def navigate_to_hackathon(a_tag):
    href = a_tag.get('href')
    if not href:
        return

    slug = href.rstrip('/').split('/')[-1]
    full_url = urljoin(base_url, href)
    driver.get(full_url)
    time.sleep(1)

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    menu = soup.find('ul', class_='menu')
    if not menu:
        return

    hackathon_description = ""
    for li in menu.find_all('li'):
        a = li.find('a')
        if a and a.text.strip() == 'Overview':
            driver.get(urljoin(base_url, a['href']))
            time.sleep(1)
            overview_soup = BeautifulSoup(driver.page_source, 'html.parser')
            div = overview_soup.find('div', class_='html-editor-body')
            if div:
                hackathon_description = div.get_text(strip=True, separator='\n')
            break

    hackathon_id = save_hackathon_data(slug, {
        "url": full_url,
        "description": hackathon_description
    })
    if not hackathon_id:
        return

    for li in menu.find_all('li'):
        a = li.find('a')
        if a and a.text.strip() == 'Projects':
            driver.get(urljoin(base_url, a['href']))
            time.sleep(1)
            extract_data_from_hackathon(hackathon_id)
            break

# Initial scrape
def scrape():
    driver.get(list_url)
    time.sleep(1)

    while True:
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        hackathon_divs = soup.select('div.jIFIob')
        print(f"Found {len(hackathon_divs)} hackathons on current page.")

        for div in hackathon_divs:
            a_tag = div.find('a')
            if a_tag:
                 navigate_to_hackathon(a_tag)

        # Verificar si hay un botón "Next" habilitado
        next_button = soup.select_one('ul.pagination li.next a')
        if not next_button or next_button.get('aria-disabled') == 'true':
            print("✅ No more hackathon pages.")
            break

        # Click en el botón "Next"
        try:
            next_elem = driver.find_element("css selector", 'ul.pagination li.next a')
            driver.execute_script("arguments[0].click();", next_elem)
            print("➡️ Moving to next hackathon page...")
            time.sleep(2)
        except Exception as e:
            print(f"❌ Failed to click next page: {e}")
            break

# Run
if __name__ == '__main__':
    try:
        scrape()
    finally:
        driver.quit()
