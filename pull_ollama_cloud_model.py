import time
import subprocess
import sys
import os

# 固定存在 ~/.ollama/ 下，不受腳本執行位置影響
FAILED_LIST_PATH = os.path.join(os.path.expanduser("~"), ".ollama", "failed_models.txt")

def load_failed_models():
    if not os.path.exists(FAILED_LIST_PATH):
        return set()
    with open(FAILED_LIST_PATH, "r") as f:
        return set(line.strip().lower() for line in f if line.strip())

def save_failed_model(model_name):
    with open(FAILED_LIST_PATH, "a") as f:
        f.write(model_name.lower() + "\n")
    print(f"  [記錄] {model_name} 已寫入失敗清單，下次將跳過。")

def ensure_dependencies():
    """確保環境依賴已安裝"""
    # 1. 檢查並安裝 playwright python 套件
    try:
        import playwright
    except ImportError:
        print("[*] 正在安裝 Playwright 套件...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright"])
    
    # 2. 檢查並安裝 Chromium 瀏覽器核心
    # 我們嘗試啟動 playwright，如果失敗就執行 install
    print("[*] 正在檢查瀏覽器核心環境...")
    install_cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    try:
        # 這裡不直接啟動，而是執行安裝指令，Playwright 會自動跳過已安裝的部分
        subprocess.run(install_cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"[!] 安裝瀏覽器核心時發生錯誤，嘗試強制安裝: {e}")
        subprocess.run(install_cmd, check=True)

def get_installed_models():
    """取得目前 ollama 已安裝的模型清單"""
    try:
        result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
        installed = set()
        for line in result.stdout.splitlines()[1:]:  # 跳過標題行
            parts = line.split()
            if parts:
                installed.add(parts[0].lower())
        return installed
    except Exception:
        return set()

def scrape_and_pull():
    from playwright.sync_api import sync_playwright

    print("[*] 讀取已安裝的模型清單...")
    installed_models = get_installed_models()
    if installed_models:
        print(f"[*] 已安裝 {len(installed_models)} 個模型，掃描時將自動跳過。")

    failed_models = load_failed_models()
    if failed_models:
        print(f"[*] 讀取到 {len(failed_models)} 個上次失敗的模型，將自動跳過。")

    with sync_playwright() as p:
        print("[*] 啟動自動化引擎...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        page = context.new_page()

        page_num = 1
        total_installed = 0
        total_skipped = 0

        while True:
            url = f"https://ollama.com/search?c=cloud&page={page_num}"
            print(f"\n[*] 正在掃描第 {page_num} 頁: {url}")
            
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
                
                # 取得所有模型區塊 li
                items = page.query_selector_all('li') 
                
                if not items or len(items) == 0:
                    print(f"[!] 第 {page_num} 頁沒有更多模型，掃描結束。")
                    break

                found_in_page = 0
                for item in items:
                    name_el = item.query_selector('h2, span.text-lg, a[href^="/library/"]')
                    if not name_el: continue
                    
                    # 取得純淨的模型名稱
                    raw_text = name_el.inner_text().strip()
                    base_name = raw_text.split('\n')[0].split()[0]
                    
                    content = item.inner_text().lower()
                    
                    if "cloud" in content:
                        model_full = f"{base_name}:cloud"
                        model_key = model_full.lower()

                        if model_key in installed_models:
                            print(f"--- [跳過] {model_full} 已安裝 ---")
                            total_skipped += 1
                            found_in_page += 1
                            continue

                        if model_key in failed_models:
                            print(f"--- [跳過] {model_full} 上次失敗，略過 ---")
                            total_skipped += 1
                            found_in_page += 1
                            continue

                        print(f"--- [偵測到] {model_full} ---")
                        if run_ollama_pull(model_full):
                            installed_models.add(model_key)
                            found_in_page += 1
                            total_installed += 1
                        else:
                            save_failed_model(model_full)
                            failed_models.add(model_key)

                        time.sleep(1)

                if found_in_page == 0:
                    print(f"[!] 第 {page_num} 頁未發現新的 Cloud 模型。")
                    break
                
                page_num += 1

            except Exception as e:
                print(f"[錯誤] 處理頁面時發生異常: {e}")
                break

        browser.close()
        print(f"\n[任務完成] 新安裝 {total_installed} 個，跳過 {total_skipped} 個已安裝的雲端模型。")

def run_ollama_pull(model_name):
    try:
        # stdout 讓使用者看到進度，stderr 捕捉來偵測錯誤
        process = subprocess.run(
            ["ollama", "pull", model_name],
            stderr=subprocess.PIPE,
            text=True
        )
        # return code 不是 0 → 失敗
        if process.returncode != 0:
            if process.stderr:
                print(process.stderr.strip())
            return False
        # return code 是 0 但 stderr 含 error 關鍵字 → 也算失敗
        if process.stderr and "error" in process.stderr.lower():
            print(process.stderr.strip())
            return False
        return True
    except FileNotFoundError:
        print("[危險] 系統找不到 'ollama' 指令，請先安裝 Ollama 並加入 PATH。")
        return False

if __name__ == "__main__":
    print("========================================")
    print("   Ollama Cloud 一鍵掃描與安裝工具")
    print("========================================\n")
    
    # 第一步：環境自癒 (Self-healing environment)
    ensure_dependencies()
    
    # 第二步：執行爬蟲安裝
    scrape_and_pull()